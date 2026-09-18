"""服务端日志解析：一个真实的踩坑点。

2026-09-16 首次试跑得到了一条极差 326% 的 prefill 序列。根因是：同一槽位上重复发送
同一提示词时，服务端复用 KV 前缀，日志给出的 ``prompt eval time = 34.89 ms / 1 tokens``
**不是** prefill 测量。所以解析结果必须带上"被评估的 token 数"，
让上层能拒绝这种行——这个测试锁住的就是这件事。
"""

from __future__ import annotations

import pathlib

import pytest

from agent_sec_perf.bench.protocol import RunParams
from agent_sec_perf.bench.runner import (
    LlamaServer,
    parse_model_load_seconds,
    parse_server_info,
    parse_timings,
)
from agent_sec_perf.foundation import proc

#: 取自 2026-09-16 的实际日志（首条为真实 prefill，后两条为缓存命中）
REAL_LOG_SNIPPET = """
0.01.138.650 I srv    load_model: initializing, n_slots = 1, n_ctx_slot = 4096, kv_unified = 'true'
0.06.193.153 I slot print_timing: id  0 | task 0 | prompt eval time =    3954.18 ms /   462 tokens (    8.56 ms per token,   116.84 tokens per second)
0.06.193.204 I slot print_timing: id  0 | task 0 |        eval time =    2911.41 ms /    83 tokens (   35.51 ms per token,    28.17 tokens per second)
0.07.498.684 I slot print_timing: id  0 | task 1 | prompt eval time =      34.89 ms /     1 tokens (   34.89 ms per token,    28.66 tokens per second)
0.07.499.100 I slot print_timing: id  0 | task 1 |        eval time =     900.10 ms /    25 tokens (   36.00 ms per token,    27.78 tokens per second)
"""


@pytest.mark.unit
def test_parse_timings_keeps_evaluated_token_counts() -> None:
    """每条计时都要带上被评估的 token 数（缓存命中与真实 prefill 的区别所在）。"""
    timings = parse_timings(REAL_LOG_SNIPPET)

    assert [item.tokens for item in timings.prefill] == [462, 1]
    assert timings.prefill[0].tok_per_s == pytest.approx(116.84)
    assert timings.prefill[1].tok_per_s == pytest.approx(28.66)


@pytest.mark.unit
def test_prefill_and_generation_are_not_mixed() -> None:
    """反向后行断言必须把 generation 行排除在 prefill 之外（否则 prefill 会被统计两次）。"""
    timings = parse_timings(REAL_LOG_SNIPPET)

    assert len(timings.prefill) == 2
    assert len(timings.gen) == 2
    assert all(item.tokens < 100 for item in timings.gen)


@pytest.mark.unit
def test_parse_server_info_reads_slot_layout() -> None:
    """槽位口径要能被读出来（V-14：正式基准需显式固定 -np 1）。"""
    info = parse_server_info(REAL_LOG_SNIPPET)

    assert info == {"n_slots": 1, "n_ctx_slot": 4096}


@pytest.mark.unit
def test_parse_server_info_on_log_without_slot_line() -> None:
    """缺失槽位行时给出 None，而不是编造一个默认值。"""
    assert parse_server_info("nothing here") == {"n_slots": None, "n_ctx_slot": None}


@pytest.mark.unit
def test_parse_model_load_seconds_returns_none_without_line() -> None:
    """加载耗时行缺失时为 None。"""
    assert parse_model_load_seconds("nothing here") is None


@pytest.mark.unit
def test_llama_server_spawn_passes_minimal_env(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    popen_spy: list[dict[str, object]],
) -> None:
    """服务端启动时**传给子进程**的环境必须是清洁的最小环境（T-08 的调用点一层）。

    与 ``tests/security/test_spawn_env_explicit.py``（静态守卫：调用点必须写 ``env=``）
    刻意互补——这里证明"传出去的确实是最小环境"，那里证明"调用点写了 ``env=``"。
    注意反方向变异：只把调用点的 ``env=`` 删掉**不会**让本用例变红（``spawn`` 的默认
    已是最小环境），这正是"显式 + 默认"两层并存的意义。

    取证边界：本环境没有 ``llama-server`` 二进制与模型，因此这里看到的是**交给
    ``Popen`` 的环境**，不是真实服务进程实际看到的环境。
    """
    monkeypatch.setenv("CNB_TOKEN", "synthetic-canary-0000000000000000")
    params = RunParams()
    log_dir = tmp_path / "logs"
    monkeypatch.setattr(LlamaServer, "_wait_ready", lambda _self: 0.0)

    with LlamaServer(
        params=params,
        binary="/usr/local/bin/llama-server",
        model_path=tmp_path / "model.gguf",
        log_path=log_dir / "server.out",
    ):
        pass

    recorded_env = popen_spy[-1]["env"]
    assert isinstance(recorded_env, dict)
    assert recorded_env == proc.minimal_env(log_dir)
    assert "CNB_TOKEN" not in recorded_env
