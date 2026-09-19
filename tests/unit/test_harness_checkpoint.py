"""``checkpoint`` 的行为断言：往返保真、严格校验、失败即 ``SchemaError``（契约 §3.1）。

这是实现侧的**功能**断言。检查点载荷来自磁盘 ⇒ 一律按不可信数据处理，
故这里的失败路径占比刻意高于正常路径（校验是信任边界，不是形式）。

变异探针（说明这些断言不是"陪跑"，逐条能被一个具体改动杀死）：

* 删掉 ``_reject_unknown_fields`` 的调用 ⇒ :func:`test_from_payload_rejects_unknown_field` 失败；
* 把 ``_require_non_negative_int`` 的 ``isinstance(value, bool)`` 去掉 ⇒
  :func:`test_from_payload_rejects_bool_step` 失败；
* 删掉 ``_check_message_invariants`` ⇒
  :func:`test_from_payload_rejects_tool_message_without_tool_call_id` 失败；
* 把 ``_require_non_empty_str`` 换成 ``_require_str`` ⇒
  :func:`test_from_payload_rejects_empty_identifiers` 失败；
* 把 ``to_payload`` 的 ``role.value`` 改成 ``str(role)`` 以外的对象 ⇒
  :func:`test_role_is_serialized_as_its_value` 失败。
"""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from agent_sec_perf.contracts.model import ChatMessage, Role
from agent_sec_perf.contracts.tools import ToolCallRequest
from agent_sec_perf.foundation.errors import BenchError, SchemaError
from agent_sec_perf.harness.checkpoint import SessionState, from_payload, to_payload

#: 唯一 sentinel：出现在载荷里，但**不得**出现在异常消息中（不回显不可信内容）。
_SENTINEL = "SENTINEL-7f3a-not-for-logs"


def _state() -> SessionState:
    """一份合法的会话状态（含 ``ASSISTANT(tool_calls)`` 与 ``TOOL`` 配对）。"""
    return SessionState(
        session_id="sess-1",
        task="把 README 的前 20 行读出来",
        step=2,
        messages=(
            ChatMessage(role=Role.SYSTEM, content="系统提示"),
            ChatMessage(role=Role.USER, content="任务"),
            ChatMessage(
                role=Role.ASSISTANT,
                content=None,
                tool_calls=(ToolCallRequest(call_id="c1", name="read_file", arguments_json="{}"),),
            ),
            ChatMessage(role=Role.TOOL, content="文件内容", tool_call_id="c1"),
        ),
    )


def _payload(**overrides: object) -> dict[str, object]:
    """在合法载荷上施加顶层字段覆盖。"""
    payload = dict(to_payload(_state()))
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# 正向：序列化形状与往返保真
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_round_trip_preserves_the_state() -> None:
    """``from_payload(to_payload(state)) == state``（含工具调用与 ``None`` 字段）。"""
    state = _state()

    assert from_payload(to_payload(state)) == state


@pytest.mark.unit
def test_round_trip_survives_a_json_persistence_cycle() -> None:
    """载荷是纯数据：经 ``json.dumps/loads`` 往返后仍能还原（覆盖"落盘再读回"）。"""
    state = _state()

    restored = from_payload(json.loads(json.dumps(to_payload(state))))

    assert restored == state


@pytest.mark.unit
def test_payload_keeps_every_field_even_when_null() -> None:
    """所有字段都保留（``None`` 写 ``null``）——省略会让"字段不存在"与"字段为 null"同形。"""
    payload = dict(to_payload(_state()))

    assert set(payload) == {"session_id", "task", "step", "messages"}
    first_message = payload["messages"][0]
    assert isinstance(first_message, dict)
    assert set(first_message) == {"role", "content", "tool_calls", "tool_call_id"}
    assert first_message["tool_call_id"] is None
    assert first_message["tool_calls"] == []


@pytest.mark.unit
def test_role_is_serialized_as_its_value() -> None:
    """枚举写**值**（``role.value``），不是枚举对象、也不是 ``repr``。"""
    payload = to_payload(_state())

    roles = [message["role"] for message in payload["messages"]]  # type: ignore[index,union-attr]
    assert roles == ["system", "user", "assistant", "tool"]


@pytest.mark.unit
def test_message_order_is_preserved() -> None:
    """消息顺序（``ASSISTANT``/``TOOL`` 的配对依据）必须保序。"""
    restored = from_payload(to_payload(_state()))

    assert [message.role for message in restored.messages] == [
        Role.SYSTEM,
        Role.USER,
        Role.ASSISTANT,
        Role.TOOL,
    ]


@pytest.mark.unit
def test_assistant_without_content_but_with_tool_calls_round_trips() -> None:
    """``content is None`` 但带工具调用的 ``ASSISTANT`` 消息是合法的，可往返。"""
    state = SessionState(
        session_id="s",
        task="t",
        step=1,
        messages=(
            ChatMessage(
                role=Role.ASSISTANT,
                tool_calls=(ToolCallRequest(call_id="c", name="list_dir", arguments_json="{}"),),
            ),
        ),
    )

    assert from_payload(to_payload(state)) == state


@pytest.mark.unit
def test_empty_arguments_json_is_accepted() -> None:
    """``arguments_json`` 允许空串（无参数调用的常见形态），不因"空"被拒。"""
    state = SessionState(
        session_id="s",
        task="t",
        step=1,
        messages=(
            ChatMessage(
                role=Role.ASSISTANT,
                tool_calls=(ToolCallRequest(call_id="c", name="list_dir", arguments_json=""),),
            ),
        ),
    )

    assert from_payload(to_payload(state)) == state


# ---------------------------------------------------------------------------
# 反向：严格校验（fail-secure）
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("bad", [None, [], "payload", 42, ("a",)])
def test_from_payload_rejects_non_mapping(bad: object) -> None:
    """顶层不是映射 ⇒ ``SchemaError``（不尝试从别的形状里"猜"）。"""
    with pytest.raises(SchemaError):
        from_payload(bad)  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize("missing", ["session_id", "task", "step", "messages"])
def test_from_payload_rejects_missing_field(missing: str) -> None:
    """缺任一必填字段 ⇒ ``SchemaError``（不回退默认值）。"""
    payload = _payload()
    del payload[missing]

    with pytest.raises(SchemaError, match="缺少字段"):
        from_payload(payload)


@pytest.mark.unit
def test_from_payload_rejects_unknown_field() -> None:
    """未知字段 ⇒ ``SchemaError``（不得忽略，与 domain_pack 对未知键一致）。"""
    payload = _payload(unexpected="x")

    with pytest.raises(SchemaError, match="未定义"):
        from_payload(payload)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("session_id", 1),
        ("session_id", ""),
        ("task", None),
        ("step", "2"),
        ("step", 2.0),
        ("step", -1),
        ("messages", "not-a-list"),
        ("messages", None),
    ],
)
def test_from_payload_rejects_invalid_top_level_values(field: str, value: object) -> None:
    """顶层字段类型 / 取值非法 ⇒ ``SchemaError``。"""
    with pytest.raises(SchemaError):
        from_payload(_payload(**{field: value}))


@pytest.mark.unit
def test_from_payload_rejects_bool_step() -> None:
    """``step`` 是 ``bool`` ⇒ 拒绝（``True`` 是 ``int`` 的子类，但语义上不是"步数"）。"""
    with pytest.raises(SchemaError, match="整数"):
        from_payload(_payload(step=True))


@pytest.mark.unit
@pytest.mark.parametrize("message", [None, "text", 3, []])
def test_from_payload_rejects_non_object_message(message: object) -> None:
    """``messages`` 数组里的元素不是对象 ⇒ ``SchemaError``。"""
    with pytest.raises(SchemaError):
        from_payload(_payload(messages=[message]))


@pytest.mark.unit
def test_from_payload_rejects_missing_message_field() -> None:
    """消息缺字段 ⇒ ``SchemaError``。"""
    message = dict(to_payload(_state())["messages"][0])  # type: ignore[index]
    del message["role"]

    with pytest.raises(SchemaError, match="缺少字段"):
        from_payload(_payload(messages=[message]))


@pytest.mark.unit
def test_from_payload_rejects_unknown_message_field() -> None:
    """消息含未知字段 ⇒ ``SchemaError``。"""
    message = dict(to_payload(_state())["messages"][0])  # type: ignore[index]
    message["extra"] = "x"

    with pytest.raises(SchemaError, match="未定义"):
        from_payload(_payload(messages=[message]))


@pytest.mark.unit
def test_from_payload_rejects_unknown_role() -> None:
    """未知 ``role`` 取值 ⇒ ``SchemaError``（不猜测、不回退）。"""
    message = {"role": "root", "content": "x", "tool_calls": [], "tool_call_id": None}

    with pytest.raises(SchemaError, match="role"):
        from_payload(_payload(messages=[message]))


@pytest.mark.unit
def test_from_payload_rejects_non_string_role() -> None:
    """``role`` 不是字符串 ⇒ ``SchemaError``。"""
    message = {"role": 7, "content": "x", "tool_calls": [], "tool_call_id": None}

    with pytest.raises(SchemaError, match="role"):
        from_payload(_payload(messages=[message]))


@pytest.mark.unit
def test_from_payload_rejects_tool_calls_not_a_list() -> None:
    """``tool_calls`` 不是数组 ⇒ ``SchemaError``。"""
    message = {"role": "user", "content": "x", "tool_calls": {}, "tool_call_id": None}

    with pytest.raises(SchemaError, match="tool_calls"):
        from_payload(_payload(messages=[message]))


@pytest.mark.unit
def test_from_payload_rejects_missing_tool_call_field() -> None:
    """工具调用缺字段 ⇒ ``SchemaError``。"""
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"call_id": "c", "name": "read_file"}],
        "tool_call_id": None,
    }

    with pytest.raises(SchemaError, match="缺少字段"):
        from_payload(_payload(messages=[message]))


@pytest.mark.unit
def test_from_payload_rejects_unknown_tool_call_field() -> None:
    """工具调用含未知字段 ⇒ ``SchemaError``。"""
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"call_id": "c", "name": "read_file", "arguments_json": "{}", "x": 1}],
        "tool_call_id": None,
    }

    with pytest.raises(SchemaError, match="未定义"):
        from_payload(_payload(messages=[message]))


@pytest.mark.unit
@pytest.mark.parametrize(
    "tool_call",
    [
        {"call_id": "", "name": "read_file", "arguments_json": "{}"},
        {"call_id": "c", "name": "", "arguments_json": "{}"},
        {"call_id": 1, "name": "read_file", "arguments_json": "{}"},
        {"call_id": "c", "name": "read_file", "arguments_json": 5},
    ],
)
def test_from_payload_rejects_invalid_tool_call_values(tool_call: dict[str, object]) -> None:
    """工具调用的标识为空 / 类型不符 ⇒ ``SchemaError``。"""
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [tool_call],
        "tool_call_id": None,
    }

    with pytest.raises(SchemaError):
        from_payload(_payload(messages=[message]))


@pytest.mark.unit
@pytest.mark.parametrize(
    "message",
    [
        # SYSTEM / USER 不得带工具调用字段（model.md §2.1）。
        {
            "role": "system",
            "content": "x",
            "tool_calls": [{"call_id": "c", "name": "n", "arguments_json": "{}"}],
            "tool_call_id": None,
        },
        {"role": "user", "content": "x", "tool_calls": [], "tool_call_id": "c"},
        # TOOL 必须带 tool_call_id。
        {"role": "tool", "content": "x", "tool_calls": [], "tool_call_id": None},
        # ASSISTANT 的 content 与 tool_calls 至少一个非空。
        {"role": "assistant", "content": None, "tool_calls": [], "tool_call_id": None},
        {"role": "assistant", "content": "", "tool_calls": [], "tool_call_id": None},
    ],
)
def test_from_payload_rejects_invariant_violations(message: dict[str, object]) -> None:
    """重建的消息违反 ``model.md`` §2.1 不变式 ⇒ ``SchemaError``（损坏的检查点不得还原）。"""
    with pytest.raises(SchemaError):
        from_payload(_payload(messages=[message]))


@pytest.mark.unit
def test_from_payload_rejects_empty_tool_message_id() -> None:
    """``TOOL`` 的 ``tool_call_id`` 为空串 ⇒ ``SchemaError``（空标识无法回指）。"""
    message = {"role": "tool", "content": "x", "tool_calls": [], "tool_call_id": ""}

    with pytest.raises(SchemaError, match="tool_call_id"):
        from_payload(_payload(messages=[message]))


@pytest.mark.unit
def test_schema_error_is_a_project_error() -> None:
    """失败类型是项目基类 ``BenchError`` 的子类（``cli/`` 的分类不会漏接）。"""
    assert issubclass(SchemaError, BenchError)


@pytest.mark.unit
def test_error_message_does_not_echo_untrusted_payload_content() -> None:
    """错误信息**不回显**载荷内容（键名/值都可能来自外部，是日志注入面）。"""
    with pytest.raises(SchemaError) as excinfo:
        from_payload(_payload(step=_SENTINEL))

    assert _SENTINEL not in str(excinfo.value)


@pytest.mark.unit
def test_error_message_does_not_echo_unknown_field_names() -> None:
    """未知字段名来自载荷 ⇒ 不拼进异常消息（只描述"出现了未定义字段"）。"""
    with pytest.raises(SchemaError) as excinfo:
        from_payload(_payload(**{_SENTINEL: 1}))

    assert _SENTINEL not in str(excinfo.value)


# ---------------------------------------------------------------------------
# 纯函数与不可变性
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_to_payload_is_deterministic() -> None:
    """同一状态两次序列化结果相同（纯函数，不读时钟/环境）。"""
    state = _state()

    assert to_payload(state) == to_payload(state)


@pytest.mark.unit
def test_session_state_is_frozen() -> None:
    """状态是不可变快照：不允许就地改动（改动会让"检查点"与内存态静默分叉）。"""
    state = _state()

    with pytest.raises(FrozenInstanceError):
        state.step = 99  # type: ignore[misc]
