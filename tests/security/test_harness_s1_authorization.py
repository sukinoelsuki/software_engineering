"""``S1`` / ``S1-b``（契约 §6.2）：未经授权的工具调用**不得执行**，且审计可回放。

攻击视角（契约原文）：

* ``S1``：模型请求一个**未被授权**的工具（如 ``WRITE_FILE`` 未授予）⇒ 调用未执行
  （``Tool.invoke`` 未被调用）+ ``TOOL_RESULT(result=None)`` + 审计里能查到
  ``TOOL_CALL/DENY`` **与** ``POLICY_DECISION`` 两条事件（可回放）。
* ``S1-b①``：模型**幻觉**一个不存在的工具名 ⇒ ``denied_reason == "unknown_tool"``。
* ``S1-b②``：模型调用一个**存在但未暴露**的工具（被裁剪 / 不在白名单）⇒
  ``denied_reason == "not_exposed"``（与 ``unknown_tool`` 必须可区分，``REQ-HARNESS-03``）。

被测对象（只读）：``agent_sec_perf.harness.Session.run`` 经真实 ``TaskLoop`` 决策序列
（契约 §3.3 步 1/3）。依赖全部以构造注入的 ``Protocol`` 替身驱动（见 ``_harness_fakes``），
替身**不承担断言**——断言都在本文件里针对真实行为。

变异 / 对照组：每个"拒绝"用例都配一个"放松注入条件"的对照组（策略放行 / 工具被暴露）。
对照组里同一 harness 路径会让工具**真的被执行**、``TOOL_RESULT.result`` 非 ``None``——
这证明上面的"未执行"断言确实由真实保护（策略拒绝 / 暴露判定）导致，而非偶然通过
（防"绿着但没生效"）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from _harness_fakes import (
    FakeArgumentValidator,
    FakeModelClient,
    FakePolicyEngine,
    FakeRegistry,
    FakeTool,
    RecordingSink,
    collect,
    make_session,
    make_tool_call_response,
    make_tool_spec,
)

from agent_sec_perf.contracts.audit import AuditEventKind, AuditOutcome
from agent_sec_perf.contracts.harness import (
    CapabilityTier,
    PolicyDecision,
    SessionEventKind,
)
from agent_sec_perf.contracts.policy import Capability, RiskLevel
from agent_sec_perf.contracts.tools import ToolResult

_READ_SPEC = make_tool_spec(name="read_file", capabilities=frozenset({Capability.READ_FILE}))
_WRITE_SPEC = make_tool_spec(name="write_file", capabilities=frozenset({Capability.WRITE_FILE}))


def _deny_decision(audit_id: str = "pol-deny-1") -> PolicyDecision:
    return PolicyDecision(
        allow=False,
        requires_confirmation=False,
        risk_level=RiskLevel.HIGH,
        reason="denied",
        audit_id=audit_id,
    )


def _allow_decision(audit_id: str = "pol-allow-1") -> PolicyDecision:
    return PolicyDecision(
        allow=True,
        requires_confirmation=False,
        risk_level=RiskLevel.LOW,
        reason="ok",
        audit_id=audit_id,
    )


def _run(
    tmp_path: Path,
    *,
    specs,
    allowlist: frozenset[str],
    tier: CapabilityTier,
    decision: PolicyDecision,
    called_name: str,
    arguments_json: str = "{}",
    tool_result: ToolResult | None = None,
    approval=None,
) -> tuple[list, RecordingSink, dict[str, FakeTool]]:
    """装配一个真实 ``Session`` 并跑完一次任务，返回（事件流, 审计桩, 各工具替身）。"""
    sink = RecordingSink()
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
    events = collect(session, "do something")
    return events, sink, tools


def _tool_results(events: list) -> list:
    return [e for e in events if e.kind is SessionEventKind.TOOL_RESULT]


def _deny_audit(sink: RecordingSink, reason: str):
    return [
        a
        for a in sink.events
        if a.kind is AuditEventKind.TOOL_CALL
        and a.outcome is AuditOutcome.DENY
        and a.detail.get("denied_reason") == reason
    ]


@pytest.mark.security
def test_s1_unauthorized_tool_not_executed(tmp_path: Path) -> None:
    """``S1``：未经授权的工具 ⇒ 不执行 + ``TOOL_RESULT(result=None)`` + 审计可回放。"""
    events, sink, tools = _run(
        tmp_path,
        specs=[_READ_SPEC],
        allowlist=frozenset({"read_file"}),
        tier=CapabilityTier.BASIC,
        decision=_deny_decision(),
        called_name="read_file",
    )

    assert tools["read_file"].invoke_calls == [], "未被授权的工具不得被执行（Tool.invoke 未被调用）"
    results = _tool_results(events)
    assert len(results) == 1, "必须恰好一条 TOOL_RESULT"
    assert results[0].result is None, "TOOL_RESULT.result 必须为 None（未执行）"

    deny = _deny_audit(sink, "policy_denied")
    assert deny, "审计必须含 TOOL_CALL/DENY 且 denied_reason=policy_denied"
    pol = [a for a in sink.events if a.kind is AuditEventKind.POLICY_DECISION]
    assert pol, "审计必须含 POLICY_DECISION 事件（可回放判据）"
    assert results[0].audit_id == deny[0].event_id, (
        "审计可回放：TOOL_RESULT.audit_id 必须对应 DENY 审计"
    )


@pytest.mark.security
def test_s1_control_policy_allowed_invokes_tool(tmp_path: Path) -> None:
    """``S1`` 的变异 / 对照组：策略放行时同一 harness 路径会**执行**工具。

    原用例的"未执行"断言之所以成立，是因为真实保护（策略拒绝）在起作用；把注入的决策
    放行为 allow，工具即被调用、``TOOL_RESULT.result`` 非 ``None``——证明原断言不是偶然通过。
    """
    events, _sink, tools = _run(
        tmp_path,
        specs=[_READ_SPEC],
        allowlist=frozenset({"read_file"}),
        tier=CapabilityTier.BASIC,
        decision=_allow_decision(),
        called_name="read_file",
        tool_result=ToolResult(ok=True, content="read", audit_id="tool-ok-1"),
    )

    assert tools["read_file"].invoke_calls, "对照组：授权工具应被执行"
    results = _tool_results(events)
    assert results and results[0].result is not None, "对照组：授权工具应有非空 result"


@pytest.mark.security
def test_s1b_hallucinated_tool_is_unknown(tmp_path: Path) -> None:
    """``S1-b①``：模型幻觉不存在的工具名 ⇒ ``denied_reason == "unknown_tool"``。"""
    events, sink, _tools = _run(
        tmp_path,
        specs=[_READ_SPEC],
        allowlist=frozenset({"read_file"}),
        tier=CapabilityTier.BASIC,
        decision=_deny_decision(),
        called_name="ghost_tool",
    )

    results = _tool_results(events)
    assert results and results[0].result is None, "幻觉工具不得执行"
    deny = _deny_audit(sink, "unknown_tool")
    assert deny, "幻觉工具必须记为 unknown_tool"
    assert not any(a.kind is AuditEventKind.POLICY_DECISION for a in sink.events), (
        "未知工具根本不应进入策略求值（无 POLICY_DECISION 审计）"
    )


@pytest.mark.security
def test_s1b_existing_but_not_exposed(tmp_path: Path) -> None:
    """``S1-b②``：存在但未暴露的工具 ⇒ ``denied_reason == "not_exposed"``（与 unknown_tool 区分）。"""
    events, sink, tools = _run(
        tmp_path,
        specs=[_READ_SPEC, _WRITE_SPEC],
        allowlist=frozenset({"read_file"}),
        tier=CapabilityTier.BASIC,
        decision=_deny_decision(),
        called_name="write_file",
    )

    results = _tool_results(events)
    assert results and results[0].result is None, "未暴露工具不得执行"
    deny = _deny_audit(sink, "not_exposed")
    assert deny, "存在但未暴露的工具必须记为 not_exposed"
    assert not tools["write_file"].invoke_calls, "未暴露工具不得被执行"


@pytest.mark.security
def test_s1b_control_exposed_write_file_invoked_when_allowed(tmp_path: Path) -> None:
    """``S1-b②`` 的变异 / 对照组：把工具**暴露**（ADVANCED 档 + 白名单含它）且策略放行即被执行。

    证明上面的 not_exposed 拒绝确实由"暴露判定"这层真实保护导致，而非偶然通过。
    """
    events, _sink, tools = _run(
        tmp_path,
        specs=[_READ_SPEC, _WRITE_SPEC],
        allowlist=frozenset({"read_file", "write_file"}),
        tier=CapabilityTier.ADVANCED,
        decision=_allow_decision(),
        called_name="write_file",
        tool_result=ToolResult(ok=True, content="wrote", audit_id="tool-ok-2"),
    )

    assert tools["write_file"].invoke_calls, "对照组：暴露且授权的 write_file 应被执行"
    results = _tool_results(events)
    assert results and results[0].result is not None
