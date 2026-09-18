"""``ShellCommandTool`` 的行为断言（``G8``）。

覆盖 happy path 与**失败路径**：``argv`` 形状违规、路径越界、隔离失败（**不得回退**）、
超时 / 无法启动、非零退出码、输出截断、审计关联键与"审计失败必须冒泡"。
``foundation.proc`` 用替身打桩，**不真的执行命令**（CI 里没有隔离所需的权限）。

这是实现侧的功能断言；``S1``/``S3`` 一类对抗性与安全断言属 ``tests/security/``，
由验证角色独立完成。
"""

from __future__ import annotations

import pathlib

import pytest

from agent_sec_perf.contracts.audit import AuditEvent, AuditEventKind, AuditOutcome
from agent_sec_perf.contracts.policy import Capability
from agent_sec_perf.contracts.tools import ExecutionContext
from agent_sec_perf.foundation import proc
from agent_sec_perf.foundation.errors import IsolationError, ProtocolError
from agent_sec_perf.tools.shell import ShellCommandTool


class RecordingSink:
    """记录事件的内存审计落点（``AuditSink`` 的替身）。"""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def emit(self, event: AuditEvent) -> None:
        self.events.append(event)

    def flush(self) -> None:
        return


class FailingSink:
    """``emit`` 必然失败的落点：用于验证"审计失败必须冒泡"。"""

    def emit(self, event: AuditEvent) -> None:
        raise OSError("disk full")

    def flush(self) -> None:
        return


BINARY = "/usr/bin/echo"


@pytest.fixture
def sink() -> RecordingSink:
    return RecordingSink()


@pytest.fixture
def root(tmp_path: pathlib.Path) -> pathlib.Path:
    directory = tmp_path / "root"
    directory.mkdir()
    return directory


def make_ctx(root: pathlib.Path) -> ExecutionContext:
    return ExecutionContext(
        session_id="s1",
        call_id="c1",
        working_dir=root,
        allowed_roots=(root,),
        timeout_s=5.0,
    )


def patch_proc(
    monkeypatch: pytest.MonkeyPatch, run_impl: object, *, calls: list[dict[str, object]]
) -> None:
    """把 ``resolve_binary`` 与 ``run`` 换成替身（记录调用参数）。"""
    monkeypatch.setattr(proc, "resolve_binary", lambda name: BINARY)
    monkeypatch.setattr(proc, "run", run_impl)


@pytest.mark.unit
def test_spec_declares_execute_capability() -> None:
    spec = ShellCommandTool(RecordingSink()).spec
    assert spec.name == "run_command"
    assert spec.capabilities == frozenset({Capability.EXECUTE_COMMAND})


@pytest.mark.unit
def test_run_command_happy_path(
    sink: RecordingSink, root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(
        argv: list[str], *, cwd: pathlib.Path, timeout_s: float, isolation: str = "user"
    ) -> proc.CommandResult:
        calls.append({"argv": argv, "cwd": cwd, "timeout_s": timeout_s, "isolation": isolation})
        return proc.CommandResult(argv=tuple(argv), returncode=0, stdout="hi\n", stderr="")

    patch_proc(monkeypatch, fake_run, calls=calls)
    ctx = make_ctx(root)
    result = ShellCommandTool(sink).invoke({"argv": ["echo", "hi"]}, ctx=ctx)

    assert result.ok is True
    assert "hi" in result.content
    assert result.truncated is False
    # argv[0] 被解析成绝对路径；argv 以列表传入；固定 user 隔离。
    assert calls[0]["argv"] == [BINARY, "hi"]
    assert calls[0]["isolation"] == "user"
    assert calls[0]["cwd"] == root
    assert sink.events[0].kind is AuditEventKind.TOOL_CALL
    assert sink.events[0].outcome is AuditOutcome.OK
    assert result.audit_id == sink.events[0].event_id


@pytest.mark.unit
def test_run_command_forwards_timeout(
    sink: RecordingSink, root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []

    def fake_run(
        argv: list[str], *, cwd: pathlib.Path, timeout_s: float, isolation: str = "user"
    ) -> proc.CommandResult:
        calls.append({"timeout_s": timeout_s})
        return proc.CommandResult(argv=tuple(argv), returncode=0, stdout="", stderr="")

    patch_proc(monkeypatch, fake_run, calls=calls)
    ShellCommandTool(sink).invoke({"argv": ["echo"], "timeout_s": 2.5}, ctx=make_ctx(root))
    assert calls[0]["timeout_s"] == 2.5


@pytest.mark.unit
def test_rejects_string_argv(sink: RecordingSink, root: pathlib.Path) -> None:
    """裸字符串是"把整串当命令"的形态，必须拒绝（不得被当成单参数列表）。"""
    assert ShellCommandTool(sink).invoke({"argv": "echo hi"}, ctx=make_ctx(root)).ok is False


@pytest.mark.unit
def test_rejects_argv_with_non_string_member(sink: RecordingSink, root: pathlib.Path) -> None:
    assert ShellCommandTool(sink).invoke({"argv": ["echo", 1]}, ctx=make_ctx(root)).ok is False


@pytest.mark.unit
def test_rejects_empty_argv(sink: RecordingSink, root: pathlib.Path) -> None:
    assert ShellCommandTool(sink).invoke({"argv": []}, ctx=make_ctx(root)).ok is False


@pytest.mark.unit
def test_rejects_unknown_argument(sink: RecordingSink, root: pathlib.Path) -> None:
    assert ShellCommandTool(sink).invoke({"argv": ["echo"], "x": 1}, ctx=make_ctx(root)).ok is False


@pytest.mark.unit
def test_rejects_cwd_outside_allowed_roots(
    sink: RecordingSink, root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, object]] = []
    patch_proc(monkeypatch, lambda *a, **k: None, calls=calls)
    result = ShellCommandTool(sink).invoke(
        {"argv": ["echo"], "cwd": str(root / ".." / "elsewhere")}, ctx=make_ctx(root)
    )
    assert result.ok is False
    assert calls == []  # 越界在解析阶段即拒绝，命令从未被启动


@pytest.mark.unit
def test_isolation_failure_is_a_tool_failure_without_fallback(
    sink: RecordingSink, root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts: list[str] = []

    def failing_run(
        argv: list[str], *, cwd: pathlib.Path, timeout_s: float, isolation: str = "user"
    ) -> proc.CommandResult:
        attempts.append(isolation)
        raise IsolationError("cannot drop privileges")

    monkeypatch.setattr(proc, "resolve_binary", lambda name: BINARY)
    monkeypatch.setattr(proc, "run", failing_run)
    result = ShellCommandTool(sink).invoke({"argv": ["echo"]}, ctx=make_ctx(root))

    assert result.ok is False
    # 只尝试过一次，且始终是 user 隔离——**没有**回退为非隔离执行。
    assert attempts == ["user"]
    assert sink.events[0].outcome is AuditOutcome.ERROR


@pytest.mark.unit
def test_timeout_is_a_tool_failure(
    sink: RecordingSink, root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def timeout_run(*args: object, **kwargs: object) -> proc.CommandResult:
        raise ProtocolError("命令超时")

    monkeypatch.setattr(proc, "resolve_binary", lambda name: BINARY)
    monkeypatch.setattr(proc, "run", timeout_run)
    assert ShellCommandTool(sink).invoke({"argv": ["sleep", "99"]}, ctx=make_ctx(root)).ok is False


@pytest.mark.unit
def test_missing_binary_is_a_tool_failure(
    sink: RecordingSink, root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(name: str) -> str:
        raise ProtocolError("找不到可执行文件")

    monkeypatch.setattr(proc, "resolve_binary", missing)
    result = ShellCommandTool(sink).invoke({"argv": ["nope"]}, ctx=make_ctx(root))
    assert result.ok is False


@pytest.mark.unit
def test_nonzero_exit_is_a_tool_failure(
    sink: RecordingSink, root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_command(
        argv: list[str], *, cwd: pathlib.Path, timeout_s: float, isolation: str = "user"
    ) -> proc.CommandResult:
        return proc.CommandResult(argv=tuple(argv), returncode=2, stdout="oops", stderr="")

    monkeypatch.setattr(proc, "resolve_binary", lambda name: BINARY)
    monkeypatch.setattr(proc, "run", failing_command)
    result = ShellCommandTool(sink).invoke({"argv": ["false"]}, ctx=make_ctx(root))

    assert result.ok is False
    assert result.error is not None
    assert "oops" in result.content
    assert sink.events[0].outcome is AuditOutcome.ERROR


@pytest.mark.unit
def test_output_is_truncated(
    sink: RecordingSink, root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def loud_command(
        argv: list[str], *, cwd: pathlib.Path, timeout_s: float, isolation: str = "user"
    ) -> proc.CommandResult:
        return proc.CommandResult(argv=tuple(argv), returncode=0, stdout="x" * 100_000, stderr="")

    monkeypatch.setattr(proc, "resolve_binary", lambda name: BINARY)
    monkeypatch.setattr(proc, "run", loud_command)
    result = ShellCommandTool(sink).invoke({"argv": ["cat"]}, ctx=make_ctx(root))
    assert result.ok is True
    assert result.truncated is True


@pytest.mark.unit
def test_audit_failure_propagates(root: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def ok_command(
        argv: list[str], *, cwd: pathlib.Path, timeout_s: float, isolation: str = "user"
    ) -> proc.CommandResult:
        return proc.CommandResult(argv=tuple(argv), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(proc, "resolve_binary", lambda name: BINARY)
    monkeypatch.setattr(proc, "run", ok_command)
    with pytest.raises(OSError):
        ShellCommandTool(FailingSink()).invoke({"argv": ["echo"]}, ctx=make_ctx(root))
