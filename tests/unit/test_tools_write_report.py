"""受限写工具 ``write_report`` 的行为与边界（不调用模型）。"""

from __future__ import annotations

from pathlib import Path

from agent_sec_perf.contracts.audit import AuditEvent, AuditSink
from agent_sec_perf.contracts.tools import ExecutionContext
from agent_sec_perf.tools.files import (
    REPORT_FILENAME,
    ReviseReportTool,
    WriteIncidentReportTool,
    WriteReportTool,
)


class _RecordingSink(AuditSink):
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def emit(self, event: AuditEvent) -> None:
        self.events.append(event)

    def flush(self) -> None:
        return


def _context(workspace: Path) -> ExecutionContext:
    return ExecutionContext(
        session_id="s-write-report",
        call_id="c-1",
        working_dir=workspace,
        allowed_roots=(workspace,),
        timeout_s=30.0,
        network_allowed=False,
    )


def test_writes_report_and_audits_artifact(tmp_path: Path) -> None:
    sink = _RecordingSink()
    tool = WriteReportTool(sink)
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = tool.invoke({"content": "CODE=E42\n"}, ctx=_context(workspace))

    assert result.ok is True
    assert result.audit_id
    assert (workspace / REPORT_FILENAME).read_text(encoding="utf-8") == "CODE=E42\n"
    assert len(sink.events) == 1
    assert sink.events[0].detail["artifact"] == REPORT_FILENAME


def test_overwrites_for_revision(tmp_path: Path) -> None:
    tool = WriteReportTool(_RecordingSink())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / REPORT_FILENAME).write_text("VERDICT=待复核\n", encoding="utf-8")

    result = tool.invoke({"content": "VERDICT=已修订\n"}, ctx=_context(workspace))

    assert result.ok is True
    assert (workspace / REPORT_FILENAME).read_text(encoding="utf-8") == "VERDICT=已修订\n"


def test_rejects_empty_content(tmp_path: Path) -> None:
    tool = WriteReportTool(_RecordingSink())
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = tool.invoke({"content": ""}, ctx=_context(workspace))

    assert result.ok is False
    assert "content" in (result.error or "")


def test_rejects_symlink_escape(tmp_path: Path) -> None:
    """目标已存在且是指向允许根之外的符号链接 ⇒ 必须拒绝（不得顺着链接写出去）。"""
    tool = WriteReportTool(_RecordingSink())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("original", encoding="utf-8")
    (workspace / REPORT_FILENAME).symlink_to(outside)

    result = tool.invoke({"content": "overwrite\n"}, ctx=_context(workspace))

    assert result.ok is False
    assert outside.read_text(encoding="utf-8") == "original"


def test_rejects_unreplaced_placeholder(tmp_path: Path) -> None:
    """照抄提示里的尖括号占位符 ⇒ 写入时即被拒（小模型的典型失真，2026-10-26 实测）。"""
    tool = WriteReportTool(_RecordingSink())
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = tool.invoke(
        {"content": "CODE=<第一条 ERROR 的 code 值>\nVERDICT=待复核\n"},
        ctx=_context(workspace),
    )

    assert result.ok is False
    assert "占位符" in (result.error or "")
    assert not (workspace / REPORT_FILENAME).exists()


def test_rejects_key_without_value(tmp_path: Path) -> None:
    """把 ``CODE=E42`` 写成只有 ``CODE`` ⇒ 拒绝（改写时丢值，2026-10-26 实测）。"""
    tool = WriteReportTool(_RecordingSink())
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = tool.invoke({"content": "CODE\nVERDICT=待复核\n"}, ctx=_context(workspace))

    assert result.ok is False
    assert "KEY=VALUE" in (result.error or "")


def test_rejects_unknown_arguments(tmp_path: Path) -> None:
    tool = WriteReportTool(_RecordingSink())
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    result = tool.invoke({"content": "x", "path": "../../etc/passwd"}, ctx=_context(workspace))

    assert result.ok is False
    assert "path" in (result.error or "")


_FIELDS: dict[str, str] = {
    "code": "E42",
    "log_timeout_ms": "3000",
    "config_timeout_ms": "8000",
    "verdict": "待复核",
}


_EVIDENCE: dict[str, str] = {
    "app.log": "2026-10-26T09:13:52Z ERROR upstream=payment timeout_ms=3000 code=E42 path=/pay\n",
    "service.toml": "[upstream.payment]\ntimeout_ms = 8000\n",
}


def _workspace_with_evidence(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for name, text in _EVIDENCE.items():
        (workspace / name).write_text(text, encoding="utf-8")
    return workspace


def test_incident_report_writes_fixed_lines(tmp_path: Path) -> None:
    """结构化写：模型只给四个字段，``KEY=VALUE`` 行由工具按固定顺序生成。"""
    workspace = _workspace_with_evidence(tmp_path)
    tool = WriteIncidentReportTool(_RecordingSink())

    result = tool.invoke(dict(_FIELDS), ctx=_context(workspace))

    assert result.ok is True
    assert (workspace / REPORT_FILENAME).read_text(encoding="utf-8") == (
        "CODE=E42\nLOG_TIMEOUT_MS=3000\nCONFIG_TIMEOUT_MS=8000\nVERDICT=待复核\n"
    )


def test_incident_report_rejects_value_not_in_evidence(tmp_path: Path) -> None:
    """取值必须逐字出现在现场文件里；编造（幻觉）在**写入时**就被拒。"""
    workspace = _workspace_with_evidence(tmp_path)
    tool = WriteIncidentReportTool(_RecordingSink())

    result = tool.invoke({**_FIELDS, "code": "PAY001"}, ctx=_context(workspace))

    assert result.ok is False
    assert "code" in (result.error or "")
    assert "不在这两份现场文件里" in (result.error or "")
    assert not (workspace / REPORT_FILENAME).exists()


def test_incident_report_rejects_when_evidence_missing(tmp_path: Path) -> None:
    """证据文件读不到 ⇒ 拒绝写入（不能"没法校验就放行"）。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool = WriteIncidentReportTool(_RecordingSink())

    result = tool.invoke(dict(_FIELDS), ctx=_context(workspace))

    assert result.ok is False
    assert "无法读取现场文件" in (result.error or "")


def test_incident_report_rejects_empty_value(tmp_path: Path) -> None:
    workspace = _workspace_with_evidence(tmp_path)
    tool = WriteIncidentReportTool(_RecordingSink())

    result = tool.invoke({**_FIELDS, "code": "  "}, ctx=_context(workspace))

    assert result.ok is False
    assert "值为空" in (result.error or "")


def test_incident_report_rejects_placeholder(tmp_path: Path) -> None:
    workspace = _workspace_with_evidence(tmp_path)
    tool = WriteIncidentReportTool(_RecordingSink())

    result = tool.invoke({**_FIELDS, "code": "<code 值>"}, ctx=_context(workspace))

    assert result.ok is False
    assert "占位符" in (result.error or "")


def test_incident_report_rejects_extra_argument(tmp_path: Path) -> None:
    """参数表是封闭的：多给一个 ``path`` 就拒绝（不能借它指定写入目标）。"""
    workspace = _workspace_with_evidence(tmp_path)
    tool = WriteIncidentReportTool(_RecordingSink())

    result = tool.invoke({**_FIELDS, "path": "../../etc/passwd"}, ctx=_context(workspace))

    assert result.ok is False
    assert "path" in (result.error or "")


def _report(workspace: Path, text: str) -> None:
    (workspace / REPORT_FILENAME).write_text(text, encoding="utf-8")


def test_revise_only_touches_target_line(tmp_path: Path) -> None:
    """`revise_report` 只改指定行，其余行**逐字**保留（含换行）。"""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    original = "CODE=E42\nLOG_TIMEOUT_MS=3000\nCONFIG_TIMEOUT_MS=8000\nVERDICT=待复核\n"
    _report(workspace, original)
    tool = ReviseReportTool(_RecordingSink())

    result = tool.invoke({"key": "verdict", "value": "已复核：配置侧更宽"}, ctx=_context(workspace))

    assert result.ok is True
    assert (workspace / REPORT_FILENAME).read_text(encoding="utf-8") == (
        "CODE=E42\nLOG_TIMEOUT_MS=3000\nCONFIG_TIMEOUT_MS=8000\nVERDICT=已复核：配置侧更宽\n"
    )


def test_revise_rejects_missing_key(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _report(workspace, "CODE=E42\n")
    tool = ReviseReportTool(_RecordingSink())

    result = tool.invoke({"key": "VERDICT", "value": "x"}, ctx=_context(workspace))

    assert result.ok is False
    assert "0 次" in (result.error or "")


def test_revise_rejects_duplicate_key(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _report(workspace, "VERDICT=a\nVERDICT=b\n")
    tool = ReviseReportTool(_RecordingSink())

    result = tool.invoke({"key": "VERDICT", "value": "c"}, ctx=_context(workspace))

    assert result.ok is False
    assert "2 次" in (result.error or "")


def test_revise_rejects_multiline_value(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _report(workspace, "VERDICT=待复核\n")
    tool = ReviseReportTool(_RecordingSink())

    result = tool.invoke({"key": "VERDICT", "value": "a\nCODE=INJECTED"}, ctx=_context(workspace))

    assert result.ok is False
    assert "换行" in (result.error or "")
    assert (workspace / REPORT_FILENAME).read_text(encoding="utf-8") == "VERDICT=待复核\n"


def test_revise_requires_existing_report(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    tool = ReviseReportTool(_RecordingSink())

    result = tool.invoke({"key": "VERDICT", "value": "x"}, ctx=_context(workspace))

    assert result.ok is False
    assert "write_report" in (result.error or "")
