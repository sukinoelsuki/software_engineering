"""T-08 独立复核：spawn 默认最小环境的**运行时**验证（验证工程师独立用例）。

独立性说明（关键）：本用例**不**依赖任何 monkeypatch spy，也不复用实现者的断言逻辑。
它**真实**启动一个子进程，让子进程把自己的 ``os.environ`` 键集 dump 出来，再与验证工程师
**独立写死**的允许清单比对。这样即便实现者写的 `test_spawn_credentials_canary.py` /
`test_foundation_proc.py` 全部被删，本用例仍能抓住两类回归：

* 变异 A——把 ``spawn`` 默认改回 ``dict(os.environ)``（继承父环境）：子进程环境会多出父进程
  变量（含合成凭据哨兵）⇒ 与最小清单不相等、且出现凭据类键名。
* 变异 B——让 ``minimal_env`` 混入 ``os.environ``：子进程环境多出父进程变量 ⇒ 不相等。

合成哨兵 ``CNB_TOKEN`` 绝不含真实令牌（``SECURITY.md`` 强制要求）。

复现方式：

    PYTHONPATH=src .venv/bin/python -m pytest -q tests/security/test_t08_independent_spawn_env.py
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time

import pytest

from agent_sec_perf.foundation import proc

# 验证工程师独立写死的最小环境允许清单：不调用 minimal_env 取期望值，避免"用被测对象给自己出题"。
_EXPECTED_ENV_KEYS = frozenset(
    {"PATH", "HOME", "LANG", "LC_ALL", "PYTHONDONTWRITEBYTECODE", "PYTHONPATH"}
)

# 合成凭据哨兵：明确标识 synthetic，绝不包含任何真实令牌/凭据。
_CANARY_KEY = "CNB_TOKEN"
_CANARY_VALUE = "synthetic-canary-0000000000000000"


@pytest.mark.security
def test_spawned_child_env_is_exactly_minimal_whitelist(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """真实子进程的环境必须**恰好**等于最小清单：多/少任意键都失败（攻击者视角）。"""
    monkeypatch.setenv(_CANARY_KEY, _CANARY_VALUE)
    monkeypatch.setenv("SYNTHETIC_PARENT_ONLY", "1")

    log_path = tmp_path / "child.env.out"
    argv = [
        sys.executable,
        "-c",
        "import os,json,sys; sys.stdout.write(json.dumps(sorted(os.environ)))",
    ]
    child = proc.spawn(argv, cwd=tmp_path, log_path=log_path)
    try:
        deadline = time.monotonic() + 5.0
        while child.is_running() and time.monotonic() < deadline:
            time.sleep(0.02)
        payload = log_path.read_text(encoding="utf-8", errors="replace").strip()
    finally:
        child.terminate()

    child_keys = set(json.loads(payload))

    # 恰等：不多不少——任何多出来的父进程键都是新开的泄漏面。
    assert child_keys == _EXPECTED_ENV_KEYS, (
        "子进程环境键集与最小清单不符（T-08 默认最小环境被突破）：\n"
        f"  期望={sorted(_EXPECTED_ENV_KEYS)}\n  实际={sorted(child_keys)}"
    )
    # 凭据类键名不得出现（攻击者视角：TOKEN/KEY/SECRET 后缀）。
    leaked = [k for k in child_keys if k.upper().endswith(("TOKEN", "KEY", "SECRET"))]
    assert leaked == [], f"子进程环境出现凭据类键名：{leaked}"
    # 合成哨兵绝不可达子进程。
    assert _CANARY_KEY not in child_keys, "合成凭据哨兵泄漏进了子进程环境"
