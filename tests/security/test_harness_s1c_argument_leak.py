"""``S1-c``（契约 §6.2 / I8 / V4）：参数值（含未声明的键名）不得泄漏进事件流 ``text`` / 审计 ``detail`` / 日志。

依赖 ``src/agent_sec_perf/harness/arguments.py``（真实参数校验器 ``SubsetArgumentValidator``，ADR-0020）。
校验动作前已确认该文件已落地（``importlib.util.find_spec`` 返回真实路径），故按任务口径「存在则可开测」，
用**真实校验器**驱动，而非 fake 顶替（用 fake 让"值不出现"=测的是 fake 自己，违反"被测行为不得是 fake 语义"）。

攻击面（来自真实实现 ``loop.py`` 步 2）：校验失败时 harness 将 ``sanitize_for_display(str(exc))``
拼进 ``TOOL_RESULT`` 的 ``text``（即回喂模型的观察内容），但审计 ``detail`` 只放结构性短码
``{"denied_reason": ...}``，绝不放大原始参数。真实校验器 ``SubsetArgumentValidator`` 的错误文案只描述
"哪个键、期望什么类型"（``V4``），不回显取值也未声明键名。

判据三条：
1. 注入 sentinel 取值（合法参数的取值）+ 未声明的键名（其值也是 sentinel）；
2. 跑完整决策序列；
3. sentinel 字符串与未声明键名**不**出现在：任意事件 ``text``、任意审计 ``detail``、捕获的日志中。

变异探针见 :func:`test_s1c_leak_flips_when_validator_echoes`：把两层真实保护（``sanitize_for_display`` 退化为
恒等、校验器把原始参数回显进错误=``V4`` 失效）摘掉后，sentinel 必然出现在事件 ``text``，原断言翻红——
证明上面的"无泄漏"断言确实由真实保护产生，而非偶然通过（防"绿着但没生效"）。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from _harness_fakes import (
    FakeModelClient,
    FakePolicyEngine,
    FakeRegistry,
    FakeTool,
    RecordingSink,
    collect,
    make_session,
    make_tool_call_response,
)

from agent_sec_perf.contracts.harness import CapabilityTier, PolicyDecision
from agent_sec_perf.contracts.policy import Capability, RiskLevel
from agent_sec_perf.contracts.tools import ToolResult, ToolSpec
from agent_sec_perf.foundation.errors import ToolArgumentsInvalidError
from agent_sec_perf.harness.arguments import SubsetArgumentValidator

_VALUE_SENTINEL = "SENTINEL-7f3a9c-value"
_KEY_SENTINEL = "__leak__sentinel__"
_KEYVAL_SENTINEL = "SENTINEL-7f3a9c-keyval"

_SPEC = ToolSpec(
    name="read_file",
    description="fake read_file",
    parameters_schema={
        "type": "object",
        "properties": {"q": {"type": "string"}},
        "required": ["q"],
        "additionalProperties": False,
    },
    capabilities=frozenset({Capability.READ_FILE}),
    source="builtin",
    description_digest=None,
)


def _decision(*, allow: bool, requires_confirmation: bool, audit_id: str) -> PolicyDecision:
    return PolicyDecision(
        allow=allow,
        requires_confirmation=requires_confirmation,
        risk_level=RiskLevel.HIGH if requires_confirmation else RiskLevel.LOW,
        reason="decision",
        audit_id=audit_id,
    )


def _build_session(
    tmp_path,
    *,
    arguments_json: str,
    validator,
    decision: PolicyDecision,
    sink: RecordingSink,
    tool_result: ToolResult | None = None,
) -> list:
    model = FakeModelClient(
        make_tool_call_response(call_id="c1", name="read_file", arguments_json=arguments_json)
    )
    tools = {"read_file": FakeTool(_SPEC, result=tool_result)}
    registry = FakeRegistry([_SPEC], tools)
    policy = FakePolicyEngine(decision, sink)
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
    return collect(session, "task")


def _leak_in_events(events: list) -> bool:
    needles = (_VALUE_SENTINEL, _KEY_SENTINEL, _KEYVAL_SENTINEL)
    for ev in events:
        text = getattr(ev, "text", None)
        if text and any(n in text for n in needles):
            return True
    return False


def _leak_in_audit(sink: RecordingSink) -> bool:
    needles = (_VALUE_SENTINEL, _KEY_SENTINEL, _KEYVAL_SENTINEL)
    for audit in sink.events:
        detail = getattr(audit, "detail", None)
        if detail and any(n in str(detail) for n in needles):
            return True
    return False


@pytest.mark.security
def test_s1c_argument_value_and_undeclared_key_do_not_leak(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """S1-c：未声明键 + sentinel 取值 ⇒ 任何事件 text / 审计 detail / 日志都不含 sentinel。"""
    sink = RecordingSink()
    validator = SubsetArgumentValidator()
    arguments_json = json.dumps({"q": _VALUE_SENTINEL, _KEY_SENTINEL: _KEYVAL_SENTINEL})

    with caplog.at_level(logging.DEBUG):
        events = _build_session(
            tmp_path,
            arguments_json=arguments_json,
            validator=validator,
            decision=_decision(allow=False, requires_confirmation=False, audit_id="pol-s1c-1"),
            sink=sink,
        )

    assert _leak_in_events(events) is False, "sentinel 不得出现在事件 text（回喂模型的观察内容）"
    assert _leak_in_audit(sink) is False, "sentinel 不得出现在审计 detail（只应放结构短码）"
    assert _VALUE_SENTINEL not in caplog.text and _KEY_SENTINEL not in caplog.text, (
        "sentinel 不得出现在日志"
    )


@pytest.mark.security
def test_s1c_valid_value_not_echoed_on_success(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """S1-c 强化：即便 sentinel 是合法取值（通过校验），也不得回显进事件 text / 审计 / 日志。"""
    sink = RecordingSink()
    validator = SubsetArgumentValidator()
    arguments_json = json.dumps({"q": _VALUE_SENTINEL})  # 合法取值，校验通过

    with caplog.at_level(logging.DEBUG):
        events = _build_session(
            tmp_path,
            arguments_json=arguments_json,
            validator=validator,
            decision=_decision(allow=True, requires_confirmation=False, audit_id="pol-s1c-2"),
            sink=sink,
            tool_result=ToolResult(ok=True, content="read", audit_id="tool-ok-s1c"),
        )

    assert _leak_in_events(events) is False, "合法参数取值不得被回显进事件 text"
    assert _leak_in_audit(sink) is False
    assert _VALUE_SENTINEL not in caplog.text


@pytest.mark.security
def test_s1c_leak_flips_when_validator_echoes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """S1-c 变异探针：摘掉两层真实保护（sanitize 退化+校验器回显原始参数=V4 失效）。

    原"无泄漏"断言应翻红——此处验证：保护被摘掉后 sentinel 必然出现在事件 text，证明原断言
    确实由真实保护产生，而非偶然通过。
    """
    import agent_sec_perf.foundation.logging as log_mod

    # ① 摘掉 harness 的纵深防御：sanitize_for_display 退化为恒等
    monkeypatch.setattr(log_mod, "sanitize_for_display", lambda s, limit=200: s)

    # ② 摘掉校验器的 V4：错误文案回显原始参数（含 sentinel）
    def _leaky(self, *, spec, arguments_json) -> None:
        raise ToolArgumentsInvalidError(f"invalid params: {arguments_json}")

    monkeypatch.setattr(SubsetArgumentValidator, "validate", _leaky)

    sink = RecordingSink()
    validator = SubsetArgumentValidator()  # 已被 monkeypatch 成泄漏版
    arguments_json = json.dumps({"q": _VALUE_SENTINEL, _KEY_SENTINEL: _KEYVAL_SENTINEL})

    with caplog.at_level(logging.DEBUG):
        events = _build_session(
            tmp_path,
            arguments_json=arguments_json,
            validator=validator,
            decision=_decision(allow=False, requires_confirmation=False, audit_id="pol-s1c-3"),
            sink=sink,
        )

    assert _leak_in_events(events) is True, "变异探针：摘掉保护后，原'无泄漏'断言应翻红"
