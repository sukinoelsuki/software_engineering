"""``PolicyEngine.decide()`` 的行为断言（四格覆盖 + 两种相反的失败处置）。

覆盖 ``docs/design/interfaces/policy.md`` §2.4 的**四种** ``allow`` / ``requires_confirmation``
组合，以及 §2.5 的两条关键顺序约定：

* 求值异常 ⇒ **收敛为拒绝**（``False/True``，``risk_level=CRITICAL``），不得逃逸为 allow；
* ``AuditSink.emit()`` 异常 ⇒ **原样冒泡**，不得被上面的收敛吞掉。

这是实现侧的功能断言；"未授权操作拦截率 100%"一类安全行为断言属 ``tests/security/``，
由验证角色独立完成（安全断言不得由实现者自证）。
"""

from __future__ import annotations

import inspect
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from types import MappingProxyType
from typing import cast

import pytest

from agent_sec_perf.contracts.audit import AuditEvent, AuditEventKind, AuditOutcome
from agent_sec_perf.contracts.policy import Capability, PolicyDecision, PolicyRequest, RiskLevel
from agent_sec_perf.security.capabilities import CapabilitySet
from agent_sec_perf.security.policy import (
    _INVALID_LABEL_LIMIT,
    DEFAULT_TOOL_RISK,
    PolicyEngine,
)

#: ``detail["requested"]`` 允许出现的元素集合（审计里存的是 ``Capability`` 的**值**字符串）。
_CAPABILITY_VALUES = frozenset(member.value for member in Capability)


def _assert_policy_event_invariants(event: AuditEvent) -> None:
    """对每条 ``POLICY_DECISION`` 事件套用 ``audit.md`` §2.3 的 I1~I3（裁决 V6）。

    由 :meth:`RecordingSink.emit` 调用 ⇒ **每一条**策略用例都自动受检，而不是只在少数用例里
    手工断言一次（后者容易在新增分支时漏掉）。
    """
    assert event.kind is AuditEventKind.POLICY_DECISION

    # I1：关联键必须显式
    assert event.call_id is not None, "I1：POLICY_DECISION 的 call_id 不得为空"
    assert event.risk_level is not None, "I1：POLICY_DECISION 的 risk_level 不得为空"

    # I2：能力集合必须可回放（升序、去重、只用枚举值）
    assert "requested" in event.detail, "I2：POLICY_DECISION 事件必须携带 detail['requested']"
    requested = event.detail["requested"]
    assert isinstance(requested, list), "I2：detail['requested'] 必须是列表"
    assert all(isinstance(name, str) and name in _CAPABILITY_VALUES for name in requested), (
        "I2：元素必须是 Capability 的值"
    )
    assert requested == sorted(requested), "I2：detail['requested'] 必须升序"
    assert len(set(requested)) == len(requested), "I2：detail['requested'] 不得有重复"

    # I4：capability 必须是 Capability **实例**或 None——裸 str 即使取值合法也拒绝。
    # 放在 I3 之前：否则 `event.capability.value` 会先抛 AttributeError 而不是干净的断言失败。
    assert event.capability is None or isinstance(event.capability, Capability), (
        "I4：capability 必须是 Capability 实例或 None"
    )

    # I3：单值字段与集合字段必须一致
    if event.capability is None:
        assert requested == [], "I3：capability 为 None ⇔ detail['requested'] 为空"
    else:
        assert event.capability.value in requested, "I3：capability 必须是 requested 中的一员"


class RecordingSink:
    """审计落点替身：先套用不变式、再记录（不落盘，事件按调用顺序累积）。"""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []
        self.flush_calls = 0

    def emit(self, event: AuditEvent) -> None:
        _assert_policy_event_invariants(event)
        self.events.append(event)

    def flush(self) -> None:
        self.flush_calls += 1


class ExplodingSink(RecordingSink):
    """写入必失败的审计落点：用于证明审计故障**冒泡**而不是被收敛成一次普通拒绝。"""

    def emit(self, event: AuditEvent) -> None:
        del event
        raise OSError("审计写入失败（模拟磁盘故障）")


class ExplodingCapabilities:
    """能力模型替身：``missing()`` 直接抛错，用于触发求值阶段的收敛路径。"""

    def missing(self, requested: Iterable[Capability]) -> frozenset[Capability]:
        del requested
        raise RuntimeError("能力模型不可用（模拟内部故障）")


def _request(
    *,
    requested: object = frozenset({Capability.WRITE_FILE}),
    tool_name: str = "write_file",
    domain_pack: str | None = None,
    session_id: str = "session-1",
    call_id: str = "call-1",
) -> PolicyRequest:
    return PolicyRequest(
        session_id=session_id,
        call_id=call_id,
        tool_name=tool_name,
        arguments={},
        requested=cast("frozenset[Capability]", requested),
        domain_pack=domain_pack,
    )


def _engine(
    *,
    granted: Iterable[Capability] = (),
    tool_risk: Mapping[str, RiskLevel] | None = None,
    default_risk: RiskLevel = RiskLevel.MEDIUM,
    sink: RecordingSink | None = None,
) -> tuple[PolicyEngine, RecordingSink]:
    recorder = RecordingSink() if sink is None else sink
    engine = PolicyEngine(
        granted=CapabilitySet(granted=frozenset(granted)),
        sink=recorder,
        tool_risk={} if tool_risk is None else tool_risk,
        default_risk=default_risk,
    )
    return engine, recorder


def _broken_engine(
    *,
    tool_risk: Mapping[str, RiskLevel] | None = None,
    default_risk: RiskLevel = RiskLevel.MEDIUM,
    sink: RecordingSink | None = None,
) -> tuple[PolicyEngine, RecordingSink]:
    """能力模型损坏的引擎：``missing()`` 必抛，用于触发求值阶段的收敛路径。"""
    recorder = RecordingSink() if sink is None else sink
    engine = PolicyEngine(
        granted=cast("CapabilitySet", ExplodingCapabilities()),
        sink=recorder,
        tool_risk={} if tool_risk is None else tool_risk,
        default_risk=default_risk,
    )
    return engine, recorder


# ---------------------------------------------------------------------------
# 四格：自动放行 / 须人工确认 / 可升级拒绝 / 硬拒绝
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_low_risk_granted_request_is_allowed_without_confirmation() -> None:
    """已授权 + 低风险 ⇒ 自动放行（``True`` / ``False``）。"""
    engine, sink = _engine(granted=[Capability.READ_FILE], tool_risk={"read_file": RiskLevel.LOW})

    decision = engine.decide(
        _request(requested=frozenset({Capability.READ_FILE}), tool_name="read_file")
    )

    assert (decision.allow, decision.requires_confirmation) == (True, False)
    assert decision.risk_level is RiskLevel.LOW
    assert sink.events[0].outcome is AuditOutcome.ALLOW


@pytest.mark.unit
def test_medium_risk_granted_request_requires_confirmation() -> None:
    """已授权 + 常规特权操作 ⇒ 须人工确认（``True`` / ``True``）。"""
    engine, sink = _engine(
        granted=[Capability.WRITE_FILE], tool_risk={"write_file": RiskLevel.MEDIUM}
    )

    decision = engine.decide(_request())

    assert (decision.allow, decision.requires_confirmation) == (True, True)
    assert decision.risk_level is RiskLevel.MEDIUM
    assert sink.events[0].outcome is AuditOutcome.CONFIRM


@pytest.mark.unit
def test_high_risk_granted_request_requires_confirmation_with_risk_explanation() -> None:
    """高代价/难以撤销 ⇒ 须人工确认，且理由**必须**给出风险说明（``REQ-SEC-02``）。"""
    engine, _ = _engine(granted=[Capability.WRITE_FILE], tool_risk={"write_file": RiskLevel.HIGH})

    decision = engine.decide(_request())

    assert (decision.allow, decision.requires_confirmation) == (True, True)
    assert decision.risk_level is RiskLevel.HIGH
    assert "high" in decision.reason
    assert "确认" in decision.reason


@pytest.mark.unit
def test_critical_risk_request_is_hard_denied_even_with_capability_granted() -> None:
    """不可逆/高危 ⇒ 硬拒绝（``False`` / ``False``）：**连人工确认也不接受**（``REQ-SEC-08``）。"""
    engine, sink = _engine(
        granted=[Capability.NETWORK_OUTBOUND],
        tool_risk={"send_data": RiskLevel.CRITICAL},
    )

    decision = engine.decide(
        _request(requested=frozenset({Capability.NETWORK_OUTBOUND}), tool_name="send_data")
    )

    assert (decision.allow, decision.requires_confirmation) == (False, False)
    assert decision.risk_level is RiskLevel.CRITICAL
    assert sink.events[0].outcome is AuditOutcome.DENY


@pytest.mark.unit
def test_ungranted_capability_is_hard_denied_and_not_escalatable() -> None:
    """未授权 ⇒ 硬拒绝：授权集合只能由显式配置改变，单次确认不得扩权（``REQ-SEC-01``）。"""
    engine, sink = _engine(granted=[], tool_risk={"read_file": RiskLevel.LOW})

    decision = engine.decide(_request(requested=frozenset({Capability.READ_FILE})))

    assert (decision.allow, decision.requires_confirmation) == (False, False)
    assert decision.risk_level is RiskLevel.CRITICAL
    assert sink.events[0].capability is Capability.READ_FILE
    assert "未授权" in decision.reason


@pytest.mark.unit
def test_partial_grant_denies_the_whole_request() -> None:
    """多能力请求缺一个即整体拒绝（不得按"部分满足"降级执行）。"""
    engine, _ = _engine(granted=[Capability.READ_FILE], tool_risk={"read_file": RiskLevel.LOW})

    decision = engine.decide(
        _request(requested=frozenset({Capability.READ_FILE, Capability.EXECUTE_COMMAND}))
    )

    assert (decision.allow, decision.requires_confirmation) == (False, False)


@pytest.mark.unit
def test_request_without_capabilities_is_denied_even_when_everything_is_granted() -> None:
    """V1：**即使全部能力都已授予**，空集请求仍硬拒绝——"没有要求"不等于"没有限制"。

    且必须**仍然留下**一条 ``POLICY_DECISION`` 事件（禁止用"不审计"回避契约冲突）。
    """
    engine, sink = _engine(granted=list(Capability))

    decision = engine.decide(_request(requested=frozenset()))

    assert (decision.allow, decision.requires_confirmation) == (False, False)
    assert decision.risk_level is RiskLevel.CRITICAL
    assert "未声明" in decision.reason
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.outcome is AuditOutcome.DENY
    assert event.capability is None
    assert event.detail["requested"] == []


@pytest.mark.unit
def test_four_combinations_are_all_reachable() -> None:
    """四格全部可达且互不混淆（只测 ``allow`` 一个布尔会漏掉三格）。"""
    low, _ = _engine(granted=[Capability.READ_FILE], tool_risk={"read_file": RiskLevel.LOW})
    medium, _ = _engine(granted=[Capability.WRITE_FILE], tool_risk={"write_file": RiskLevel.MEDIUM})
    critical, _ = _engine(
        granted=[Capability.WRITE_FILE], tool_risk={"write_file": RiskLevel.CRITICAL}
    )
    broken, _ = _broken_engine(tool_risk={"write_file": RiskLevel.LOW})

    produced = {
        (decision.allow, decision.requires_confirmation): decision
        for decision in (
            low.decide(
                _request(requested=frozenset({Capability.READ_FILE}), tool_name="read_file")
            ),
            medium.decide(_request()),
            critical.decide(_request()),
            broken.decide(_request()),
        )
    }

    assert set(produced) == {(True, False), (True, True), (False, True), (False, False)}
    assert produced[(False, True)].risk_level is RiskLevel.CRITICAL
    assert produced[(False, False)].risk_level is RiskLevel.CRITICAL


# ---------------------------------------------------------------------------
# 求值失败：收敛为拒绝；审计失败：冒泡（两者处置相反）
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_evaluation_failure_converges_to_escalatable_denial() -> None:
    """求值阶段任何异常都收敛为 ``allow=False, requires_confirmation=True, CRITICAL``。"""
    engine, sink = _broken_engine(tool_risk={"write_file": RiskLevel.LOW})

    decision = engine.decide(_request())

    assert (decision.allow, decision.requires_confirmation) == (False, True)
    assert decision.risk_level is RiskLevel.CRITICAL
    assert "策略求值失败" in decision.reason
    assert len(sink.events) == 1
    assert sink.events[0].outcome is AuditOutcome.CONFIRM


@pytest.mark.unit
def test_evaluation_failure_records_requested_but_never_missing() -> None:
    """V4（裁决 §2.5）：失败路径必须记 ``detail["requested"]``，且**不得**写 ``missing``。

    写 ``missing: []`` 会让这条事件读起来像"无缺失 ⇒ 已授权"，把**故障伪装成授权充足**。
    """
    engine, sink = _broken_engine(tool_risk={"write_file": RiskLevel.LOW})

    decision = engine.decide(_request())

    event = sink.events[0]
    assert decision.allow is False
    assert event.capability is Capability.WRITE_FILE
    assert event.detail["requested"] == ["write_file"]
    assert "missing" not in event.detail
    assert event.detail["error"] == "RuntimeError"


@pytest.mark.unit
def test_evaluation_failure_does_not_leak_exception_message_into_audit() -> None:
    """只记录异常**类型名**：异常消息可能内嵌参数内容，而审计要长期留存。"""
    engine, sink = _broken_engine()

    engine.decide(_request())

    detail = sink.events[0].detail
    assert detail["error"] == "RuntimeError"
    assert "能力模型不可用" not in str(detail)


@pytest.mark.unit
def test_unparsable_capability_request_still_converges_to_denial() -> None:
    """V5：调用方误传 ``None`` 不能让异常逃逸（决策是返回值），且证据仍完整。

    ``requested == []`` 与"确为空集"共用同一取值，由 ``detail`` 是否含 ``error`` 键区分
    （``audit.md`` §2.3 的 I2）。
    """
    engine, sink = _engine(granted=list(Capability))

    decision = engine.decide(_request(requested=None))

    assert decision.allow is False
    assert decision.requires_confirmation is True
    event = sink.events[0]
    assert event.capability is None
    assert event.detail["requested"] == []
    assert "error" in event.detail


@pytest.mark.unit
def test_bare_string_with_valid_value_cannot_buy_authorization() -> None:
    """`1b`/`I4`：**裸 `str` 即使取值合法也必须拒绝**——这是一条实测过的 default-deny 绕过。

    机理：`StrEnum` 成员与其 `str` 值 `==`/`hash` 相等 ⇒
    `frozenset({"read_file"}) - frozenset({Capability.READ_FILE})` 是**空集** ⇒
    若按名字判"缺失"，就会得出"无缺失 ⇒ 已授权" ⇒ **`allow=True`**。
    ⇒ 判据必须是**类型检查**（`isinstance`），不能是名字/取值检查。
    """
    engine, sink = _engine(granted=[Capability.READ_FILE])

    decision = engine.decide(_request(requested=frozenset({"read_file"})))

    assert decision.allow is False, "裸 str 换到了授权：default-deny 被绕过"
    assert decision.requires_confirmation is False, "应是硬拒绝，不得给人确认放行的通道"
    assert decision.risk_level is RiskLevel.CRITICAL
    event = sink.events[0]
    assert event.capability is None
    assert event.detail["requested"] == []
    assert event.detail["invalid"] == ["read_file"]


@pytest.mark.unit
def test_non_string_member_rejects_whole_request_and_never_echoes_content() -> None:
    """`1b`：非 `str` 成员**只记类型名**；且**整体拒绝**（不得用合法子集继续求值）。"""
    engine, sink = _engine(granted=list(Capability))

    decision = engine.decide(_request(requested=frozenset({Capability.WRITE_FILE, 7})))

    assert decision.allow is False
    event = sink.events[0]
    assert event.capability is None, "含非法成员时不得另挑一个合法成员当代表"
    assert event.detail["requested"] == [], "不得只记合法子集（会被读成'合法部分被考虑了'）"
    assert event.detail["invalid"] == ["<int>"], "非 str 只记类型名"
    assert "7" not in str(event.detail["invalid"]), "不得回显内容"


@pytest.mark.unit
def test_invalid_member_names_are_sanitized_and_truncated() -> None:
    """非法成员名来自**不可信输入** ⇒ 先脱敏、再截断，且不进 `reason`（审计是长期证据）。"""
    engine, sink = _engine(granted=list(Capability))
    nasty = "bad\nname" + "x" * 100

    decision = engine.decide(_request(requested=frozenset({nasty})))

    recorded = sink.events[0].detail["invalid"]
    assert isinstance(recorded, list) and len(recorded) == 1
    only = recorded[0]
    assert isinstance(only, str)
    assert "\n" not in only, "换行必须被中和（否则审计按行读时会被拆开）"
    # `limit` 是**正文**字符数上限，截断时会追加省略号标记 ⇒ 总长上限为 limit + 1。
    assert len(only) <= _INVALID_LABEL_LIMIT + 1, "必须截断（+1 = 省略号标记）"
    assert only.endswith("…"), "截断必须有可见标记（不得静默切短）"
    assert nasty not in decision.reason, "面向用户的理由不得回显不可信取值"


@pytest.mark.unit
def test_equal_valued_bare_string_is_collapsed_and_is_not_a_bypass() -> None:
    """边界（刻意记录）：**同值裸 `str` 在 `frozenset` 构造时就与枚举成员折叠**。

    `frozenset({Capability.WRITE_FILE, "write_file"})` 只有**一个**元素 ⇒"含非法成员"这一
    信息在进入引擎前已消失 ⇒ 既**不可检测**、也**不构成绕过**（有效请求就等于那个等值能力）。
    此处按已授予与否正常判定，**不得**据此放宽任何分支。
    """
    engine, sink = _engine(granted=[Capability.WRITE_FILE])

    decision = engine.decide(_request(requested=frozenset({Capability.WRITE_FILE, "write_file"})))

    assert sink.events[0].detail.get("invalid") is None, "该情形不可检测（集合已折叠）"
    assert decision.allow is True, "等值能力已授予 ⇒ 正常放行，不是绕过"
    assert isinstance(sink.events[0].capability, Capability), "I4：代表必须是实例"


@pytest.mark.unit
def test_invariant_helper_actually_catches_violations() -> None:
    """自检探针：不变式断言函数**能**失败（否则它在所有用例里恒真 = 没有在检查）。"""
    violating = AuditEvent(
        event_id="e",
        kind=AuditEventKind.POLICY_DECISION,
        timestamp="2026-09-19T08:06:08+00:00",
        session_id="s",
        outcome=AuditOutcome.DENY,
        call_id="c",
        risk_level=RiskLevel.LOW,
        capability=None,  # 与 requested 非空矛盾（违反 I3）
        detail={"requested": ["read_file"]},
    )

    with pytest.raises(AssertionError):
        _assert_policy_event_invariants(violating)


@pytest.mark.unit
def test_audit_failure_propagates_instead_of_being_swallowed() -> None:
    """审计写入失败必须冒泡——静默丢事件等于 ``REQ-SEC-06`` 验收失败。"""
    engine, _ = _engine(granted=[Capability.WRITE_FILE], sink=ExplodingSink())

    with pytest.raises(OSError):
        engine.decide(_request())


@pytest.mark.unit
def test_audit_failure_is_not_disguised_as_a_policy_denial() -> None:
    """审计故障不得被伪装成"普通拒绝"（两种表象都是"没执行"，但性质不同）。"""
    exploding = ExplodingSink()
    engine, _ = _broken_engine(sink=exploding)

    with pytest.raises(OSError):
        engine.decide(_request())


# ---------------------------------------------------------------------------
# 审计内容与可回放
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_every_decision_emits_one_event_with_matching_audit_id() -> None:
    """每次决策恰好一条 ``POLICY_DECISION`` 事件，``audit_id`` 与 ``event_id`` 同源。"""
    engine, sink = _engine(granted=[Capability.WRITE_FILE], tool_risk={"write_file": RiskLevel.LOW})

    decision = engine.decide(_request(session_id="s-9", call_id="c-9", tool_name="write_file"))

    assert len(sink.events) == 1
    event = sink.events[0]
    assert decision.audit_id == event.event_id
    assert len(decision.audit_id) == 32
    assert event.kind is AuditEventKind.POLICY_DECISION
    assert (event.session_id, event.call_id, event.tool_name) == ("s-9", "c-9", "write_file")
    assert event.risk_level is decision.risk_level
    assert event.capability is Capability.WRITE_FILE
    assert event.timestamp.endswith("+00:00")


@pytest.mark.unit
def test_denied_decision_records_missing_capabilities_for_replay() -> None:
    """拒绝必须留下"缺什么"的证据（``REQ-SEC-06``：每次拒绝均有可回放记录）。"""
    engine, sink = _engine(granted=[Capability.READ_FILE])

    decision = engine.decide(
        _request(requested=frozenset({Capability.READ_FILE, Capability.EXECUTE_COMMAND}))
    )

    detail = sink.events[0].detail
    assert detail["missing"] == ["execute_command"]
    assert detail["requested"] == ["execute_command", "read_file"]
    assert detail["reason"] == decision.reason
    assert sink.events[0].capability is Capability.EXECUTE_COMMAND


@pytest.mark.unit
def test_multi_capability_request_uses_the_lexicographically_smallest_representative() -> None:
    """V3：多能力请求合法（不得拒绝）；``capability`` 取**名字字典序最小**者，重复调用一致。

    多元素是"先读后写"这类操作的正常形态；代表规则必须与集合迭代顺序无关，
    否则同一输入在不同版本会给出不同审计内容，历史事件无法比对（``policy.md`` §2.5）。
    """
    engine, sink = _engine(
        granted=[Capability.READ_FILE, Capability.WRITE_FILE],
        tool_risk={"write_file": RiskLevel.LOW},
    )
    requested = frozenset({Capability.READ_FILE, Capability.WRITE_FILE})

    engine.decide(_request(requested=requested, tool_name="write_file"))
    engine.decide(_request(requested=requested, tool_name="write_file"))

    assert [event.capability for event in sink.events] == [Capability.READ_FILE] * 2
    assert sink.events[0].detail["requested"] == ["read_file", "write_file"]
    assert sink.events[0].detail["missing"] == []


@pytest.mark.unit
def test_domain_pack_is_recorded_in_the_event_detail() -> None:
    """领域包标识进入审计（``REQ-HARNESS-08``：包只能声明策略，声明内容要可追溯）。"""
    engine, sink = _engine(granted=[Capability.READ_FILE])

    engine.decide(_request(requested=frozenset({Capability.READ_FILE}), domain_pack="coding"))

    assert sink.events[0].detail["domain_pack"] == "coding"


# ---------------------------------------------------------------------------
# 风险规则
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_unregistered_tool_uses_conservative_default_risk() -> None:
    """未声明风险的工具取保守默认（须人工确认），不得被当成低风险自动放行。"""
    engine, _ = _engine(granted=[Capability.READ_FILE], default_risk=DEFAULT_TOOL_RISK)

    decision = engine.decide(
        _request(requested=frozenset({Capability.READ_FILE}), tool_name="mystery")
    )

    assert (decision.allow, decision.requires_confirmation) == (True, True)
    assert decision.risk_level is DEFAULT_TOOL_RISK
    assert DEFAULT_TOOL_RISK is not RiskLevel.LOW


@pytest.mark.unit
def test_domain_pack_scoped_risk_takes_precedence_over_tool_risk() -> None:
    """带包前缀的声明优先于工具级声明（同一工具在不同领域包里可以不同）。"""
    engine, _ = _engine(
        granted=[Capability.WRITE_FILE],
        tool_risk={
            "write_file": RiskLevel.LOW,
            "coding:write_file": RiskLevel.CRITICAL,
        },
    )

    scoped = engine.decide(_request(domain_pack="coding"))
    unscoped = engine.decide(_request(domain_pack=None))

    assert scoped.risk_level is RiskLevel.CRITICAL
    assert (scoped.allow, scoped.requires_confirmation) == (False, False)
    assert unscoped.risk_level is RiskLevel.LOW
    assert unscoped.allow is True


@pytest.mark.unit
def test_invalid_risk_level_in_rules_is_rejected_at_construction() -> None:
    """规则里塞进非 ``RiskLevel``（如 TOML 字符串忘了转换）⇒ 构造即失败，不留到判定时。"""
    with pytest.raises(TypeError):
        PolicyEngine(
            granted=CapabilitySet(),
            sink=RecordingSink(),
            tool_risk={"write_file": cast("RiskLevel", "high")},
        )

    with pytest.raises(TypeError):
        PolicyEngine(
            granted=CapabilitySet(),
            sink=RecordingSink(),
            default_risk=cast("RiskLevel", "high"),
        )


@pytest.mark.unit
def test_invalid_risk_error_does_not_echo_untrusted_text() -> None:
    """非法风险等级的错误信息不原样回显工具名（防换行/转义序列伪造日志）。"""
    with pytest.raises(TypeError) as excinfo:
        PolicyEngine(
            granted=CapabilitySet(),
            sink=RecordingSink(),
            tool_risk={"evil\nname\x1b[31m": cast("RiskLevel", "high")},
        )

    message = str(excinfo.value)
    assert "\n" not in message
    assert "\x1b" not in message


@pytest.mark.unit
def test_tool_risk_rules_are_copied_and_read_only() -> None:
    """规则表在构造时被复制为只读视图：外部再改原字典也不影响已构造的引擎。"""
    rules = {"write_file": RiskLevel.LOW}
    engine, _ = _engine(granted=[Capability.WRITE_FILE], tool_risk=rules)

    rules["write_file"] = RiskLevel.CRITICAL

    assert engine.decide(_request()).risk_level is RiskLevel.LOW
    assert isinstance(engine.tool_risk, MappingProxyType)
    with pytest.raises(TypeError):
        cast("dict[str, RiskLevel]", engine.tool_risk)["write_file"] = RiskLevel.CRITICAL


# ---------------------------------------------------------------------------
# 不变式与其它
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_decision_invariants_hold_for_a_range_of_scenarios() -> None:
    """两条不变式：``CRITICAL ⇒ not allow``；``(not allow and not confirm) ⇒ CRITICAL``。"""
    scenarios: list[PolicyDecision] = []
    for granted, tool_risk, default_risk in (
        ([Capability.READ_FILE], {"read_file": RiskLevel.LOW}, RiskLevel.MEDIUM),
        ([Capability.WRITE_FILE], {"write_file": RiskLevel.MEDIUM}, RiskLevel.MEDIUM),
        ([Capability.WRITE_FILE], {"write_file": RiskLevel.HIGH}, RiskLevel.MEDIUM),
        ([Capability.WRITE_FILE], {"write_file": RiskLevel.CRITICAL}, RiskLevel.MEDIUM),
        ([], {"write_file": RiskLevel.LOW}, RiskLevel.MEDIUM),
        ([Capability.WRITE_FILE], {}, RiskLevel.HIGH),
    ):
        engine, _ = _engine(granted=granted, tool_risk=tool_risk, default_risk=default_risk)
        scenarios.append(engine.decide(_request(requested=frozenset({Capability.WRITE_FILE}))))

    for decision in scenarios:
        assert decision.reason.strip() != ""
        assert decision.audit_id != ""
        if decision.risk_level is RiskLevel.CRITICAL:
            assert decision.allow is False
        if not decision.allow and not decision.requires_confirmation:
            assert decision.risk_level is RiskLevel.CRITICAL


@pytest.mark.unit
def test_tool_name_with_control_characters_is_neutralized_in_reason() -> None:
    """理由会展示给用户，工具名来自模型输出 ⇒ 展示前必须中和换行/转义序列。"""
    engine, sink = _engine(granted=[Capability.WRITE_FILE], tool_risk={"evil": RiskLevel.HIGH})

    decision = engine.decide(_request(tool_name="evil\ntool\x1b[31m"))

    assert "\n" not in decision.reason
    assert "\x1b" not in decision.reason
    # 审计字段保留原始值（证据不被改写），由 JSON 序列化负责转义。
    assert sink.events[0].tool_name == "evil\ntool\x1b[31m"


@pytest.mark.unit
def test_decide_is_deterministic_apart_from_the_audit_id() -> None:
    """同一输入连续判定：决策相同、审计关联键各不相同（每次都留一条证据）。"""
    engine, sink = _engine(
        granted=[Capability.WRITE_FILE], tool_risk={"write_file": RiskLevel.MEDIUM}
    )

    first = engine.decide(_request())
    second = engine.decide(_request())

    assert (first.allow, first.requires_confirmation, first.risk_level, first.reason) == (
        second.allow,
        second.requires_confirmation,
        second.risk_level,
        second.reason,
    )
    assert first.audit_id != second.audit_id
    assert len(sink.events) == 2


@pytest.mark.unit
def test_grants_and_sink_have_no_defaults() -> None:
    """``granted`` / ``sink`` 无默认值：不允许"忘了传"退化成全权或不审计。"""
    parameters = inspect.signature(PolicyEngine).parameters

    assert parameters["granted"].default is inspect.Parameter.empty
    assert parameters["sink"].default is inspect.Parameter.empty


@pytest.mark.unit
def test_decide_is_safe_to_call_from_multiple_threads() -> None:
    """无状态、纯函数式：多线程并发判定不串味、不丢事件（并发契约见 §2.5）。"""
    engine, sink = _engine(granted=[Capability.WRITE_FILE], tool_risk={"write_file": RiskLevel.LOW})
    calls = 40

    with ThreadPoolExecutor(max_workers=8) as pool:
        decisions = list(pool.map(lambda _: engine.decide(_request()), range(calls)))

    assert len(decisions) == calls
    assert all(decision.allow for decision in decisions)
    assert len(sink.events) == calls
    assert len({decision.audit_id for decision in decisions}) == calls
