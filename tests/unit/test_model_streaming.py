"""流式补全的解析与回调（不启动真实后端）。

覆盖四件事：``reasoning_content`` 与 ``content`` 分道回调、``tool_calls`` 分片拼接、
流式与非流式**同一错误语义**、以及"回调故障不打断推理"。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from agent_sec_perf.contracts.model import ChatMessage, Role
from agent_sec_perf.foundation.errors import ModelProtocolError
from agent_sec_perf.model.client import HttpResponse, LocalLlamaClient

_Delta = Callable[[str, str], None]


class _StreamTransport:
    """只实现流式的传输替身；走非流式路径会直接失败（防止悄悄退回）。"""

    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines
        self.requests: list[bytes] = []

    def request(
        self, method: str, path: str, *, body: bytes | None, timeout_s: float
    ) -> HttpResponse:
        raise AssertionError("流式路径不应调用 request()")

    def stream(self, path: str, *, body: bytes, timeout_s: float) -> Iterator[bytes]:
        self.requests.append(body)
        yield from self._lines

    def close(self) -> None:
        return


def _data(payload: dict[str, object]) -> bytes:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n".encode()


def _client(transport: _StreamTransport, callback: _Delta) -> LocalLlamaClient:
    return LocalLlamaClient(
        binary="llama-server",
        model_path=Path("/tmp/model.gguf"),
        log_path=Path("/tmp/llama-server.log"),
        transport=transport,
        start_server=False,
        on_delta=callback,
    )


def test_stream_splits_reasoning_and_content() -> None:
    seen: list[tuple[str, str]] = []
    transport = _StreamTransport(
        [
            _data({"choices": [{"delta": {"role": "assistant"}}]}),
            _data({"choices": [{"delta": {"reasoning_content": "先看第一条 ERROR"}}]}),
            _data({"choices": [{"delta": {"reasoning_content": "，再对照配置"}}]}),
            _data({"choices": [{"delta": {"content": "结论如下"}, "finish_reason": "stop"}]}),
            b"data: [DONE]\n",
        ]
    )

    response = _client(transport, lambda channel, text: seen.append((channel, text))).chat(
        [_message()], timeout_s=30.0
    )

    assert response.content == "结论如下"
    assert response.tool_calls == ()
    assert [channel for channel, _ in seen] == ["reasoning", "reasoning", "content"]
    assert "".join(text for channel, text in seen if channel == "reasoning") == (
        "先看第一条 ERROR，再对照配置"
    )
    # 请求体必须是流式（否则后端会一次性等完，展示层拿不到增量）。
    assert json.loads(transport.requests[0])["stream"] is True


def test_stream_joins_fragmented_tool_calls() -> None:
    transport = _StreamTransport(
        [
            _data(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {"name": "read_file", "arguments": '{"path":'},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ),
            _data(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {"index": 0, "function": {"arguments": '"app.log"}'}}
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            ),
            b"data: [DONE]\n",
        ]
    )

    response = _client(transport, lambda channel, text: None).chat([_message()], timeout_s=30.0)

    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "read_file"
    assert response.tool_calls[0].arguments_json == '{"path":"app.log"}'


def test_stream_rejects_empty_response() -> None:
    """只有 role / finish_reason、既无 content 也无 tool_calls ⇒ 协议错误（与非流式一致）。"""
    transport = _StreamTransport(
        [
            _data({"choices": [{"delta": {"role": "assistant"}, "finish_reason": "stop"}]}),
            b"data: [DONE]\n",
        ]
    )

    with pytest.raises(ModelProtocolError):
        _client(transport, lambda channel, text: None).chat([_message()], timeout_s=30.0)


def test_stream_rejects_tool_call_without_id() -> None:
    transport = _StreamTransport(
        [
            _data(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {"name": "read_file", "arguments": "{}"},
                                    }
                                ]
                            }
                        }
                    ]
                }
            ),
            b"data: [DONE]\n",
        ]
    )

    with pytest.raises(ModelProtocolError):
        _client(transport, lambda channel, text: None).chat([_message()], timeout_s=30.0)


def test_stream_survives_broken_callback() -> None:
    """展示回调抛异常不能把一次成功的推理变成失败。"""

    def broken(channel: str, text: str) -> None:
        raise RuntimeError("终端断了")

    transport = _StreamTransport(
        [
            _data({"choices": [{"delta": {"reasoning_content": "想"}}]}),
            _data({"choices": [{"delta": {"content": "答"}, "finish_reason": "stop"}]}),
            b"data: [DONE]\n",
        ]
    )

    response = _client(transport, broken).chat([_message()], timeout_s=30.0)

    assert response.content == "答"


def _message() -> ChatMessage:
    return ChatMessage(role=Role.USER, content="读 app.log 并给结论")
