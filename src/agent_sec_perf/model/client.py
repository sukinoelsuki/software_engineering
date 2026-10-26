"""本地 ``llama-server`` 的模型客户端（``ModelClient`` 实现，``G7``，L2 能力层）。

契约与错误语义见 ``docs/design/interfaces/model.md``；本模块**只实现**它，不改契约。
思路借鉴 ``bench/runner.py``（启动 → ``/health`` 就绪 → 请求 → 解析 ``choices`` / ``usage``），
但**不 import ``bench/``**（``ADR-0015`` §7.1 R1 的依赖白名单）。

边界处的四条硬约束（各有明确对手）：

1. **只连回环地址**：``host`` 必须是 ``127.0.0.1`` / ``::1`` / ``localhost``，其余一律拒绝。
   出站不得成为"任意可达后端"（``SECURITY.md``：网络出站默认拒绝）。HTTP 用标准库
   ``http.client`` 直连，不用 ``urllib``（同时避开 scheme 注入面 ``B310``）。
2. **超时必须真正生效**：``timeout_s`` 原样传给传输层；``None`` / 非正数**拒绝**，
   **不得**被当作"无限等待"（否则一次挂起的后端会永久占用会话）。
3. **响应体必须有上限**：每次读取最多 ``max_response_bytes`` 字节，超限即
   ``ModelProtocolError``。没有上限时，一个超大响应体就能把进程内存耗尽。
4. **常驻进程显式最小环境**：经 ``foundation.proc.spawn`` 启动，并**显式**传
   ``env=proc.minimal_env(...)``——``llama-server`` 是不受本项目控制的第三方二进制，
   父进程的令牌 / 凭据不得进入它（``T-08``）。

错误语义（``model.md`` §3）：后端不可达 / 未就绪 / 超时 ⇒ ``ModelUnavailableError``
（调用方**路由降级**）；响应不符合契约 ⇒ ``ModelProtocolError``（调用方**重试一次**后回喂）。
``chat`` 内**不吞异常**、**不返回空响应**。

序列化的一处关键约定：``tools`` 为 ``None``（或空序列）时**完全省略** ``tools`` 字段，
**不**发空数组——"不暴露任何工具"必须在线上协议里也是"没有这个字段"（default-deny）；
模型返回的 ``tool_calls[].function.arguments`` 是**原始 JSON 文本**，**原样**放进
``ToolCallRequest.arguments_json``，**不解析**（解析只能发生在 HARNESS 侧的信任边界）。
"""

from __future__ import annotations

import http.client
import json
import logging
import math
import pathlib
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol, cast

from agent_sec_perf.contracts.model import (
    ChatMessage,
    FinishReason,
    ModelClient,
    ModelResponse,
    Role,
    TokenUsage,
)
from agent_sec_perf.contracts.tools import ToolCallRequest, ToolSpec
from agent_sec_perf.foundation import proc
from agent_sec_perf.foundation.errors import (
    ModelProtocolError,
    ModelUnavailableError,
    ProtocolError,
)
from agent_sec_perf.foundation.logging import sanitize_for_display

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_MAX_RESPONSE_BYTES",
    "DEFAULT_PORT",
    "LOOPBACK_HOSTS",
    "DeltaCallback",
    "HttpResponse",
    "HttpTransport",
    "LocalLlamaClient",
    "LoopbackHttpTransport",
    "validate_loopback_host",
]

#: 流式增量回调：``(channel, text)``，``channel`` ∈ ``{"reasoning", "content"}``。
#:
#: 存在的理由：端侧 4B 模型单次补全要 3~4 分钟（约 2.8 tok/s）。非流式时这期间
#: **一个字节都没有**，看的人无法区分"在算"与"卡死"，更看不出"这是本地模型在生成"。
#: 回调只用于**展示**（不参与控制流、不进事件流）；异常由回调自己吞掉，
#: 展示失败绝不能打断推理。
DeltaCallback = Callable[[str, str], None]

#: 允许的后端主机：**只允许回环地址**，其余一律拒绝（默认拒绝）。
LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "localhost"})

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080

#: 单次响应体的字节上限（安全基线：限制响应体大小）。1 MiB 足以容纳一次补全的 JSON 回执。
DEFAULT_MAX_RESPONSE_BYTES = 1024 * 1024

#: 就绪轮询的间隔与单次健康检查超时（与 ``bench/runner.py`` 同口径）。
_READY_POLL_INTERVAL_S = 0.25
_HEALTH_TIMEOUT_S = 2.0

_CHAT_PATH = "/v1/chat/completions"
_HEALTH_PATH = "/health"

#: 展示上限：不可信文本进入错误信息前先净化并截断。
_LABEL_LIMIT = 64

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class HttpResponse:
    """一次 HTTP 响应的最小形状（状态码 + 原始字节体）。"""

    status: int
    body: bytes


class HttpTransport(Protocol):
    """HTTP 传输的最小抽象（可替换：单测注入替身，无需真实网络）。

    实现约定：网络层故障（连接失败 / 超时）抛 ``OSError``（含 ``TimeoutError``），
    由 :meth:`LocalLlamaClient.chat` 统一映射为 ``ModelUnavailableError``；
    响应体超限抛 :class:`~agent_sec_perf.foundation.errors.ModelProtocolError`。
    """

    def request(
        self, method: str, path: str, *, body: bytes | None, timeout_s: float
    ) -> HttpResponse: ...

    def close(self) -> None: ...


class LoopbackHttpTransport:
    """基于 ``http.client`` 的**回环**传输。

    只用标准库：``urllib`` 的 scheme 处理是额外的注入面（``B310``），而本项目只需要
    "连本机的一个已知端口"，因此把这件事写死在结构里（构造期即校验 host）。
    连接**每次请求新建、用完即关**：不持有跨调用的可变态，超时语义也更直接。
    """

    def __init__(
        self,
        host: str,
        port: int,
        *,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self._host = validate_loopback_host(host)
        self._port = _validated_port(port)
        self._max_response_bytes = _validated_positive_int(
            max_response_bytes, what="max_response_bytes"
        )

    def request(
        self, method: str, path: str, *, body: bytes | None, timeout_s: float
    ) -> HttpResponse:
        """发送一次请求并读取响应（响应体读取**受上限保护**）。"""
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection = http.client.HTTPConnection(self._host, self._port, timeout=timeout_s)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            return HttpResponse(
                status=response.status,
                body=_read_limited(response, self._max_response_bytes),
            )
        finally:
            connection.close()

    def stream(self, path: str, *, body: bytes, timeout_s: float) -> Iterator[bytes]:
        """以 SSE **逐行**读取响应（流式补全用）。

        与 :meth:`request` 的三点区别，都是流式特有的约束：

        1. 逐行 yield，调用方边读边展示——这正是"看得到模型在生成"的前提；
        2. **单行与累计**都受 ``max_response_bytes`` 保护：不设限时，一个不换行的
           超大响应就能让 ``readline`` 把内存吃满；
        3. 非 200 时先读完（受上限保护的）错误体再抛，错误分类与
           :meth:`request` 完全一致——流式**不得**自创一套错误语义。
        """
        connection = http.client.HTTPConnection(self._host, self._port, timeout=timeout_s)
        try:
            connection.request(
                "POST", path, body=body, headers={"Content-Type": "application/json"}
            )
            response = connection.getresponse()
            if response.status != 200:
                # 不读错误体：它同样受"响应体上限"约束，而这里只需要状态码定性。
                _raise_for_status(response.status)
            total = 0
            while True:
                line = response.readline()
                if not line:
                    return
                total += len(line)
                if len(line) > self._max_response_bytes or total > self._max_response_bytes:
                    msg = f"流式响应体超过上限 {self._max_response_bytes} 字节"
                    raise ModelProtocolError(msg)
                yield line
        finally:
            connection.close()

    def close(self) -> None:
        """无状态传输：没有需要释放的资源（幂等）。"""
        return


class LocalLlamaClient(ModelClient):
    """本地 ``llama-server`` 的 :class:`ModelClient` 实现。

    Args:
        binary: ``llama-server`` 的可执行文件名或路径（启动时经 ``proc.resolve_binary``
            解析为**绝对路径**，避免 PATH 被污染时执行到别的程序）。
        model_path: GGUF 模型路径（同时作为 ``model_id`` 的后备取值）。
        log_path: 服务日志落点（性能数据与排障证据；其父目录用作工作目录）。
        host: 后端主机，**只允许回环地址**。
        port: 后端端口。
        server_args: 追加给 ``llama-server`` 的参数（原样透传，逐项显式给出）。
        ready_timeout_s: 就绪等待上限（超时即 ``ModelUnavailableError``）。
        max_response_bytes: 单次响应体字节上限。
        transport: 注入的传输层；``None`` ⇒ 用 :class:`LoopbackHttpTransport`。
        start_server: ``True`` ⇒ 由本实例启动并托管子进程；``False`` ⇒ 假定后端已在运行
            （单测与"外部托管的服务"两种用法），此时 :meth:`close` 不触碰任何进程。

    并发假设：**非线程安全**（一个会话一个实例，``llama-server`` 默认 ``-np 1``）。
    资源生命周期：:meth:`close` **幂等**；进程型后端经 ``foundation.proc.spawn`` 托管，
    启动失败路径也会回收已起的进程（不留僵尸）。
    """

    def __init__(
        self,
        *,
        binary: str,
        model_path: pathlib.Path,
        log_path: pathlib.Path,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        server_args: Sequence[str] = (),
        ready_timeout_s: float = 60.0,
        max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
        transport: HttpTransport | None = None,
        start_server: bool = True,
        on_delta: DeltaCallback | None = None,
    ) -> None:
        #: 给了回调 ⇒ 走**流式**请求（``stream: true``），边生成边回调。
        #: 没给 ⇒ 保持原来的非流式路径，行为与之前逐字一致（含错误语义）。
        self._on_delta = on_delta
        self._binary = binary
        self._model_path = pathlib.Path(model_path)
        self._log_path = pathlib.Path(log_path)
        self._host = validate_loopback_host(host)
        self._port = _validated_port(port)
        self._server_args = tuple(server_args)
        # ``ready_timeout_s == 0`` 是合法取值（"不等待，假定已就绪"）；负数 / NaN / None 拒绝。
        self._ready_timeout_s = _validated_finite_float(
            ready_timeout_s, what="ready_timeout_s", minimum=0.0
        )
        self._max_response_bytes = _validated_positive_int(
            max_response_bytes, what="max_response_bytes"
        )
        self._transport: HttpTransport = (
            LoopbackHttpTransport(
                self._host, self._port, max_response_bytes=self._max_response_bytes
            )
            if transport is None
            else transport
        )
        self._process: proc.BackgroundProcess | None = None
        self._closed = False
        if start_server:
            self._start()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    @property
    def model_id(self) -> str:
        """本客户端对应的后端标识（服务端未回执 ``model`` 时的后备取值）。"""
        return str(self._model_path)

    def close(self) -> None:
        """关闭客户端（**幂等**；不留僵尸进程）。"""
        if self._closed:
            return
        self._closed = True
        self._terminate_process()
        self._transport.close()

    def _start(self) -> None:
        """启动 ``llama-server`` 并等待就绪。"""
        workdir = self._log_path.parent
        try:
            binary = proc.resolve_binary(self._binary)
        except ProtocolError as exc:
            msg = f"找不到 llama-server 可执行文件：{sanitize_for_display(self._binary)}"
            raise ModelUnavailableError(msg) from exc

        argv = [
            binary,
            "-m",
            str(self._model_path),
            "--host",
            self._host,
            "--port",
            str(self._port),
            *self._server_args,
        ]
        try:
            # 显式最小环境：第三方二进制的子进程不得继承父进程的令牌 / 凭据（T-08）。
            self._process = proc.spawn(
                argv,
                cwd=workdir,
                log_path=self._log_path,
                env=proc.minimal_env(workdir),
            )
        except OSError as exc:
            msg = f"llama-server 无法启动：{exc}"
            raise ModelUnavailableError(msg) from exc

        try:
            self._wait_ready()
        except ModelUnavailableError:
            # 未就绪即失败：必须回收已起的进程，否则每次重试都泄漏一个常驻进程。
            self._terminate_process()
            raise

    def _terminate_process(self) -> None:
        """终止托管的服务进程（幂等；无进程时为空操作）。"""
        if self._process is None:
            return
        process = self._process
        self._process = None
        process.terminate()

    def _wait_ready(self) -> None:
        """轮询 ``/health`` 直到就绪、进程提前退出或超出等待上限。"""
        deadline = time.monotonic() + self._ready_timeout_s
        while True:
            if self._process is not None and not self._process.is_running():
                msg = f"llama-server 提前退出，见日志：{self._log_path}"
                raise ModelUnavailableError(msg)
            try:
                response = self._transport.request(
                    "GET", _HEALTH_PATH, body=None, timeout_s=_HEALTH_TIMEOUT_S
                )
            except (OSError, ModelProtocolError):
                # 启动期连接失败 / 非常规响应都是"尚未就绪"的正常形态，继续轮询。
                pass
            else:
                if response.status == 200 and _health_ok(response.body):
                    return
            if time.monotonic() >= deadline:
                msg = f"llama-server 未在 {self._ready_timeout_s:.0f}s 内就绪，见日志：{self._log_path}"
                raise ModelUnavailableError(msg)
            time.sleep(_READY_POLL_INTERVAL_S)

    # ------------------------------------------------------------------
    # 推理
    # ------------------------------------------------------------------

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        timeout_s: float = 60.0,
    ) -> ModelResponse:
        """发送一次补全请求并映射为 :class:`ModelResponse`。

        Raises:
            ValueError: 入参形状非法（空消息、非 :class:`ChatMessage`、``timeout_s``
                非正数或为 ``None`` 等）。这是**调用方缺陷**，不伪装成后端故障。
            ModelUnavailableError: 后端已关闭 / 不可达 / 超时。
            ModelProtocolError: 响应不符合契约（状态码、缺 ``choices``、
                ``content`` 与 ``tool_calls`` 皆空、响应体超限）。
        """
        if self._closed:
            msg = "模型客户端已关闭（close() 之后不得再调用 chat）"
            raise ModelUnavailableError(msg)

        checked = _validated_messages(messages)
        timeout = _validated_positive_float(timeout_s, what="timeout_s")
        payload = _build_payload(
            checked, tools=tools, temperature=temperature, max_tokens=max_tokens
        )
        if self._on_delta is not None:
            return self._chat_streaming(payload=payload, timeout=timeout)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        try:
            response = self._transport.request("POST", _CHAT_PATH, body=body, timeout_s=timeout)
        except ModelProtocolError:
            raise
        except OSError as exc:
            msg = f"模型后端不可达：{type(exc).__name__}"
            raise ModelUnavailableError(msg) from exc

        return self._parse_response(response)

    def _chat_streaming(self, *, payload: Mapping[str, object], timeout: float) -> ModelResponse:
        """流式补全：边读边回调增量，最后拼成与非流式**同一形状**的 ``ModelResponse``。

        三条与"能不能显示"无关、但与**正确性**有关的约定：

        1. 流式**不改错误语义**：状态码、非法 JSON、``content`` 与 ``tool_calls`` 皆空
           都在同一处判定（``_raise_for_status`` / ``_sse_chunks`` / ``_StreamState``）；
        2. 增量回调**只用于展示**：回调抛异常只记 debug 日志，不打断推理
           （它不是控制流的一部分，更不是工具执行的一部分）；
        3. 传输层不支持 ``stream`` 时**退回非流式**：展示不了增量，但结果正确——
           不抛异常、不伪装"已流式"。
        """
        stream = getattr(self._transport, "stream", None)
        if not callable(stream):
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            response = self._transport.request("POST", _CHAT_PATH, body=body, timeout_s=timeout)
            return self._parse_response(response)

        streaming_payload = {**payload, "stream": True}
        body = json.dumps(streaming_payload, ensure_ascii=False).encode("utf-8")
        state = _StreamState()
        try:
            for line in cast("Iterator[bytes]", stream(_CHAT_PATH, body=body, timeout_s=timeout)):
                for chunk in _sse_chunks(line):
                    state.apply(chunk, on_delta=self._on_delta)
        except ModelProtocolError:
            raise
        except OSError as exc:
            msg = f"模型后端不可达：{type(exc).__name__}"
            raise ModelUnavailableError(msg) from exc
        return state.to_response(model_id=str(self._model_path))

    def _parse_response(self, response: HttpResponse) -> ModelResponse:
        """把 HTTP 响应映射为 :class:`ModelResponse`，逐项校验契约不变式。"""
        if len(response.body) > self._max_response_bytes:
            # 纵深防御：即便注入的传输层没有在读时设限，也不接受超限响应。
            msg = f"响应体超过上限 {self._max_response_bytes} 字节"
            raise ModelProtocolError(msg)
        _raise_for_status(response.status)

        payload = _json_object(response.body)
        raw_choices = payload.get("choices")
        if not isinstance(raw_choices, list) or not raw_choices:
            msg = "响应缺少非空的 choices"
            raise ModelProtocolError(msg)
        choice = _require_object(raw_choices[0], what="choices[0]")
        message = _require_object(choice.get("message"), what="choices[0].message")

        content = message.get("content")
        if content is not None and not isinstance(content, str):
            msg = "choices[0].message.content 不是字符串或 null"
            raise ModelProtocolError(msg)
        normalized_content = content if content else None

        tool_calls = _parse_tool_calls(message.get("tool_calls"))
        if normalized_content is None and not tool_calls:
            msg = "响应中 content 与 tool_calls 皆空（不符合契约）"
            raise ModelProtocolError(msg)

        return ModelResponse(
            content=normalized_content,
            tool_calls=tool_calls,
            finish_reason=_parse_finish_reason(choice.get("finish_reason")),
            usage=_parse_usage(payload.get("usage")),
            model_id=_parse_model_id(payload.get("model"), fallback=str(self._model_path)),
        )


# ----------------------------------------------------------------------
# 载荷构造
# ----------------------------------------------------------------------


def _build_payload(
    messages: Sequence[ChatMessage],
    *,
    tools: Sequence[ToolSpec] | None,
    temperature: float,
    max_tokens: int | None,
) -> dict[str, object]:
    """构造请求体。

    ``tools`` 为 ``None`` **或空序列**时**完全省略**该字段：空数组在协议上与
    "显式声明零工具"难以区分，而 default-deny 要的是"没有这个字段"。
    """
    payload: dict[str, object] = {
        "messages": [_message_payload(message) for message in messages],
        "temperature": _validated_temperature(temperature),
        "stream": False,
    }
    if max_tokens is not None:
        payload["max_tokens"] = _validated_max_tokens(max_tokens)
    if tools:
        payload["tools"] = [_tool_payload(spec) for spec in tools]
    return payload


def _message_payload(message: ChatMessage) -> dict[str, object]:
    """把一条 :class:`ChatMessage` 序列化为 OpenAI 兼容形状。

    ``role == TOOL`` 必须带 ``tool_call_id``（否则回指断裂、审计无法回放）；
    ``role == ASSISTANT`` 的 ``tool_calls`` 里 ``arguments`` 是**原始 JSON 文本**，
    原样透传（**不解析**）。
    """
    data: dict[str, object] = {"role": message.role.value}
    if message.role is Role.ASSISTANT and message.tool_calls:
        data["content"] = message.content
        data["tool_calls"] = [_tool_call_payload(call) for call in message.tool_calls]
        return data

    data["content"] = "" if message.content is None else message.content
    if message.role is Role.TOOL:
        data["tool_call_id"] = message.tool_call_id
    return data


def _tool_call_payload(call: ToolCallRequest) -> dict[str, object]:
    return {
        "id": call.call_id,
        "type": "function",
        "function": {"name": call.name, "arguments": call.arguments_json},
    }


def _tool_payload(spec: ToolSpec) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": dict(spec.parameters_schema),
        },
    }


# ----------------------------------------------------------------------
# 响应解析
# ----------------------------------------------------------------------


def _parse_tool_calls(raw: object) -> tuple[ToolCallRequest, ...]:
    """解析 ``tool_calls``；``arguments`` 保持**原始文本**，不做任何 JSON 解析。"""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        msg = "choices[0].message.tool_calls 不是数组"
        raise ModelProtocolError(msg)

    calls: list[ToolCallRequest] = []
    for entry in raw:
        item = _require_object(entry, what="tool_calls[]")
        function = _require_object(item.get("function"), what="tool_calls[].function")
        call_id = item.get("id")
        name = function.get("name")
        arguments = function.get("arguments")
        if not isinstance(call_id, str) or not call_id:
            msg = "tool_calls[].id 必须是非空字符串"
            raise ModelProtocolError(msg)
        if not isinstance(name, str) or not name:
            msg = "tool_calls[].function.name 必须是非空字符串"
            raise ModelProtocolError(msg)
        if not isinstance(arguments, str):
            # 契约要求它是**原始 JSON 文本**：这里只校验类型，绝不解析。
            msg = "tool_calls[].function.arguments 必须是 JSON 文本（字符串）"
            raise ModelProtocolError(msg)
        calls.append(ToolCallRequest(call_id=call_id, name=name, arguments_json=arguments))
    return tuple(calls)


def _parse_finish_reason(raw: object) -> FinishReason:
    """映射 ``finish_reason``；**无法识别即 ``UNKNOWN``**（不得默认成 ``STOP``）。"""
    if isinstance(raw, str):
        try:
            return FinishReason(raw)
        except ValueError:
            return FinishReason.UNKNOWN
    return FinishReason.UNKNOWN


def _parse_usage(raw: object) -> TokenUsage:
    """映射 ``usage``；缺失即零值，字段存在但类型非法即 ``ModelProtocolError``。"""
    if not isinstance(raw, Mapping):
        return TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0)
    usage = cast("Mapping[str, object]", raw)
    return TokenUsage(
        prompt_tokens=_non_negative_int(usage.get("prompt_tokens"), what="prompt_tokens"),
        completion_tokens=_non_negative_int(
            usage.get("completion_tokens"), what="completion_tokens"
        ),
        total_tokens=_non_negative_int(usage.get("total_tokens"), what="total_tokens"),
    )


def _parse_model_id(raw: object, *, fallback: str) -> str:
    """后端标识：优先取服务端回执，缺失时回退到配置的模型路径。"""
    if isinstance(raw, str) and raw:
        return raw
    return fallback


def _raise_for_status(status: int) -> None:
    """状态码 → 异常（**流式与非流式共用**，避免两套错误语义）。"""
    if status == 200:
        return
    if status >= 500:
        msg = f"模型后端返回 {status}（服务端故障 / 未就绪）"
        raise ModelUnavailableError(msg)
    msg = f"模型后端返回 {status}（请求或协议错误）"
    raise ModelProtocolError(msg)


def _sse_chunks(line: bytes) -> Iterator[Mapping[str, object]]:
    """把一行 SSE 解析成**顶层** chunk；非 ``data:`` 行与 ``[DONE]`` 不产出。"""
    text = line.decode("utf-8", "replace").strip()
    if not text.startswith("data:"):
        return
    payload = text[5:].strip()
    if not payload or payload == "[DONE]":
        return
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        msg = "流式响应含非法 JSON 数据"
        raise ModelProtocolError(msg) from exc
    if not isinstance(parsed, dict):
        msg = "流式响应的数据不是 JSON 对象"
        raise ModelProtocolError(msg)
    yield cast("Mapping[str, object]", parsed)


def _emit(on_delta: DeltaCallback | None, channel: str, text: str) -> None:
    """把增量交给展示回调；**回调失败不影响推理**（它不是控制流的一部分）。"""
    if on_delta is None:
        return
    try:
        on_delta(channel, text)
    except Exception:
        # 展示层故障（终端断开、富文本编码失败）不应把一次成功的推理变成失败。
        _LOG.debug("增量回调失败（不影响推理）", exc_info=True)


@dataclass
class _StreamState:
    """把 SSE 增量拼成与非流式响应等价的形状（``content`` / ``tool_calls`` / usage）。"""

    _content: list[str] = field(default_factory=list)
    _reasoning: list[str] = field(default_factory=list)
    _calls: dict[int, dict[str, str]] = field(default_factory=dict)
    _finish: str | None = None
    _usage: Mapping[str, object] | None = None

    def apply(self, chunk: Mapping[str, object], *, on_delta: DeltaCallback | None) -> None:
        usage = chunk.get("usage")
        if isinstance(usage, Mapping):
            self._usage = cast("Mapping[str, object]", usage)
        choices = chunk.get("choices")
        if not isinstance(choices, list):
            return
        for entry in choices:
            if not isinstance(entry, dict):
                continue
            choice = cast("Mapping[str, object]", entry)
            delta = choice.get("delta")
            if isinstance(delta, Mapping):
                self._apply_delta(cast("Mapping[str, object]", delta), on_delta=on_delta)
            finish = choice.get("finish_reason")
            if isinstance(finish, str) and finish:
                self._finish = finish

    def _apply_delta(self, delta: Mapping[str, object], *, on_delta: DeltaCallback | None) -> None:
        reasoning = delta.get("reasoning_content")
        if isinstance(reasoning, str) and reasoning:
            self._reasoning.append(reasoning)
            _emit(on_delta, "reasoning", reasoning)
        content = delta.get("content")
        if isinstance(content, str) and content:
            self._content.append(content)
            _emit(on_delta, "content", content)
        calls = delta.get("tool_calls")
        if isinstance(calls, list):
            self._apply_tool_calls(calls)

    def _apply_tool_calls(self, calls: list[object]) -> None:
        """工具调用是**分片**到达的：``id`` / ``name`` / ``arguments`` 各走各的增量。"""
        for entry in calls:
            if not isinstance(entry, dict):
                continue
            item = cast("Mapping[str, object]", entry)
            raw_index = item.get("index")
            index = raw_index if isinstance(raw_index, int) else 0
            slot = self._calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
            call_id = item.get("id")
            if isinstance(call_id, str) and call_id:
                slot["id"] = call_id
            function = item.get("function")
            if not isinstance(function, Mapping):
                continue
            spec = cast("Mapping[str, object]", function)
            name = spec.get("name")
            if isinstance(name, str):
                slot["name"] += name
            arguments = spec.get("arguments")
            if isinstance(arguments, str):
                slot["arguments"] += arguments

    def to_response(self, *, model_id: str) -> ModelResponse:
        content = "".join(self._content) or None
        parsed: list[ToolCallRequest] = []
        for _, slot in sorted(self._calls.items()):
            if not slot["name"]:
                continue
            if not slot["id"]:
                msg = "流式 tool_calls 缺少 id（不符合契约）"
                raise ModelProtocolError(msg)
            parsed.append(
                ToolCallRequest(
                    call_id=slot["id"],
                    name=slot["name"],
                    arguments_json=slot["arguments"],
                )
            )
        calls = tuple(parsed)
        if content is None and not calls:
            msg = "流式响应中 content 与 tool_calls 皆空（不符合契约）"
            raise ModelProtocolError(msg)
        return ModelResponse(
            content=content,
            tool_calls=calls,
            finish_reason=_parse_finish_reason(self._finish),
            usage=_parse_usage(self._usage),
            model_id=model_id,
        )


def _json_object(body: bytes) -> Mapping[str, object]:
    """把响应体解析为 JSON 对象；非 UTF-8 / 非 JSON / 非对象一律 ``ModelProtocolError``。"""
    try:
        decoded = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        msg = "响应体不是 UTF-8 文本"
        raise ModelProtocolError(msg) from exc
    try:
        parsed = json.loads(decoded)
    except json.JSONDecodeError as exc:
        msg = "响应体不是合法 JSON"
        raise ModelProtocolError(msg) from exc
    return _require_object(parsed, what="响应体")


def _require_object(value: object, *, what: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        msg = f"{what} 不是 JSON 对象"
        raise ModelProtocolError(msg)
    return cast("Mapping[str, object]", value)


def _non_negative_int(value: object, *, what: str) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        msg = f"usage.{what} 不是非负整数"
        raise ModelProtocolError(msg)
    return value


def _health_ok(body: bytes) -> bool:
    """``/health`` 是否报告 ``status == ok``（解析失败一律视为未就绪）。"""
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(parsed, dict) and parsed.get("status") == "ok"


def _read_limited(response: http.client.HTTPResponse, limit: int) -> bytes:
    """读取响应体，最多 ``limit`` 字节；超限即拒绝（不把超大响应读进内存）。"""
    raw = response.read(limit + 1)
    if len(raw) > limit:
        msg = f"响应体超过上限 {limit} 字节"
        raise ModelProtocolError(msg)
    return raw


# ----------------------------------------------------------------------
# 入参校验
# ----------------------------------------------------------------------


def validate_loopback_host(host: str) -> str:
    """校验后端主机是回环地址（``127.0.0.1`` / ``::1`` / ``localhost``），否则拒绝。

    Raises:
        ValueError: 主机不是回环地址（或不是字符串）。
    """
    if isinstance(host, str) and host in LOOPBACK_HOSTS:
        return host
    shown = sanitize_for_display(str(host), limit=_LABEL_LIMIT)
    allowed = " / ".join(sorted(LOOPBACK_HOSTS))
    msg = f"只允许回环地址（{allowed}），收到：{shown!r}"
    raise ValueError(msg)


def _validated_messages(messages: Sequence[ChatMessage]) -> tuple[ChatMessage, ...]:
    """校验消息序列形状（空序列、非 :class:`ChatMessage` 即拒绝）。"""
    # 形参标注是 ``Sequence[ChatMessage]``，但类型标注挡不住运行期传入的任意对象
    # （调用方可能来自鸭子类型的装配点）⇒ 按 ``object`` 逐项做类型检查。
    items: tuple[object, ...] = tuple(messages)
    if not items:
        msg = "messages 不得为空（至少一条系统或用户消息）"
        raise ValueError(msg)
    validated: list[ChatMessage] = []
    for index, item in enumerate(items):
        if not isinstance(item, ChatMessage):
            msg = f"messages[{index}] 不是 ChatMessage"
            raise ValueError(msg)
        validated.append(item)
    return tuple(validated)


def _validated_temperature(temperature: float) -> float:
    return _validated_finite_float(temperature, what="temperature", minimum=0.0)


def _validated_max_tokens(max_tokens: int) -> int:
    return _validated_positive_int(max_tokens, what="max_tokens")


def _validated_port(port: int) -> int:
    value = _validated_positive_int(port, what="port")
    if value > 65535:
        msg = "port 必须在 1~65535 之间"
        raise ValueError(msg)
    return value


def _validated_positive_int(value: object, *, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        msg = f"{what} 必须是正整数"
        raise ValueError(msg)
    return value


def _validated_positive_float(value: object, *, what: str) -> float:
    """校验有限正数；``None`` / ``NaN`` / 非正数一律拒绝（**不得**当作无限等待）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        msg = f"{what} 必须是有限正数（None 不被当作无限等待）"
        raise ValueError(msg)
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        msg = f"{what} 必须是有限正数（None 不被当作无限等待）"
        raise ValueError(msg)
    return number


def _validated_finite_float(value: object, *, what: str, minimum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        msg = f"{what} 必须是有限数值"
        raise ValueError(msg)
    number = float(value)
    if not math.isfinite(number) or number < minimum:
        msg = f"{what} 必须是不小于 {minimum} 的有限数值"
        raise ValueError(msg)
    return number
