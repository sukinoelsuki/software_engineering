"""文件工具（``ReadFileTool`` / ``WriteFileTool`` / ``ListDirTool``）的行为断言（``G8``）。

覆盖 happy path 与**失败路径**：目录穿越、类型违规、输出截断、审计关联键、
以及"审计写入失败必须冒泡"。用真实临时目录（``tmp_path``）验证白名单，
**不依赖网络或外部二进制**。

这是实现侧的功能断言；``S2``（路径穿越被拒且留审计）一类对抗性断言属 ``tests/security/``，
由验证角色独立完成。
"""

from __future__ import annotations

import pathlib

import pytest

from agent_sec_perf.contracts.audit import AuditEvent, AuditEventKind, AuditOutcome
from agent_sec_perf.contracts.policy import Capability
from agent_sec_perf.contracts.tools import ExecutionContext
from agent_sec_perf.tools.files import ListDirTool, ReadFileTool, WriteFileTool


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


@pytest.fixture
def sink() -> RecordingSink:
    return RecordingSink()


def make_ctx(
    root: pathlib.Path, *, allowed: tuple[pathlib.Path, ...] | None = None
) -> ExecutionContext:
    return ExecutionContext(
        session_id="s1",
        call_id="c1",
        working_dir=root,
        allowed_roots=(root,) if allowed is None else allowed,
        timeout_s=5.0,
    )


@pytest.fixture
def root(tmp_path: pathlib.Path) -> pathlib.Path:
    directory = tmp_path / "root"
    directory.mkdir()
    return directory


# ---------------------------------------------------------------------------
# 工具声明
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("tool_cls", "name", "capability"),
    [
        (ReadFileTool, "read_file", Capability.READ_FILE),
        (WriteFileTool, "write_file", Capability.WRITE_FILE),
        (ListDirTool, "list_dir", Capability.READ_FILE),
    ],
)
def test_spec_declares_name_and_capability(
    tool_cls: type[ReadFileTool] | type[WriteFileTool] | type[ListDirTool],
    name: str,
    capability: Capability,
) -> None:
    spec = tool_cls(RecordingSink()).spec
    assert spec.name == name
    assert spec.capabilities == frozenset({capability})
    assert spec.source == "builtin"


# ---------------------------------------------------------------------------
# ReadFileTool
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_read_happy_path_emits_audit(sink: RecordingSink, root: pathlib.Path) -> None:
    (root / "a.txt").write_text("hello", encoding="utf-8")
    ctx = make_ctx(root)
    result = ReadFileTool(sink).invoke({"path": "a.txt"}, ctx=ctx)

    assert result.ok is True
    assert result.content == "hello"
    assert result.truncated is False
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.kind is AuditEventKind.TOOL_CALL
    assert event.outcome is AuditOutcome.OK
    assert event.call_id == ctx.call_id
    assert event.tool_name == "read_file"
    assert result.audit_id == event.event_id


@pytest.mark.unit
def test_read_rejects_path_traversal(sink: RecordingSink, root: pathlib.Path) -> None:
    secret = root.parent / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")
    result = ReadFileTool(sink).invoke(
        {"path": str(root / ".." / "secret.txt")}, ctx=make_ctx(root)
    )

    assert result.ok is False
    assert result.error is not None
    assert "top secret" not in result.content
    assert sink.events[0].outcome is AuditOutcome.ERROR
    assert result.audit_id == sink.events[0].event_id


@pytest.mark.unit
def test_read_rejects_symlink_escape(sink: RecordingSink, root: pathlib.Path) -> None:
    outside = root.parent / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (root / "link.txt").symlink_to(outside)
    result = ReadFileTool(sink).invoke({"path": "link.txt"}, ctx=make_ctx(root))
    assert result.ok is False


@pytest.mark.unit
def test_read_rejects_non_string_path(sink: RecordingSink, root: pathlib.Path) -> None:
    result = ReadFileTool(sink).invoke({"path": 123}, ctx=make_ctx(root))
    assert result.ok is False
    assert sink.events[0].outcome is AuditOutcome.ERROR


@pytest.mark.unit
def test_read_rejects_missing_path(sink: RecordingSink, root: pathlib.Path) -> None:
    assert ReadFileTool(sink).invoke({}, ctx=make_ctx(root)).ok is False


@pytest.mark.unit
def test_read_rejects_unknown_argument(sink: RecordingSink, root: pathlib.Path) -> None:
    result = ReadFileTool(sink).invoke({"path": "a.txt", "extra": 1}, ctx=make_ctx(root))
    assert result.ok is False


@pytest.mark.unit
def test_read_missing_file_is_a_tool_failure(sink: RecordingSink, root: pathlib.Path) -> None:
    result = ReadFileTool(sink).invoke({"path": "nope.txt"}, ctx=make_ctx(root))
    assert result.ok is False
    assert sink.events[0].outcome is AuditOutcome.ERROR


@pytest.mark.unit
def test_read_directory_is_a_tool_failure(sink: RecordingSink, root: pathlib.Path) -> None:
    (root / "sub").mkdir()
    assert ReadFileTool(sink).invoke({"path": "sub"}, ctx=make_ctx(root)).ok is False


@pytest.mark.unit
def test_read_truncates_output(sink: RecordingSink, root: pathlib.Path) -> None:
    (root / "big.txt").write_text("x" * 100, encoding="utf-8")
    result = ReadFileTool(sink).invoke({"path": "big.txt", "max_bytes": 10}, ctx=make_ctx(root))
    assert result.ok is True
    assert result.truncated is True
    assert len(result.content.encode("utf-8")) <= 10 + len("\n…（输出已按上限截断）".encode())


@pytest.mark.unit
def test_read_rejects_non_integer_max_bytes(sink: RecordingSink, root: pathlib.Path) -> None:
    (root / "a.txt").write_text("hello", encoding="utf-8")
    result = ReadFileTool(sink).invoke({"path": "a.txt", "max_bytes": "10"}, ctx=make_ctx(root))
    assert result.ok is False


@pytest.mark.unit
def test_read_audit_failure_propagates_on_success(root: pathlib.Path) -> None:
    (root / "a.txt").write_text("hello", encoding="utf-8")
    with pytest.raises(OSError):
        ReadFileTool(FailingSink()).invoke({"path": "a.txt"}, ctx=make_ctx(root))


@pytest.mark.unit
def test_read_audit_failure_propagates_on_rejection(root: pathlib.Path) -> None:
    """审计失败**不得**被"工具失败"吞掉——否则拒绝路径会静默丢证据。"""
    with pytest.raises(OSError):
        ReadFileTool(FailingSink()).invoke({"path": "../etc/passwd"}, ctx=make_ctx(root))


# ---------------------------------------------------------------------------
# WriteFileTool
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_write_happy_path(sink: RecordingSink, root: pathlib.Path) -> None:
    ctx = make_ctx(root)
    result = WriteFileTool(sink).invoke({"path": "out/note.txt", "content": "hi"}, ctx=ctx)

    assert result.ok is True
    assert (root / "out" / "note.txt").read_text(encoding="utf-8") == "hi"
    assert sink.events[0].kind is AuditEventKind.TOOL_CALL
    assert sink.events[0].outcome is AuditOutcome.OK
    assert result.audit_id == sink.events[0].event_id


@pytest.mark.unit
def test_write_rejects_path_traversal_without_creating_file(
    sink: RecordingSink, root: pathlib.Path
) -> None:
    target = root.parent / "escaped.txt"
    result = WriteFileTool(sink).invoke(
        {"path": str(root / ".." / "escaped.txt"), "content": "x"}, ctx=make_ctx(root)
    )
    assert result.ok is False
    assert not target.exists()


@pytest.mark.unit
def test_write_rejects_non_string_content(sink: RecordingSink, root: pathlib.Path) -> None:
    result = WriteFileTool(sink).invoke({"path": "a.txt", "content": 1}, ctx=make_ctx(root))
    assert result.ok is False


@pytest.mark.unit
def test_write_allows_empty_content(sink: RecordingSink, root: pathlib.Path) -> None:
    result = WriteFileTool(sink).invoke({"path": "empty.txt", "content": ""}, ctx=make_ctx(root))
    assert result.ok is True
    assert (root / "empty.txt").read_text(encoding="utf-8") == ""


# ---------------------------------------------------------------------------
# ListDirTool
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_list_dir_returns_sorted_names(sink: RecordingSink, root: pathlib.Path) -> None:
    for name in ("b.txt", "a.txt"):
        (root / name).write_text("", encoding="utf-8")
    result = ListDirTool(sink).invoke({"path": "."}, ctx=make_ctx(root))
    assert result.ok is True
    assert result.content.splitlines() == ["a.txt", "b.txt"]


@pytest.mark.unit
def test_list_dir_truncates_by_entry_limit(sink: RecordingSink, root: pathlib.Path) -> None:
    for name in ("a", "b", "c"):
        (root / name).write_text("", encoding="utf-8")
    result = ListDirTool(sink).invoke({"path": ".", "max_entries": 2}, ctx=make_ctx(root))
    assert result.ok is True
    assert result.truncated is True
    assert len(result.content.splitlines()) == 2


@pytest.mark.unit
def test_list_dir_on_file_is_a_tool_failure(sink: RecordingSink, root: pathlib.Path) -> None:
    (root / "a.txt").write_text("", encoding="utf-8")
    assert ListDirTool(sink).invoke({"path": "a.txt"}, ctx=make_ctx(root)).ok is False
