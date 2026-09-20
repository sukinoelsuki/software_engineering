"""会话状态与检查点（``harness.md`` §3.1；``REQ-HARNESS-05`` 的状态侧）。

本模块把一次会话的状态（``session_id`` / ``task`` / ``step`` / 消息历史）与一份
**可持久化的载荷**互相转换，供"中断后可恢复"使用。它只做**纯函数式**的
序列化 / 反序列化——**不决定**检查点落到哪里（那是"检查点持久化的落点"这一未决项，
契约 §1 明确不在本轮范围），因此本模块不读盘、不写盘、不读时钟、不读环境。

**反序列化是信任边界**：检查点载荷来自磁盘，可能被改动或来自旧版本，
一律视为**不可信数据**。:func:`from_payload` 因此做**严格校验**：

* 顶层与嵌套对象都**拒绝未知字段**（未知字段是"写入方以为生效、读取方没读"的来源，
  与 ``domain_pack`` 对未知键的取向一致）；
* 缺字段 / 类型不符 / 取值非法（未知 ``role``、负 ``step``、空标识）一律
  :class:`~agent_sec_perf.foundation.errors.SchemaError`，**不得**回退默认值继续；
* 重建出的 :class:`ChatMessage` 还必须满足 ``model.md`` §2.1 的不变式
  （``SYSTEM``/``USER`` 不带工具调用字段、``TOOL`` 必须带 ``tool_call_id``、
  ``ASSISTANT`` 的 ``content`` 与 ``tool_calls`` 至少一个非空）——
  一份损坏的检查点不得被还原成"看起来合法、下游却会出错"的消息。

**错误信息不回显载荷内容**：载荷里的键名与值都可能带控制字符（来自模型 / 文件），
把它们拼进异常消息等于给日志开一条注入面（``REQ-SEC-07`` 的对手与
``foundation.logging`` 同源）。因此报错只描述**我方期望的字段与类型**，不回显具体值。

依赖：``contracts`` + ``foundation.errors``（契约 §3.1 的 ``checkpoint`` 段）。
不 import 任何 L2/L4 实现，也不与其它 harness 叶子模块互相 import（契约 §3.2 的 H1/H2）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from agent_sec_perf.contracts.model import ChatMessage, Role
from agent_sec_perf.contracts.tools import ToolCallRequest
from agent_sec_perf.foundation.errors import SchemaError

__all__ = ["SessionState", "from_payload", "to_payload"]

#: 载荷的字段集（``to_payload`` 写全量、``from_payload`` 拒未知）。这些是我方常量，
#: 因此报"缺了哪些字段"时可以直接列出它们；而**未知**字段名来自载荷，故不回显。
_STATE_KEYS = frozenset({"session_id", "task", "step", "messages"})
_MESSAGE_KEYS = frozenset({"role", "content", "tool_calls", "tool_call_id"})
_TOOL_CALL_KEYS = frozenset({"call_id", "name", "arguments_json"})


@dataclass(frozen=True)
class SessionState:
    """一次会话在某个时刻的状态快照（契约 §3.1）。

    ``messages`` 是会话历史的**有序**快照；顺序即 ``ASSISTANT``/``TOOL`` 回指的配对依据，
    因此序列化必须保序（``list`` 而非 ``set``）。
    """

    session_id: str
    task: str
    step: int
    messages: tuple[ChatMessage, ...]


def to_payload(state: SessionState) -> Mapping[str, object]:
    """把会话状态序列化成一份**纯数据**载荷（全部字段保留，``None`` 写 ``null``）。

    键名与 :class:`SessionState` / :class:`~agent_sec_perf.contracts.model.ChatMessage`
    的字段一致；枚举写**值**（``role.value``）。返回的嵌套结构只含 ``dict`` / ``list`` /
    ``str`` / ``int`` / ``None``，可直接交给 JSON 序列化器（``json.dumps``）。
    """
    return {
        "session_id": state.session_id,
        "task": state.task,
        "step": state.step,
        "messages": [_message_to_payload(message) for message in state.messages],
    }


def from_payload(payload: Mapping[str, object]) -> SessionState:
    """从一份**不可信**载荷重建会话状态（严格校验，失败 ⇒ ``SchemaError``）。

    Args:
        payload: 由 :func:`to_payload` 写出、经持久化往返后的载荷。任何来源都要当作
            不可信数据处理：字段可能缺失、类型可能不对、``role`` 可能是未知取值。

    Returns:
        通过全部校验的 :class:`SessionState`。

    Raises:
        SchemaError: 载荷不是映射、缺字段、含未知字段、类型不符、取值非法，
            或重建的消息不满足 ``model.md`` §2.1 的不变式。
    """
    table = _as_mapping(payload, what="检查点载荷")
    _reject_unknown_fields(table, _STATE_KEYS, what="检查点载荷")
    _require_present(table, _STATE_KEYS, what="检查点载荷")

    return SessionState(
        session_id=_require_non_empty_str(table["session_id"], what="session_id"),
        task=_require_str(table["task"], what="task"),
        step=_require_non_negative_int(table["step"], what="step"),
        messages=_parse_messages(table["messages"]),
    )


def _message_to_payload(message: ChatMessage) -> dict[str, object]:
    return {
        "role": message.role.value,
        "content": message.content,
        "tool_calls": [
            {
                "call_id": call.call_id,
                "name": call.name,
                "arguments_json": call.arguments_json,
            }
            for call in message.tool_calls
        ],
        "tool_call_id": message.tool_call_id,
    }


def _parse_messages(value: object) -> tuple[ChatMessage, ...]:
    if not isinstance(value, list):
        raise SchemaError("messages 必须是消息数组")
    return tuple(_parse_message(item) for item in value)


def _parse_message(value: object) -> ChatMessage:
    table = _as_mapping(value, what="messages 的元素")
    _reject_unknown_fields(table, _MESSAGE_KEYS, what="消息")
    _require_present(table, _MESSAGE_KEYS, what="消息")

    role = _parse_role(table["role"])
    content = _optional_str(table["content"], what="消息 content")
    tool_call_id = _optional_non_empty_str(table["tool_call_id"], what="消息 tool_call_id")
    tool_calls = _parse_tool_calls(table["tool_calls"])
    _check_message_invariants(
        role=role, content=content, tool_calls=tool_calls, tool_call_id=tool_call_id
    )
    return ChatMessage(role=role, content=content, tool_calls=tool_calls, tool_call_id=tool_call_id)


def _parse_role(value: object) -> Role:
    if not isinstance(value, str):
        raise SchemaError("消息 role 必须是字符串")
    try:
        return Role(value)
    except ValueError as exc:
        raise SchemaError("消息 role 不是已知取值（拒绝而不是猜测）") from exc


def _parse_tool_calls(value: object) -> tuple[ToolCallRequest, ...]:
    if not isinstance(value, list):
        raise SchemaError("消息 tool_calls 必须是数组")
    return tuple(_parse_tool_call(item) for item in value)


def _parse_tool_call(value: object) -> ToolCallRequest:
    table = _as_mapping(value, what="tool_calls 的元素")
    _reject_unknown_fields(table, _TOOL_CALL_KEYS, what="工具调用")
    _require_present(table, _TOOL_CALL_KEYS, what="工具调用")
    return ToolCallRequest(
        call_id=_require_non_empty_str(table["call_id"], what="工具调用 call_id"),
        name=_require_non_empty_str(table["name"], what="工具调用 name"),
        arguments_json=_require_str(table["arguments_json"], what="工具调用 arguments_json"),
    )


def _check_message_invariants(
    *,
    role: Role,
    content: str | None,
    tool_calls: tuple[ToolCallRequest, ...],
    tool_call_id: str | None,
) -> None:
    """重建的消息必须满足 ``model.md`` §2.1 的不变式（否则检查点已损坏）。"""
    if role in (Role.SYSTEM, Role.USER) and (tool_calls or tool_call_id is not None):
        raise SchemaError("SYSTEM/USER 消息不得携带工具调用字段（model.md §2.1）")
    if role is Role.TOOL and tool_call_id is None:
        raise SchemaError("TOOL 消息必须带 tool_call_id（model.md §2.1）")
    if role is Role.ASSISTANT and not content and not tool_calls:
        raise SchemaError("ASSISTANT 消息的 content 与 tool_calls 至少一个非空（model.md §2.1）")


def _as_mapping(value: object, *, what: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SchemaError(f"{what} 必须是映射（object）")
    return value


def _require_str(value: object, *, what: str) -> str:
    if not isinstance(value, str):
        raise SchemaError(f"{what} 必须是字符串")
    return value


def _require_non_empty_str(value: object, *, what: str) -> str:
    text = _require_str(value, what=what)
    if not text:
        raise SchemaError(f"{what} 不得为空")
    return text


def _optional_str(value: object, *, what: str) -> str | None:
    if value is None:
        return None
    return _require_str(value, what=what)


def _optional_non_empty_str(value: object, *, what: str) -> str | None:
    if value is None:
        return None
    return _require_non_empty_str(value, what=what)


def _require_non_negative_int(value: object, *, what: str) -> int:
    """要求真正的非负整数（``bool`` 是 ``int`` 的子类，必须显式拒掉）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaError(f"{what} 必须是整数")
    if value < 0:
        raise SchemaError(f"{what} 不得为负")
    return value


def _require_present(table: Mapping[str, object], keys: frozenset[str], *, what: str) -> None:
    missing = keys - set(table)
    if missing:
        listed = "、".join(sorted(missing))
        raise SchemaError(f"{what} 缺少字段：{listed}")


def _reject_unknown_fields(
    table: Mapping[str, object], allowed: frozenset[str], *, what: str
) -> None:
    """未知字段一律拒绝；**不回显字段名**（它来自载荷，可能带控制字符）。"""
    if set(table) - allowed:
        raise SchemaError(f"{what} 出现未定义的字段（严格校验，拒绝未知字段）")
