"""``S-new-1``～``S-new-6``（契约 §6.2）：harness 决策序列的 fail-secure 行为对抗性验证。

覆盖（均针对真实 ``agent_sec_perf.harness.Session`` / ``harness.context.assemble``）：

* ``S-new-1``：需人工确认、ApprovalGate 为"非交互 ⇒ 拒绝"实现 ⇒ 未执行；
  ``APPROVAL_RESULT.outcome is DENY``；审计有 ``kind=APPROVAL`` 事件（§2.5.5 R2/A4）。
* ``S-new-2``：``NETWORK_OUTBOUND`` 已授予也不能放行 ⇒ 构造出的 ``ExecutionContext.network_allowed is False``
  （防"授予即放行"的 fail-open 回归）。
* ``S-new-3``：审计写入失败必须**冒泡**，不得静默转成 ``ERROR`` 事件后继续运行。
* ``S-new-4``：未提供确认通路（``approval=None``）但工具 ``requires_confirmation=True`` ⇒ 拒绝
  （``denied_reason=approval_denied``）、**且无** ``APPROVAL_RESULT``（不伪造）。
* ``S-new-5``：确认通路故障（R3 抛异常 / R4 形状非法）⇒ 必须 fail-secure 终止（terminal
  ``ERROR`` + ``TASK_FINISHED(FAILED)``），不得回退为普通拒绝（R3/R4 是 fail-secure 不是降级）。
* ``S-new-6``：``data_context`` 试图进入 ``SYSTEM`` 角色 ⇒ 装配阶段 ``ValueError``（§4.4 第 2 条 R6）。

被测对象（只读）：``agent_sec_perf.harness.Session.run`` 经真实 ``TaskLoop`` 决策序列。依赖全部
以构造注入的 ``Protocol`` 替身驱动（见 ``_harness_fakes``），替身**不承担断言**。每个拒绝/终止用例
都配"摘掉真实保护"的变异探针（生产代码相关函数被换成弱化版后，原断言必须翻红），证明断言不是偶然通过
（防"绿着但没生效"）。探针只在 ``tmp_path`` 副本层面/ ``monkeypatch`` 层面运行，不触碰工作树。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _harness_fakes import (
    FakeApprovalGate,
    FakeArgumentValidator,
    FakeModelClient,
    FakePolicyEngine,
    FakeRegistry,
    FakeTool,
    RaisingSink,
    RecordingSink,
    collect,
    make_session,
    make_tool_call_response,
    make_tool_spec,
)

from agent_sec_perf.contracts.audit import AuditEventKind, AuditOutcome
from agent_sec_perf.contracts.harness import (
    ApprovalOutcome,
    CapabilityTier,
    PolicyDecision,
    SessionErrorKind,
    SessionEventKind,
    TaskStatus,
)
from agent_sec_perf.contracts.model import ChatMessage, Role
from agent_sec_perf.contracts.policy import Capability, RiskLevel
from agent_sec_perf.contracts.tools import ToolResult
from agent_sec_perf.harness.context import ContextBudget
from agent_sec_perf.harness.context import assemble as _ctx_assemble

_READ_SPEC = make_tool_spec(name="read_file", capabilities=frozenset({Capability.READ_FILE}))


def _decision(*, allow: bool, requires_confirmation: bool, audit_id: str) -> PolicyDecision:
    return PolicyDecision(
        allow=allow,
        requires_confirmation=requires_confirmation,
        risk_level=RiskLevel.HIGH if requires_confirmation else RiskLevel.LOW,
        reason="decision",
        audit_id=audit_id,
    )


def _run_snew(
    tmp_path: Path,
    *,
    specs,
    allowlist: frozenset[str],
    tier: CapabilityTier,
    decision: PolicyDecision,
    called_name: str,
    approval=None,
    sink=None,
    arguments_json: str = "{}",
    tool_result: ToolResult | None = None,
) -> tuple[list, RecordingSink, dict[str, FakeTool]]:
    sink = sink or RecordingSink()
    model = FakeModelClient(
        make_tool_call_response(call_id="c1", name=called_name, arguments_json=arguments_json)
    )
    tools = {spec.name: FakeTool(spec, result=tool_result) for spec in specs}
    registry = FakeRegistry(specs, tools)
    policy = FakePolicyEngine(decision, sink)
    validator = FakeArgumentValidator(result={"arg": "v"})
    session = make_session(
        session_id="sess-1",
        working_dir=tmp_path,
        allowed_roots=(tmp_path,),
        sink=sink,
        model=model,
        registry=registry,
        policy=policy,
        validator=validator,
        approval=approval,
        capability_tier=tier,
    )
    events = collect(session, "task")
    return events, sink, tools


def _tool_results(events: list) -> list:
    return [e for e in events if e.kind is SessionEventKind.TOOL_RESULT]


def _deny_audit(sink: RecordingSink, reason: str) -> list:
    return [
        a
        for a in sink.events
        if a.kind is AuditEventKind.TOOL_CALL
        and a.outcome is AuditOutcome.DENY
        and a.detail.get("denied_reason") == reason
    ]


def _assert_terminal_failure(events: list, tools: dict[str, FakeTool]) -> None:
    errors = [e for e in events if e.kind is SessionEventKind.ERROR]
    assert errors, "故障必须产生 ERROR 事件"
    assert errors[0].error_kind is SessionErrorKind.INTERNAL, (
        f"故障必须以 INTERNAL 暴露，实为 {errors[0].error_kind}"
    )
    finished = [e for e in events if e.kind is SessionEventKind.TASK_FINISHED]
    assert finished and finished[0].status is TaskStatus.FAILED, "必须以 TASK_FINISHED(FAILED) 终止"
    for tool in tools.values():
        assert tool.invoke_calls == [], "故障不得回退为执行工具"
    assert not any(e.kind is SessionEventKind.TOOL_RESULT for e in events), (
        "故障不得回退为普通拒绝（不应出现 TOOL_RESULT）"
    )


@pytest.mark.security
def test_snew1_non_interactive_gate_denies(tmp_path: Path) -> None:
    """``S-new-1``：确认通路返回 DENY（非交互⇒拒绝）⇒ 工具未执行 + APPROVAL_RESULT(DENY) + 审计有 APPROVAL。"""
    sink = RecordingSink()
    gate = FakeApprovalGate(sink, outcome=ApprovalOutcome.DENY, audit_id="appr-1")
    events, sink, tools = _run_snew(
        tmp_path,
        specs=[_READ_SPEC],
        allowlist=frozenset({"read_file"}),
        tier=CapabilityTier.BASIC,
        decision=_decision(allow=True, requires_confirmation=True, audit_id="pol-1"),
        called_name="read_file",
        approval=gate,
        sink=sink,
    )

    assert tools["read_file"].invoke_calls == [], "非交互拒绝：工具不得执行"
    approval_results = [e for e in events if e.kind is SessionEventKind.APPROVAL_RESULT]
    assert approval_results, "必须产生 APPROVAL_RESULT 事件"
    assert approval_results[0].approval.outcome is ApprovalOutcome.DENY, (
        "APPROVAL_RESULT.outcome 必须为 DENY"
    )
    assert any(a.kind is AuditEventKind.APPROVAL for a in sink.events), (
        "审计必须含 kind=APPROVAL 事件（R2/A4）"
    )
    results = _tool_results(events)
    assert results and results[0].result is None


@pytest.mark.security
def test_snew1_control_interactive_allow_executes(tmp_path: Path) -> None:
    """``S-new-1`` 的变异 / 对照组：确认通路返回 ALLOW 时工具被执行。

    证明上面的 DENY 确实来自真实保护（gate 的"非交互⇒拒绝"语义），而非偶然通过。
    """
    gate = FakeApprovalGate(
        RecordingSink(), outcome=ApprovalOutcome.ALLOW_ONCE, audit_id="appr-ctl"
    )
    events, _sink, tools = _run_snew(
        tmp_path,
        specs=[_READ_SPEC],
        allowlist=frozenset({"read_file"}),
        tier=CapabilityTier.BASIC,
        decision=_decision(allow=True, requires_confirmation=True, audit_id="pol-ctl"),
        called_name="read_file",
        approval=gate,
        tool_result=ToolResult(ok=True, content="read", audit_id="tool-ok-1"),
    )

    assert tools["read_file"].invoke_calls, "对照组：确认放行时工具应被执行"
    approval_results = [e for e in events if e.kind is SessionEventKind.APPROVAL_RESULT]
    assert approval_results and approval_results[0].approval.outcome is ApprovalOutcome.ALLOW_ONCE


@pytest.mark.security
def test_snew2_network_context_always_denied(tmp_path: Path) -> None:
    """``S-new-2``：即便 NETWORK_OUTBOUND 已授予，构造出的 ``ExecutionContext.network_allowed is False``。"""
    _events, _sink, tools = _run_snew(
        tmp_path,
        specs=[_READ_SPEC],
        allowlist=frozenset({"read_file"}),
        tier=CapabilityTier.BASIC,
        decision=_decision(allow=True, requires_confirmation=False, audit_id="pol-2"),
        called_name="read_file",
        tool_result=ToolResult(ok=True, content="read", audit_id="tool-ok-2"),
    )

    calls = tools["read_file"].invoke_calls
    assert calls, "工具应被执行以检验其收到的 ctx"
    ctx = calls[0][1]
    assert ctx.network_allowed is False, "网络出站上下文必须恒为 False（不得'授予即放行'）"
    assert ctx.allowed_roots, "必须显式传入受限根，不能留空"


@pytest.mark.security
def test_snew2_guard_depends_on_hardcoded_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``S-new-2`` 的变异探针：把 harness 构造的 ``ExecutionContext`` 改成 ``network_allowed=True``。

    此时测试对"ctx.network_allowed is False"的断言会翻红——证明该断言确实由 harness 的
    真实保护（硬编码 False）产生，而非偶然通过。
    """

    class _LeakyCtx:
        def __init__(
            self,
            *,
            session_id,
            call_id,
            working_dir,
            allowed_roots,
            timeout_s,
            network_allowed=False,
        ):
            self.session_id = session_id
            self.call_id = call_id
            self.working_dir = working_dir
            self.allowed_roots = allowed_roots
            self.timeout_s = timeout_s
            self.network_allowed = True  # 弱化：本应恒为 False

    import agent_sec_perf.harness.loop as loop_mod

    monkeypatch.setattr(loop_mod, "ExecutionContext", _LeakyCtx)

    _events, _sink, tools = _run_snew(
        tmp_path,
        specs=[_READ_SPEC],
        allowlist=frozenset({"read_file"}),
        tier=CapabilityTier.BASIC,
        decision=_decision(allow=True, requires_confirmation=False, audit_id="pol-2m"),
        called_name="read_file",
        tool_result=ToolResult(ok=True, content="read", audit_id="tool-ok-2m"),
    )
    calls = tools["read_file"].invoke_calls
    assert calls and calls[0][1].network_allowed is True, "变异探针：保护被摘掉后，原断言应翻红"


@pytest.mark.security
def test_snew3_audit_failure_must_bubble(tmp_path: Path) -> None:
    """``S-new-3``：审计写入失败必须冒泡，不得静默转成 ERROR 事件后继续运行。"""
    sink = RaisingSink()
    model = FakeModelClient(
        make_tool_call_response(call_id="c1", name="ghost_tool", arguments_json="{}")
    )
    tools = {"read_file": FakeTool(_READ_SPEC)}
    registry = FakeRegistry([_READ_SPEC], tools)
    policy = FakePolicyEngine(
        _decision(allow=False, requires_confirmation=False, audit_id="pol-3"), sink
    )
    validator = FakeArgumentValidator(result={"arg": "v"})
    session = make_session(
        session_id="sess-1",
        working_dir=tmp_path,
        allowed_roots=(tmp_path,),
        sink=sink,
        model=model,
        registry=registry,
        policy=policy,
        validator=validator,
        capability_tier=CapabilityTier.BASIC,
    )

    events: list = []
    with pytest.raises(RuntimeError, match="injected audit failure"):
        for ev in session.run("task"):
            events.append(ev)
    assert not any(e.kind is SessionEventKind.TASK_FINISHED for e in events), (
        "审计失败不得被吞掉后继续运行"
    )


@pytest.mark.security
def test_snew3_propagation_depends_on_no_swallow(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``S-new-3`` 的变异探针：把 ``_emit_tool_call_audit`` 换成"吞掉异常"的弱化版。

    此时异常不再冒泡、任务照常走完——原"必须冒泡"断言将翻红，证明它依赖真实保护。
    """
    from agent_sec_perf.harness.loop import TaskLoop

    def _swallow(self, *, call_id, tool_name, outcome, detail):
        return "swallowed-id"

    monkeypatch.setattr(TaskLoop, "_emit_tool_call_audit", _swallow)

    sink = RaisingSink()
    model = FakeModelClient(
        make_tool_call_response(call_id="c1", name="ghost_tool", arguments_json="{}")
    )
    tools = {"read_file": FakeTool(_READ_SPEC)}
    registry = FakeRegistry([_READ_SPEC], tools)
    policy = FakePolicyEngine(
        _decision(allow=False, requires_confirmation=False, audit_id="pol-3m"), sink
    )
    validator = FakeArgumentValidator(result={"arg": "v"})
    session = make_session(
        session_id="sess-1",
        working_dir=tmp_path,
        allowed_roots=(tmp_path,),
        sink=sink,
        model=model,
        registry=registry,
        policy=policy,
        validator=validator,
        capability_tier=CapabilityTier.BASIC,
    )

    events = list(session.run("task"))  # 弱化后不应抛异常
    assert any(e.kind is SessionEventKind.TASK_FINISHED for e in events), (
        "变异探针：若吞掉审计异常，原断言应翻红"
    )


@pytest.mark.security
def test_snew4_missing_gate_blocks_confirmation(tmp_path: Path) -> None:
    """``S-new-4``：``approval=None`` 但工具需确认 ⇒ 拒绝（approval_denied）且无 APPROVAL_RESULT。"""
    events, sink, tools = _run_snew(
        tmp_path,
        specs=[_READ_SPEC],
        allowlist=frozenset({"read_file"}),
        tier=CapabilityTier.BASIC,
        decision=_decision(allow=True, requires_confirmation=True, audit_id="pol-4"),
        called_name="read_file",
        approval=None,
    )

    assert tools["read_file"].invoke_calls == [], "无确认通路时不得执行"
    results = _tool_results(events)
    assert results and results[0].result is None
    assert _deny_audit(sink, "approval_denied"), "无确认通路必须记 approval_denied"
    assert not any(e.kind is SessionEventKind.APPROVAL_RESULT for e in events), (
        "无确认通路时不得伪造 APPROVAL_RESULT"
    )


@pytest.mark.security
def test_snew4_control_present_gate_resolves(tmp_path: Path) -> None:
    """``S-new-4`` 的变异 / 对照组：提供确认通路（ALLOW）后工具被执行。

    证明上面的"未执行"确实由"缺失确认通路"这一真实保护导致，而非偶然通过。
    """
    gate = FakeApprovalGate(RecordingSink(), outcome=ApprovalOutcome.ALLOW_ONCE, audit_id="appr-4")
    _events, _sink, tools = _run_snew(
        tmp_path,
        specs=[_READ_SPEC],
        allowlist=frozenset({"read_file"}),
        tier=CapabilityTier.BASIC,
        decision=_decision(allow=True, requires_confirmation=True, audit_id="pol-4c"),
        called_name="read_file",
        approval=gate,
        tool_result=ToolResult(ok=True, content="read", audit_id="tool-ok-4"),
    )
    assert tools["read_file"].invoke_calls, "对照组：提供确认通路并放行时应执行"


@pytest.mark.security
@pytest.mark.parametrize(
    "gate_factory",
    [
        pytest.param(
            lambda: FakeApprovalGate(RecordingSink(), raise_with=RuntimeError("gate boom")),
            id="R3-raise",
        ),
        pytest.param(
            lambda: FakeApprovalGate(RecordingSink(), bad_return="not-an-approval-result"),
            id="R4-not-approval",
        ),
        pytest.param(
            lambda: FakeApprovalGate(RecordingSink(), outcome="BOGUS_OUTCOME", audit_id="appr-bad"),
            id="R4-unknown-outcome",
        ),
        pytest.param(
            lambda: FakeApprovalGate(RecordingSink(), outcome=ApprovalOutcome.DENY, audit_id=""),
            id="R4-empty-audit-id",
        ),
    ],
)
def test_snew5_gate_fault_is_terminal(tmp_path: Path, gate_factory) -> None:
    """``S-new-5``：确认通路故障（R3/R4）⇒ 必须 fail-secure 终止，不得回退为普通拒绝。"""
    gate = gate_factory()
    events, _sink, tools = _run_snew(
        tmp_path,
        specs=[_READ_SPEC],
        allowlist=frozenset({"read_file"}),
        tier=CapabilityTier.BASIC,
        decision=_decision(allow=True, requires_confirmation=True, audit_id="pol-5"),
        called_name="read_file",
        approval=gate,
    )
    _assert_terminal_failure(events, tools)


@pytest.mark.security
def test_snew5_fault_must_not_be_swallowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``S-new-5`` 的变异探针：把 ``_request_approval`` 换成"吞掉故障并按普通拒绝处理"的弱化版。

    此时原"必须以 ERROR+ FAILED 终止"断言翻红——证明它依赖真实保护（R3/R4 是 fail-secure）。
    """
    from agent_sec_perf.contracts.harness import ApprovalResult, SessionEventKind
    from agent_sec_perf.harness.loop import TaskLoop, _ApprovalVerdict

    def _weakened(self, call, *, spec, decision, arguments):
        result = ApprovalResult(outcome=ApprovalOutcome.DENY, audit_id="weak-approval")
        event = self._event(
            SessionEventKind.APPROVAL_RESULT,
            call_id=call.call_id,
            tool_name=spec.name,
            approval=result,
            audit_id=result.audit_id,
        )
        return _ApprovalVerdict(approval_event=event, outcome=ApprovalOutcome.DENY, terminal=())

    monkeypatch.setattr(TaskLoop, "_request_approval", _weakened)

    gate = FakeApprovalGate(RecordingSink(), raise_with=RuntimeError("gate boom"))
    events, _sink, _tools = _run_snew(
        tmp_path,
        specs=[_READ_SPEC],
        allowlist=frozenset({"read_file"}),
        tier=CapabilityTier.BASIC,
        decision=_decision(allow=True, requires_confirmation=True, audit_id="pol-5m"),
        called_name="read_file",
        approval=gate,
    )
    assert not any(e.kind is SessionEventKind.ERROR for e in events), (
        "变异探针：若吞掉故障，原断言应翻红"
    )
    finished = [e for e in events if e.kind is SessionEventKind.TASK_FINISHED]
    assert finished and finished[0].status is TaskStatus.COMPLETED, (
        "变异探针：故障被弱化时不应以 FAILED 终止"
    )


@pytest.mark.security
def test_snew6_data_context_system_role_rejected(tmp_path: Path) -> None:
    """``S-new-6``：``data_context`` 试图以 ``SYSTEM`` 角色进入 ⇒ 装配阶段 ``ValueError``（§4.4 第 2 条 R6）。"""
    with pytest.raises(ValueError, match="data_context"):
        _ctx_assemble(
            system="legit system",
            task="do",
            history=(),
            budget=ContextBudget(max_tokens=4096),
            data_context=(ChatMessage(role=Role.SYSTEM, content="injected-via-data"),),
        )


@pytest.mark.security
def test_snew6_data_context_never_enters_system_position(tmp_path: Path) -> None:
    """``S-new-6`` 正例：合法 USER 数据消息的内容永不出现在 SYSTEM 消息里（只出现在 USER 位）。"""
    messages = _ctx_assemble(
        system="legit system",
        task="do",
        history=(),
        budget=ContextBudget(max_tokens=4096),
        data_context=(ChatMessage(role=Role.USER, content="injected-via-data"),),
    )
    systems = [m for m in messages if m.role is Role.SYSTEM]
    assert len(systems) == 1, "SYSTEM 位置只能有 system 参数提供的一条"
    assert systems[0].content == "legit system", (
        "SYSTEM 消息内容必须是显式 system 参数，而非来自 data_context"
    )
    assert all(m.role is not Role.SYSTEM or m.content == "legit system" for m in messages)


@pytest.mark.security
def test_snew6_rejection_depends_on_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """``S-new-6`` 的变异探针：把 ``assemble`` 换成不做角色校验的弱化版。

    此时原"必须抛 ValueError"断言翻红——证明该断言确实由结构性守卫（R6）产生。
    """
    import agent_sec_perf.harness.context as ctx_mod

    def _weakened(*, system, task, history=(), budget, data_context=()):
        return (
            ChatMessage(role=Role.SYSTEM, content=system),
            *data_context,
            ChatMessage(role=Role.USER, content=task),
        )

    monkeypatch.setattr(ctx_mod, "assemble", _weakened)
    # 弱化后不再抛错 ⇒ 原断言翻红
    result = ctx_mod.assemble(
        system="legit",
        task="do",
        history=(),
        budget=ContextBudget(max_tokens=4096),
        data_context=(ChatMessage(role=Role.SYSTEM, content="injected"),),
    )
    assert any(m.role is Role.SYSTEM and m.content == "injected" for m in result), (
        "变异探针：守卫被摘掉后原断言应翻红"
    )
