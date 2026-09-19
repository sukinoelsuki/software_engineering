"""会话事件流的终端渲染与 JSON 序列化（``harness.md`` §2.6 / §5.2；``cli/`` 的表现层叶子）。

本模块只消费 ``contracts.harness.SessionEvent``，不读盘、不读时钟、不读环境
（:func:`event_to_payload` 是**纯函数**，契约 §2.6 第 6 条）。

两条输出通道必须分清（契约 §5.2）：

* :func:`event_to_payload` / :func:`serialize_event` —— **序列化**（``--output-format json``）：
  键 = 字段名、``None`` 写 ``null`` 且**不省略键**、枚举写**值**、``response`` / ``decision`` /
  ``approval`` / ``result`` 按各自契约字段表**递归展开**（禁止整对象 ``repr`` / ``str``）。
  JSON 的转义由 :func:`json.dumps` 负责——**不得**手工拼接，否则一个引号就能破坏行式协议。
* :func:`render_event` —— **终端渲染**：返回给人看的**一行**文本；``None`` ⇒ 本事件不打印
  （例如纯工具调用、``content is None`` 的 ``MODEL_RESPONSE``，见 §5.2 的备注列）。

**净化是硬规定**（§2.6 第 5 条，不是可选的美化）：``tool_name`` / ``response.content`` /
``result.content`` / ``result.error`` 来自**模型 / 工具**（不可信文本），写进终端前**必须**
经 :func:`~agent_sec_perf.foundation.logging.sanitize_for_display`——它把控制字符（含换行与
ESC）替换为 ``?``。少了这一步，一条 ``\\x1b[2J`` 就能伪造终端显示。
``reason`` / ``text`` 是**我方生成**的中文说明，按契约可直接显示。

**为什么渲染不走 Rich 的 markup**：Rich 的标记语法由**可打印字符**构成（``[bold]``、
``[link=...]``），而 :func:`sanitize_for_display` 只中和**控制字符** ⇒ 把不可信文本交给一个
markup 解析器，等于在"净化之后"新开一处显示篡改面。本模块因此输出**纯文本行**；
``rich`` 仍是已声明依赖，但本轮不把它引入不可信文本的渲染路径。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Final

from agent_sec_perf.contracts.harness import ApprovalResult, SessionEvent, SessionEventKind
from agent_sec_perf.contracts.model import FinishReason, ModelResponse
from agent_sec_perf.contracts.policy import PolicyDecision
from agent_sec_perf.contracts.tools import ToolCallRequest, ToolResult
from agent_sec_perf.foundation.logging import sanitize_for_display

__all__ = ["event_to_payload", "render_event", "serialize_event"]

#: 工具名一类短标识的展示上限（不可信文本，先净化再截断）。
_NAME_LIMIT: Final = 64

#: 模型 / 工具正文的展示上限。刻意远大于 :data:`_NAME_LIMIT`：终端要能看清模型答复，
#: 但**不**允许一条答复无限撑满终端（截断发生在净化之后，因此不会把控制字符漏回来）。
_CONTENT_LIMIT: Final = 4096

#: ``finish_reason is LENGTH`` 时的追加标注（§5.2 的 ``MODEL_RESPONSE`` 行）。
_TRUNCATED_NOTE: Final = "（输出被截断）"

#: 工具结果被截断时的标注（§5.2 的 ``TOOL_RESULT`` 行）。
_TOOL_TRUNCATED_NOTE: Final = "（已截断）"

#: "事件缺少必需的载荷" 的兜底文案。契约的 ``I1``~``I10`` 保证每个 kind 都带齐载荷，
#: 走到这里说明生产者破约；此时**不静默**（不返回空行、不抛异常打断整个 CLI），
#: 而是渲染一条不含任何不可信内容的诊断行。
_MISSING_PAYLOAD: Final = "! 事件缺少 {kind} 载荷（生产者破约，未渲染）"


# ---------------------------------------------------------------------------
# 公开接口：终端渲染（§5.2 的完整分支表）
# ---------------------------------------------------------------------------


def render_event(event: SessionEvent) -> str | None:
    """把一个事件渲染成**一行**终端文本；``None`` ⇒ 本事件不打印（§5.2）。

    返回的字符串**不含换行**：所有来自模型 / 工具的正文都先经 ``sanitize_for_display``，
    其中换行属控制字符 ⇒ 会被替换为 ``?``（因此"一行一条事件"是结构性质，不靠调用方）。
    """
    renderer = _RENDERERS.get(event.kind)
    if renderer is None:
        return _missing_payload(event)
    return renderer(event)


# ---------------------------------------------------------------------------
# 公开接口：序列化（§2.6）
# ---------------------------------------------------------------------------


def event_to_payload(event: SessionEvent) -> dict[str, object]:
    """把事件转成一行 JSON 的载荷（§2.6 第 1~3 条；**纯函数**）。

    * 键 = 字段名，**全部保留**（``None`` 写 ``null``）——省略会让"字段不存在"与
      "字段为 ``null``"在消费侧同形（沿用 ``observability/audit.py::_event_to_payload``）；
    * 枚举写**值**（``kind.value`` 等）；
    * ``response`` / ``decision`` / ``approval`` / ``result`` 递归展开，**不** ``repr``。

    ⚠️ **如实登记一处契约张力（上报领导裁决，本模块不自改契约）**：契约 §1.1 与 §2.2 的
    ``I8`` 写"事件**不得承载**原始 ``arguments_json``"，而 §2.6 第 3 条又要求 ``response``
    **按契约字段表递归展开**——``ModelResponse.tool_calls[].arguments_json`` 正是原始 JSON 文本。
    两条字面冲突。本实现依 §2.6 第 3 条**如实展开**（这也是已入库的 ``harness/loop.py``
    把完整 ``ModelResponse`` 放进事件的既有事实：``_assert_invariants`` 对 ``I8`` 的判据是
    结构性的——"事件没有 ``arguments_json`` 字段，且 sentinel 不出现在 ``text`` / ``tool_name``"）。
    若领导裁决为"序列化面上也须剔除 ``arguments_json``"，属**契约变更**，需回改本函数与测试。
    """
    return {
        "kind": event.kind.value,
        "session_id": event.session_id,
        "seq": event.seq,
        "timestamp": event.timestamp,
        "call_id": event.call_id,
        "tool_name": event.tool_name,
        "response": None if event.response is None else _response_payload(event.response),
        "decision": None if event.decision is None else _decision_payload(event.decision),
        "approval": None if event.approval is None else _approval_payload(event.approval),
        "result": None if event.result is None else _result_payload(event.result),
        "text": event.text,
        "error_kind": None if event.error_kind is None else event.error_kind.value,
        "status": None if event.status is None else event.status.value,
        "audit_id": event.audit_id,
    }


def serialize_event(event: SessionEvent) -> str:
    """把一个事件序列化为**恰好一行** JSON（JSONL 的一行；§2.6 第 4 条）。

    转义一律交给 :func:`json.dumps`（``ensure_ascii=False`` 保留中文，
    ``sort_keys=True`` 让输出可 ``diff``）。**不得**手工拼接 JSON。
    """
    return json.dumps(event_to_payload(event), ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------------------
# 渲染分支（§5.2 逐行对应）
# ---------------------------------------------------------------------------


def _missing_payload(event: SessionEvent) -> str:
    """载荷缺失时的兜底行（``kind`` 是我方枚举值，不含不可信内容）。"""
    return _MISSING_PAYLOAD.format(kind=event.kind.value)


def _render_model_response(event: SessionEvent) -> str | None:
    """``MODEL_RESPONSE``：显示 ``response.content``；``LENGTH`` 附截断标注；``None`` ⇒ 不打印。"""
    response = event.response
    if response is None:
        return _missing_payload(event)
    content = response.content
    if content is None:
        # 纯工具调用的响应：没有可展示的正文，**不打印空行**（§5.2 的备注列）。
        return None
    line = sanitize_for_display(content, limit=_CONTENT_LIMIT)
    if response.finish_reason is FinishReason.LENGTH:
        return f"{line}{_TRUNCATED_NOTE}"
    return line


def _render_tool_call(event: SessionEvent) -> str:
    """``TOOL_CALL``：``→ 调用 <tool_name>``（``tool_name`` 不可信 ⇒ 净化）。"""
    if event.tool_name is None:
        return _missing_payload(event)
    return f"→ 调用 {_shown_name(event.tool_name)}"


def _render_policy_decision(event: SessionEvent) -> str:
    """``POLICY_DECISION``：需确认 / 放行 / 拒绝三种分支（§5.2）。

    ``reason`` 是**我方生成**的中文说明（含 ``REQ-SEC-02`` 要求的风险说明），可直接显示。
    """
    decision = event.decision
    if decision is None or event.tool_name is None:
        return _missing_payload(event)
    tool = _shown_name(event.tool_name)
    risk = decision.risk_level.value
    if decision.requires_confirmation:
        return f"需确认：{tool}（风险 {risk}）— {decision.reason}"
    if decision.allow:
        return f"放行：{tool}（风险 {risk}）"
    return f"拒绝：{tool}（风险 {risk}）— {decision.reason}"


def _render_approval_result(event: SessionEvent) -> str:
    """``APPROVAL_RESULT``：一行用户选择（``allow_once`` / ``allow_always`` / ``deny``）。"""
    approval = event.approval
    if approval is None:
        return _missing_payload(event)
    tool = "（未知工具）" if event.tool_name is None else _shown_name(event.tool_name)
    return f"人工确认结果：{approval.outcome.value}（{tool}）"


def _render_tool_result(event: SessionEvent) -> str:
    """``TOOL_RESULT``：**"未执行"与"执行失败"必须在显示上可区分**（§1 第 4 条）。"""
    if event.tool_name is None:
        return _missing_payload(event)
    tool = _shown_name(event.tool_name)
    result = event.result
    if result is None:
        # 未执行：``text`` 是我方生成的中文说明（``I3`` 要求它非空）。
        return f"✗ 未执行：{event.text or ''}"
    if result.ok:
        note = _TOOL_TRUNCATED_NOTE if result.truncated else ""
        return f"✓ {tool}{note}"
    error = "" if result.error is None else sanitize_for_display(result.error, limit=_CONTENT_LIMIT)
    return f"✗ {tool}：{error}"


def _render_error(event: SessionEvent) -> str:
    """``ERROR``：``! <error_kind>：<text>``（是否终止看其后有无 ``TASK_FINISHED``，``I7``）。"""
    if event.error_kind is None:
        return _missing_payload(event)
    return f"! {event.error_kind.value}：{event.text or ''}"


def _render_task_finished(event: SessionEvent) -> str:
    """``TASK_FINISHED``：``<status>：<text>``（决定退出码，见 ``cli/app.py``）。"""
    if event.status is None:
        return _missing_payload(event)
    return f"{event.status.value}：{event.text or ''}"


def _shown_name(tool_name: str) -> str:
    """工具名（不可信文本）进入终端前的净化 + 截断。"""
    return sanitize_for_display(tool_name, limit=_NAME_LIMIT)


#: ``kind`` → 渲染器。用映射而不是 ``if/elif`` 链：枚举成员若增删，这里会**显式**缺项
#: （由 :func:`render_event` 兜底），而不是被一个 ``else`` 静默当成最后一个分支。
_RENDERERS: Final[Mapping[SessionEventKind, Callable[[SessionEvent], str | None]]] = {
    SessionEventKind.MODEL_RESPONSE: _render_model_response,
    SessionEventKind.TOOL_CALL: _render_tool_call,
    SessionEventKind.POLICY_DECISION: _render_policy_decision,
    SessionEventKind.APPROVAL_RESULT: _render_approval_result,
    SessionEventKind.TOOL_RESULT: _render_tool_result,
    SessionEventKind.ERROR: _render_error,
    SessionEventKind.TASK_FINISHED: _render_task_finished,
}


# ---------------------------------------------------------------------------
# 序列化辅助（§2.6 第 3 条：按各自契约字段表递归展开）
# ---------------------------------------------------------------------------


def _response_payload(response: ModelResponse) -> dict[str, object]:
    """``ModelResponse`` 的字段表（``contracts/model.py``）。"""
    return {
        "content": response.content,
        "tool_calls": [_tool_call_payload(call) for call in response.tool_calls],
        "finish_reason": response.finish_reason.value,
        "usage": {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
        },
        "model_id": response.model_id,
    }


def _tool_call_payload(call: ToolCallRequest) -> dict[str, object]:
    """``ToolCallRequest`` 的字段表；``arguments_json`` 是**原始 JSON 文本**（见
    :func:`event_to_payload` 的张力登记）。"""
    return {
        "call_id": call.call_id,
        "name": call.name,
        "arguments_json": call.arguments_json,
    }


def _decision_payload(decision: PolicyDecision) -> dict[str, object]:
    """``PolicyDecision`` 的字段表（``contracts/policy.py``）。"""
    return {
        "allow": decision.allow,
        "requires_confirmation": decision.requires_confirmation,
        "risk_level": decision.risk_level.value,
        "reason": decision.reason,
        "audit_id": decision.audit_id,
    }


def _approval_payload(approval: ApprovalResult) -> dict[str, object]:
    """``ApprovalResult`` 的字段表（``contracts/harness.py``）。"""
    return {"outcome": approval.outcome.value, "audit_id": approval.audit_id}


def _result_payload(result: ToolResult) -> dict[str, object]:
    """``ToolResult`` 的字段表（``contracts/tools.py``）。"""
    return {
        "ok": result.ok,
        "content": result.content,
        "error": result.error,
        "truncated": result.truncated,
        "audit_id": result.audit_id,
    }
