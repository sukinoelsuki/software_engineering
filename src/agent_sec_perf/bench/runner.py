"""llama-server 的生命周期管理与测量采集。

测量口径（与三档对比实验一致）：**prefill 与生成速率取自服务端日志的
``slot print_timing`` 行**，而不是客户端端到端计时——后者会把 HTTP、JSON
解析与客户端调度都算进去。

HTTP 用标准库 ``http.client`` 直连回环地址：既避免 ``urllib`` 的 scheme 注入面
（bandit ``B310``），也把"只连本机"这件事写在结构里（:class:`RunParams` 只接受
``127.0.0.1``）。
"""

from __future__ import annotations

import http.client
import json
import pathlib
import re
import time
from dataclasses import dataclass
from typing import cast

from agent_sec_perf.bench import proc
from agent_sec_perf.bench.errors import BenchError
from agent_sec_perf.bench.protocol import RunParams

_PREFILL_RE = re.compile(
    r"prompt eval time =\s*([\d.]+) ms /\s*(\d+) tokens \(.*?([\d.]+) tokens per second"
)
_GEN_RE = re.compile(
    r"(?<!prompt )eval time =\s*([\d.]+) ms /\s*(\d+) tokens \(.*?([\d.]+) tokens per second"
)
_SLOTS_RE = re.compile(r"n_slots = (\d+), n_ctx_slot = (\d+)")
_LOAD_MS_RE = re.compile(r"load time = \s*([\d.]+) ms")


@dataclass(frozen=True)
class ChatResult:
    """一次补全请求的结果。"""

    finish_reason: str
    content: str
    prompt_tokens: int
    completion_tokens: int
    elapsed_s: float


@dataclass(frozen=True)
class EvalTiming:
    """一条服务端耗时行。

    **必须同时带上被评估的 token 数**：只留速率会把"命中缓存后的 1 个 token"
    当成一次 prefill 测量（2026-09-16 的实际翻车点）。
    """

    tokens: int
    milliseconds: float
    tok_per_s: float


@dataclass(frozen=True)
class RepeatTimings:
    """一次重复内的服务端计时（逐条保留，便于事后复核）。"""

    prefill: tuple[EvalTiming, ...]
    gen: tuple[EvalTiming, ...]


def parse_server_info(log_text: str) -> dict[str, object]:
    """从日志中提取槽位口径（V-14：未指定 ``-np`` 时默认 4 槽位且共享 KV）。"""
    match = _SLOTS_RE.search(log_text)
    if match is None:
        return {"n_slots": None, "n_ctx_slot": None}
    return {"n_slots": int(match.group(1)), "n_ctx_slot": int(match.group(2))}


def parse_model_load_seconds(log_text: str) -> float | None:
    """从日志中提取模型加载耗时（秒）；取最后一次出现（重试场景以最终加载为准）。"""
    matches = _LOAD_MS_RE.findall(log_text)
    return float(matches[-1]) / 1000.0 if matches else None


def parse_timings(log_text: str) -> RepeatTimings:
    """解析服务端计时（逐条带 token 数）。

    反向后行断言用于排除 ``prompt eval time`` 行——否则 prefill 会被统计两次。

    注意：``tokens`` 是**本次实际评估**的 token 数。若它很小（例如 1），说明这一行
    是缓存命中，不是 prefill 测量；判定逻辑在 ``rounds`` 里据此拒绝该重复。
    """

    def _entries(pattern: re.Pattern[str]) -> tuple[EvalTiming, ...]:
        return tuple(
            EvalTiming(tokens=int(tokens), milliseconds=float(ms), tok_per_s=float(rate))
            for ms, tokens, rate in pattern.findall(log_text)
        )

    return RepeatTimings(prefill=_entries(_PREFILL_RE), gen=_entries(_GEN_RE))


class LlamaServer:
    """llama-server 的上下文管理器：启动 → 等待就绪 → 关闭。"""

    def __init__(
        self,
        *,
        params: RunParams,
        binary: str,
        model_path: pathlib.Path,
        log_path: pathlib.Path,
        ready_timeout_s: float | None = None,
    ) -> None:
        self._params = params
        self._binary = binary
        self._model_path = model_path
        self._log_path = log_path
        self._ready_timeout_s = ready_timeout_s or params.ready_timeout_s
        self._process: proc.BackgroundProcess | None = None
        self.load_seconds: float = 0.0

    def __enter__(self) -> LlamaServer:
        argv = [self._binary, *self._params.server_argv(self._model_path)[1:]]
        self._process = proc.spawn(argv, cwd=self._log_path.parent, log_path=self._log_path)
        self.load_seconds = self._wait_ready()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self.stop()

    def stop(self) -> None:
        """关闭服务进程（幂等）。"""
        if self._process is not None:
            self._process.terminate()
            self._process = None

    def peak_memory_gib(self) -> float | None:
        """当前进程的生命周期内存峰值（GiB）。"""
        if self._process is None:
            return None
        return proc.peak_memory_gib(self._process.pid())

    def _wait_ready(self) -> float:
        started = time.monotonic()
        while time.monotonic() - started < self._ready_timeout_s:
            if self._process is not None and not self._process.is_running():
                msg = f"llama-server 提前退出，见日志：{self._log_path}"
                raise BenchError(msg)
            try:
                payload = _request(self._params, "GET", "/health", timeout_s=2.0)
            except BenchError:
                time.sleep(0.25)
                continue
            if payload.get("status") == "ok":
                return time.monotonic() - started
            time.sleep(0.25)
        msg = f"llama-server 未在 {self._ready_timeout_s:.0f}s 内就绪，见日志：{self._log_path}"
        raise BenchError(msg)


def chat(params: RunParams, prompt: str, *, max_tokens: int | None = None) -> ChatResult:
    """发送一次补全请求（温度固定为 0，保证可比）。"""
    payload = {
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.0,
        "max_tokens": max_tokens or params.max_tokens,
        "stream": False,
    }
    started = time.monotonic()
    body = _request(
        params,
        "POST",
        "/v1/chat/completions",
        payload=payload,
        timeout_s=params.request_timeout_s,
    )
    elapsed = time.monotonic() - started

    choices = cast("list[dict[str, object]]", body.get("choices") or [])
    if not choices:
        msg = "服务端返回中没有 choices"
        raise BenchError(msg)
    choice = choices[0]
    message = cast("dict[str, object]", choice.get("message") or {})
    usage = cast("dict[str, object]", body.get("usage") or {})
    return ChatResult(
        finish_reason=str(choice.get("finish_reason") or "unknown"),
        content=str(message.get("content") or ""),
        prompt_tokens=int(cast("int", usage.get("prompt_tokens") or 0)),
        completion_tokens=int(cast("int", usage.get("completion_tokens") or 0)),
        elapsed_s=elapsed,
    )


def _request(
    params: RunParams,
    method: str,
    path: str,
    *,
    payload: dict[str, object] | None = None,
    timeout_s: float,
) -> dict[str, object]:
    """向回环地址上的服务端发送一次 JSON 请求。"""
    connection = http.client.HTTPConnection(params.host, params.port, timeout=timeout_s)
    try:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"} if body is not None else {}
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read()
        if response.status != 200:
            msg = f"{path} 返回 {response.status}：{raw[:200]!r}"
            raise BenchError(msg)
        parsed = json.loads(raw)
    except OSError as exc:
        msg = f"请求 {path} 失败：{exc}"
        raise BenchError(msg) from exc
    finally:
        connection.close()
    if not isinstance(parsed, dict):
        msg = f"{path} 返回的不是 JSON 对象"
        raise BenchError(msg)
    return cast("dict[str, object]", parsed)


__all__ = [
    "ChatResult",
    "EvalTiming",
    "LlamaServer",
    "RepeatTimings",
    "chat",
    "parse_model_load_seconds",
    "parse_server_info",
    "parse_timings",
]
