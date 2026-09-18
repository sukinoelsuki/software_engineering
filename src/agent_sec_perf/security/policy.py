"""策略求值：``PolicyEngine.decide()`` 的实现（``REQ-SEC-01/02/06/08``，横切 SEC 层）。

契约（字段、四格语义、错误处置）见 ``docs/design/interfaces/policy.md`` §2.4/§2.5；
本模块**只实现**它，不改契约。

**实现顺序是契约的一部分**（§2.5），因此代码结构与它逐条对应：

```text
1. 求值（风险判定 + 能力检查）   ← 用 try/except 收敛为"拒绝"，任何异常都不得逃逸为 allow
2. 计算 risk_level
3. 生成 audit_id
4. 构造 AuditEvent(kind=POLICY_DECISION)
5. sink.emit(event)              ← **不**包 try：审计失败必须冒泡（与第 1 步处置相反）
6. 返回 PolicyDecision(audit_id=audit_id)
```

为什么 1 与 5 处置相反：若把 ``emit`` 也放进第 1 步的 ``except``，一次**审计基础设施故障**
会被伪装成一次**普通的策略拒绝**——两者表象都是"没执行"，但一个是正常的默认拒绝、
一个是系统坏了，混为一谈就等于静默丢弃审计证据（``REQ-SEC-06`` 验收失败）。

**四格覆盖**（``allow`` 与 ``requires_confirmation`` 的组合，§2.4）：

| 情形 | 取值 | 本实现的来源 |
| --- | --- | --- |
| 自动放行 | ``True`` / ``False`` | 已授权 + ``risk_level = LOW`` |
| 须人工确认 | ``True`` / ``True`` | 已授权 + ``MEDIUM`` / ``HIGH``（含"工具未声明风险"的保守默认） |
| 可升级拒绝 | ``False`` / ``True`` | **求值失败**（契约指定的收敛点） |
| 硬拒绝 | ``False`` / ``False`` | ``CRITICAL``；以及**能力未授予**（授权集合只能由显式配置改变，单次确认不得扩权） |

不变式（由单测钉住）：``risk_level is CRITICAL ⇒ allow is False``；
``not allow and not requires_confirmation ⇒ risk_level is CRITICAL``。

本模块无 I/O 之外的副作用、无内部可变状态（``tool_risk`` 构造后为只读视图），可多线程调用。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import MappingProxyType

from agent_sec_perf.contracts import policy as policy_contract
from agent_sec_perf.contracts.audit import AuditEvent, AuditEventKind, AuditOutcome, AuditSink
from agent_sec_perf.contracts.policy import Capability, PolicyDecision, PolicyRequest, RiskLevel
from agent_sec_perf.foundation.logging import sanitize_for_display
from agent_sec_perf.security.capabilities import CapabilitySet

__all__ = ["DEFAULT_TOOL_RISK", "PolicyEngine"]

#: 未声明风险等级的工具的保守默认：**必须人工确认**。
#: 取 ``HIGH`` 而不是 ``LOW``/``MEDIUM``：把"未知"解释成"低风险"是 fail-open；
#: 也不取 ``CRITICAL``——那会让"未声明风险"和"不可逆操作"混为一谈，而工具的**存在性**
#: 由 ``ToolRegistry.resolve()`` 负责，不由风险评估负责。
DEFAULT_TOOL_RISK = RiskLevel.HIGH

#: 日志/展示中工具名一类不可信文本的最大展示长度（防"理由"被塞进海量文本）。
_TOOL_LABEL_LIMIT = 64

#: ``detail["invalid"]`` 中单个**非法**成员名的最大展示长度。与 ``parse_capabilities()``
#: 回显未知名时的既有口径一致：那是不信输入，而审计是长期留存的证据。
_INVALID_LABEL_LIMIT = 32

#: 决策理由的文案（中文面向用户：``REQ-UX-04`` 中文优先，``REQ-SEC-02`` 必须展示风险说明）。
_REASON_GRANTED_LOW = "低风险（risk_level=low）且所需能力已授予：自动放行"
_REASON_CONFIRM_MEDIUM = "常规特权操作（risk_level=medium）：需人工确认后执行"
_REASON_CONFIRM_HIGH = (
    "高代价或难以撤销的操作（risk_level=high）：需人工确认，并请核对操作目标与后果"
)
_REASON_DENY_CRITICAL = (
    "高危或不可逆操作（risk_level=critical）：系统不予执行，也不接受单次确认（REQ-SEC-08）"
)
_REASON_DENY_UNGRANTED = (
    "未授权操作：缺少所需能力，能力只能由显式配置授予，单次确认不改变授权集合（REQ-SEC-01）"
)
_REASON_DENY_NO_CAPABILITY = "策略请求未声明所需能力，无法对照授权集合求值：按 fail-secure 拒绝"
_REASON_DENY_INVALID_CAPABILITY = (
    "策略请求包含非法的能力成员（必须是 Capability 实例）：整个请求按 fail-secure 拒绝"
)


@dataclass(frozen=True)
class _Verdict:
    """一次求值的内部结果（尚未生成审计关联键）。"""

    allow: bool
    requires_confirmation: bool
    risk_level: RiskLevel
    reason: str
    capability: Capability | None = None
    extra: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicyEngine(policy_contract.PolicyEngine):
    """策略引擎：无状态、纯函数式，逐次求值。

    Args:
        granted: **显式授予**的能力集合（``CapabilitySet()`` = 什么都不授予）。
            刻意**无默认值**：调用方必须显式表态，避免"忘了传"退化成全权。
        sink: 审计落点。刻意**无默认值**：不存在"悄悄不审计"的默认实现。
        tool_risk: 工具风险等级声明；键为 ``tool_name``，或
            ``"{domain_pack}:{tool_name}"``（**带包前缀的键优先**，用于领域包声明自己的安全策略，
            ``REQ-HARNESS-08``）。未声明的工具取 :data:`DEFAULT_TOOL_RISK`。
        default_risk: 未声明工具的风险等级；构造时校验必须是 ``RiskLevel``。
    """

    granted: CapabilitySet
    sink: AuditSink
    tool_risk: Mapping[str, RiskLevel] = field(default_factory=dict)
    default_risk: RiskLevel = DEFAULT_TOOL_RISK

    def __post_init__(self) -> None:
        """把规则表复制成**只读视图**并就地校验风险等级。

        复制的原因：规则是安全决策的输入，构造后被谁改了都不该影响本次会话的判定
        （调用方持有的原字典再被修改，也不得改动已构造的引擎）。
        校验的原因：配置里的风险等级可能来自 TOML 字符串一类的**未转换**来源，
        非法值必须在构造点暴露，而不是在第一次判定时悄悄退化成别的等级。
        """
        checked = {
            str(tool): _validated_risk(level, tool=str(tool))
            for tool, level in self.tool_risk.items()
        }
        object.__setattr__(self, "tool_risk", MappingProxyType(checked))
        object.__setattr__(
            self, "default_risk", _validated_risk(self.default_risk, tool="默认风险等级")
        )

    def decide(self, request: PolicyRequest) -> PolicyDecision:
        """对一次操作求权限，返回可断言、可回放的决策。

        Raises:
            Exception: **审计写入失败**原样冒泡（``sink.emit`` 的异常不得被本方法吞掉）；
                求值阶段的异常则**不会**冒泡——它们收敛为 ``allow=False`` 的决策。
        """
        verdict = self._evaluate_safely(request)

        audit_id = uuid.uuid4().hex
        event = AuditEvent(
            event_id=audit_id,
            kind=AuditEventKind.POLICY_DECISION,
            timestamp=datetime.now(tz=UTC).isoformat(),
            session_id=request.session_id,
            outcome=_outcome_of(verdict),
            call_id=request.call_id,
            tool_name=request.tool_name,
            capability=verdict.capability,
            risk_level=verdict.risk_level,
            detail={**verdict.extra, "reason": verdict.reason},
        )
        # 契约 §2.5 第 5 步：此调用**不得**被 try/except 包住（审计失败必须冒泡）。
        self.sink.emit(event)

        return PolicyDecision(
            allow=verdict.allow,
            requires_confirmation=verdict.requires_confirmation,
            risk_level=verdict.risk_level,
            reason=verdict.reason,
            audit_id=audit_id,
        )

    # ------------------------------------------------------------------
    # 求值（第 1 步）
    # ------------------------------------------------------------------

    def _evaluate_safely(self, request: PolicyRequest) -> _Verdict:
        """求值阶段的 fail-secure 收敛（契约 §2.5 第 1 步）。

        捕获 ``Exception`` 而不是更窄的类型，是有意的：契约要求**任何**求值故障都收敛为拒绝。
        窄捕获会让未列举的故障逃逸成"调用失败"，而调用方一旦按"没拒绝就是允许"处理，
        就等于绕过了策略——这正是 fail-open 的入口。

        只记录异常**类型名**，不记录异常消息：消息可能内嵌参数内容（不可信数据），
        而审计是长期留存的证据。

        事件内容按 ``policy.md`` §2.5「补充规定」：``detail["requested"]`` **必存在**（升序能力名；
        不可解析记 ``[]``），且**不写** ``detail["missing"]``——``[]`` 的语义是"无缺失 ⇒ 已授权"，
        用它表示"求值失败"会把**故障伪装成授权充足**（``audit.md`` §2.3 的 I2）。
        """
        try:
            return self._evaluate(request)
        except Exception as exc:
            error_type = type(exc).__name__
            members = _requested_members(request)
            return _Verdict(
                allow=False,
                requires_confirmation=True,
                risk_level=RiskLevel.CRITICAL,
                reason=f"策略求值失败（{error_type}）：按 fail-secure 默认拒绝，须人工确认后再决定",
                capability=members[0] if members else None,
                extra={"requested": [member.value for member in members], "error": error_type},
            )

    def _evaluate(self, request: PolicyRequest) -> _Verdict:
        """规则匹配 + 能力检查（**分支顺序是契约的一部分**：`1a` 空集 → `1b` 类型 → `1c` 常规）。"""
        # 先把请求里的能力集合定下来。
        requested = frozenset(request.requested)

        # ---- 1a：空集（`policy.md` §2.5「补充规定」）------------------------------------
        # 空集时没有能力可写进 ``AuditEvent.capability``，而 `audit.md` §2.3 要求该字段在
        # POLICY_DECISION 上非 None ⇒ 视为**请求不合法**，fail-secure 拒绝（宁可不做，不可乱做）。
        # 刻意**不**取 `False/True`：那会让"所需能力未知"变成**可被人确认放行**的通道，
        # 即绕过 default-deny。
        if not requested:
            return _Verdict(
                allow=False,
                requires_confirmation=False,
                risk_level=RiskLevel.CRITICAL,
                reason=_REASON_DENY_NO_CAPABILITY,
                capability=None,
                extra={"requested": [], "missing": [], "domain_pack": request.domain_pack},
            )

        # ---- 1b：含非 `Capability` 成员 ⇒ **整体拒绝** ----------------------------------
        # 判据**必须是类型检查**，不能是名字/取值检查：`StrEnum` 成员与其 `str` 值 `==`/`hash`
        # 相等，于是 `frozenset({"read_file"}) - frozenset({Capability.READ_FILE})` 是**空集**
        # ⇒ 若按名字判"缺失"，会得出"无缺失 ⇒ 已授权" ⇒ **`allow=True`**（2026-09-19 实测的
        # default-deny 绕过；`policy.md` §2.5 已把它写成 `I4`）。
        # 也**不得**"忽略非法成员、用合法子集继续求值"：**丢弃哪些成员由不可信输入决定**，
        # 那是 fail-open，且静默缩小了请求面。
        invalid = _invalid_members(requested)
        if invalid:
            return _Verdict(
                allow=False,
                requires_confirmation=False,
                risk_level=RiskLevel.CRITICAL,
                reason=_REASON_DENY_INVALID_CAPABILITY,
                capability=None,
                # 刻意写 `[]` 而**不是**合法子集：`capability is None ⇔ requested == []`（I3）
                # 必须成立；只记合法子集会让读者以为"合法部分被考虑了"。
                # 该分支**不写** `missing`——请求本身不合法，缺失语义无意义。
                extra={
                    "requested": [],
                    "invalid": invalid,
                    "domain_pack": request.domain_pack,
                },
            )

        # ---- 1c：常规求值 ---------------------------------------------------------------
        # 缺失集合（未授权判定的唯一依据）与风险等级（带包前缀 > 工具级 > 保守默认）。
        missing = self.granted.missing(requested)
        risk = self._risk_for(request)

        reporter = {
            "requested": sorted(str(member) for member in requested),
            "missing": sorted(str(member) for member in missing),
            "domain_pack": request.domain_pack,
        }

        if missing:
            return _Verdict(
                allow=False,
                requires_confirmation=False,
                risk_level=RiskLevel.CRITICAL,
                reason=_REASON_DENY_UNGRANTED,
                capability=_smallest(missing),
                extra=reporter,
            )

        if risk is RiskLevel.CRITICAL:
            return _Verdict(
                allow=False,
                requires_confirmation=False,
                risk_level=RiskLevel.CRITICAL,
                reason=_REASON_DENY_CRITICAL,
                capability=_smallest(requested),
                extra=reporter,
            )

        if risk is RiskLevel.LOW:
            return _Verdict(
                allow=True,
                requires_confirmation=False,
                risk_level=RiskLevel.LOW,
                reason=_REASON_GRANTED_LOW,
                capability=_smallest(requested),
                extra=reporter,
            )

        high = risk is RiskLevel.HIGH
        return _Verdict(
            allow=True,
            requires_confirmation=True,
            risk_level=risk,
            reason=_REASON_CONFIRM_HIGH if high else _REASON_CONFIRM_MEDIUM,
            capability=_smallest(requested),
            extra=reporter,
        )

    def _risk_for(self, request: PolicyRequest) -> RiskLevel:
        """逐次求值风险等级：带领域包前缀的声明 > 工具级声明 > 保守默认。

        为什么带包前缀：同一工具在不同领域包里的危险度可以不同，而领域包**只能声明**
        策略、不能新增代码（``REQ-HARNESS-08``）；用同一个映射的键前缀表达，避免为它
        再引入一层嵌套结构。
        """
        tool_name = request.tool_name
        if request.domain_pack is not None:
            scoped = self.tool_risk.get(f"{request.domain_pack}:{tool_name}")
            if scoped is not None:
                return _validated_risk(scoped, tool=tool_name)
        declared = self.tool_risk.get(tool_name, self.default_risk)
        return _validated_risk(declared, tool=tool_name)


# ----------------------------------------------------------------------
# 内部工具
# ----------------------------------------------------------------------


def _validated_risk(value: object, *, tool: str) -> RiskLevel:
    """校验风险等级取值。

    形参类型是 ``object``（调用点把已声明为 ``RiskLevel`` 的值传进来）：
    安全关键配置的**运行期**校验不能只靠类型标注——标注挡不住来自 TOML/JSON 的字符串。
    非法值即抛错，由求值阶段的收敛逻辑变成一次拒绝。
    """
    if isinstance(value, RiskLevel):
        return value
    shown = sanitize_for_display(tool, limit=_TOOL_LABEL_LIMIT)
    msg = f"风险等级非法（工具 {shown}）：必须是 RiskLevel 成员"
    raise TypeError(msg)


def _smallest(members: Iterable[Capability]) -> Capability:
    """取名字最小的能力（**确定性**：审计与测试都要求同一输入给出同一结果）。"""
    return min(members, key=str)


def _requested_members(request: PolicyRequest) -> tuple[Capability, ...]:
    """求值失败时**尽力**取出请求中的合法能力成员（按名字升序）；不可解析时返回空元组。

    只用于收敛分支（正常路径直接对原始集合求值）。由它统一产出
    ``capability`` 与 ``detail["requested"]``，从而让 ``audit.md`` §2.3 的 **I3**
    （``capability is None`` ⇔ ``requested == []``；否则 ``capability.value ∈ requested``）
    由**构造**保证，而不是事后补一次校验——校验可能被跳过，构造不会。

    非 ``Capability`` 的元素被剔除：它们的"名字"不是契约规定的枚举值，写进 ``requested``
    会让 I2 也不成立。请求完全不可解析（例如调用方误传 ``None``）时返回空元组，
    这是对该情形的**显式**处理、不是吞异常——失败原因由 ``detail["error"]`` 承载。
    """
    try:
        raw = frozenset(request.requested)
    except TypeError:
        return ()
    members = [member for member in raw if isinstance(member, Capability)]
    return tuple(sorted(members, key=str))


def _invalid_members(requested: frozenset[object]) -> list[str]:
    """列出请求里**不是 `Capability` 实例**的成员（升序去重、已脱敏；空列表 = 全部合法）。

    判据必须是**类型检查**：`StrEnum` 成员与其 `str` 值 `==`/`hash` 相等
    ⇒ `frozenset({"read_file"}) - frozenset({Capability.READ_FILE})` 是空集
    ⇒ 按名字判"缺失"会得出"无缺失 ⇒ 已授权" ⇒ **default-deny 被绕过**（2026-09-19 实测）。

    取值处理（`policy.md` §2.5）：`str` 先**脱敏**并截断（不可信输入，而审计是长期留存的证据）；
    非 `str` **只记类型名**（如 `<int>`），不回显内容。
    **返回值只是数据**——不得写进 `capability`/`requested`，也不得据它做权限判定或解析。
    """
    names: set[str] = set()
    for member in requested:
        if isinstance(member, Capability):
            continue
        if isinstance(member, str):
            names.add(sanitize_for_display(member, limit=_INVALID_LABEL_LIMIT))
        else:
            names.add(f"<{type(member).__name__}>")
    return sorted(names)


def _outcome_of(verdict: _Verdict) -> AuditOutcome:
    """把决策映射成审计结果（``docs/design/interfaces/audit.md`` §2.2 的 kind→outcome 约束）。

    ``allow=False, requires_confirmation=True``（可升级拒绝）映射为 ``CONFIRM``：
    自动路径**尚未执行**，仍等人工决定，语义上正是"待人工确认"。
    """
    if verdict.allow:
        return AuditOutcome.ALLOW if not verdict.requires_confirmation else AuditOutcome.CONFIRM
    if verdict.requires_confirmation:
        return AuditOutcome.CONFIRM
    return AuditOutcome.DENY
