"""子进程封装层：全项目唯一的进程启动点。

安全基线要求（``SECURITY.md`` / ``.codebuddy/rules/security-baseline``）：
任何子进程调用必须 (1) 不经 shell；(2) 参数以列表传入；(3) 位于统一封装层。
本模块就是那个封装层，因此 bandit 的 ``B404``（导入 subprocess）与
``B603``（subprocess 调用）豁免**只**出现在这里，理由与影响已登记在
``docs/adr/0014-benchmark-automation.md``，不做全局 ``skips``。

另外两个刻意的选择：

* 可执行文件一律用 :func:`resolve_binary` 解析为**绝对路径**再执行
  （既避免 ``B607``，也避免 PATH 被污染时执行到别的程序）；
* 隔离执行时**重建一份最小环境变量**，不继承父进程环境——CI 令牌等敏感变量
  因此不会进入任何执行模型产物的子进程。
"""

from __future__ import annotations

import os
import pathlib
import resource
import shutil
import subprocess  # nosec B404 —— 本模块是全项目唯一子进程封装层，导入 subprocess 即其职责（登记：ADR-0014 §2.9）
from collections.abc import Mapping
from dataclasses import dataclass

from agent_sec_perf.foundation.errors import IsolationError, ProtocolError

#: 隔离执行使用的非特权 uid/gid（nobody）。
UNPRIVILEGED_UID = 65534
UNPRIVILEGED_GID = 65534

#: 隔离执行的资源上限。内存维度**只能**用 setrlimit：本平台 cgroup 无 v2 委派，
#: ``--memory`` / ``--pids-limit`` 会静默失效（ADR-0007 §3.1）。
CPU_LIMIT_S = 300
ADDRESS_SPACE_LIMIT_B = 2 * 1024**3
FILE_SIZE_LIMIT_B = 64 * 1024**2
NOFILE_LIMIT = 256


@dataclass(frozen=True)
class CommandResult:
    """一次子进程执行的结果。"""

    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    def output(self) -> str:
        """合并 stdout 与 stderr（判定输出时通常需要一起看）。"""
        return f"{self.stdout}\n{self.stderr}".strip()


@dataclass
class BackgroundProcess:
    """后台进程的薄封装（llama-server 这类需要常驻的子进程）。"""

    _process: subprocess.Popen[bytes]

    def pid(self) -> int:
        """进程号（用于读取 /proc/<pid>/status 的内存峰值）。"""
        return self._process.pid

    def is_running(self) -> bool:
        """是否仍在运行。"""
        return self._process.poll() is None

    def terminate(self, timeout_s: float = 30.0) -> None:
        """先 SIGTERM，超时再 SIGKILL（不留僵尸进程）。"""
        if self._process.poll() is not None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=timeout_s)


def resolve_binary(name: str) -> str:
    """把可执行文件名解析为绝对路径。

    Raises:
        ProtocolError: 找不到该可执行文件（宁可直接失败，也不猜路径）。
    """
    found = shutil.which(name)
    if not found:
        msg = f"找不到可执行文件：{name}（请确认已安装且在 PATH 中）"
        raise ProtocolError(msg)
    return found


def _isolated_env(workdir: pathlib.Path) -> dict[str, str]:
    """隔离执行用的最小环境变量集合。

    刻意**不**继承 ``os.environ``：父进程里的令牌、凭据与网络代理配置都不会
    传递给执行模型产物的子进程。
    """
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(workdir),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(workdir),
    }


def _apply_limits() -> None:
    """在子进程 exec 之前收紧资源上限。"""
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_LIMIT_S, CPU_LIMIT_S))
    resource.setrlimit(resource.RLIMIT_AS, (ADDRESS_SPACE_LIMIT_B, ADDRESS_SPACE_LIMIT_B))
    resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_SIZE_LIMIT_B, FILE_SIZE_LIMIT_B))
    resource.setrlimit(resource.RLIMIT_NOFILE, (NOFILE_LIMIT, NOFILE_LIMIT))


def run(
    argv: list[str],
    *,
    cwd: pathlib.Path,
    timeout_s: float,
    isolation: str = "user",
) -> CommandResult:
    """执行一个命令并收集输出（不经 shell，参数以列表传入）。

    Args:
        argv: 命令与参数列表；``argv[0]`` 应为绝对路径。
        cwd: 工作目录（隔离执行时会被当作 HOME）。
        timeout_s: 超时时间（超时即失败，不重试：重试会掩盖问题）。
        isolation: ``user`` 以非特权 uid + 资源上限 + 最小环境执行；
            ``root`` 用于本地开发，**在 CI 中不得使用**。

    Raises:
        IsolationError: 无法切换到非特权用户（不得静默回退）。
        ProtocolError: 超时或命令无法启动。
    """
    isolated = isolation == "user"
    env = _isolated_env(cwd) if isolated else dict(os.environ)

    try:
        # 安全豁免说明（ruff S603 / bandit B603）：本模块是全项目**唯一**的进程启动点，
        # 且每次调用都不经 shell、参数以列表传入、执行前先施加隔离与资源上限。
        # 豁免已登记：docs/adr/0014-benchmark-automation.md。
        completed = subprocess.run(  # noqa: S603 —— 不经 shell、参数为列表；执行前施加非特权 uid + rlimit + 最小环境（登记：ADR-0014 §2.9）
            argv,
            cwd=str(cwd),
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            preexec_fn=_apply_limits if isolated else None,
            user=UNPRIVILEGED_UID if isolated else None,
            group=UNPRIVILEGED_GID if isolated else None,
        )  # nosec B603
    except PermissionError as exc:
        msg = f"无法以非特权用户执行（隔离失败，不得回退）：{argv[0]}"
        raise IsolationError(msg) from exc
    except subprocess.TimeoutExpired as exc:
        msg = f"命令超时（{timeout_s}s）：{' '.join(argv)}"
        raise ProtocolError(msg) from exc
    except OSError as exc:
        msg = f"命令无法启动：{' '.join(argv)}（{exc}）"
        raise ProtocolError(msg) from exc

    return CommandResult(
        argv=tuple(argv),
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def run_inherit_env(
    argv: list[str],
    *,
    cwd: pathlib.Path,
    timeout_s: float,
) -> CommandResult:
    """执行一个**可信**工具命令（不隔离，继承当前环境）。

    仅用于我们自己的工具链（``mypy`` 等不执行被测代码的静态检查）。
    凡是要执行模型产物的调用，必须走 :func:`run`。
    """
    try:
        completed = subprocess.run(  # noqa: S603 —— 仅执行自有工具链静态检查、不执行模型产物；不经 shell、参数为列表（登记：ADR-0014 §2.9）
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )  # nosec B603
    except subprocess.TimeoutExpired as exc:
        msg = f"命令超时（{timeout_s}s）：{' '.join(argv)}"
        raise ProtocolError(msg) from exc
    except OSError as exc:
        msg = f"命令无法启动：{' '.join(argv)}（{exc}）"
        raise ProtocolError(msg) from exc
    return CommandResult(
        argv=tuple(argv),
        returncode=completed.returncode,
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


def spawn(
    argv: list[str],
    *,
    cwd: pathlib.Path,
    log_path: pathlib.Path,
    env: Mapping[str, str] | None = None,
) -> BackgroundProcess:
    """启动常驻子进程，输出重定向到日志文件。

    日志文件是性能数据的主证据（``slot print_timing`` 行），因此必须先于进程创建。
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handle = log_path.open("wb")
    try:
        process = subprocess.Popen(  # noqa: S603 —— 启动常驻 llama-server（自有二进制、绝对路径）；不经 shell、参数为列表（登记：ADR-0014 §2.9）
            argv,
            cwd=str(cwd),
            stdout=handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=dict(os.environ) if env is None else dict(env),
        )  # nosec B603
    finally:
        handle.close()
    return BackgroundProcess(_process=process)


def peak_memory_gib(pid: int) -> float | None:
    """读取 ``/proc/<pid>/status`` 的 ``VmHWM``（进程生命周期内存峰值，GiB）。"""
    status = pathlib.Path(f"/proc/{pid}/status")
    try:
        for line in status.read_text(encoding="utf-8").splitlines():
            if line.startswith("VmHWM:"):
                return int(line.split()[1]) / (1024 * 1024)
    except OSError:
        return None
    return None


def gib(value: float) -> str:
    """把 GiB 数值格式化为便于阅读的字符串。"""
    return f"{value:.2f} GiB"


__all__ = [
    "UNPRIVILEGED_GID",
    "UNPRIVILEGED_UID",
    "BackgroundProcess",
    "CommandResult",
    "gib",
    "peak_memory_gib",
    "resolve_binary",
    "run",
    "run_inherit_env",
    "spawn",
]
