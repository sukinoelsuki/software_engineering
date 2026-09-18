"""T-08（威胁模型 T-08 凭据隔离）：spawn 出的常驻子进程不得继承父进程凭据。

背景（已核实的真实缺陷，见 ``bench/runner.py:122`` 与 ``foundation/proc.py:235``）：

* ``proc.spawn`` 默认 ``env=dict(os.environ) if env is None``，即**全量继承**父环境；
* ``bench/runner.py:122`` 是全仓**唯一**的 ``spawn`` 调用点，且**未传 ``env``**；
* CI 夜轮拉起的常驻 ``llama-server`` 因此继承了含 ``CNB_TOKEN`` 的完整环境 ⇒ 凭据泄漏。

本用例把该缺陷钉成**确定性 canary**：

* 构造一个**合成哨兵**环境变量（命名含 ``synthetic-canary``，绝不包含真实令牌/凭据）；
* 以「未传 env」的方式调用 ``proc.spawn``（复刻 ``runner.py:122`` 的调用形态）；
* 断言子进程环境中**不含**该哨兵。

因为缺陷当前存在（默认全量继承），子进程**会**带上哨兵，断言失败 ⇒
``xfail(strict=True)`` 把它记为「预期失败」，**不红**。
一旦缺陷被修复（``runner.py:122`` 显式传最小 env，或 ``spawn`` 默认改最小 env），
断言将转为通过 ⇒ xfail(strict) 报 XPASS 并**失败**，强制把本用例翻正为普通断言。

变异验证（开发期手动，仓库保持干净）：临时把 ``spawn`` 默认改为最小环境后，
该用例会转为 XPASS(strict) 失败，证明它能感知修复——见回报 §变异证据。
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
@pytest.mark.xfail(
    strict=True,
    reason=(
        "bench/runner.py:122 未传 env，proc.spawn 默认全量继承 os.environ，"
        "CI 夜轮 llama-server 继承含 CNB_TOKEN 的完整环境（凭据泄漏缺陷，待修复）"
    ),
)
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
    # 缺陷存在时此断言失败 ⇒ xfail 捕获；修复后通过 ⇒ xfail(strict) 翻红强制翻正。
    assert _SYNTHETIC_TOKEN not in output, (
        f"spawn 出的子进程继承了父进程凭据：在输出中发现合成哨兵 {_SYNTHETIC_TOKEN!r}。"
        "bench/runner.py:122 必须显式传最小 env，或 proc.spawn 默认改为最小环境。"
    )
