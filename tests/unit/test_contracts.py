"""契约层的数据约定：不可变、fail-secure 默认值、枚举可 JSON 序列化。

依据 ``docs/design/interfaces/README.md`` §2 的通用约定 C2~C7。契约是**零行为**的，
因此这里只断言"数据形状与默认值"，不测行为逻辑；类型不变式（如 ``role == TOOL`` ⇒
``tool_call_id`` 非空）由**生产者**（HARNESS）保证，待其落地后在对应层测试。
"""

from __future__ import annotations

import dataclasses
import inspect
import json
from pathlib import Path

import pytest

from agent_sec_perf.contracts.audit import AuditEventKind, AuditOutcome
from agent_sec_perf.contracts.harness import (
    ApprovalGate,
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalResult,
    ArgumentValidator,
    Session,
    SessionConfig,
    SessionErrorKind,
    SessionEvent,
    SessionEventKind,
    TaskStatus,
)
from agent_sec_perf.contracts.model import (
    CapabilityTier,
    ChatMessage,
    FinishReason,
    HardwareTier,
    ModelClient,
    Role,
)
from agent_sec_perf.contracts.policy import Capability, RiskLevel
from agent_sec_perf.contracts.sandbox import IsolationMechanism, SandboxRequest, SandboxTier
from agent_sec_perf.contracts.tools import ExecutionContext


@pytest.mark.unit
def test_data_contracts_are_frozen() -> None:
    """数据契约必须不可变（C2/C4）：就地赋值应抛 FrozenInstanceError。"""
    message = ChatMessage(role=Role.USER, content="hi")

    with pytest.raises(dataclasses.FrozenInstanceError):
        message.__setattr__("content", "tampered")


@pytest.mark.unit
def test_fail_secure_defaults_deny_by_default() -> None:
    """面向"是否允许"的字段默认取最保守值（C6 / default-deny）。"""
    context = ExecutionContext(
        session_id="s1",
        call_id="c1",
        working_dir=Path("/tmp/w"),
        allowed_roots=(Path("/tmp/w"),),
        timeout_s=30.0,
    )
    sandbox_request = SandboxRequest(
        argv=("/bin/echo",),
        cwd=Path("/tmp/w"),
        allowed_roots=(Path("/tmp/w"),),
        timeout_s=30.0,
    )

    assert context.network_allowed is False
    assert sandbox_request.network_allowed is False
    assert dict(sandbox_request.env) == {}


@pytest.mark.unit
def test_model_client_hides_tools_and_sets_timeout_by_default() -> None:
    """``chat`` 默认不暴露工具、默认温度 0、默认带超时（C6；安全基线要求出站必设超时）。"""
    parameters = inspect.signature(ModelClient.chat).parameters

    assert parameters["messages"].default is inspect.Parameter.empty
    assert parameters["tools"].default is None
    assert parameters["temperature"].default == 0.0
    assert parameters["timeout_s"].default == 60.0


@pytest.mark.unit
def test_enums_are_lowercase_and_json_serializable() -> None:
    """枚举用 ``(str, Enum)``、值小写，可直接 JSON 序列化（C3）。"""
    samples = {
        Role.USER: "user",
        Capability.WRITE_FILE: "write_file",
        RiskLevel.CRITICAL: "critical",
        IsolationMechanism.SETRLIMIT: "setrlimit",
        SandboxTier.L2_NAMESPACE: "l2-namespace",
        AuditOutcome.DEGRADED: "degraded",
        AuditEventKind.POLICY_DECISION: "policy_decision",
        FinishReason.LENGTH: "length",
        CapabilityTier.ADVANCED: "advanced",
        HardwareTier.S: "s",
    }

    for member, expected in samples.items():
        assert member.value == expected
        assert json.dumps(member) == f'"{expected}"'


@pytest.mark.unit
def test_hardware_tier_members_are_exactly_sml() -> None:
    """``HardwareTier`` 的成员**恰好**是 S/M/L（成员与语义由 ``ADR-0011 §5.1`` 定义）。

    用"恰好相等"而不是"存在"：多一个或少一个档位都会让"映射到固定 3 档"（``REQ-PERF-06``）
    失去可验证性。
    """
    assert {tier.value for tier in HardwareTier} == {"s", "m", "l"}
    assert set(HardwareTier.__members__) == {"S", "M", "L"}


@pytest.mark.unit
def test_capability_and_hardware_tiers_are_disjoint_axes() -> None:
    """两条轴正交：成员名与取值都不得相交（``CapabilityTier`` 不得取名为 ``S``/``M``/``L``）。

    依据 ``docs/design/interfaces/model.md`` §2.3.2 的硬禁令：``S``/``M``/``L`` **只属于**
    ``HardwareTier``；两轴混用会让"同一模型换硬件后能力档位不变"无法成立。
    """
    capability_names = set(CapabilityTier.__members__)
    hardware_names = set(HardwareTier.__members__)
    capability_values = {tier.value for tier in CapabilityTier}
    hardware_values = {tier.value for tier in HardwareTier}

    assert capability_names & hardware_names == set(), "两条轴的成员名不得相交"
    assert capability_names & {"S", "M", "L"} == set(), "CapabilityTier 不得取名为 S/M/L"
    assert capability_values & hardware_values == set(), "两条轴的取值不得相交"


# ---------------------------------------------------------------------------
# 编排层契约（``contracts/harness.py``；字段级定义见 interfaces/harness.md §2）
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_session_event_kinds_are_exactly_the_seven_contract_kinds() -> None:
    """事件类别**恰好**是契约 §2.1 的 7 个。

    用"恰好相等"而不是"存在"：多一个 kind 就多出一组未定义的不变式（``I1``~``I10``
    只覆盖这 7 个），少一个则对应需求失去可观测载体。
    """
    assert {kind.value for kind in SessionEventKind} == {
        "model_response",
        "tool_call",
        "policy_decision",
        "approval_result",
        "tool_result",
        "error",
        "task_finished",
    }
    assert set(SessionEventKind.__members__) == {
        "MODEL_RESPONSE",
        "TOOL_CALL",
        "POLICY_DECISION",
        "APPROVAL_RESULT",
        "TOOL_RESULT",
        "ERROR",
        "TASK_FINISHED",
    }


@pytest.mark.unit
def test_task_status_members_are_exactly_three() -> None:
    """终态恰好 3 个；``LIMIT_REACHED`` 与 ``FAILED`` **必须并存**（§2.3 的区分理由）。"""
    assert set(TaskStatus.__members__) == {"COMPLETED", "FAILED", "LIMIT_REACHED"}


@pytest.mark.unit
def test_session_error_kinds_are_exactly_five() -> None:
    """``ERROR`` 的分级恰好 5 个；**不含**"工具级失败"（那是回喂的数据通路）。"""
    assert set(SessionErrorKind.__members__) == {
        "TRANSIENT",
        "UNREACHABLE",
        "PROTOCOL",
        "STALLED",
        "INTERNAL",
    }


@pytest.mark.unit
def test_approval_outcomes_match_the_three_ux_choices() -> None:
    """``REQ-UX-02`` 的三种选择（本次 / 总是 / 拒绝）必须都可用。"""
    assert set(ApprovalOutcome.__members__) == {"ALLOW_ONCE", "ALLOW_ALWAYS", "DENY"}


@pytest.mark.unit
def test_session_event_has_the_fourteen_contract_fields() -> None:
    """字段集合**恰好**是契约 §2.2 的 14 个（多一个就多一处未定义的不变式）。"""
    assert {field.name for field in dataclasses.fields(SessionEvent)} == {
        "kind",
        "session_id",
        "seq",
        "timestamp",
        "call_id",
        "tool_name",
        "response",
        "decision",
        "approval",
        "result",
        "text",
        "error_kind",
        "status",
        "audit_id",
    }


@pytest.mark.unit
def test_session_event_optional_fields_default_to_none() -> None:
    """除 4 个必填字段外全部默认 ``None``——"没填"不得被静默当成某个语义。"""
    event = SessionEvent(
        kind=SessionEventKind.TASK_FINISHED,
        session_id="s1",
        seq=0,
        timestamp="2026-09-19T00:00:00+08:00",
    )

    assert event.call_id is None
    assert event.tool_name is None
    assert event.response is None
    assert event.decision is None
    assert event.approval is None
    assert event.result is None
    assert event.text is None
    assert event.error_kind is None
    assert event.status is None
    assert event.audit_id is None


@pytest.mark.unit
def test_session_event_is_frozen() -> None:
    """事件是**事实记录**：不得被就地改写（审计与事件流的可回放前提）。"""
    event = SessionEvent(
        kind=SessionEventKind.ERROR,
        session_id="s1",
        seq=1,
        timestamp="2026-09-19T00:00:01+08:00",
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        event.__setattr__("text", "tampered")


@pytest.mark.unit
def test_session_config_defaults_take_the_most_conservative_side() -> None:
    """``capability_tier`` 默认**最弱档**（工具最少、提示最结构化）⇒ fail-secure 方向。"""
    config = SessionConfig(working_dir=Path("/tmp/w"), allowed_roots=(Path("/tmp/w"),))

    assert config.capability_tier is CapabilityTier.BASIC
    assert config.max_steps == 12
    assert config.max_consecutive_failures == 3
    assert config.tool_timeout_s == 30.0
    assert config.max_prompt_tokens == 8192
    assert config.max_completion_tokens is None


@pytest.mark.unit
def test_session_config_requires_working_dir_and_allowed_roots() -> None:
    """两个"根"字段**无默认值**：白名单根不得来自猜测（C6 / default-deny）。"""
    parameters = inspect.signature(SessionConfig).parameters

    assert parameters["working_dir"].default is inspect.Parameter.empty
    assert parameters["allowed_roots"].default is inspect.Parameter.empty


@pytest.mark.unit
def test_session_protocol_is_a_synchronous_iterator() -> None:
    """``Session.run`` 是**同步** ``Iterator``（契约 §2.9 的裁决：不引入 asyncio）。"""
    assert inspect.iscoroutinefunction(Session.run) is False
    assert list(inspect.signature(Session.run).parameters) == ["self", "task"]
    assert list(inspect.signature(Session.close).parameters) == ["self"]


@pytest.mark.unit
def test_argument_validator_takes_raw_json_as_keyword_only() -> None:
    """校验器的输入是**原始 JSON 文本**（信任边界钉在 HARNESS 侧，``tools.md`` §1 第 2 条）。"""
    parameters = inspect.signature(ArgumentValidator.validate).parameters

    assert list(parameters) == ["self", "spec", "arguments_json"]
    assert parameters["spec"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["arguments_json"].kind is inspect.Parameter.KEYWORD_ONLY


@pytest.mark.unit
def test_approval_gate_request_is_the_only_method() -> None:
    """确认通路只有一个同步方法；``harness`` 不轮询、不超时（``harness.md`` §2.5 的 ``A2``）。"""
    assert inspect.iscoroutinefunction(ApprovalGate.request) is False
    assert list(inspect.signature(ApprovalGate.request).parameters) == ["self", "request"]


@pytest.mark.unit
def test_approval_types_are_frozen_and_summary_is_optional() -> None:
    """审批数据不可变；``arguments_summary`` 可缺省（参数为空映射时**不**编造占位）。"""
    request = ApprovalRequest(
        session_id="s1",
        call_id="c1",
        tool_name="write_file",
        risk_level=RiskLevel.HIGH,
        reason="写文件属高风险操作",
    )
    result = ApprovalResult(outcome=ApprovalOutcome.DENY, audit_id="a1")

    assert request.arguments_summary is None
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.__setattr__("outcome", ApprovalOutcome.ALLOW_ONCE)


@pytest.mark.unit
def test_harness_enums_are_lowercase_and_json_serializable() -> None:
    """编排层枚举同样满足 C3（值小写、可直接 JSON 序列化）。"""
    samples = {
        SessionEventKind.POLICY_DECISION: "policy_decision",
        TaskStatus.LIMIT_REACHED: "limit_reached",
        SessionErrorKind.STALLED: "stalled",
        ApprovalOutcome.ALLOW_ALWAYS: "allow_always",
    }

    for member, expected in samples.items():
        assert member.value == expected
        assert json.dumps(member) == f'"{expected}"'
