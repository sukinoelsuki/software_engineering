"""基准模块单测的共用构造器。

刻意**手写**记录而不调用生产代码来构造：测试要能发现"生产代码写出来的结构与
schema 不一致"，用生产代码构造就等于让被测对象自己出题。

另有一个进程边界替身（``popen_spy``）：证明"传给常驻子进程的环境到底是什么"
——本环境没有 ``llama-server`` 二进制与模型，只能在**调用边界**上取证。
"""

from __future__ import annotations

import statistics
import subprocess
from typing import Any

import pytest


def make_series(values: list[float]) -> dict[str, Any]:
    """构造一个与 :func:`agent_sec_perf.bench.stats.summarize` 口径一致的汇总量。"""
    ordered = sorted(values)
    median = statistics.median(values)
    spread = 0.0 if median == 0 else (ordered[-1] - ordered[0]) / median * 100.0
    return {
        "n": len(values),
        "median": round(median, 4),
        "min": round(ordered[0], 4),
        "max": round(ordered[-1], 4),
        "spread_pct": round(spread, 2),
        "values": [round(value, 4) for value in values],
    }


def make_record(
    *,
    status: str = "complete",
    label: str = "nightly",
    threads: int = 8,
    prefill_values: list[float] | None = None,
    finish_reasons: list[str] | None = None,
    isolation: str = "user",
    tiers: tuple[str, ...] = ("S", "M"),
) -> dict[str, Any]:
    """构造一条通过 schema 校验的轮次记录。"""
    prefill = prefill_values or [100.0, 102.0, 98.0]
    gen = [20.0, 20.5, 19.5]
    elapsed = [50.0, 51.0, 49.0]
    reasons = finish_reasons or ["stop"] * len(prefill)
    perf: dict[str, Any] = {}
    capability: dict[str, Any] = {}
    for tier in tiers:
        perf[tier] = {
            "repeats": len(prefill),
            "prefill_tok_per_s": make_series(list(prefill)),
            "gen_tok_per_s": make_series(list(gen)),
            "request_elapsed_s": make_series(list(elapsed)),
            "load_seconds": 1.28,
            "load_spread_pct": 6.1,
            "model_load_seconds_log": 1.3,
            "peak_memory_gib": 2.68,
            "valid_prefill_repeats": len(prefill),
            "server": {"n_slots": 1, "n_ctx_slot": 4096},
        }
        capability[tier] = {
            "round_pass_rate": 0.5,
            "tasks": {
                "t1": {
                    "passed": 2,
                    "n": 3,
                    "rate": 0.6667,
                    "finish_reasons": list(reasons),
                    "verdicts": [{"task": "t1", "passed": True}],
                }
            },
        }
    return {
        "env": {
            "protocol": "bench-v2",
            "round_id": "2026-09-17-nightly",
            "started_at": "2026-09-17T04:00:00+08:00",
            "finished_at": "2026-09-17T04:50:00+08:00",
            "duration_s": 3000.0,
            "status": status,
            "errors": [] if status == "complete" else ["S 档失败：示例"],
            "isolation": isolation,
            "params": {
                "ctx": 4096,
                "threads": threads,
                "repeats": len(prefill),
                "tiers": list(tiers),
                "host": "127.0.0.1",
                "port": 8080,
                "max_tokens": 2048,
                "label": label,
                "isolation": isolation,
            },
            "trigger": {
                "event": "crontab",
                "branch": "bench/nightly",
                "commit": "0123456789abcdef",
                "is_cron": "true",
                "build_id": "123",
                "build_url": "",
            },
            "runner": {
                "cpus": "8",
                "memory_gib": "16.0",
                "nproc": 8,
                "cpu_model": "Example CPU @ 2.50GHz",
            },
            "llama": {
                "binary": "/opt/llama.cpp/build/bin/llama-server",
                "version": "version: 0.4.1-dev",
            },
            "python": "3.12.14",
            "models": {
                "S": {"file": "MiniCPM5-2B-Q4_K_M.gguf", "sha256": "a" * 64, "quant": "Q4_K_M"},
                "M": {"file": "Qwen3-4B-Q4_K_M.gguf", "sha256": "b" * 64, "quant": "Q4_K_M"},
            },
        },
        "perf": perf,
        "capability": capability,
    }


@pytest.fixture
def valid_record() -> dict[str, Any]:
    """一条干净的轮次记录。"""
    return make_record()


@pytest.fixture
def popen_spy(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """替换 ``subprocess.Popen``：记录每次启动的 argv 与 ``env``，不真的起进程。

    返回的列表按启动顺序累积；``entry["env"]`` 就是**将要交给子进程**的环境
    （T-08 的取证点：凭据是否进入了子进程环境）。
    """
    captured: list[dict[str, Any]] = []

    class _Spy:
        """``Popen`` 的最小替身：只保留 :class:`BackgroundProcess` 用到的方法。"""

        def __init__(self, argv: list[str], **kwargs: object) -> None:
            captured.append({"argv": argv, "env": kwargs.get("env")})
            self.pid = 4242

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            return None

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def kill(self) -> None:
            return None

    monkeypatch.setattr(subprocess, "Popen", _Spy)
    return captured
