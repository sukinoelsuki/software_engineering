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
