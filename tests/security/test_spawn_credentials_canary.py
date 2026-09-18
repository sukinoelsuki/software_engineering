"""T-08（威胁模型 T-08 凭据隔离）：spawn 出的常驻子进程不得继承父进程凭据。

背景（已核实的真实缺陷，见 ``bench/runner.py:122`` 与 ``foundation/proc.py:235``）：

* ``proc.spawn`` 默认 ``env=dict(os.environ) if env is None``，即**全量继承**父环境；
* ``bench/runner.py:122`` 是全仓**唯一**的 ``spawn`` 调用点，且**未传 ``env``**；
* CI 夜轮拉起的常驻 ``llama-server`` 因此继承了含 ``CNB_TOKEN`` 的完整环境 ⇒ 凭据泄漏。

本用例把该缺陷钉成**确定性 canary**：

* 构造一个**合成哨兵**环境变量（命名含 ``synthetic-canary``，绝不包含真实令牌/凭据）；
* 以「未传 env」的方式调用 ``proc.spawn``（复刻 ``runner.py:122`` 的调用形态）；
* 断言子进程环境中**不含**该哨兵。

历史与本用例的当前形态：缺陷当时确实存在（``spawn`` 默认全量继承 ``os.environ``，且唯一的
调用点 ``runner.py:122`` 未传 ``env``），子进程**会**带上哨兵 ⇒ 断言失败 ⇒
``xfail(strict=True)`` 把它记为「预期失败」，**不红**（**曾以 xfail 钉住**这件事本身就是
缺陷当时存在的证据）。2026-09-18 随修复（``spawn`` 默认改为最小环境 + ``runner.py:122``
显式传最小 env）**翻正为常态断言**，`xfail` 标记已移除。

变异验证（修复后手动，仓库保持干净）：把 ``spawn`` 的默认改回继承父环境后，
子进程会重新拿到哨兵 ⇒ 本用例失败，证明它仍能钉住该缺陷。
"""

from __future__ import annotations

import pathlib
import sys
import time

import pytest

from agent_sec_perf.foundation import proc

# 合成哨兵：明确标识为 synthetic-canary，绝不包含任何真实令牌/凭据（SECURITY.md 要求）。
_SYNTHETIC_TOKEN = "synthetic-canary-CNB_TOKEN-0000000000000000"


@pytest.mark.security
def test_spawned_child_must_not_inherit_credentials(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """spawn 出的子进程环境不得含有合成哨兵凭据。

    复刻 ``runner.py:122`` 的调用形态（不传 env），子进程应只拿到最小/显式环境。
    """
    # 在当前进程注入合成哨兵，模拟 CI 环境中真实存在的 CNB_TOKEN。
    monkeypatch.setenv("CNB_TOKEN", _SYNTHETIC_TOKEN)

    log_path = tmp_path / "child.out"
    argv = [
        sys.executable,
        "-c",
        "import os,sys; sys.stdout.write(os.environ.get('CNB_TOKEN','') or '<absent>')",
    ]
    # 复刻 runner.py:122：不传 env，依赖 spawn 的默认行为。
    child = proc.spawn(argv, cwd=tmp_path, log_path=log_path)

    try:
        # 子进程打印极轻量，最多等 5 秒。
        deadline = time.monotonic() + 5.0
        while child.is_running() and time.monotonic() < deadline:
            time.sleep(0.02)
        output = log_path.read_text(encoding="utf-8", errors="replace")
    finally:
        child.terminate()

    # 关键断言：子进程环境绝不得出现该合成凭据。
    # 修复前此断言失败（由 xfail(strict) 记为预期失败）；修复后为常态断言。
    assert _SYNTHETIC_TOKEN not in output, (
        f"spawn 出的子进程继承了父进程凭据：在输出中发现合成哨兵 {_SYNTHETIC_TOKEN!r}。"
        "bench/runner.py:122 必须显式传最小 env，或 proc.spawn 默认改为最小环境。"
    )
