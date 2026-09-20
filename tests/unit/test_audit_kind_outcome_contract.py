"""``audit.md`` §2.2 的 kind→outcome 约束的**机器检查**（文档 ↔ 声明 ↔ 实际调用点）。

**为什么需要本文件**：``contracts/audit.py`` 只在 docstring 里**指向** §2.2 的约束表，
``JsonlAuditSink.emit`` 也不校验 ``kind`` 与 ``outcome`` 的组合 ⇒ 该约束此前**没有任何机器检查**，
违反它不会让任何门禁变红（静默违反）。

**本文件的边界（重要）**：只做**测试侧**检查，**不改变任何运行期行为**——不加异常、
不动 ``AuditSink`` 契约、不动 ``AuditEvent`` 字段、不引入运行期校验。

三层各管什么（**请勿只留一层**，单靠任一层都会漏）：

1. **表 ↔ 声明**：把 ``docs/design/interfaces/audit.md`` §2.2 的表格**解析出来**，与
   :data:`DECLARED_ALLOWED_OUTCOMES` **双向**比对（表里有而声明里缺、声明里有而表里缺，
   均判红）。声明放在**测试侧**：生产代码里没有显式的允许集，而把它塞进 ``src/`` 会把
   "零行为契约层"（``ADR-0015`` §5.1）的边界问题拉进来——故按最小改动落在本文件。
2. **声明 ↔ 实际行为**：真的触发四个 ``emit`` 调用点（``tools/registry.py``、
   ``harness/loop.py``、``security/policy.py``、``cli/approval.py``），断言**实际产出的组合**
   都落在声明集合内，且**恰好**是预期的那几个。⇒ 表与声明一致**不等于**实现照着做，
   这一层是"实现真的没违反"的证据。
3. **变异探针**（三条）：① 表外组合被判违规、表内组合不误报；② 收窄声明 ⇒ (a) 与 (b) 都翻红；
   ③ 内存里改文档 ⇒ 解析结果随之变化（证明解析器读的是文档，不是硬编码副本）。

⚠️ **探针的强度上限（如实标注，不得当更强结论用）**：三条探针证明的都是"比较函数真的挂在
**文档文本 / 声明 / 实际调用点**上"，**不是**"该检查真的挂在生产 ``emit`` 上"——本轮**没有**
运行期校验，故**未经触发的调用路径仍可能产出表外组合而不被拦下**（(b) 只覆盖被触发的路径）。
运行期取舍见本笔提交正文的"需领导裁决"。
"""

from __future__ import annotations

import io
import pathlib
import re
from collections.abc import Mapping, Sequence
from typing import Final

import pytest

from agent_sec_perf.cli.approval import InteractiveApprovalGate
from agent_sec_perf.contracts.audit import AuditEvent, AuditEventKind, AuditOutcome
from agent_sec_perf.contracts.harness import ApprovalRequest, SessionConfig
from agent_sec_perf.contracts.model import FinishReason, ModelResponse, TokenUsage
from agent_sec_perf.contracts.policy import (
    Capability,
    PolicyDecision,
    PolicyRequest,
    RiskLevel,
)
from agent_sec_perf.contracts.tools import (
    ExecutionContext,
    ToolCallRequest,
    ToolResult,
    ToolSpec,
)
from agent_sec_perf.harness.loop import TaskLoop
from agent_sec_perf.security.capabilities import CapabilitySet
from agent_sec_perf.security.policy import PolicyEngine
from agent_sec_perf.tools.registry import audit_tool_call

#: 契约文档（``parse`` 的对象就是它；路径变了本文件必须跟着改，故失败信息写清预期位置）。
CONTRACT_PATH: Final = (
    pathlib.Path(__file__).resolve().parents[2] / "docs" / "design" / "interfaces" / "audit.md"
)

#: §2.2 的小节边界：约束表只允许出现在这一节里。
_SECTION_START: Final = "### 2.2"
_SECTION_END: Final = "### 2.3"

#: 约束表的表头特征（不靠"第几个表"这种位置约定：§2.2 内另有一张 `D1`~`D4` 的表）。
_TABLE_HEADER_HINTS: Final = ("`kind`", "`outcome`", "允许的")

#: Markdown 表格行/分隔行的行首字符（据此判断"约束表到哪结束"）。
_TABLE_LINE_PREFIX: Final = "|"

#: 表格单元格里的 ``代码`` 记号（``**`DENY`**`` 这类强调也一并取到反引号里的内容）。
_BACKTICKED: Final = re.compile(r"`([A-Za-z_]+)`")

#: §2.2 约束表的**机器可读声明**（**派生自**该表，不是另一套口径）。
#: 放在测试侧的理由见模块 docstring 第 1 条；若将来把它提升进 ``src/``，本文件应改为 import，
#: 而不是再留一份副本（两份必然漂移）。
DECLARED_ALLOWED_OUTCOMES: Final[Mapping[AuditEventKind, frozenset[AuditOutcome]]] = {
    AuditEventKind.POLICY_DECISION: frozenset(
        {AuditOutcome.ALLOW, AuditOutcome.DENY, AuditOutcome.CONFIRM}
    ),
    AuditEventKind.APPROVAL: frozenset({AuditOutcome.ALLOW, AuditOutcome.DENY}),
    AuditEventKind.TOOL_CALL: frozenset({AuditOutcome.OK, AuditOutcome.ERROR, AuditOutcome.DENY}),
    AuditEventKind.EXECUTION_DEGRADATION: frozenset({AuditOutcome.DEGRADED}),
    AuditEventKind.REFUSAL: frozenset({AuditOutcome.DENY}),
}

#: 测试用的会话标识与工作目录（本文件不触碰文件系统，故无需 ``tmp_path``）。
SESSION_ID: Final = "s-contract"
WORKING_DIR: Final = pathlib.Path("/tmp/lowspec-contract-audit-contract")


# ---------------------------------------------------------------------------
# 解析与比较（纯函数：探针直接喂给它违规输入，不必先把违规写进生产代码）
# ---------------------------------------------------------------------------


def _contract_text() -> str:
    """读契约文档；不在预期位置时**当场失败**并指出去哪找。"""
    assert CONTRACT_PATH.is_file(), f"契约文档不在预期位置：{CONTRACT_PATH}"
    return CONTRACT_PATH.read_text(encoding="utf-8")


def section(text: str, *, start: str, end: str) -> str:
    """取出 ``[start, end)`` 之间的小节文本（结构变了就失败，不静默返回整篇）。"""
    start_at = text.find(start)
    end_at = text.find(end, start_at + 1)
    assert start_at != -1, f"契约文档里找不到小节起点 {start!r}"
    assert end_at != -1, f"契约文档里找不到小节终点 {end!r}（结构已变，机器检查需同步）"
    return text[start_at:end_at]


def _outcome_member(name: str) -> AuditOutcome:
    """按**枚举成员名**取 ``AuditOutcome``（表里写的是 ``ALLOW`` 这类成员名，不是取值）。

    取值是 ``"allow"``：用 ``AuditOutcome("ALLOW")`` 会抛 ``ValueError``。表里出现未知名字
    即失败——那说明文档引用了不存在的成员，必须当场看见。
    """
    assert name in AuditOutcome.__members__, f"§2.2 的表里出现未知的 outcome 成员名：{name!r}"
    return AuditOutcome[name]


def _kind_member(name: str) -> AuditEventKind:
    """按**枚举成员名**取 ``AuditEventKind``（同上；未知名字即失败）。"""
    assert name in AuditEventKind.__members__, f"§2.2 的表里出现未知的 kind 成员名：{name!r}"
    return AuditEventKind[name]


def parse_contract_table(text: str) -> dict[AuditEventKind, frozenset[AuditOutcome]]:
    """把 §2.2 的 kind→outcome 约束表解析成 ``{kind: {outcome, ...}}``。

    Raises:
        AssertionError: 表头找不到、行形状不是两格、或单元格里没有可识别的枚举名。
            刻意**不**容忍这几种情况：解析不出来却"返回空表"会让一致性检查变成恒过。
    """
    block = section(text, start=_SECTION_START, end=_SECTION_END)
    lines = block.splitlines()
    header_at = next(
        (
            index
            for index, line in enumerate(lines)
            if line.startswith(_TABLE_LINE_PREFIX)
            and all(hint in line for hint in _TABLE_HEADER_HINTS)
        ),
        None,
    )
    assert header_at is not None, "§2.2 里找不到 `| `kind` | 允许的 `outcome` |` 表头"

    table: dict[AuditEventKind, frozenset[AuditOutcome]] = {}
    for line in lines[header_at + 2 :]:  # +2：跳过表头与其下的分隔行
        if not line.startswith(_TABLE_LINE_PREFIX):
            break
        cells = [cell.strip() for cell in line.strip().strip(_TABLE_LINE_PREFIX).split("|")]
        assert len(cells) == 2, f"约束表每行必须恰好两格：{line!r}"
        kinds = _BACKTICKED.findall(cells[0])
        outcomes = _BACKTICKED.findall(cells[1])
        assert len(kinds) == 1, f"约束行的第一格必须恰好一个 kind 名：{line!r}"
        assert outcomes, f"约束行的第二格没有可识别的 outcome 名：{line!r}"
        table[_kind_member(kinds[0])] = frozenset(_outcome_member(name) for name in outcomes)

    assert table, "§2.2 的约束表解析出 0 行（表头定位到了非约束表？）"
    return table


def declaration_problems(
    table: Mapping[AuditEventKind, frozenset[AuditOutcome]],
    declared: Mapping[AuditEventKind, frozenset[AuditOutcome]],
) -> list[str]:
    """双向比对"表"与"声明"，返回人类可读的问题列表（空列表 = 一致）。

    两个方向都报：**表里有而声明里缺** ⇒ 某一行根本不受检查；
    **声明里有而表里缺** ⇒ 声明在偷偷放宽（多出来的取值没有文档依据）。
    """
    problems: list[str] = []
    for kind in sorted(set(table) | set(declared), key=str):
        in_table = table.get(kind)
        in_declared = declared.get(kind)
        if in_table is None:
            problems.append(f"{kind.value}：声明里有、§2.2 的表里缺（该 kind 未被文档约束）")
            continue
        if in_declared is None:
            problems.append(f"{kind.value}：§2.2 的表里有、声明里缺（整行不受检查）")
            continue
        for outcome in sorted(in_table - in_declared, key=str):
            problems.append(f"{kind.value}+{outcome.value}：表里有、声明里缺")
        for outcome in sorted(in_declared - in_table, key=str):
            problems.append(f"{kind.value}+{outcome.value}：声明里有、表里缺")
    return problems


def violations(
    combos: Sequence[tuple[AuditEventKind, AuditOutcome]],
    allowed: Mapping[AuditEventKind, frozenset[AuditOutcome]],
) -> list[str]:
    """返回 ``combos`` 里**超出** ``allowed`` 的组合（空列表 = 全部合规）。

    未知 kind 一律判违规（``allowed.get`` 的空集），不让"枚举值拼错"变成静默放行。
    """
    return sorted(
        f"{kind.value}+{outcome.value}"
        for kind, outcome in combos
        if outcome not in allowed.get(kind, frozenset())
    )


# ---------------------------------------------------------------------------
# (a) 文档 ↔ 声明
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_contract_table_and_declaration_agree_in_both_directions() -> None:
    """§2.2 的表与测试侧声明必须逐项一致（含 TOOL_CALL 放宽后的三取值）。"""
    table = parse_contract_table(_contract_text())

    problems = declaration_problems(table, DECLARED_ALLOWED_OUTCOMES)

    assert problems == [], f"§2.2 的表与声明不一致（任一方向都判红）：{problems}"


@pytest.mark.unit
def test_every_event_kind_has_a_constraint_row() -> None:
    """``AuditEventKind`` 的每个成员都必须有约束行。

    否则"新增一个 kind 却不写约束"会静默通过：新 kind 的 outcome 无人检查。
    （新增 kind 本身需 ADR——本用例只保证"加了就必须同时补约束"。）
    """
    table = parse_contract_table(_contract_text())

    missing = sorted((kind.value for kind in set(AuditEventKind) - set(table)), key=str)

    assert missing == [], f"以下 kind 在 §2.2 的表里没有约束行：{missing}"


# ---------------------------------------------------------------------------
# (b) 实际调用点：四个 emit 生产者各自实际产出的组合
# ---------------------------------------------------------------------------


def _observed(events: Sequence[AuditEvent]) -> set[tuple[AuditEventKind, AuditOutcome]]:
    """事件序列里出现过的 ``(kind, outcome)`` 组合（去重）。"""
    return {(event.kind, event.outcome) for event in events}


def _assert_within_declaration(combos: set[tuple[AuditEventKind, AuditOutcome]]) -> None:
    """:func:`violations` 的断言封装（四个调用点共用同一条判据）。"""
    offenders = violations(sorted(combos, key=str), DECLARED_ALLOWED_OUTCOMES)
    assert offenders == [], f"实际产出的组合超出 §2.2 的允许集：{offenders}"


class _RecordingSink:
    """``AuditSink`` 替身：只追加（不落盘，避免把路径白名单牵扯进来）。"""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def emit(self, event: AuditEvent) -> None:
        self.events.append(event)

    def flush(self) -> None:
        return None


def _context() -> ExecutionContext:
    """工具层审计用的执行上下文（``audit_tool_call`` 只读其中的关联键）。"""
    return ExecutionContext(
        session_id=SESSION_ID,
        call_id="c-tool",
        working_dir=WORKING_DIR,
        allowed_roots=(WORKING_DIR.parent,),
        timeout_s=5.0,
    )


def test_tools_layer_audit_call_only_produces_ok_and_error() -> None:
    """``tools/registry.py::audit_tool_call``：只产出 ``OK`` / ``ERROR``（``D3``）。"""
    sink = _RecordingSink()

    audit_tool_call(sink, ctx=_context(), tool_name="read_file", ok=True)
    audit_tool_call(sink, ctx=_context(), tool_name="read_file", ok=False)

    observed = _observed(sink.events)
    _assert_within_declaration(observed)
    assert observed == {
        (AuditEventKind.TOOL_CALL, AuditOutcome.OK),
        (AuditEventKind.TOOL_CALL, AuditOutcome.ERROR),
    }


def _policy_request(*, tool_name: str, requested: frozenset[Capability]) -> PolicyRequest:
    return PolicyRequest(
        session_id=SESSION_ID,
        call_id="c-policy",
        tool_name=tool_name,
        arguments={},
        requested=requested,
    )


def test_policy_layer_produces_allow_confirm_and_deny() -> None:
    """``security/policy.py``：三格实际可达（放行 / 待确认 / 硬拒绝），均须在允许集内。"""
    sink = _RecordingSink()
    engine = PolicyEngine(
        granted=CapabilitySet(granted=frozenset({Capability.WRITE_FILE})),
        sink=sink,
        tool_risk={"low_tool": RiskLevel.LOW},
    )

    engine.decide(
        _policy_request(tool_name="low_tool", requested=frozenset({Capability.WRITE_FILE}))
    )
    engine.decide(
        _policy_request(tool_name="mid_tool", requested=frozenset({Capability.WRITE_FILE}))
    )
    engine.decide(
        _policy_request(tool_name="deny_tool", requested=frozenset({Capability.EXECUTE_COMMAND}))
    )

    observed = _observed(sink.events)
    _assert_within_declaration(observed)
    assert observed == {
        (AuditEventKind.POLICY_DECISION, AuditOutcome.ALLOW),
        (AuditEventKind.POLICY_DECISION, AuditOutcome.CONFIRM),
        (AuditEventKind.POLICY_DECISION, AuditOutcome.DENY),
    }


class _FakeApprovalInput:
    """``ApprovalInput`` 替身：固定为"可交互 + 指定答复"。"""

    def __init__(self, answer: str) -> None:
        self._answer = answer

    def is_interactive(self) -> bool:
        return True

    def read_line(self, *, timeout_s: float) -> str | None:
        del timeout_s
        return self._answer


def _answer_once(sink: _RecordingSink, answer: str) -> None:
    """用真实 gate 走一次确认（``cli/approval.py::_record`` 是 ``APPROVAL`` 的生产者）。"""
    gate = InteractiveApprovalGate(
        sink=sink,
        source=_FakeApprovalInput(answer),
        prompt_stream=io.StringIO(),
        timeout_s=1.0,
    )
    gate.request(
        ApprovalRequest(
            session_id=SESSION_ID,
            call_id="c-approval",
            tool_name="write_file",
            risk_level=RiskLevel.HIGH,
            reason="测试理由",
        )
    )


def test_approval_layer_produces_only_allow_and_deny() -> None:
    """``cli/approval.py``：一次允许、一次拒绝，均须在允许集内。"""
    sink = _RecordingSink()

    _answer_once(sink, "1")
    _answer_once(sink, "3")

    observed = _observed(sink.events)
    _assert_within_declaration(observed)
    assert observed == {
        (AuditEventKind.APPROVAL, AuditOutcome.ALLOW),
        (AuditEventKind.APPROVAL, AuditOutcome.DENY),
    }


class _FakeModel:
    """按脚本返回响应的模型替身；脚本用尽即失败（"多请求一次"不得被静默吞掉）。"""

    def __init__(self, responses: Sequence[ModelResponse]) -> None:
        self._script = list(responses)

    def chat(
        self,
        messages: Sequence[object],
        *,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        timeout_s: float = 60.0,
    ) -> ModelResponse:
        del messages, tools, temperature, max_tokens, timeout_s
        assert self._script, "模型脚本已用尽：用例给出的响应数少于循环实际请求数"
        return self._script.pop(0)

    def close(self) -> None:
        return None


class _FakeTool:
    """工具替身：``invoke`` 直接抛错（复现"工具内部错误"这条审计路径）。"""

    def __init__(self, spec: ToolSpec, *, error: BaseException) -> None:
        self._spec = spec
        self._error = error

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def invoke(self, args: Mapping[str, object], *, ctx: ExecutionContext) -> ToolResult:
        del args, ctx
        raise self._error


class _FakeRegistry:
    """``ToolRegistry`` 替身：全名集与句柄来自同一批工具。"""

    def __init__(self, tools: Sequence[_FakeTool]) -> None:
        self._by_name = {tool.spec.name: tool for tool in tools}

    def specs(self) -> tuple[ToolSpec, ...]:
        return tuple(tool.spec for tool in self._by_name.values())

    def resolve(self, name: str) -> _FakeTool | None:
        return self._by_name.get(name)


class _AllowValidator:
    """``ArgumentValidator`` 替身：参数一律视为合法（参数校验本身不属本文件）。"""

    def validate(self, *, spec: ToolSpec, arguments_json: str) -> Mapping[str, object]:
        del spec, arguments_json
        return {}


class _AllowPolicy:
    """``PolicyEngine`` 替身：恒放行。

    ``POLICY_DECISION`` 的产出由 :func:`test_policy_layer_produces_allow_confirm_and_deny`
    用**真实**引擎覆盖；这里只负责把流程推到执行步，避免两处重复。
    """

    def decide(self, request: PolicyRequest) -> PolicyDecision:
        del request
        return PolicyDecision(
            allow=True,
            requires_confirmation=False,
            risk_level=RiskLevel.LOW,
            reason="测试替身：恒放行",
            audit_id="stub-policy-audit",
        )


def _spec(name: str, *capabilities: Capability) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"{name}（测试用）",
        parameters_schema={"type": "object", "properties": {}, "additionalProperties": False},
        capabilities=frozenset(capabilities),
    )


def _call(name: str, *, call_id: str = "c-loop") -> ToolCallRequest:
    return ToolCallRequest(call_id=call_id, name=name, arguments_json="{}")


def _response(*calls: ToolCallRequest, content: str | None = None) -> ModelResponse:
    return ModelResponse(
        content=content,
        tool_calls=tuple(calls),
        finish_reason=FinishReason.TOOL_CALLS if calls else FinishReason.STOP,
        usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        model_id="fake-model",
    )


def _build_loop(
    *,
    sink: _RecordingSink,
    responses: Sequence[ModelResponse],
    tools: Sequence[_FakeTool] = (),
    exposed: Sequence[ToolSpec] = (),
) -> TaskLoop:
    return TaskLoop(
        session_id=SESSION_ID,
        config=SessionConfig(working_dir=WORKING_DIR, allowed_roots=(WORKING_DIR.parent,)),
        model=_FakeModel(responses),
        registry=_FakeRegistry(tools),
        exposed=tuple(exposed),
        policy=_AllowPolicy(),
        approval=None,
        sink=sink,
        validator=_AllowValidator(),
        system="SYSTEM（测试常量）",
    )


@pytest.mark.unit
def test_harness_loop_deny_path_produces_the_deny_outcome() -> None:
    """``harness/loop.py`` 的**未执行**路径（未知工具）⇒ 一条 ``TOOL_CALL/DENY``（``D1``/``D3``）。"""
    sink = _RecordingSink()
    loop = _build_loop(
        sink=sink,
        responses=[_response(_call("hallucinated")), _response(content="到此结束")],
    )

    list(loop.run("任务"))

    observed = _observed(sink.events)
    _assert_within_declaration(observed)
    assert observed == {(AuditEventKind.TOOL_CALL, AuditOutcome.DENY)}


@pytest.mark.unit
def test_harness_loop_tool_exception_path_produces_the_error_outcome() -> None:
    """``harness/loop.py`` 的**已执行但抛错**路径 ⇒ 一条 ``TOOL_CALL/ERROR``。"""
    sink = _RecordingSink()
    spec = _spec("boom", Capability.WRITE_FILE)
    loop = _build_loop(
        sink=sink,
        responses=[_response(_call("boom")), _response(content="到此结束")],
        tools=[_FakeTool(spec, error=RuntimeError("工具内部错误（测试注入）"))],
        exposed=[spec],
    )

    list(loop.run("任务"))

    observed = _observed(sink.events)
    _assert_within_declaration(observed)
    assert observed == {(AuditEventKind.TOOL_CALL, AuditOutcome.ERROR)}


# ---------------------------------------------------------------------------
# (c) 变异探针：证明上面三层检查不是恒过
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_the_check_flags_an_out_of_table_combination() -> None:
    """变异探针①：**表外组合**必须被判违规（对违规输入会失败）。

    ⚠️ 强度上限：这证明的是"比较函数会判违规"；**不**证明生产 ``emit`` 上挂了该检查。
    """
    table = parse_contract_table(_contract_text())
    out_of_table = [(AuditEventKind.TOOL_CALL, AuditOutcome.ALLOW)]

    # 前提：``TOOL_CALL + ALLOW`` 确实**不在** §2.2 的允许集里（否则探针本身失效）。
    assert AuditOutcome.ALLOW not in table[AuditEventKind.TOOL_CALL]

    assert violations(out_of_table, table) == ["tool_call+allow"]
    # 反向：表内组合不得被误报——否则"永远报违规"也能让上面的断言通过。
    assert violations([(AuditEventKind.TOOL_CALL, AuditOutcome.DENY)], table) == []


@pytest.mark.unit
def test_declaration_comparison_catches_a_narrowed_declaration() -> None:
    """变异探针②：**把声明收窄**（不动文档）⇒ (a) 的双向比对必须翻红。

    复现的是真实失效模式：有人为了"让实现过"而私改允许集，而文档仍写着更宽的口径。
    顺带证明 (b) 用的同一判据也会因此翻红——即调用点断言不是"对任何声明都绿"。
    """
    table = parse_contract_table(_contract_text())
    narrowed = dict(DECLARED_ALLOWED_OUTCOMES)
    narrowed[AuditEventKind.TOOL_CALL] = DECLARED_ALLOWED_OUTCOMES[AuditEventKind.TOOL_CALL] - {
        AuditOutcome.DENY
    }

    assert declaration_problems(table, narrowed) == ["tool_call+deny：表里有、声明里缺"]
    # (b) 方向：实现**实际**产出的 ``TOOL_CALL/DENY``（见上面 loop 的用例）会被收窄后的允许集判违规。
    loop_observed = [(AuditEventKind.TOOL_CALL, AuditOutcome.DENY)]
    assert violations(loop_observed, narrowed) == ["tool_call+deny"]


@pytest.mark.unit
def test_the_parser_reads_the_document_text_it_is_given() -> None:
    """变异探针③：**改文档**（内存里改，不动仓库文件）⇒ 解析结果随之变化。

    证明解析器真的在**读文档**，而不是读一份会与文档漂移的硬编码副本
    （否则 (a) 会退化成"声明与声明自己一致"）。
    """
    text = _contract_text()
    block = section(text, start=_SECTION_START, end=_SECTION_END)
    mutated_block = re.sub(r"(\| `TOOL_CALL` \|).*", r"\1 `OK` / `ERROR` |", block, count=1)
    assert mutated_block != block, "探针失效：没有改到 `TOOL_CALL` 那一行"
    mutated_text = text.replace(block, mutated_block, 1)

    mutated_table = parse_contract_table(mutated_text)

    assert AuditOutcome.DENY not in mutated_table[AuditEventKind.TOOL_CALL]
    assert declaration_problems(mutated_table, DECLARED_ALLOWED_OUTCOMES) == [
        "tool_call+deny：声明里有、表里缺"
    ]
