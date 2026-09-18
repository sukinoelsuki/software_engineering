"""对抗性验证：``PolicyEngine.decide()`` 的**空集**与**多能力**行为（``REQ-SEC-01``）。

依据（独立验证，不听实现者的解释）：

* ``docs/design/interfaces/policy.md`` §2.5「补充规定」的判据 V1~V3、V6；
* ``docs/design/interfaces/audit.md`` §2.3 的不变式 **I1~I4**。

期望只来自契约与攻击者视角：

* 空集（``requested=frozenset()``）⇒ 硬拒绝 ``(False, False)`` + ``CRITICAL``、
  ``capability=None``、``detail["requested"]==[]``，且**仍** ``emit`` 一条 ``DENY`` 事件
  （禁止"空集时不审计"——那会把契约冲突改写成一次静默证据丢失）；
* 多能力：任一缺失即**整体**拒绝；``capability`` 取确定性代表（缺失非空取缺失中字典序最小者，
  否则取 ``requested`` 中字典序最小者），保证 ``capability ∈ requested``；
* 全部授予且风险 ``LOW`` ⇒ 放行，代表取字典序最小者，且重复调用结果一致（可回放前提）。

审计不变式 ``I1~I4`` 由本文件复用的 ``assert_policy_event_invariants`` 套用到全部事件，
与 ``policy.md`` §2.5 的 V6 一致。

测试用 ``_RecordingSink`` 只是**捕获** ``emit`` 的桩（审计落点本身不是被测对象），
不是"预先装好结论的替身 Harness"——``decide()`` 的真实求值逻辑仍由实现承担。
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_sec_perf.contracts.audit import (
    AuditEvent,
    AuditEventKind,
    AuditOutcome,
    AuditSink,
)
from agent_sec_perf.contracts.policy import (
    Capability,
    PolicyDecision,
    PolicyRequest,
    RiskLevel,
)
from agent_sec_perf.security.capabilities import CapabilitySet
from agent_sec_perf.security.policy import PolicyEngine


class _RecordingSink(AuditSink):
    """只捕获事件的审计桩（不含任何判定逻辑）。"""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def emit(self, event: AuditEvent) -> None:
        self.events.append(event)

    def flush(self) -> None:
        pass


def _request(
    *,
    requested: frozenset[Any],
    arguments: dict[str, Any] | None = None,
    call_id: str = "call-1",
    domain_pack: str | None = None,
) -> PolicyRequest:
    return PolicyRequest(
        session_id="sess-1",
        call_id=call_id,
        tool_name="tool-under-test",
        arguments=arguments if arguments is not None else {},
        requested=requested,
        domain_pack=domain_pack,
    )


def _all_granted() -> CapabilitySet:
    return CapabilitySet(granted=frozenset(Capability))


def _engine(
    granted: CapabilitySet, *, tool_risk: dict[str, RiskLevel] | None = None
) -> PolicyEngine:
    return PolicyEngine(granted=granted, sink=_RecordingSink(), tool_risk=tool_risk or {})


def assert_policy_event_invariants(
    event: AuditEvent,
    *,
    expect_invalid: bool = False,
) -> None:
    """对一条 ``POLICY_DECISION`` 事件套用 ``audit.md`` §2.3 的 I1~I4。

    这是 ``policy.md`` §2.5 V6 要求的"一个不变式断言函数，复用到全部策略用例"。
    """
    assert event.kind == AuditEventKind.POLICY_DECISION
    # I1：关联键与风险等级（无条件）非 None。
    assert event.call_id is not None
    assert event.risk_level is not None
    # I2：detail["requested"] 必存在、为升序能力名列表、无重复。
    requested = event.detail["requested"]
    assert isinstance(requested, list)
    assert requested == sorted(requested), "detail['requested'] 必须升序"
    assert len(requested) == len(set(requested)), "detail['requested'] 不得有重复"
    for name in requested:
        assert isinstance(name, str)
        assert name in {member.value for member in Capability}, f"未知能力名 {name!r}"
    # I3：capability is None ⇔ requested == []；否则 capability.value ∈ requested。
    if event.capability is None:
        assert requested == [], "I3：capability 为 None 必须 ⇒ requested == []"
    else:
        assert isinstance(event.capability, Capability), "I4：capability 必须是 Capability 实例"
        assert event.capability.value in requested, "I3：capability.value 必须 ∈ requested"
    # 非法成员分支的判别键：I2 要求三种 [] 来源可区分（此处只校验 invalid 分支存在性）。
    if expect_invalid:
        assert "invalid" in event.detail, "非法成员分支必须有 detail['invalid'] 判别键"


@pytest.mark.security
def test_empty_requested_is_hard_denied_with_emit() -> None:
    """V1：空集即使全授予也拒绝；``capability=None``；``requested==[]``；仍 emit(DENY)。"""
    engine = _engine(_all_granted())
    decision = engine.decide(_request(requested=frozenset()))

    # 四格中的"硬拒绝"一格（False/False）+ CRITICAL。
    assert (decision.allow, decision.requires_confirmation) == (False, False)
    assert decision.risk_level is RiskLevel.CRITICAL

    # 仍必须留下审计事件（禁止"空集时不审计"）。
    assert len(engine.sink.events) == 1
    event = engine.sink.events[0]
    assert event.outcome is AuditOutcome.DENY
    assert event.capability is None
    assert event.detail["requested"] == []
    assert_policy_event_invariants(event)


@pytest.mark.security
def test_multi_request_one_missing_is_whole_denied() -> None:
    """V2：多能力中任一缺失即整体拒绝；``capability`` 取缺失中字典序最小者。"""
    engine = _engine(CapabilitySet(granted=frozenset({Capability.READ_FILE})))
    decision = engine.decide(
        _request(requested=frozenset({Capability.READ_FILE, Capability.EXECUTE_COMMAND}))
    )

    assert (decision.allow, decision.requires_confirmation) == (False, False)
    assert decision.risk_level is RiskLevel.CRITICAL

    event = engine.sink.events[0]
    # 缺失集合中字典序最小者 = EXECUTE_COMMAND（"execute_command" < "read_file"）。
    assert event.capability is Capability.EXECUTE_COMMAND
    assert event.detail["missing"] == ["execute_command"]
    assert event.detail["requested"] == ["execute_command", "read_file"]
    assert_policy_event_invariants(event)


@pytest.mark.security
def test_multi_request_all_granted_low_risk_is_allowed() -> None:
    """V3：全部授予且风险 LOW ⇒ 放行；代表取字典序最小者；重复调用一致。"""
    engine = _engine(
        _all_granted(),
        tool_risk={"tool-under-test": RiskLevel.LOW},
    )
    decision = engine.decide(
        _request(requested=frozenset({Capability.READ_FILE, Capability.WRITE_FILE}))
    )

    assert decision.allow is True
    assert decision.requires_confirmation is False
    assert decision.risk_level is RiskLevel.LOW

    event = engine.sink.events[0]
    # 字典序最小者 = READ_FILE（"read_file" < "write_file"）。
    assert event.capability is Capability.READ_FILE
    assert event.detail["requested"] == ["read_file", "write_file"]
    assert_policy_event_invariants(event)

    # 确定性：同一输入再次求值必须得到同一 capability（可回放前提）。
    # capability 在审计事件上，不在 PolicyDecision 上（后者只有 audit_id 关联键）。
    engine.decide(
        _request(
            requested=frozenset({Capability.READ_FILE, Capability.WRITE_FILE}),
            call_id="call-2",
        )
    )
    assert engine.sink.events[1].capability is Capability.READ_FILE


@pytest.mark.security
def test_policy_decision_links_audit_id() -> None:
    """V6 的回放侧：``PolicyDecision.audit_id`` 必须等于所 emit 事件的 ``event_id``。"""
    engine = _engine(_all_granted())
    decision = engine.decide(_request(requested=frozenset({Capability.READ_FILE})))
    assert isinstance(decision, PolicyDecision)
    assert engine.sink.events[0].event_id == decision.audit_id
