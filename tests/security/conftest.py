"""``tests/security`` 共享夹具：只放"捕获审计事件的桩"与"契约不变式断言"。

这些不是被测对象，只是让对抗性用例能**独立**验证 ``PolicyEngine`` / 工具产出的审计事件
是否满足 ``docs/design/interfaces/audit.md`` §2.3 的 I1~I4。
不包括任何"预先装好结论的替身 Harness"——决策逻辑仍由实现承担。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# 让同目录下的共享模块（如 ``_harness_fakes``）以裸模块名可被对抗性用例导入，
# 不受 pytest 的包导入解析（tests/security 有 __init__.py 但 tests/ 没有）影响。
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_sec_perf.contracts.audit import AuditEvent, AuditEventKind, AuditSink
from agent_sec_perf.contracts.policy import Capability
from agent_sec_perf.security.capabilities import CapabilitySet


class RecordingSink(AuditSink):
    """只捕获 ``emit`` 事件的审计桩（不含任何判定逻辑）。"""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def emit(self, event: AuditEvent) -> None:
        self.events.append(event)

    def flush(self) -> None:
        pass


def all_granted() -> CapabilitySet:
    """构造"全授予"集合（用于"即使全授予也应拒绝"类的攻击者视角用例）。"""
    return CapabilitySet(granted=frozenset(Capability))


def assert_policy_event_invariants(
    event: AuditEvent,
    *,
    expect_invalid: bool = False,
) -> None:
    """对一条 ``POLICY_DECISION`` 事件套用 ``audit.md`` §2.3 的 I1~I4。

    与 ``policy.md`` §2.5 的 V6 一致：所有策略用例复用一个不变式断言函数。
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
    allowed_names = {member.value for member in Capability}
    for name in requested:
        assert isinstance(name, str)
        assert name in allowed_names, f"未知能力名 {name!r}"
    # I3：capability is None ⇔ requested == []；否则 capability.value ∈ requested。
    if event.capability is None:
        assert requested == [], "I3：capability 为 None 必须 ⇒ requested == []"
    else:
        assert isinstance(event.capability, Capability), "I4：capability 必须是 Capability 实例"
        assert event.capability.value in requested, "I3：capability.value 必须 ∈ requested"
    # I2 的三种 [] 来源可区分：非法成员分支必须有 detail['invalid'] 判别键。
    if expect_invalid:
        assert "invalid" in event.detail, "非法成员分支必须有 detail['invalid'] 判别键"


@pytest.fixture
def sink() -> RecordingSink:
    return RecordingSink()
