"""对抗性验证：``PolicyEngine.decide()`` 对**非 ``Capability`` 成员**的处理（``REQ-SEC-01``）。

依据（独立验证，不听实现者的解释）：

* ``docs/design/interfaces/policy.md`` §2.5「补充规定」的判据 V7~V10，以及
  「非法成员的规定行为」中的硬性约束；
* ``docs/design/interfaces/audit.md`` §2.3 的 **I4**（``capability`` 必须是 ``Capability``
  实例或 ``None``，裸 ``str`` 即使取值合法也拒绝）。

攻击者视角的关键点：

* 类型违规输入 ⇒ **整体拒绝**（``False/False`` + ``CRITICAL``），**不得**"忽略非法成员、
  用合法子集继续求值"——丢弃哪些成员由不可信输入决定，是 fail-open；
* 裸 ``str`` ``"read_file"``（取值恰好合法）**必须被拒、不得放行**——这是 2026-09-19 实测过的
  真实绕过（``StrEnum`` 成员与其 ``str`` 值 ``==``/``hash`` 相等 ⇒ 差集为空 ⇒ 被当成已授权）；
* ``detail["invalid"]`` 只作数据：``str`` 先脱敏+截断 32 字符、非 ``str`` 只记类型名、
  **永远不得**进入 ``capability`` / ``requested``；
* 附**变异探针**：临时把类型检查（``policy._invalid_members``）置空，证明该用例依赖真实
  保护、非恒过（探针经 ``monkeypatch`` 自动还原，不留在共享工作树）。
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_sec_perf.contracts.audit import AuditOutcome
from agent_sec_perf.contracts.policy import Capability, PolicyRequest, RiskLevel
from agent_sec_perf.security import policy as policy_module
from agent_sec_perf.security.capabilities import CapabilitySet
from agent_sec_perf.security.policy import PolicyEngine

from .conftest import RecordingSink, all_granted, assert_policy_event_invariants


def _request(*, requested: frozenset[Any], call_id: str = "call-1") -> PolicyRequest:
    return PolicyRequest(
        session_id="sess-1",
        call_id=call_id,
        tool_name="tool-under-test",
        arguments={},
        requested=requested,
    )


def _engine(granted: CapabilitySet) -> PolicyEngine:
    return PolicyEngine(granted=granted, sink=RecordingSink())


@pytest.mark.security
def test_unknown_capability_name_is_whole_denied() -> None:
    """V7：``frozenset({"bogus"})``（全授予）→ 硬拒绝；``requested==[]``、``invalid`` 含 bogus。"""
    engine = _engine(all_granted())
    decision = engine.decide(_request(requested=frozenset({"bogus"})))

    assert (decision.allow, decision.requires_confirmation) == (False, False)
    assert decision.risk_level is RiskLevel.CRITICAL
    event = engine.sink.events[0]
    assert event.outcome is AuditOutcome.DENY
    assert event.capability is None
    assert event.detail["requested"] == []
    assert "bogus" in event.detail["invalid"]
    # 非法成员分支不得带 error 键（与"求值失败"分支区分）。
    assert "error" not in event.detail
    assert_policy_event_invariants(event, expect_invalid=True)


@pytest.mark.security
def test_bare_str_capability_is_rejected_regression() -> None:
    """V8（回归守卫）：裸 ``str`` ``"read_file"``（取值合法）即使已授予也**必须被拒、不得放行**。

    这是已发生过的真实 default-deny 绕过；修复后 ``capability`` 必须是 ``Capability``
    实例或 ``None``（I4），裸 ``str`` 即便取值合法也拒绝。
    """
    engine = _engine(CapabilitySet(granted=frozenset({Capability.READ_FILE})))
    decision = engine.decide(_request(requested=frozenset({"read_file"})))

    assert (decision.allow, decision.requires_confirmation) == (False, False)
    event = engine.sink.events[0]
    assert event.capability is None
    assert event.detail["invalid"] == ["read_file"]
    # 关键：绝不能是放行（allow=True）。
    assert decision.allow is False
    assert_policy_event_invariants(event, expect_invalid=True)


@pytest.mark.security
def test_mixed_valid_and_invalid_is_whole_denied() -> None:
    """V9：``{READ_FILE, "bogus"}`` ⇒ 整体拒绝，**不得**因 READ_FILE 已授予而放行，
    也**不得**只报 READ_FILE；``requested==[]``、``invalid==["bogus"]``。"""
    engine = _engine(CapabilitySet(granted=frozenset({Capability.READ_FILE})))
    decision = engine.decide(_request(requested=frozenset({Capability.READ_FILE, "bogus"})))

    assert (decision.allow, decision.requires_confirmation) == (False, False)
    event = engine.sink.events[0]
    assert event.capability is None
    assert event.detail["requested"] == []
    assert event.detail["invalid"] == ["bogus"]
    # 不得把合法成员塞进 requested（那会误导读者以为"合法部分被考虑/授予"）。
    assert "read_file" not in event.detail["requested"]
    assert_policy_event_invariants(event, expect_invalid=True)


@pytest.mark.security
def test_non_str_member_records_type_name_only() -> None:
    """V10：``frozenset({123})`` ⇒ 拒绝；``invalid==["<int>"]``（只类型名、不回显内容）。"""
    engine = _engine(all_granted())
    decision = engine.decide(_request(requested=frozenset({123})))

    assert decision.allow is False
    event = engine.sink.events[0]
    assert event.detail["invalid"] == ["<int>"]
    # 审计里**不得**出现该对象的 repr（如 "123" 不应作为裸值出现于 reason/invalid）。
    assert "123" not in event.detail["invalid"]
    assert_policy_event_invariants(event, expect_invalid=True)


@pytest.mark.security
def test_invalid_member_label_is_truncated() -> None:
    """``str`` 非法成员先脱敏再截断（≤32 字符），不得整段回显。"""
    long_name = "x" * 100
    engine = _engine(all_granted())
    decision = engine.decide(_request(requested=frozenset({long_name})))

    assert decision.allow is False
    invalid = engine.sink.events[0].detail["invalid"]
    # sanitize_for_display 在超长时返回 stripped[:limit] + "…"（32 个 x + 省略号）。
    assert invalid == ["x" * 32 + "…"]
    assert len(invalid[0]) == 33


@pytest.mark.security
def test_regression_guard_depends_on_type_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """变异探针（W7 风格）：移除类型检查（``_invalid_members`` 置空）后，
    裸 ``str`` ``"read_file"`` 会被**放行** ⇒ 证明上面的回归守卫依赖真实保护、非恒过。

    ``monkeypatch`` 结束自动还原实现，探针不留在共享工作树。
    """
    monkeypatch.setattr(policy_module, "_invalid_members", lambda *_a, **_k: [])
    engine = _engine(CapabilitySet(granted=frozenset({Capability.READ_FILE})))
    # 此时类型检查被移除 ⇒ 原本应被拒的裸 str 被当成已授权路径解析。
    decision = engine.decide(_request(requested=frozenset({"read_file"})))
    # 保护缺失 ⇒ 攻击得手（放行）。
    assert decision.allow is True
