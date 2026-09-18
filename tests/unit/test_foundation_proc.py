"""子进程封装层的环境策略：最小环境是**唯一**入口，且**默认拒绝**（威胁模型 T-08）。

T-08 的根因不是"某个调用点忘了传参"，而是 ``spawn`` 的默认值（``env is None`` ⇒
``dict(os.environ)``）本身是**默认放行**：常驻的第三方二进制（``llama-server``）因此把
CI 凭据 ``CNB_TOKEN`` 一起带进了进程环境。修复做两件事——把默认翻转成
:func:`agent_sec_perf.foundation.proc.minimal_env`，并在唯一的调用点**显式**写出来。

本文件锁三件事（期望值由测试侧**独立**写死，避免"用被测对象给自己出题"）：

1. :func:`minimal_env` 的键与取值**恰好**等于允许清单；
2. :func:`spawn` 不传 ``env`` 时只给最小环境；显式 ``env=dict(os.environ)`` 才继承；
3. :func:`run` 的既有语义（``user`` 用最小环境 / ``root`` 继承）**未被本次改动波及**。

已知取证边界（不得省略）：本环境没有 ``llama-server`` 二进制与模型，因此这些用例证明的
是**传给 ``Popen`` 的环境**，不是真实服务进程实际看到的环境。
"""

from __future__ import annotations

import os
import pathlib
import subprocess

import pytest

from agent_sec_perf.foundation import proc

#: 允许清单的**固定**部分（另外两个键随工作目录变化，见下）。独立于被测代码写死：
#: 若直接调用 ``minimal_env`` 来取期望值，断言就退化为自证。
EXPECTED_FIXED_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
    "PYTHONDONTWRITEBYTECODE": "1",
}

#: 合成哨兵：模拟 CI 里的凭据变量，**不得**使用任何真实令牌（``SECURITY.md``）。
CANARY_KEY = "CNB_TOKEN"
CANARY_VALUE = "synthetic-canary-0000000000000000"


class _CompletedProcessSpy:
    """``subprocess.run`` 的替身返回值。"""

    returncode = 0
    stdout = ""
    stderr = ""


@pytest.fixture
def run_spy(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """替换 ``subprocess.run``：记录每次执行的 ``env``，不真的执行任何东西。"""
    captured: list[dict[str, object]] = []

    def _fake_run(argv: list[str], **kwargs: object) -> _CompletedProcessSpy:
        captured.append({"argv": argv, "env": kwargs.get("env")})
        return _CompletedProcessSpy()

    monkeypatch.setattr(subprocess, "run", _fake_run)
    return captured


@pytest.mark.unit
def test_minimal_env_is_exactly_the_allowed_whitelist(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """键与取值**恰好**等于允许清单：多出任何一个父进程变量，本用例即失败。

    用"恰好相等"而不是"包含"：这份环境是**唯一**策略的载体，多一个键就是新开了一个
    泄漏面（例如有人顺手把 ``LD_*`` 或代理变量加回来）。
    """
    monkeypatch.setenv(CANARY_KEY, CANARY_VALUE)
    monkeypatch.setenv("SYNTHETIC_PARENT_ONLY", "1")

    assert proc.minimal_env(tmp_path) == {
        **EXPECTED_FIXED_ENV,
        "HOME": str(tmp_path),
        "PYTHONPATH": str(tmp_path),
    }


@pytest.mark.unit
def test_minimal_env_exposes_no_credential_like_keys(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """攻击者视角：即便父进程里塞满凭据类变量，最小环境里也不得出现任何凭据键名。"""
    monkeypatch.setenv(CANARY_KEY, CANARY_VALUE)
    monkeypatch.setenv("SYNTHETIC_SECRET", "synthetic-secret-0000")

    env = proc.minimal_env(tmp_path)

    leaked = [key for key in env if key.upper().endswith(("TOKEN", "KEY", "SECRET"))]
    assert leaked == [], f"最小环境中出现了凭据类键名：{leaked}"


@pytest.mark.unit
def test_spawn_defaults_to_minimal_env_not_inherited(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    popen_spy: list[dict[str, object]],
) -> None:
    """**不传** ``env`` 的 ``spawn`` 只拿到最小环境——T-08 的根因在这一层被消除。"""
    monkeypatch.setenv(CANARY_KEY, CANARY_VALUE)
    log_path = tmp_path / "logs" / "server.out"

    child = proc.spawn(["/bin/true"], cwd=tmp_path, log_path=log_path)
    child.terminate()

    recorded_env = popen_spy[-1]["env"]
    assert isinstance(recorded_env, dict)
    assert recorded_env == proc.minimal_env(tmp_path)
    assert CANARY_KEY not in recorded_env


@pytest.mark.unit
def test_spawn_inherits_only_when_env_is_explicitly_passed(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    popen_spy: list[dict[str, object]],
) -> None:
    """继承父环境的口子**仍在**，但必须由调用方显式写出（默认拒绝 ⇒ 显式选择）。"""
    monkeypatch.setenv(CANARY_KEY, CANARY_VALUE)

    proc.spawn(
        ["/bin/true"],
        cwd=tmp_path,
        log_path=tmp_path / "logs" / "server.out",
        env=dict(os.environ),
    )

    recorded_env = popen_spy[-1]["env"]
    assert isinstance(recorded_env, dict)
    assert recorded_env[CANARY_KEY] == CANARY_VALUE


@pytest.mark.unit
def test_run_user_isolation_still_uses_minimal_env(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, run_spy: list[dict[str, object]]
) -> None:
    """``run(isolation="user")`` 的既有语义未被本次改动波及：仍是清洁的最小环境。"""
    monkeypatch.setenv(CANARY_KEY, CANARY_VALUE)

    proc.run(["/bin/true"], cwd=tmp_path, timeout_s=1.0, isolation="user")

    recorded_env = run_spy[-1]["env"]
    assert isinstance(recorded_env, dict)
    assert recorded_env == proc.minimal_env(tmp_path)
    assert CANARY_KEY not in recorded_env


@pytest.mark.unit
def test_run_root_isolation_still_inherits_parent_env(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, run_spy: list[dict[str, object]]
) -> None:
    """``isolation="root"`` 的继承行为是**回归锁**，不是认可。

    本次只按裁决改 ``spawn`` 的默认与唯一调用点，``run()`` 的语义**刻意不动**；
    该路径的继承凭据属于 T-08 未缓解的残余（见威胁模型），因此本用例只在"有人顺手
    改掉它"时发出信号——它**不**构成"该路径已安全"的结论。
    """
    monkeypatch.setenv(CANARY_KEY, CANARY_VALUE)

    proc.run(["/bin/true"], cwd=tmp_path, timeout_s=1.0, isolation="root")

    recorded_env = run_spy[-1]["env"]
    assert isinstance(recorded_env, dict)
    assert recorded_env[CANARY_KEY] == CANARY_VALUE
