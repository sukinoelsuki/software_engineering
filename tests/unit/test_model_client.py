"""``LocalLlamaClient`` 的行为断言（``G7``）。

单测**不依赖真实 ``llama-server`` 二进制或网络**（CI 里没有）：传输层用替身，
进程边界用替身（``proc.spawn`` / ``proc.resolve_binary`` 打桩）。因此这里证明的是：

* 线上协议形状（消息序列化 / ``tools`` 字段的省略 / ``arguments`` 原样透传）；
* 响应映射与**失败路径**（缺 ``choices``、``content`` 与 ``tool_calls`` 皆空、超限……）；
* 边界（只允许回环地址、``timeout_s`` 真正传给传输层、``close()`` 幂等、``spawn`` 的最小环境）。

这是实现侧的功能断言；"未授权 / 越权"一类对抗性安全断言属 ``tests/security/``，
由验证角色独立完成（安全断言不得由实现者自证）。
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Sequence

import pytest

from agent_sec_perf.contracts.model import ChatMessage, FinishReason, Role
from agent_sec_perf.contracts.policy import Capability
from agent_sec_perf.contracts.tools import ToolCallRequest, ToolSpec
from agent_sec_perf.foundation import proc
from agent_sec_perf.foundation.errors import ModelProtocolError, ModelUnavailableError
from agent_sec_perf.model.client import (
    LOOPBACK_HOSTS,
    HttpResponse,
    LocalLlamaClient,
    LoopbackHttpTransport,
    validate_loopback_host,
)

MODEL_PATH = pathlib.Path("/models/qwen.gguf")


# ---------------------------------------------------------------------------
# 替身
# ---------------------------------------------------------------------------


class FakeTransport:
    """传输层替身：按序返回预设结果（``HttpResponse`` 或要抛的异常）。"""

    def __init__(
        self,
        outcomes: Sequence[object] | None = None,
        *,
        default: object | None = None,
    ) -> None:
        self.outcomes = list(outcomes or [])
        self.default = _chat_response() if default is None else default
        self.calls: list[dict[str, object]] = []
        self.close_calls = 0

    def request(
        self, method: str, path: str, *, body: bytes | None, timeout_s: float
    ) -> HttpResponse:
        self.calls.append({"method": method, "path": path, "body": body, "timeout_s": timeout_s})
        outcome = self.outcomes.pop(0) if self.outcomes else self.default
        if isinstance(outcome, BaseException):
            raise outcome
        assert isinstance(outcome, HttpResponse)
        return outcome

    def close(self) -> None:
        self.close_calls += 1

    def payload(self, index: int = -1) -> dict[str, object]:
        """把第 ``index`` 次调用的请求体解析为 JSON（便于断言协议形状）。"""
        raw = self.calls[index]["body"]
        assert isinstance(raw, bytes)
        parsed = json.loads(raw.decode("utf-8"))
        assert isinstance(parsed, dict)
        return parsed


class FakeProcess:
    """``BackgroundProcess`` 的最小替身（只保留客户端用到的方法）。"""

    def __init__(self, *, running: bool = True) -> None:
        self.running = running
        self.terminate_calls = 0

    def pid(self) -> int:
        return 4242

    def is_running(self) -> bool:
        return self.running

    def terminate(self, timeout_s: float = 30.0) -> None:
        self.terminate_calls += 1
        self.running = False


def _chat_response(payload: object | None = None, *, status: int = 200) -> HttpResponse:
    if payload is None:
        payload = _chat_payload()
    return HttpResponse(status=status, body=json.dumps(payload).encode("utf-8"))


def _chat_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "model": "local-qwen",
        "choices": [{"message": {"role": "assistant", "content": "你好"}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7},
    }
    payload.update(overrides)
    return payload


def make_client(transport: FakeTransport, **kwargs: object) -> LocalLlamaClient:
    """构造一个**不启动进程**的客户端（传输层由替身提供）。"""
    options: dict[str, object] = {
        "binary": "llama-server",
        "model_path": MODEL_PATH,
        "log_path": pathlib.Path("/tmp/lowspec-test/llama.log"),
        "transport": transport,
        "start_server": False,
    }
    options.update(kwargs)
    return LocalLlamaClient(**options)  # type: ignore[arg-type]


def _user(content: str = "ping") -> ChatMessage:
    return ChatMessage(role=Role.USER, content=content)


def _spec(name: str = "read_file") -> ToolSpec:
    return ToolSpec(
        name=name,
        description="读取文件",
        parameters_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        capabilities=frozenset({Capability.READ_FILE}),
    )


# ---------------------------------------------------------------------------
# 边界：只连回环
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("host", sorted(LOOPBACK_HOSTS))
def test_loopback_host_is_accepted(host: str) -> None:
    assert validate_loopback_host(host) == host


@pytest.mark.unit
@pytest.mark.parametrize("host", ["0.0.0.0", "8.8.8.8", "192.168.1.10", "example.com", ""])
def test_non_loopback_host_is_rejected(host: str) -> None:
    with pytest.raises(ValueError):
        validate_loopback_host(host)


@pytest.mark.unit
def test_client_rejects_non_loopback_host() -> None:
    transport = FakeTransport()
    with pytest.raises(ValueError):
        make_client(transport, host="0.0.0.0")


@pytest.mark.unit
def test_loopback_transport_rejects_non_loopback_host() -> None:
    with pytest.raises(ValueError):
        LoopbackHttpTransport("10.0.0.1", 8080)


@pytest.mark.unit
def test_loopback_transport_rejects_out_of_range_port() -> None:
    with pytest.raises(ValueError):
        LoopbackHttpTransport("127.0.0.1", 70000)


# ---------------------------------------------------------------------------
# 请求序列化
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tools_none_omits_tools_field() -> None:
    transport = FakeTransport()
    make_client(transport).chat([_user()], tools=None)
    assert "tools" not in transport.payload()


@pytest.mark.unit
def test_empty_tools_omits_tools_field() -> None:
    transport = FakeTransport()
    make_client(transport).chat([_user()], tools=[])
    assert "tools" not in transport.payload()


@pytest.mark.unit
def test_tools_are_serialized_in_function_shape() -> None:
    transport = FakeTransport()
    make_client(transport).chat([_user()], tools=[_spec()])
    payload = transport.payload()
    tools = payload["tools"]
    assert isinstance(tools, list)
    assert tools[0]["type"] == "function"
    function = tools[0]["function"]
    assert function["name"] == "read_file"
    assert function["description"] == "读取文件"
    assert function["parameters"] == {
        "type": "object",
        "properties": {"path": {"type": "string"}},
    }


@pytest.mark.unit
def test_assistant_tool_calls_keep_raw_arguments() -> None:
    transport = FakeTransport()
    raw_arguments = '{"path": "a.txt"}'
    message = ChatMessage(
        role=Role.ASSISTANT,
        content=None,
        tool_calls=(ToolCallRequest(call_id="c1", name="read_file", arguments_json=raw_arguments),),
    )
    make_client(transport).chat([_user(), message])
    sent = transport.payload()["messages"][1]
    assert sent["role"] == "assistant"
    assert sent["content"] is None
    call = sent["tool_calls"][0]
    assert call["id"] == "c1"
    assert call["type"] == "function"
    assert call["function"]["name"] == "read_file"
    # 原样透传：这里是**文本**，不是被解析后的对象。
    assert call["function"]["arguments"] == raw_arguments


@pytest.mark.unit
def test_tool_message_carries_tool_call_id() -> None:
    transport = FakeTransport()
    message = ChatMessage(role=Role.TOOL, content="file body", tool_call_id="c1")
    make_client(transport).chat([_user(), message])
    sent = transport.payload()["messages"][1]
    assert sent["role"] == "tool"
    assert sent["tool_call_id"] == "c1"
    assert sent["content"] == "file body"


@pytest.mark.unit
def test_system_and_user_messages_are_serialized() -> None:
    transport = FakeTransport()
    make_client(transport).chat(
        [ChatMessage(role=Role.SYSTEM, content="sys"), _user("hi")],
    )
    messages = transport.payload()["messages"]
    assert messages[0] == {"role": "system", "content": "sys"}
    assert messages[1] == {"role": "user", "content": "hi"}


@pytest.mark.unit
def test_max_tokens_omitted_when_none_present_when_given() -> None:
    first = FakeTransport()
    make_client(first).chat([_user()], max_tokens=None)
    assert "max_tokens" not in first.payload()

    second = FakeTransport()
    make_client(second).chat([_user()], max_tokens=64)
    assert second.payload()["max_tokens"] == 64


@pytest.mark.unit
def test_stream_is_disabled_and_temperature_forwarded() -> None:
    transport = FakeTransport()
    make_client(transport).chat([_user()], temperature=0.25)
    payload = transport.payload()
    assert payload["stream"] is False
    assert payload["temperature"] == 0.25


@pytest.mark.unit
def test_timeout_s_is_forwarded_to_transport() -> None:
    transport = FakeTransport()
    make_client(transport).chat([_user()], timeout_s=1.5)
    assert transport.calls[-1]["timeout_s"] == 1.5


@pytest.mark.unit
def test_none_timeout_is_rejected_not_treated_as_infinite() -> None:
    transport = FakeTransport()
    with pytest.raises(ValueError):
        make_client(transport).chat([_user()], timeout_s=None)  # type: ignore[arg-type]
    assert transport.calls == []


@pytest.mark.unit
@pytest.mark.parametrize("timeout", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_timeout_is_rejected(timeout: float) -> None:
    transport = FakeTransport()
    with pytest.raises(ValueError):
        make_client(transport).chat([_user()], timeout_s=timeout)


@pytest.mark.unit
def test_empty_messages_is_rejected() -> None:
    transport = FakeTransport()
    with pytest.raises(ValueError):
        make_client(transport).chat([])


# ---------------------------------------------------------------------------
# 响应映射
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_response_is_mapped() -> None:
    transport = FakeTransport()
    response = make_client(transport).chat([_user()])
    assert response.content == "你好"
    assert response.tool_calls == ()
    assert response.finish_reason is FinishReason.STOP
    assert response.usage.prompt_tokens == 3
    assert response.usage.completion_tokens == 4
    assert response.usage.total_tokens == 7
    assert response.model_id == "local-qwen"


@pytest.mark.unit
def test_model_id_falls_back_to_model_path() -> None:
    transport = FakeTransport([_chat_response(_chat_payload(model=None))])
    response = make_client(transport).chat([_user()])
    assert response.model_id == str(MODEL_PATH)


@pytest.mark.unit
def test_tool_call_response_is_mapped_without_parsing_arguments() -> None:
    payload = _chat_payload(
        choices=[
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c9",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{not valid json"},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    )
    transport = FakeTransport([_chat_response(payload)])
    response = make_client(transport).chat([_user()])
    assert response.finish_reason is FinishReason.TOOL_CALLS
    assert response.content is None
    assert response.tool_calls[0].call_id == "c9"
    # 即便不是合法 JSON 也**原样**保留：解析属 HARNESS 侧的信任边界。
    assert response.tool_calls[0].arguments_json == "{not valid json"


@pytest.mark.unit
def test_unknown_finish_reason_maps_to_unknown() -> None:
    payload = _chat_payload(
        choices=[{"message": {"role": "assistant", "content": "x"}, "finish_reason": "warp"}]
    )
    transport = FakeTransport([_chat_response(payload)])
    response = make_client(transport).chat([_user()])
    assert response.finish_reason is FinishReason.UNKNOWN


@pytest.mark.unit
def test_length_finish_reason_is_preserved() -> None:
    payload = _chat_payload(
        choices=[{"message": {"role": "assistant", "content": "x"}, "finish_reason": "length"}]
    )
    transport = FakeTransport([_chat_response(payload)])
    response = make_client(transport).chat([_user()])
    assert response.finish_reason is FinishReason.LENGTH


@pytest.mark.unit
def test_missing_usage_maps_to_zeros() -> None:
    payload = _chat_payload()
    del payload["usage"]
    transport = FakeTransport([_chat_response(payload)])
    response = make_client(transport).chat([_user()])
    assert (response.usage.prompt_tokens, response.usage.completion_tokens) == (0, 0)
    assert response.usage.total_tokens == 0


@pytest.mark.unit
def test_malformed_usage_is_a_protocol_error() -> None:
    payload = _chat_payload(usage={"prompt_tokens": "three", "completion_tokens": 1})
    transport = FakeTransport([_chat_response(payload)])
    with pytest.raises(ModelProtocolError):
        make_client(transport).chat([_user()])


# ---------------------------------------------------------------------------
# 失败路径
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_missing_choices_is_a_protocol_error() -> None:
    transport = FakeTransport([_chat_response({"model": "m"})])
    with pytest.raises(ModelProtocolError):
        make_client(transport).chat([_user()])


@pytest.mark.unit
def test_empty_choices_is_a_protocol_error() -> None:
    transport = FakeTransport([_chat_response(_chat_payload(choices=[]))])
    with pytest.raises(ModelProtocolError):
        make_client(transport).chat([_user()])


@pytest.mark.unit
def test_both_content_and_tool_calls_empty_is_a_protocol_error() -> None:
    payload = _chat_payload(
        choices=[{"message": {"role": "assistant", "content": ""}, "finish_reason": "stop"}]
    )
    transport = FakeTransport([_chat_response(payload)])
    with pytest.raises(ModelProtocolError):
        make_client(transport).chat([_user()])


@pytest.mark.unit
def test_non_string_arguments_is_a_protocol_error() -> None:
    payload = _chat_payload(
        choices=[
            {
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "read_file", "arguments": {"path": "a"}},
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    )
    transport = FakeTransport([_chat_response(payload)])
    with pytest.raises(ModelProtocolError):
        make_client(transport).chat([_user()])


@pytest.mark.unit
def test_invalid_json_body_is_a_protocol_error() -> None:
    transport = FakeTransport([HttpResponse(status=200, body=b"not json")])
    with pytest.raises(ModelProtocolError):
        make_client(transport).chat([_user()])


@pytest.mark.unit
def test_non_object_body_is_a_protocol_error() -> None:
    transport = FakeTransport([HttpResponse(status=200, body=b"[1, 2, 3]")])
    with pytest.raises(ModelProtocolError):
        make_client(transport).chat([_user()])


@pytest.mark.unit
def test_oversized_body_is_a_protocol_error() -> None:
    big = HttpResponse(status=200, body=b"x" * 4096)
    transport = FakeTransport([big])
    client = make_client(transport, max_response_bytes=1024)
    with pytest.raises(ModelProtocolError):
        client.chat([_user()])


@pytest.mark.unit
def test_server_error_status_is_unavailable() -> None:
    transport = FakeTransport([HttpResponse(status=503, body=b"")])
    with pytest.raises(ModelUnavailableError):
        make_client(transport).chat([_user()])


@pytest.mark.unit
def test_client_error_status_is_protocol_error() -> None:
    transport = FakeTransport([HttpResponse(status=400, body=b"")])
    with pytest.raises(ModelProtocolError):
        make_client(transport).chat([_user()])


@pytest.mark.unit
def test_transport_oserror_maps_to_unavailable() -> None:
    transport = FakeTransport([ConnectionRefusedError("refused")])
    with pytest.raises(ModelUnavailableError):
        make_client(transport).chat([_user()])


@pytest.mark.unit
def test_chat_after_close_is_unavailable() -> None:
    transport = FakeTransport()
    client = make_client(transport)
    client.close()
    with pytest.raises(ModelUnavailableError):
        client.chat([_user()])


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_close_is_idempotent() -> None:
    transport = FakeTransport()
    client = make_client(transport)
    client.close()
    client.close()
    assert transport.close_calls == 1


@pytest.mark.unit
def test_start_server_passes_minimal_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """T-08：常驻第三方二进制**不得**继承父进程环境（凭据不进子进程）。"""
    captured: dict[str, object] = {}
    process = FakeProcess()

    def fake_spawn(
        argv: list[str],
        *,
        cwd: pathlib.Path,
        log_path: pathlib.Path,
        env: object = None,
    ) -> FakeProcess:
        captured.update({"argv": argv, "cwd": cwd, "log_path": log_path, "env": env})
        return process

    monkeypatch.setattr(proc, "resolve_binary", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(proc, "spawn", fake_spawn)

    transport = FakeTransport([HttpResponse(status=200, body=b'{"status": "ok"}')])
    client = LocalLlamaClient(
        binary="llama-server",
        model_path=MODEL_PATH,
        log_path=tmp_path / "llama.log",
        transport=transport,
        start_server=True,
    )
    try:
        assert captured["env"] == proc.minimal_env(tmp_path)
        assert captured["argv"][0] == "/usr/bin/llama-server"
        assert "--port" in captured["argv"]
    finally:
        client.close()
    assert process.terminate_calls == 1


@pytest.mark.unit
def test_not_ready_raises_unavailable_and_reaps_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    process = FakeProcess(running=True)
    monkeypatch.setattr(proc, "resolve_binary", lambda name: "/usr/bin/llama-server")
    monkeypatch.setattr(proc, "spawn", lambda *args, **kwargs: process)

    transport = FakeTransport([ConnectionRefusedError("refused")])
    with pytest.raises(ModelUnavailableError):
        LocalLlamaClient(
            binary="llama-server",
            model_path=MODEL_PATH,
            log_path=tmp_path / "llama.log",
            ready_timeout_s=0.0,
            transport=transport,
            start_server=True,
        )
    # 未就绪即失败，但已起的进程必须被回收（不得留僵尸）。
    assert process.terminate_calls == 1


@pytest.mark.unit
def test_early_exit_raises_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    process = FakeProcess(running=False)
    monkeypatch.setattr(proc, "resolve_binary", lambda name: "/usr/bin/llama-server")
    monkeypatch.setattr(proc, "spawn", lambda *args, **kwargs: process)

    transport = FakeTransport([HttpResponse(status=200, body=b'{"status": "ok"}')])
    with pytest.raises(ModelUnavailableError):
        LocalLlamaClient(
            binary="llama-server",
            model_path=MODEL_PATH,
            log_path=tmp_path / "llama.log",
            ready_timeout_s=0.0,
            transport=transport,
            start_server=True,
        )
    assert process.terminate_calls == 1


@pytest.mark.unit
def test_missing_binary_raises_unavailable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    from agent_sec_perf.foundation.errors import ProtocolError

    def fake_resolve(name: str) -> str:
        raise ProtocolError("not found")

    monkeypatch.setattr(proc, "resolve_binary", fake_resolve)
    transport = FakeTransport()
    with pytest.raises(ModelUnavailableError):
        LocalLlamaClient(
            binary="llama-server",
            model_path=MODEL_PATH,
            log_path=tmp_path / "llama.log",
            transport=transport,
            start_server=True,
        )
