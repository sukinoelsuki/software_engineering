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
    }

    for member, expected in samples.items():
        assert member.value == expected
        assert json.dumps(member) == f'"{expected}"'
