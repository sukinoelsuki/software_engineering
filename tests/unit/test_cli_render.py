"""``cli/render.py`` 的行为断言（契约 ``harness.md`` §5.2 的渲染分支表 + §2.6 的序列化口径）。

这是实现侧的功能断言，**不**证明安全属性：对抗性验收属 ``tests/security/``
（``CODEBUDDY.md`` §10.2 规则 4）。本文件只钉住三件事：

1. §5.2 的**每个** ``kind`` 至少一条渲染用例（含三种 ``POLICY_DECISION`` 与三种
   ``TOOL_RESULT`` 分支——"未执行"与"执行失败"必须可区分）；
2. **不可信内容经净化**：``tool_name`` / ``response.content`` / ``result.error`` 里的
   控制字符（含 ESC）不得出现在渲染结果里（§2.6 第 5 条）；
3. §2.6 的序列化口径：键 = 字段名且全部保留、枚举写值、嵌套对象递归展开、可交给
   :func:`json.loads` 往返。
"""

from __future__ import annotations

import json
from typing import Final

import pytest

from agent_sec_perf.cli.render import event_to_payload, render_event, serialize_event
from agent_sec_perf.contracts.harness import (
    ApprovalOutcome,
    ApprovalResult,
    SessionErrorKind,
    SessionEvent,
    SessionEventKind,
    TaskStatus,
)
from agent_sec_perf.contracts.model import FinishReason, ModelResponse, TokenUsage
from agent_sec_perf.contracts.policy import PolicyDecision, RiskLevel
from agent_sec_perf.contracts.tools import ToolCallRequest, ToolResult

#: 唯一的 sentinel：用于证明"不可信内容不会以未净化形态进终端"。
SENTINEL: Final = "SENTINEL-7f3a"
ESC: Final = "\x1b[2J"

#: ``SessionEvent`` 的字段全集（§2.6 第 1 条：序列化时**一个都不省略**）。
EVENT_FIELDS: Final = {
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


def _event(kind: SessionEventKind, **fields: object) -> SessionEvent:
    return SessionEvent(
        kind=kind,
        session_id="s-render",
        seq=0,
        timestamp="2026-09-19T00:00:00+00:00",
        **fields,  # type: ignore[arg-type]
    )


def _response(
    *,
    content: str | None,
    finish: FinishReason = FinishReason.STOP,
    calls: tuple[ToolCallRequest, ...] = (),
) -> ModelResponse:
    return ModelResponse(
        content=content,
        tool_calls=calls,
        finish_reason=finish,
        usage=TokenUsage(prompt_tokens=1, completion_tokens=2, total_tokens=3),
        model_id="fake",
    )


def _call(call_id: str = "c1") -> ToolCallRequest:
    return ToolCallRequest(call_id=call_id, name="read_file", arguments_json="{}")


def _decision(*, allow: bool, confirm: bool, risk: RiskLevel = RiskLevel.HIGH) -> PolicyDecision:
    return PolicyDecision(
        allow=allow,
        requires_confirmation=confirm,
        risk_level=risk,
        reason="我方生成的中文理由",
        audit_id="audit-policy",
    )


# ---------------------------------------------------------------------------
# §5.2：每个 kind 的渲染分支
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_model_response_is_rendered_and_truncation_is_marked() -> None:
    """``MODEL_RESPONSE``：显示正文；``finish_reason is LENGTH`` 附"输出被截断"。"""
    plain = _event(SessionEventKind.MODEL_RESPONSE, response=_response(content="最终答复"))
    truncated = _event(
        SessionEventKind.MODEL_RESPONSE,
        response=_response(content="半截", finish=FinishReason.LENGTH),
    )

    assert render_event(plain) == "最终答复"
    assert render_event(truncated) == "半截（输出被截断）"


@pytest.mark.unit
def test_model_response_without_content_prints_nothing() -> None:
    """纯工具调用（``content is None``）⇒ **不打印空行**（§5.2 的备注列）。"""
    event = _event(
        SessionEventKind.MODEL_RESPONSE,
        response=_response(content=None, finish=FinishReason.TOOL_CALLS, calls=(_call(),)),
    )

    assert render_event(event) is None


@pytest.mark.unit
def test_tool_call_is_rendered() -> None:
    """``TOOL_CALL``：``→ 调用 <tool_name>``。"""
    event = _event(SessionEventKind.TOOL_CALL, call_id="c1", tool_name="read_file")

    assert render_event(event) == "→ 调用 read_file"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("allow", "confirm", "expected"),
    [
        pytest.param(
            True, True, "需确认：read_file（风险 high）— 我方生成的中文理由", id="confirm"
        ),
        pytest.param(True, False, "放行：read_file（风险 high）", id="allow"),
        pytest.param(False, False, "拒绝：read_file（风险 high）— 我方生成的中文理由", id="deny"),
    ],
)
def test_policy_decision_has_three_branches(allow: bool, confirm: bool, expected: str) -> None:
    """``POLICY_DECISION``：需确认 / 放行 / 拒绝三种分支都必须存在（§5.2）。"""
    event = _event(
        SessionEventKind.POLICY_DECISION,
        call_id="c1",
        tool_name="read_file",
        decision=_decision(allow=allow, confirm=confirm),
        audit_id="audit-policy",
    )

    assert render_event(event) == expected


@pytest.mark.unit
def test_approval_result_is_rendered() -> None:
    """``APPROVAL_RESULT``：一行用户选择（三种选择都可见，``REQ-UX-02``）。"""
    event = _event(
        SessionEventKind.APPROVAL_RESULT,
        call_id="c1",
        tool_name="read_file",
        approval=ApprovalResult(outcome=ApprovalOutcome.ALLOW_ALWAYS, audit_id="audit-approval"),
        audit_id="audit-approval",
    )

    assert render_event(event) == "人工确认结果：allow_always（read_file）"


@pytest.mark.unit
def test_tool_result_not_executed_is_distinct_from_execution_failure() -> None:
    """``TOOL_RESULT``：``result is None``（未执行）与 ``result.ok is False``（执行失败）不同形。"""
    denied = _event(
        SessionEventKind.TOOL_RESULT,
        call_id="c1",
        tool_name="read_file",
        result=None,
        text="策略拒绝，本次调用未执行",
        audit_id="audit-deny",
    )
    failed = _event(
        SessionEventKind.TOOL_RESULT,
        call_id="c1",
        tool_name="read_file",
        result=ToolResult(ok=False, content="", error="读不到", audit_id="audit-tool"),
        audit_id="audit-tool",
    )

    assert render_event(denied) == "✗ 未执行：策略拒绝，本次调用未执行"
    assert render_event(failed) == "✗ read_file：读不到"
    assert render_event(denied) != render_event(failed)


@pytest.mark.unit
def test_tool_result_success_marks_truncation() -> None:
    """``TOOL_RESULT`` 成功：``✓ <tool_name>``，``truncated`` 时注明"已截断"。"""
    ok = _event(
        SessionEventKind.TOOL_RESULT,
        call_id="c1",
        tool_name="read_file",
        result=ToolResult(ok=True, content="x", audit_id="audit-tool"),
        audit_id="audit-tool",
    )
    cut = _event(
        SessionEventKind.TOOL_RESULT,
        call_id="c1",
        tool_name="read_file",
        result=ToolResult(ok=True, content="x", truncated=True, audit_id="audit-tool"),
        audit_id="audit-tool",
    )

    assert render_event(ok) == "✓ read_file"
    assert render_event(cut) == "✓ read_file（已截断）"


@pytest.mark.unit
def test_error_is_rendered() -> None:
    """``ERROR``：``! <error_kind>：<text>``。"""
    event = _event(
        SessionEventKind.ERROR,
        error_kind=SessionErrorKind.INTERNAL,
        text="人工确认通路故障",
    )

    assert render_event(event) == "! internal：人工确认通路故障"


@pytest.mark.unit
def test_task_finished_is_rendered() -> None:
    """``TASK_FINISHED``：``<status>：<text>``。"""
    event = _event(
        SessionEventKind.TASK_FINISHED,
        status=TaskStatus.LIMIT_REACHED,
        text="已达步数上限",
    )

    assert render_event(event) == "limit_reached：已达步数上限"


@pytest.mark.unit
def test_missing_payload_is_reported_not_silently_skipped() -> None:
    """载荷缺失（生产者破约）⇒ 渲染一条诊断行，**不静默**返回空。"""
    event = _event(SessionEventKind.TOOL_CALL, call_id="c1")

    line = render_event(event)

    assert line is not None and "tool_call" in line


# ---------------------------------------------------------------------------
# §2.6 第 5 条：不可信内容经净化
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kind", "fields"),
    [
        pytest.param(
            SessionEventKind.MODEL_RESPONSE,
            {"response": _response(content=f"模型输出{ESC}")},
            id="model_content",
        ),
        pytest.param(
            SessionEventKind.TOOL_CALL,
            {"call_id": "c1", "tool_name": f"ghost{ESC}"},
            id="tool_name",
        ),
        pytest.param(
            SessionEventKind.TOOL_RESULT,
            {
                "call_id": "c1",
                "tool_name": "read_file",
                "result": ToolResult(ok=False, content="", error=f"错误{ESC}", audit_id="a"),
                "audit_id": "a",
            },
            id="tool_error",
        ),
    ],
)
def test_untrusted_content_is_sanitized_before_display(
    kind: SessionEventKind, fields: dict[str, object]
) -> None:
    """模型 / 工具来的字符串写进终端前必须经 ``sanitize_for_display``（ESC 被替换为 ``?``）。"""
    line = render_event(_event(kind, **fields))

    assert line is not None
    assert "\x1b" not in line
    assert "?" in line


@pytest.mark.unit
def test_model_content_newlines_are_neutralised_so_render_is_one_line() -> None:
    """换行属控制字符 ⇒ 被替换为 ``?``，因此"一行一条事件"是**结构性质**。"""
    line = render_event(
        _event(SessionEventKind.MODEL_RESPONSE, response=_response(content="第一行\n第二行"))
    )

    assert line is not None
    assert "\n" not in line


# ---------------------------------------------------------------------------
# §2.6：序列化
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_payload_keeps_every_field_and_writes_enum_values() -> None:
    """键 = 字段名且**全部保留**；``None`` 写 ``null``（不省略键）；枚举写值（§2.6 第 1~2 条）。"""
    payload = event_to_payload(
        _event(SessionEventKind.TASK_FINISHED, status=TaskStatus.COMPLETED, text="完成")
    )

    assert set(payload) == EVENT_FIELDS
    assert payload["kind"] == "task_finished"
    assert payload["status"] == "completed"
    assert payload["response"] is None
    assert payload["decision"] is None
    assert payload["approval"] is None
    assert payload["result"] is None
    assert payload["error_kind"] is None
    assert payload["call_id"] is None


@pytest.mark.unit
def test_payload_expands_nested_objects_recursively() -> None:
    """嵌套对象按各自契约字段表展开（禁止整对象 ``repr`` / ``str()``）。"""
    call = ToolCallRequest(call_id="c1", name="read_file", arguments_json='{"path":"a.txt"}')
    payload = event_to_payload(
        _event(
            SessionEventKind.MODEL_RESPONSE,
            response=_response(content="答复", finish=FinishReason.TOOL_CALLS, calls=(call,)),
        )
    )

    response = payload["response"]
    assert isinstance(response, dict)
    assert set(response) == {"content", "tool_calls", "finish_reason", "usage", "model_id"}
    assert response["finish_reason"] == "tool_calls"
    tool_calls = response["tool_calls"]
    assert isinstance(tool_calls, list)
    assert tool_calls[0]["call_id"] == "c1"
    # usage 也是展开的嵌套对象，而不是 ``str()``。
    assert response["usage"] == {
        "prompt_tokens": 1,
        "completion_tokens": 2,
        "total_tokens": 3,
    }


@pytest.mark.unit
def test_serialized_event_is_a_single_json_line() -> None:
    """``serialize_event`` 的输出是**恰好一行**合法 JSON（JSONL 的一行；§2.6 第 4 条）。"""
    event = _event(
        SessionEventKind.TOOL_RESULT,
        call_id="c1",
        tool_name="read_file",
        result=ToolResult(ok=True, content='含"引号"与换行\n', audit_id="a"),
        audit_id="a",
    )

    line = serialize_event(event)

    assert "\n" not in line
    assert json.loads(line)["tool_name"] == "read_file"
    # JSON 的转义由 json.dumps 负责：正文原样往返（净化只发生在**终端渲染**面）。
    assert json.loads(line)["result"]["content"] == '含"引号"与换行\n'


@pytest.mark.unit
def test_chinese_is_not_escaped() -> None:
    """``ensure_ascii=False``：中文以原字符写出（可读、可 diff）。"""
    line = serialize_event(
        _event(SessionEventKind.TASK_FINISHED, status=TaskStatus.COMPLETED, text="完成")
    )

    assert "完成" in line
