"""对抗性验证：``PolicyEngine.decide()`` 的**求值失败收敛**与**审计失败冒泡**（``REQ-SEC-01/06``）。

依据（独立验证，不听实现者的解释）：

* ``docs/design/interfaces/policy.md`` §2.5 判据 **V4 / V5** 与第 1 步「求值失败路径」规定；
* ``docs/design/interfaces/audit.md`` §2.4「失败必须冒泡」与 §2.3 的 **I3**。

攻击者视角 / 失效模式：

* 能力模型求值抛错 ⇒ 收敛为拒绝；但 ``detail["requested"]`` 必须补（不可解析记 ``[]``），
  🔴 **且不得写 ``detail["missing"]``**——``[]`` 的语义是"无缺失 ⇒ 已授权"，用它表示"求值失败"
  会把**故障伪装成授权充足**；
* ``requested`` 不可解析（如 ``None``）⇒ 同样收敛为拒绝，``requested==[]`` 且保留 ``detail["error"]``；
* **审计写入失败必须原样冒泡**：``emit`` 的异常不得被"求值收敛"的 try/except 吞掉——
  否则一次审计基础设施故障会被伪装成一次普通策略拒绝，``REQ-SEC-06`` 验收失败。
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_sec_perf.contracts.audit import AuditEvent
from agent_sec_perf.contracts.policy import Capability, PolicyRequest, RiskLevel
from agent_sec_perf.security import policy as policy_module
from agent_sec_perf.security.capabilities import CapabilitySet
from agent_sec_perf.security.policy import PolicyEngine

from .conftest import RecordingSink, all_granted, assert_policy_event_invariants


class _RaisingSink(RecordingSink):
    """``emit`` 必抛的审计桩：用于验证"审计失败必须冒泡"。"""

    def emit(self, event: AuditEvent) -> None:
        raise RuntimeError("audit sink unavailable")


def _request(*, requested: Any, call_id: str = "call-1") -> PolicyRequest:
    return PolicyRequest(
        session_id="sess-1",
        call_id=call_id,
        tool_name="tool-under-test",
        arguments={},
        requested=requested,
    )


def _engine(granted: CapabilitySet, sink: RecordingSink | None = None) -> PolicyEngine:
    return PolicyEngine(granted=granted, sink=sink or RecordingSink())


@pytest.mark.security
def test_eval_failure_converges_to_deny_without_missing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """V4：能力模型求值抛错、``requested={WRITE_FILE}`` ⇒ 收敛为拒绝；
    ``capability=WRITE_FILE``；``detail['requested']==['write_file']``；**无** ``missing`` 键。"""
    # 模拟"能力模型抛错"——求值阶段的异常必须收敛为拒绝，而非逃逸为 allow。
    monkeypatch.setattr(
        policy_module.CapabilitySet,
        "missing",
        classmethod(lambda cls, *a, **k: (_ for _ in ()).throw(RuntimeError("model broke"))),
    )

    engine = _engine(CapabilitySet(granted=frozenset({Capability.READ_FILE})))
    decision = engine.decide(_request(requested=frozenset({Capability.WRITE_FILE})))

    assert decision.allow is False
    assert decision.requires_confirmation is True  # 可升级拒绝（收敛点）
    assert decision.risk_level is RiskLevel.CRITICAL
    event = engine.sink.events[0]
    assert event.capability is Capability.WRITE_FILE
    assert event.detail["requested"] == ["write_file"]
    # 🔴 关键：求值失败路径**不得**写 missing（否则把故障伪装成"无缺失=已授权"）。
    assert "missing" not in event.detail, "求值失败路径不得写 detail['missing']"
    assert event.detail["error"] == "RuntimeError"
    assert_policy_event_invariants(event)


@pytest.mark.security
def test_unparseable_requested_converges_with_error_key() -> None:
    """V5：``requested=None``（不可解析）⇒ 收敛为拒绝；``capability=None``、
    ``detail['requested']==[]`` **且** ``detail['error']`` 存在。"""
    engine = _engine(all_granted())
    decision = engine.decide(_request(requested=None))

    assert decision.allow is False
    assert decision.requires_confirmation is True
    event = engine.sink.events[0]
    assert event.capability is None
    assert event.detail["requested"] == []
    assert event.detail["error"] == "TypeError"  # frozenset(None) 抛 TypeError
    assert_policy_event_invariants(event)


@pytest.mark.security
def test_audit_failure_bubbles_not_swallowed() -> None:
    """审计写入失败必须原样冒泡：``emit`` 抛错不得被"求值收敛"吞掉。

    否则一次审计基础设施故障会被伪装成一次普通策略拒绝（``REQ-SEC-06`` 验收失败）。
    """
    engine = _engine(all_granted(), sink=_RaisingSink())
    with pytest.raises(RuntimeError, match="audit sink unavailable"):
        engine.decide(_request(requested=frozenset({Capability.READ_FILE})))
