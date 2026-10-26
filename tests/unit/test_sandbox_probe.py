"""隔离探针与降级留痕的单元测试（不依赖真实 bwrap / root）。"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from agent_sec_perf.contracts.audit import AuditEvent, AuditEventKind, AuditOutcome, AuditSink
from agent_sec_perf.contracts.sandbox import (
    IsolationDimension,
    IsolationDimensionStatus,
    IsolationMatrix,
    IsolationMechanism,
    SandboxTier,
)
from agent_sec_perf.foundation.errors import IsolationError
from agent_sec_perf.security.sandbox import IsolationReport, emit_degradation_audit, probe_isolation
from agent_sec_perf.security.sandbox.probe import _status


class _RecordingSink(AuditSink):
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def emit(self, event: AuditEvent) -> None:
        self.events.append(event)

    def flush(self) -> None:
        return


def _report(reason: str = "测试用降级原因") -> IsolationReport:
    status = IsolationDimensionStatus(
        dimension=IsolationDimension.USER,
        enforced=True,
        mechanism=IsolationMechanism.PRIVDROP,
        evidence="uid=65534",
    )
    matrix = IsolationMatrix(
        tier=SandboxTier.L1,
        dimensions={IsolationDimension.USER: status},
        degraded=True,
        reason=reason,
    )
    return IsolationReport(matrix=matrix, notes=(reason,))


def test_degradation_audit_records_tier_and_reason() -> None:
    sink = _RecordingSink()
    event_id = emit_degradation_audit(sink, session_id="s-1", report=_report())

    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.event_id == event_id
    assert event.kind is AuditEventKind.EXECUTION_DEGRADATION
    assert event.outcome is AuditOutcome.DEGRADED
    assert event.session_id == "s-1"
    detail: Mapping[str, object] = event.detail
    assert detail["tier"] == "l1"
    assert detail["degraded"] is True
    assert detail["reason"] == "测试用降级原因"
    assert detail["enforced_dimensions"] == ["user"]


def test_mechanism_none_cannot_claim_enforced() -> None:
    """契约不变式：``mechanism=NONE`` ⇒ ``enforced`` 必须为 False（不得多报）。"""
    with pytest.raises(ValueError):
        _status(IsolationDimension.NETWORK, True, IsolationMechanism.NONE, "伪造的隔离")


def test_probe_degrades_to_l0_without_privilege(monkeypatch: pytest.MonkeyPatch) -> None:
    """非特权执行不可用 ⇒ L0、全部维度未隔离、原因非空（fail-secure，不回退为 root）。"""

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise IsolationError("无法切换到非特权用户")

    monkeypatch.setattr("agent_sec_perf.security.sandbox.probe.proc.run", _boom)
    monkeypatch.setattr(
        "agent_sec_perf.security.sandbox.probe.probe_namespace_backend",
        lambda: (False, "测试：命名空间不可用"),
    )

    report = probe_isolation()

    assert report.tier is SandboxTier.L0
    assert report.matrix.degraded is True
    assert report.matrix.reason
    assert all(not item.enforced for item in report.matrix.dimensions.values())
