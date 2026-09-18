"""对抗性验证：``G8`` 工具层对抗面（``REQ-SEC-05``）。

依据（独立验证，不听实现者的解释）：``docs/design/interfaces/tools.md`` §1 与
``src/agent_sec_perf/tools/{files,shell}.py`` 顶部约定。

**不重复** ``tests/security/test_path_traversal_rejected.py``（已覆盖 ``..`` 上跳 /
绝对路径越界 / 符号链接外指 / 深穿越 / 相邻同前缀目录）。本文件只补它**没覆盖**的四点：

* **相对路径基准**：相对路径必须相对于 ``ctx.working_dir``，**不是**进程 CWD——
  这是已修过的真实缺陷（曾误用进程 CWD）；
* **``argv`` 形状**：必须是列表、不经 shell（``;`` / ``$(...)`` 不会被解释）；
  裸字符串 ``argv`` 必须被拒（fail-closed，不得当命令）；
* **隔离失败不得回退**：``IsolationError`` ⇒ ``ok=False``，不得静默退化为非隔离执行；
* **审计失败必须冒泡**：``emit`` 抛错必须原样上浮，不得被工具收敛成 ``ok=False`` 假成功。
"""

from __future__ import annotations

import pathlib

import pytest

from agent_sec_perf.contracts.audit import AuditEvent
from agent_sec_perf.contracts.tools import ExecutionContext
from agent_sec_perf.foundation import proc as proc_module
from agent_sec_perf.foundation.errors import IsolationError
from agent_sec_perf.observability.audit import JsonlAuditSink
from agent_sec_perf.tools.files import ReadFileTool
from agent_sec_perf.tools.shell import ShellCommandTool


class _RaisingSink(JsonlAuditSink):
    """``emit`` 必抛的审计桩：用于验证"审计失败必须冒泡"。"""

    def emit(self, event: AuditEvent) -> None:
        raise RuntimeError("audit sink unavailable")


def _ctx(tmp_path: pathlib.Path, work: pathlib.Path) -> ExecutionContext:
    return ExecutionContext(
        session_id="sess-1",
        call_id="call-1",
        working_dir=work,
        allowed_roots=(work,),
        timeout_s=5.0,
    )


@pytest.mark.security
def test_relative_path_resolves_to_working_dir_not_cwd(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """相对路径必须相对 ``ctx.working_dir``，而非进程 CWD（已修过的真实缺陷）。

    在 work 与"另一个 CWD"各放一个同名 ``rel.txt``，内容不同：若解析用的是 CWD，
    读到的会是 CWD_FAKE；用 working_dir 才会是 WORKDIR。
    """
    work = tmp_path / "work"
    work.mkdir()
    (work / "rel.txt").write_text("WORKDIR", encoding="utf-8")

    other_cwd = tmp_path / "other_cwd"
    other_cwd.mkdir()
    (other_cwd / "rel.txt").write_text("CWD_FAKE", encoding="utf-8")

    # 把进程 CWD 切到与 working_dir 不同的目录，证明工具不依赖 CWD。
    monkeypatch.chdir(other_cwd)

    sink = JsonlAuditSink(tmp_path, roots=(tmp_path,))
    tool = ReadFileTool(sink=sink)
    result = tool.invoke({"path": "rel.txt"}, ctx=_ctx(tmp_path, work))

    assert isinstance(result, object)
    assert result.ok is True
    # 读到的必须是 working_dir 下的那份，而非 CWD 下的那份 ⇒ 解析基准是 working_dir。
    assert result.content == "WORKDIR"


@pytest.mark.security
def test_argv_must_be_list_bare_str_rejected(tmp_path: pathlib.Path) -> None:
    """裸字符串 ``argv`` 必须被拒（fail-closed），不得被当成"整串命令"拆分执行。"""
    sink = JsonlAuditSink(tmp_path, roots=(tmp_path,))
    tool = ShellCommandTool(sink=sink)

    result = tool.invoke({"argv": "echo hi"}, ctx=_ctx(tmp_path, tmp_path))
    assert result.ok is False
    assert "argv" in (result.error or "")


@pytest.mark.security
def test_argv_passed_without_shell_interpretation(tmp_path: pathlib.Path) -> None:
    """不经 shell：``;`` 与 ``$(...)`` 只作普通参数，不会被解释（无命令注入面）。"""
    sink = JsonlAuditSink(tmp_path, roots=(tmp_path,))
    tool = ShellCommandTool(sink=sink)

    payload = "$(whoami); rm -rf /"
    result = tool.invoke({"argv": ["printf", "%s", payload]}, ctx=_ctx(tmp_path, tmp_path))
    assert result.ok is True
    # printf 收到的是字面量，未做 shell 展开/解释。
    assert result.content.strip() == payload


@pytest.mark.security
def test_isolation_failure_is_not_silently_downgraded(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``IsolationError`` ⇒ ``ok=False``，**不得**回退为非隔离执行（REQ-SEC-05）。"""
    calls: list[object] = []

    def _fake_run(*args: object, **kwargs: object) -> object:
        calls.append((args, kwargs))
        raise IsolationError("cannot drop privileges")

    monkeypatch.setattr(proc_module, "run", _fake_run)

    sink = JsonlAuditSink(tmp_path, roots=(tmp_path,))
    tool = ShellCommandTool(sink=sink)
    result = tool.invoke({"argv": ["printf", "%s", "x"]}, ctx=_ctx(tmp_path, tmp_path))

    # 失败时拒绝执行（不会让命令"跑成功"）。
    assert result.ok is False
    assert "隔离" in (result.error or "")
    # 隔离执行确实被调用过 ⇒ 没有"跳过隔离直接裸跑"的退路。
    assert calls, "proc.run 应被调用；隔离失败不得绕过执行入口"


@pytest.mark.security
def test_audit_failure_bubbles_not_swallowed(tmp_path: pathlib.Path) -> None:
    """审计写入失败必须原样冒泡：``emit`` 抛错不得被工具收敛成 ``ok=False`` 假成功。"""
    sink = _RaisingSink(tmp_path, roots=(tmp_path,))
    tool = ShellCommandTool(sink=sink)

    with pytest.raises(RuntimeError, match="audit sink unavailable"):
        # 命令本身会成功，但随后 audit_tool_call 的 emit 抛错必须上浮。
        tool.invoke({"argv": ["printf", "%s", "x"]}, ctx=_ctx(tmp_path, tmp_path))
