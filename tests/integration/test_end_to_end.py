"""真模型端到端用例：让"能跑起来"成为**可复现证据**（``tests/integration/``）。

被测对象是**产品自己的路径**：``agent_sec_perf.cli.app`` 的 :class:`RunRequest` +
:func:`~agent_sec_perf.cli.app.execute`（契约 ``docs/design/interfaces/harness.md``
§5.1 的装配清单 / §5.2 的退出码表与输出通道两条硬规定），**不是**直接调
``harness.session.Session``。真实 ``llama-server`` + 真实 GGUF，**全程无替身**：
本阶段环境已实测可用（``docs/devlog/0017`` §2.2）。

覆盖（契约条目逐条对应）：

1. **文本模式**：退出码 ``0``（``TASK_FINISHED.status is COMPLETED``）；事件流里
   **恰好一条** ``TASK_FINISHED`` 且**在最后**（``I6``）；``seq`` 从 ``0`` 连续无空洞
   （``I9``）；审计**真实落盘**且**每行可** ``json.loads``；
2. **JSON 模式**（``--output-format json``）：stdout **只出 JSONL**（逐行 ``json.loads``
   成功、无任何非 JSON 行），且行数与事件数相等（一条不漏、一条不多）；
3. **无凭据泄漏**：事件 / 审计 / 两条输出流里都不出现凭据标记，且子进程环境 canary
   不回流（与 ``tests/security/test_spawn_credentials_canary.py`` 同一取证取向，
   但此处只做**我方通道**的检查，安全断言仍归 ``tests/security/``）。

**默认不跑（两条门槛，二者都满足才执行）**：

* 标 ``slow``：``pyproject.toml`` 已注册该 marker，但 ``make test`` 目前只排除
  ``benchmark`` ⇒ **``slow`` 尚未被默认排除**。把 ``slow`` 并入排除集属
  "**改 CI 门禁强度（F 类）**"，必须由所有者事先确认 ⇒ 本文件**不**去改
  ``pyproject.toml`` / ``Makefile``；
* 环境变量开关 ``AGENT_SEC_PERF_E2E=1``：**未设置即 skip**。⇒ 在本仓库当前门禁下，
  "标 ``slow`` + 默认不跑"这一对要求是**靠用例自带的 skip 实现**的，而不是靠
  ``-m`` 的排除集；这一点**必须在仓库里如实登记**，不得表述为"门禁已排除 slow"。

**注入与隔离（不绕过任何校验）**：``working_dir`` / ``allowed_roots`` 用 ``tmp_path``；
审计落点用 ``JsonlAuditSink(tmp_path/"audit", roots=(tmp_path,))`` —— 走**构造器自带的
白名单参数**，既不改 ``ALLOWED_AUDIT_ROOTS`` 常量，也不把越界路径硬塞进去；模型日志落
``tmp_path``；硬超时（``signal.setitimer``）与端口探测双重兜底，进程泄漏会被判为失败。

跑法（唯一入口，默认不跑）::

    AGENT_SEC_PERF_E2E=1 uv run pytest tests/integration/test_end_to_end.py -m integration

**观测缝（如实登记）**：文本模式 stdout 只渲染部分事件（``render_event`` 对
"无正文的 ``MODEL_RESPONSE``"等返回 ``None``），**无法**从渲染行恢复 ``seq``。
故本模块在文本模式下用 ``monkeypatch`` 给 ``cli.app._write_event`` 装一个**只记录、
不改行为**的观察者，取得"CLI 实际消费的那条事件流"。它不改任何校验、不替换模型，
只是把不可见的事件流变成可断言的对象。
"""

from __future__ import annotations

import io
import json
import os
import signal
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

from agent_sec_perf.cli import app as cli_app
from agent_sec_perf.cli.app import EXIT_OK, RunRequest, execute
from agent_sec_perf.cli.render import serialize_event
from agent_sec_perf.contracts.harness import SessionEvent, SessionEventKind, TaskStatus
from agent_sec_perf.foundation.config import AppConfig, PolicyConfig
from agent_sec_perf.observability.audit import JsonlAuditSink

pytestmark = [pytest.mark.integration, pytest.mark.slow]

#: 环境变量开关：未设置（或不是 ``1``）⇒ 本模块整体 skip（见模块 docstring 的"默认不跑"）。
_ENABLE_ENV: Final = "AGENT_SEC_PERF_E2E"

#: 已实测存在的本地后端与模型（``docs/devlog/0017`` §2.2）。
_LLAMA_SERVER_BINARY: Final = Path("/opt/llama.cpp/build/bin/llama-server")

#: 候选模型，按顺序取**第一个存在**的。两者都是已实测的本地 GGUF（同 §2.2）。
_MODEL_CANDIDATES: Final = (
    Path("/opt/models/Qwen3-4B-Q4_K_M.gguf"),
    Path("/opt/models/MiniCPM5-2B-Q4_K_M.gguf"),
)

_HOST: Final = "127.0.0.1"

#: 硬超时：``signal.setitimer`` 兜底。内层已有各自的上限（就绪 60s + 每次模型调用 60s），
#: 这里只是"绝不挂住"的最后一层；正常一轮远小于它。
_HARD_TIMEOUT_S: Final = 600.0

#: 触发一次真实工具调用所需的最小步数预算（读文件 → 复述内容 → 收尾）。
_MAX_STEPS: Final = 4
_TOOL_TIMEOUT_S: Final = 15.0

#: 工作目录里预置的哨兵文件（用于证明"工具真的读到了磁盘上的内容"）。
_SENTINEL_FILE: Final = "hello.txt"
_SENTINEL: Final = "E2E-SENTINEL-7f3a"

#: 环境变量 canary：它**不应**出现在任何我方通道里（``proc.minimal_env`` 不继承父环境）。
_CANARY_ENV: Final = "AGENT_SEC_PERF_E2E_CANARY"
_CANARY_VALUE: Final = "CANARY-9f3a7c21-e2e-should-never-appear"

#: 任务文本：明确要求"必须真的调用工具"，否则审计面为空、用例也拿不到"能跑起来"的证据。
_TASK: Final = (
    "工作目录下有一个文件 hello.txt。请调用 read_file 工具读取它（path 取值为 hello.txt），"
    "然后把文件内容原样作为最终回答。必须先调用工具，不要凭猜测回答。"
)

#: 凭据标记（**小写**比较；这些串都不应出现在我方通道里）。
_CREDENTIAL_MARKERS: Final = (
    "sk-",
    "bearer ",
    "authorization",
    "api_key",
    "apikey",
    "password",
    "passwd",
    "secret",
    "credential",
    "private_key",
    "-----begin",
)

#: 状态行前缀（文本模式用于确认"只有一条终态行"，即渲染侧的 ``I6``）。
_STATUS_PREFIXES: Final = tuple(f"{status.value}：" for status in TaskStatus)


class _HardTimeout(BaseException):
    """硬超时（**刻意不继承** ``Exception``）。

    理由：:func:`~agent_sec_perf.cli.app.execute` 内部把所有 ``Exception`` 收敛为退出码
    （"不抛异常"是它的契约）。若超时异常继承 ``Exception``，它会被当作"未预期异常"吞掉并
    返回 ``5`` —— 用例仍会红，但**看不到"超时"这一事实**。不继承 ``Exception`` ⇒ 直接冒到
    用例，失败信息明确。
    """


@dataclass(frozen=True)
class _SessionOutcome:
    """一次真实会话的可断言快照（全部由**产品路径**产生，无替身）。"""

    exit_code: int
    stdout: str
    stderr: str
    events: tuple[SessionEvent, ...]
    audit_path: Path
    audit_lines: tuple[str, ...]
    model_log_path: Path
    working_dir: Path
    port: int


# ---------------------------------------------------------------------------
# 环境门槛
# ---------------------------------------------------------------------------


def _require_enabled() -> None:
    """环境变量开关：未设置即 skip（见模块 docstring 的"默认不跑"）。"""
    if os.environ.get(_ENABLE_ENV) != "1":
        pytest.skip(f"需要真实模型：设置 {_ENABLE_ENV}=1 后再运行")


def _require_assets() -> tuple[Path, Path]:
    """校验本地后端与模型**确实存在**；缺一即**失败**（不是 skip）。

    理由（派活口径）：环境变量已显式打开时，"资产不存在"属**环境自助检失败**——
    静默 skip 会让"以为跑了真实端到端、其实什么都没跑"与"确实跑过了"同形。
    这里选择响亮失败并给出可复现的下一步，而不是放宽门槛。
    """
    if not _LLAMA_SERVER_BINARY.is_file():
        pytest.fail(
            f"环境自助检失败：找不到本地后端 {_LLAMA_SERVER_BINARY}"
            "（见 docs/devlog/0017 §2.2 的环境事实）",
            pytrace=False,
        )
    for candidate in _MODEL_CANDIDATES:
        if candidate.is_file():
            return _LLAMA_SERVER_BINARY, candidate
    missing = "、".join(str(path) for path in _MODEL_CANDIDATES)
    pytest.fail(f"环境自助检失败：候选模型都不存在（{missing}）", pytrace=False)


# ---------------------------------------------------------------------------
# 进程与端口工具（硬超时 / 清理取证）
# ---------------------------------------------------------------------------


@contextmanager
def _hard_timeout(seconds: float) -> Iterator[None]:
    """在**主线程**安装 ``SIGALRM`` 硬超时；非主线程退化为"依赖被测代码自身的超时"。"""
    if threading.current_thread() is not threading.main_thread() or not hasattr(signal, "SIGALRM"):
        yield
        return

    def _on_alarm(signum: int, frame: object) -> None:
        del signum, frame
        raise _HardTimeout(f"端到端会话超过硬超时 {seconds:.0f}s（进程可能未清理）")

    previous = signal.signal(signal.SIGALRM, _on_alarm)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous)


def _free_port(host: str) -> int:
    """取一个当前空闲的本地端口（避免与默认 8080 上的既有服务撞车）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def _can_bind(host: str, port: int) -> bool:
    """该端口是否**没有监听者**（``SO_REUSEADDR`` 下仍能 bind ⇒ 没有活着的监听套接字）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def _reap_llama_server(*, port: int, model_path: Path) -> tuple[int, ...]:
    """尽力回收本用例启动的 ``llama-server``（**兜底**，正常路径由 teardown 完成）。

    正常路径是 ``with Session(...)`` → ``Session.close()`` → ``ModelClient.close()``
    （契约 §2.9 的可观察两步）。硬超时那类异常路径会让 ``LocalLlamaClient._start`` 的
    清理分支走不到，于是留下一个常驻进程 ⇒ 这里按 **环境变量不可控的两个事实**
    （命令行里既有 ``-m <本次模型路径>`` 又有 ``--port <本次临时端口>``）定位并 SIGTERM。
    匹配条件刻意收紧到两条同时命中，避免误伤其它进程；非 Linux（无 ``/proc``）时为空操作。
    """
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return ()
    wanted_model = str(model_path)
    wanted_port = str(port)
    killed: list[int] = []
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        argv = [token.decode("utf-8", "replace") for token in raw.split(b"\0") if token]
        if wanted_model not in argv or "--port" not in argv:
            continue
        index = argv.index("--port")
        if argv[index + 1 : index + 2] != [wanted_port]:
            continue
        pid = int(entry.name)
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            continue
        killed.append(pid)
    return tuple(killed)


def _assert_port_released(*, port: int, timeout_s: float = 10.0) -> None:
    """断言该端口已无监听者（⇒ ``llama-server`` 已被清理，不只是"关掉了客户端"）。"""
    deadline = time.monotonic() + timeout_s
    while not _can_bind(_HOST, port):
        if time.monotonic() >= deadline:
            pytest.fail(f"llama-server 未被清理：{_HOST}:{port} 仍被占用", pytrace=False)
        time.sleep(0.1)


# ---------------------------------------------------------------------------
# 解析辅助
# ---------------------------------------------------------------------------


def _loads(text: str, *, context: str) -> dict[str, object]:
    """``json.loads`` 的带上下文包装：任何失败都带**位置**回报（便于定位坏行）。"""
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        pytest.fail(f"{context} 不是合法 JSON：{text!r}（{exc}）", pytrace=False)
    assert isinstance(parsed, dict), f"{context} 不是 JSON 对象：{type(parsed).__name__}"
    return parsed


def _jsonl(stdout: str) -> list[dict[str, object]]:
    """把 stdout 解析成 JSONL；**任何非 JSON 行都会让用例失败**（这正是被测的道口）。"""
    payloads: list[dict[str, object]] = []
    for number, line in enumerate(stdout.splitlines(), start=1):
        if not line.strip():
            continue
        payloads.append(_loads(line, context=f"stdout 第 {number} 行"))
    return payloads


def _event_text(event: SessionEvent) -> str:
    """事件的序列化文本（用**产品自己的** ``serialize_event``，与 JSON 模式同源）。"""
    return serialize_event(event)


# ---------------------------------------------------------------------------
# 真实会话
# ---------------------------------------------------------------------------


def _run_real_session(
    tmp_path_factory: pytest.TempPathFactory, *, output_format: str
) -> _SessionOutcome:
    """按契约 §5.1 驱动一次**真实**会话并返回可断言快照。

    注入的点只有两处，且都在 :func:`execute` 的公开签名内：``config``（能力授予按需显式
    给出，default-deny 的默认空集在这里**不**够用）与 ``sink``（落点必须能通过
    ``JsonlAuditSink`` 自己的白名单校验）。模型由 ``execute`` 用 ``RunRequest`` 的字段
    自己构造 —— **不走替身、不直接调 ``Session``**。
    """
    _require_enabled()
    binary, model_path = _require_assets()

    working_dir = tmp_path_factory.mktemp(f"e2e-{output_format}")
    (working_dir / _SENTINEL_FILE).write_text(f"{_SENTINEL}\n", encoding="utf-8")
    audit_dir = working_dir / "audit"
    sink = JsonlAuditSink(audit_dir, roots=(working_dir,))
    model_log = working_dir / "llama-server.log"
    port = _free_port(_HOST)

    request = RunRequest(
        task=_TASK,
        working_dir=working_dir,
        allowed_roots=(working_dir,),
        output_format=output_format,
        model_binary=str(binary),
        model_path=model_path,
        model_log=model_log,
        host=_HOST,
        port=port,
        max_steps=_MAX_STEPS,
        tool_timeout_s=_TOOL_TIMEOUT_S,
    )
    # 显式授予只读能力：BASIC 档的暴露面就是 read_file / list_dir（trimming 的档位预算），
    # 而 default-deny 的默认授予是空集（`AppConfig()`）⇒ 不显式给出的话每次调用都会被策略拒绝。
    config = AppConfig(policy=PolicyConfig(granted_capabilities=("read_file",)))

    out = io.StringIO()
    err = io.StringIO()
    events: list[SessionEvent] = []

    patch = pytest.MonkeyPatch()
    patch.setenv(_CANARY_ENV, _CANARY_VALUE)
    original_write = cli_app._write_event

    def _record(event: SessionEvent, *, stdout: io.StringIO, json_mode: bool) -> None:
        events.append(event)
        original_write(event, stdout=stdout, json_mode=json_mode)

    patch.setattr(cli_app, "_write_event", _record)
    try:
        with _hard_timeout(_HARD_TIMEOUT_S):
            exit_code = execute(request, stdout=out, stderr=err, config=config, sink=sink)
    finally:
        patch.undo()
        _reap_llama_server(port=port, model_path=model_path)
        _assert_port_released(port=port)

    audit_path = sink.path
    audit_lines = (
        tuple(audit_path.read_text(encoding="utf-8").splitlines()) if audit_path.is_file() else ()
    )
    return _SessionOutcome(
        exit_code=exit_code,
        stdout=out.getvalue(),
        stderr=err.getvalue(),
        events=tuple(events),
        audit_path=audit_path,
        audit_lines=audit_lines,
        model_log_path=model_log,
        working_dir=working_dir,
        port=port,
    )


@pytest.fixture(scope="module")
def text_session(tmp_path_factory: pytest.TempPathFactory) -> _SessionOutcome:
    """文本模式（默认输出格式）的一次真实会话。"""
    return _run_real_session(tmp_path_factory, output_format="text")


@pytest.fixture(scope="module")
def json_session(tmp_path_factory: pytest.TempPathFactory) -> _SessionOutcome:
    """``--output-format json`` 的一次真实会话。"""
    return _run_real_session(tmp_path_factory, output_format="json")


# ---------------------------------------------------------------------------
# 1. 文本模式：退出码 / I6 / I9
# ---------------------------------------------------------------------------


def test_text_mode_exits_zero_and_event_stream_ends_with_single_task_finished(
    text_session: _SessionOutcome,
) -> None:
    """退出码 ``0``；``I6``（恰好一条 ``TASK_FINISHED`` 且在最后）；``I9``（``seq`` 连续）。"""
    outcome = text_session
    assert outcome.exit_code == EXIT_OK, (
        f"退出码应为 0（COMPLETED），实际 {outcome.exit_code}；stderr={outcome.stderr!r}"
    )

    events = outcome.events
    assert events, "事件流为空（至少应有 MODEL_RESPONSE 与 TASK_FINISHED）"

    kinds = [event.kind for event in events]
    assert kinds.count(SessionEventKind.TASK_FINISHED) == 1, "I6：TASK_FINISHED 必须恰好一条"
    assert kinds[-1] is SessionEventKind.TASK_FINISHED, "I6：TASK_FINISHED 必须是最后一条"

    finished = events[-1]
    assert finished.status is TaskStatus.COMPLETED, (
        f"TASK_FINISHED.status 应为 COMPLETED，实际 {finished.status}"
    )
    assert finished.text, "I6：TASK_FINISHED.text 必须非空"

    seqs = [event.seq for event in events]
    assert seqs == list(range(len(events))), f"I9：seq 必须从 0 连续无空洞，实际 {seqs}"


def test_text_mode_stdout_renders_exactly_one_terminal_status_line(
    text_session: _SessionOutcome,
) -> None:
    """文本模式 stdout 的**渲染侧** ``I6``：只有一条终态行，且它就是最后一行。"""
    lines = [line for line in text_session.stdout.splitlines() if line.strip()]
    assert lines, "文本模式 stdout 为空（渲染分支一条都没走到？）"

    status_lines = [line for line in lines if line.startswith(_STATUS_PREFIXES)]
    assert len(status_lines) == 1, f"渲染出的终态行应恰好一条，实际 {status_lines}"
    assert lines[-1].startswith(f"{TaskStatus.COMPLETED.value}："), (
        f"最后一行应是 completed 终态行，实际 {lines[-1]!r}"
    )


# ---------------------------------------------------------------------------
# 1'. 文本模式：审计真实落盘 + 工具真的执行过
# ---------------------------------------------------------------------------


def test_text_mode_audit_is_persisted_and_every_line_parses(text_session: _SessionOutcome) -> None:
    """审计**真实存在**且**每行**可 ``json.loads``（``REQ-SEC-06`` 的可回放前提）。"""
    outcome = text_session
    assert outcome.audit_path.is_file(), f"审计未落盘：{outcome.audit_path}"
    assert outcome.audit_lines, "审计文件为空：本次会话没有产生任何审计事件（I6 之外的证据面缺失）"

    for number, line in enumerate(outcome.audit_lines, start=1):
        payload = _loads(line, context=f"审计第 {number} 行")
        assert payload.get("event_id"), f"审计第 {number} 行缺 event_id（回放关联键）"


def test_text_mode_executes_a_real_tool_call(text_session: _SessionOutcome) -> None:
    """模型确实发起了工具调用，且**至少一次真的执行**（读到了磁盘上的哨兵内容）。

    这条是"能跑起来"的核心证据：只有 DENY 路径也能写审计，但没有执行就不算跑通
    工具链路（校验 → ``decide()`` → ``invoke()`` → 回喂）。
    """
    outcome = text_session
    calls = [event for event in outcome.events if event.kind is SessionEventKind.TOOL_CALL]
    assert calls, "模型未发起任何工具调用 ⇒ 工具链路与审计面都没有被验证"

    executed = [
        event
        for event in outcome.events
        if event.kind is SessionEventKind.TOOL_RESULT and event.result is not None
    ]
    assert executed, "所有工具调用都被拒绝/未执行 ⇒ 工具链路未跑通（见审计的 denied_reason）"

    contents = [event.result.content for event in executed if event.result is not None]
    assert any(_SENTINEL in content for content in contents), (
        f"已执行的工具结果里没有 {_SENTINEL_FILE} 的哨兵内容 ⇒ 工具没有真正读到该文件"
    )


def test_text_mode_started_a_real_model_server(text_session: _SessionOutcome) -> None:
    """模型日志**确实**由一次真实 ``llama-server`` 启动写出（防止"跑了假链路"）。"""
    log = text_session.model_log_path
    assert log.is_file(), f"模型日志不存在：{log}（llama-server 没被启动过？）"
    assert log.stat().st_size > 0, f"模型日志为空：{log}"


# ---------------------------------------------------------------------------
# 2. JSON 模式：stdout 只出 JSONL
# ---------------------------------------------------------------------------


def test_json_mode_stdout_is_pure_jsonl(json_session: _SessionOutcome) -> None:
    """``--output-format json``：stdout 逐行都是 JSON，且**一条不漏、一条不多**。"""
    outcome = json_session
    assert outcome.exit_code == EXIT_OK, (
        f"退出码应为 0（COMPLETED），实际 {outcome.exit_code}；stderr={outcome.stderr!r}"
    )

    payloads = _jsonl(outcome.stdout)
    assert payloads, "stdout 没有任何 JSONL 行"

    assert len(payloads) == len(outcome.events), (
        "JSONL 行数必须等于事件数："
        f"{len(payloads)} != {len(outcome.events)}（有事件没被序列化，或有非 JSON 行混入）"
    )
    assert [payload["seq"] for payload in payloads] == list(range(len(payloads))), (
        "JSONL 的 seq 必须从 0 连续且与行序一致"
    )

    kinds = [payload["kind"] for payload in payloads]
    assert kinds.count(SessionEventKind.TASK_FINISHED.value) == 1
    assert kinds[-1] == SessionEventKind.TASK_FINISHED.value
    assert payloads[-1]["status"] == TaskStatus.COMPLETED.value


# ---------------------------------------------------------------------------
# 3. 无凭据泄漏
# ---------------------------------------------------------------------------


def test_no_credential_material_leaks_into_any_channel(
    text_session: _SessionOutcome, json_session: _SessionOutcome
) -> None:
    """事件 / 审计 / 两条输出流里都不出现凭据标记，子进程环境 canary 也不回流。"""
    channels: dict[str, str] = {
        "text-stdout": text_session.stdout,
        "text-stderr": text_session.stderr,
        "json-stdout": json_session.stdout,
        "json-stderr": json_session.stderr,
        "events": "\n".join(_event_text(event) for event in text_session.events),
        "audit": "\n".join(text_session.audit_lines),
    }
    canary = _CANARY_VALUE.lower()
    for where, blob in channels.items():
        lowered = blob.lower()
        for marker in _CREDENTIAL_MARKERS:
            assert marker not in lowered, f"{where} 出现疑似凭据标记 {marker!r}"
        assert canary not in lowered, f"{where} 泄漏了子进程环境 canary"

    # 审计的 detail 是"从不可信内容再脱敏一次"的纵深防御面（audit.md）：单独再扫一遍。
    for number, line in enumerate(json_session.audit_lines, start=1):
        assert _CANARY_VALUE.lower() not in line.lower(), f"JSON 模式审计第 {number} 行泄漏 canary"
