"""``S1-c``（契约 §6.2 / §2.2 的 ``I8`` / §3.4 的 ``V4``）：参数值（含未声明的键名）不得泄漏进
事件流 ``text`` / 审计 ``detail`` / 日志。

依赖 ``src/agent_sec_perf/harness/arguments.py``（真实参数校验器 ``SubsetArgumentValidator``，ADR-0020，
提交 ``43a177c`` 已入库）。校验动作前已确认该文件已落地（``git ls-files`` 命中），故按任务口径
「存在则可开测」，用**真实校验器**驱动，而非 fake 顶替（用 fake 让"值不出现"=测的是 fake 自己，
违反"被测行为不得是 fake 语义"）。

攻击面（来自真实实现 ``loop.py`` 步 2）：校验失败时 harness 将 ``sanitize_for_display(str(exc))``
拼进 ``TOOL_RESULT`` 的 ``text``（即回喂模型的观察内容），但审计 ``detail`` 只放结构性短码
``{"denied_reason": ...}``，绝不放大原始参数。真实校验器 ``SubsetArgumentValidator`` 的错误文案只描述
"哪个键、期望什么类型"（``V4``），不回显取值也未声明键名。

三个面（按领导第 2 条指令的口径）：
- 面 1（值）：超长 / 非法 JSON / 类型不符 三类载荷，各带**唯一 sentinel** ⇒ 事件 ``text`` / 审计
  ``detail`` / 日志均不含该 sentinel；
- 面 2（未声明键名）：用含控制字符（``\\x1b[2J``）的唯一 sentinel 作**键名** ⇒ 该键名（含控制字符）
  不得出现在 ``text`` / 审计 ``detail``；
- 面 3（已声明键）：可以出现、但取值须经 ``sanitize_for_display`` 且受长度上限约束——本测试取
  "允许"的语义：声明键合法取值**通过校验并执行**（不得过度拒绝），且取值仍不得原文泄漏（``V4``）。

变异探针见 :func:`test_s1c_leak_flips_when_validator_echoes`：把两层真实保护（``sanitize_for_display``
退化为恒等、校验器把原始参数回显进错误=``V4`` 失效）摘掉后，sentinel 必然出现在事件 ``text``，
原断言翻红——证明"无泄漏"断言确实由真实保护产生，而非偶然通过（防"绿着但没生效"）。

⚠️ 范围边界：本文件只测 **harness 契约层**的事件流 ``text`` / 审计 ``detail`` / 日志（即 ``I8``/``V4``
的字面标的）。实现侧已通报一处**契约张力**：``harness.md`` §1.1/§2.2 的 ``I8`` 称"事件不得承载原始
``arguments_json``"，而 §2.6 第 3 条要求 ``response`` 按字段表递归展开 ⇒ ``event_to_payload(event)``
的 ``response.tool_calls[i].arguments_json`` **会**含原始 JSON。那是 **cli 渲染层**的另一面，
由领导裁决、不在本 harness 层断言内；本测试不依赖 cli 渲染输出，故不受该张力影响。
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

# 面 1 的三种取值载荷各带唯一 sentinel
_OSZ_SENTINEL = "SENT-OSZ-9f2a"
_OVERSIZED_VALUE = ("X" * 4900) + _OSZ_SENTINEL + ("Y" * 100)
_OVERSIZED_JSON = json.dumps({"q": _OVERSIZED_VALUE})
_BADJSON_SENTINEL = "SENT-BADJSON-3c7d"
_BADJSON_RAW = "{not valid json SENT-BADJSON-3c7d"
_TYPE_SENTINEL = "SENT-TYPE-1b8e"
_TYPE_JSON = json.dumps(
    {"q": "ok", "n": _TYPE_SENTINEL}
)  # n 声明为 integer，此处给 string ⇒ 类型不符

# 面 2：未声明键名（含控制字符）的唯一 sentinel
_KEY_SENTINEL = "\x1b[2J" + "SENT-KEY-4d2f"
_KEYVAL_SENTINEL = "VALUE-LEAK-9c1a"

# 面 3：已声明键合法取值
_DECL_SENTINEL = "SENT-DECL-7e3b"

_SPEC = ToolSpec(
    name="read_file",
    description="fake read_file",
    parameters_schema={
        "type": "object",
        "properties": {
            "q": {"type": "string"},
            "n": {"type": "integer"},
        },
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
):
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
    events = collect(session, "task")
    return events, sink, tools


def _any_leak(events, sink, caplog, needles) -> bool:
    for ev in events:
        text = getattr(ev, "text", None)
        if text and any(n in text for n in needles):
            return True
    for audit in sink.events:
        detail = getattr(audit, "detail", None)
        if detail and any(n in str(detail) for n in needles):
            return True
    return bool(any(n in caplog.text for n in needles))


@pytest.mark.security
@pytest.mark.parametrize(
    ("arguments_json", "sentinel"),
    [
        pytest.param(_OVERSIZED_JSON, _OSZ_SENTINEL, id="oversized-value"),
        pytest.param(_BADJSON_RAW, _BADJSON_SENTINEL, id="invalid-json"),
        pytest.param(_TYPE_JSON, _TYPE_SENTINEL, id="type-mismatch"),
    ],
)
def test_s1c_value_payloads_do_not_leak(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, arguments_json: str, sentinel: str
) -> None:
    """S1-c 面 1：超长 / 非法 JSON / 类型不符 三类取值载荷各带唯一 sentinel，均不得泄漏进 text/审计/日志。"""
    sink = RecordingSink()
    validator = SubsetArgumentValidator()

    with caplog.at_level(logging.DEBUG):
        events, sink, _tools = _build_session(
            tmp_path,
            arguments_json=arguments_json,
            validator=validator,
            decision=_decision(allow=False, requires_confirmation=False, audit_id="pol-s1c-v"),
            sink=sink,
        )

    assert _any_leak(events, sink, caplog, [sentinel]) is False, (
        f"取值 sentinel（{sentinel}）不得出现在事件 text / 审计 detail / 日志"
    )


@pytest.mark.security
def test_s1c_undeclared_key_name_with_control_chars_not_leaked(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """S1-c 面 2：未声明键名（含控制字符 \\x1b[2J）不得出现在 text / 审计 detail。"""
    sink = RecordingSink()
    validator = SubsetArgumentValidator()
    arguments_json = json.dumps({_KEY_SENTINEL: _KEYVAL_SENTINEL, "q": "normal"})

    with caplog.at_level(logging.DEBUG):
        events, sink, _tools = _build_session(
            tmp_path,
            arguments_json=arguments_json,
            validator=validator,
            decision=_decision(allow=False, requires_confirmation=False, audit_id="pol-s1c-k"),
            sink=sink,
        )

    assert _any_leak(events, sink, caplog, [_KEY_SENTINEL, _KEYVAL_SENTINEL]) is False, (
        "未声明键名（含控制字符）与其取值均不得出现在事件 text / 审计 detail / 日志"
    )


@pytest.mark.security
def test_s1c_declared_key_allowed_but_value_not_raw_leaked(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """S1-c 面 3：已声明键（q）合法取值通过校验并执行（不得过度拒绝），且取值仍不得原文泄漏（V4）。"""
    sink = RecordingSink()
    validator = SubsetArgumentValidator()
    arguments_json = json.dumps({"q": _DECL_SENTINEL})

    with caplog.at_level(logging.DEBUG):
        events, sink, tools = _build_session(
            tmp_path,
            arguments_json=arguments_json,
            validator=validator,
            decision=_decision(allow=True, requires_confirmation=False, audit_id="pol-s1c-d"),
            sink=sink,
            tool_result=ToolResult(ok=True, content="read", audit_id="tool-ok-d"),
        )

    # 已声明键合法取值 ⇒ 通过校验、工具被执行（正面：不得过度拒绝）
    assert tools["read_file"].invoke_calls, "已声明键合法取值应通过校验并执行工具（不得过度拒绝）"
    # 取值仍不得原文泄漏（V4 / sanitize 不变式）
    assert _any_leak(events, sink, caplog, [_DECL_SENTINEL]) is False, (
        "已声明键的合法取值仍不得原文泄漏进事件 text / 审计 detail / 日志"
    )


@pytest.mark.security
def test_s1c_leak_flips_when_validator_echoes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """S1-c 变异探针：摘掉两层真实保护（sanitize 退化 + 校验器回显原始参数=V4 失效）。

    原"无泄漏"断言应翻红——此处验证：保护被摘掉后 sentinel 必然出现在事件 ``text``，证明原断言
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
    arguments_json = json.dumps({"q": _OSZ_SENTINEL, _KEY_SENTINEL: _KEYVAL_SENTINEL})

    with caplog.at_level(logging.DEBUG):
        events, sink, _tools = _build_session(
            tmp_path,
            arguments_json=arguments_json,
            validator=validator,
            decision=_decision(allow=False, requires_confirmation=False, audit_id="pol-s1c-3"),
            sink=sink,
        )

    assert _any_leak(events, sink, caplog, [_OSZ_SENTINEL, _KEYVAL_SENTINEL]) is True, (
        "变异探针：摘掉保护后，原'无泄漏'断言应翻红"
    )
