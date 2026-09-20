"""对抗性验证：``T-02``（路径穿越）缓解的**「且留审计」半**——运行时工具层路径穿越被拒**是否真的留痕**。

依据（独立验证，不听实现者的解释）：

* 威胁模型 ``T-02`` 的缓解写的是「**拒绝** + **且留审计**」两半：
  - 「拒绝」半由 ``tests/security/test_path_traversal_rejected.py``（18 个行为用例）独立覆盖；
  - 「且留审计」半**此前无行为层证据**（该文件的旧 docstring 甚至写着「``AuditSink`` 尚未落地、
    无法验证」——此声明现已过时：sink 已于 2026-09-19 落地）。本文件用**真实** ``JsonlAuditSink``
    + **真实** ``ReadFileTool`` 端到端取证，把该半钉死。
* 取证路径（读 ``src/``，只读）：``tools/files.py`` 的 ``ReadFileTool.invoke`` 在
  ``resolve_tool_path`` 抛 ``PathNotAllowedError`` 时（``files.py`` 第 93 行）走 ``_fail``
  （第 121 行），``_fail`` 调 ``tools/registry.py::audit_tool_call``（``ok=False``）⇒
  ``sink.emit`` 落一条 ``TOOL_CALL`` 事件；``loop.py`` 的「工具失败」路径**不再重复 emit**
  （工具已记），只把 ``result.audit_id`` 回填进 ``TOOL_RESULT`` 事件。

本文件**只验证系统真实存在的行为**：真实 sink 落盘、``query_by_id`` 端到端还原，且事件携带
``detail["reason"] == "path_not_allowed"`` 这一**安全语义字段**（不只是「某次失败」）。

闭合 ``T-02``「且留审计」半的哪一部分：

* ✅ 闭合：**工具层**运行时路径穿越被拒 → 一条 ``TOOL_CALL`` 事件被**真实落盘**、可经
  ``query_by_id`` 回放，且 ``detail.reason == "path_not_allowed"``。
* ⚠️ 仍未覆盖（属不同机制，由别的用例/威胁承担，非本文件职责）：
  - ``harness/loop.py::_deny`` 的「未执行」拒绝（``unknown_tool`` / ``policy_denied`` /
    ``approval_denied`` …）走 ``outcome=DENY`` 的审计——那是能力/授权拒绝，不是路径穿越，
    由 ``test_harness_s1_authorization.py`` / ``S1`` 承担，不在 ``T-02`` 范围内；
  - 配置期/装配期的路径穿越（``audit.directory`` 越界）由 ``test_audit_landing_whitelist.py``
    的 ``W1~W8`` 承担，亦非「运行时工具路径穿越」；
  - **设计观察（非缺陷、供裁决）**：本路径穿越拒绝的事件 ``outcome`` 是 ``ERROR``（不是 ``DENY``），
    越权事实靠 ``detail.reason == "path_not_allowed"`` 区分于普通工具错误。证据**存在且可回放**
    ——「且留审计」半按「有记录」口径已闭合；若要求「拒绝必须用 ``DENY`` outcome 表达」，
    那是 ``audit.md`` §2.2 的口径调整，归领导者/架构师裁决，本验证不越权改 ``src/``。
"""

from __future__ import annotations

import pathlib

import pytest

from agent_sec_perf.contracts.audit import AuditEventKind, AuditOutcome
from agent_sec_perf.contracts.tools import ExecutionContext, ToolResult
from agent_sec_perf.observability.audit import JsonlAuditSink
from agent_sec_perf.tools import files as files_module
from agent_sec_perf.tools.files import ReadFileTool


def _ctx(tmp_path: pathlib.Path, work: pathlib.Path) -> ExecutionContext:
    return ExecutionContext(
        session_id="sess-t02",
        call_id="call-t02",
        working_dir=work,
        allowed_roots=(work,),
        timeout_s=5.0,
    )


@pytest.mark.security
def test_runtime_path_traversal_rejection_is_audited(tmp_path: pathlib.Path) -> None:
    """``T-02``「且留审计」半：工具层路径穿越被拒 ⇒ 真实 sink 落盘一条可回放审计事件。

    使用**真实** ``JsonlAuditSink``（``roots=(tmp_path,)`` 仅作测试落盘根，不改动 src）、
    **真实** ``ReadFileTool``，不搭任何替身。断言**拒绝发生** 且 **审计里存在对应事件**
    （含 ``detail.reason == "path_not_allowed"`` 这一安全语义字段）。
    """
    sink = JsonlAuditSink(tmp_path, roots=(tmp_path,))
    tool = ReadFileTool(sink=sink)

    work = tmp_path / "work"
    work.mkdir()

    result = tool.invoke({"path": "../forbidden.txt"}, ctx=_ctx(tmp_path, work))
    # 拒绝确实发生（拒绝半的既定事实，此处只作前提断言）。
    assert isinstance(result, ToolResult)
    assert result.ok is False
    assert result.error is not None

    # 「且留审计」半：拒绝产生了**可回放**的审计事件，且落到了真实 sink（磁盘）。
    assert result.audit_id is not None
    event = sink.query_by_id(result.audit_id)
    assert event is not None, "拒绝事件必须真实落盘、可被 query_by_id 还原"
    assert event.event_id == result.audit_id
    assert event.kind is AuditEventKind.TOOL_CALL
    assert event.call_id == "call-t02"
    # 安全语义字段：这条失败确实是「路径不被允许」，而非笼统的 io_error / 其它。
    assert event.outcome is AuditOutcome.ERROR
    assert event.detail.get("reason") == "path_not_allowed"


@pytest.mark.security
def test_runtime_path_traversal_audit_depends_on_real_emit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """变异探针：摘掉真实 ``emit``（``audit_tool_call`` 不写回 ``audit_id``）后，上面的用例必须变红。

    证明 ``test_runtime_path_traversal_rejection_is_audited`` 不是恒过——它依赖**真实**的
    ``audit_tool_call`` → ``sink.emit`` 落盘链路，而非「默认就有事件」。摘掉 emit 后：拒绝仍发生
    （拒绝逻辑与审计解耦），但 ``audit_id`` 为 ``None``、磁盘无事件 ⇒ 主用例的
    ``query_by_id(...) is not None`` 与 ``detail["reason"]`` 断言必然失败。

    （顺带说明：``detail["reason"] == "path_not_allowed"`` 是从**磁盘读回的真实事件**取字段；
    若有人把工具 ``_fail`` 的 reason 改掉，主用例同样会变红，无需本探针额外覆盖。）
    """
    monkeypatch.setattr(files_module, "audit_tool_call", lambda *a, **k: None)

    sink = JsonlAuditSink(tmp_path, roots=(tmp_path,))
    tool = ReadFileTool(sink=sink)

    work = tmp_path / "work"
    work.mkdir()

    result = tool.invoke({"path": "../forbidden.txt"}, ctx=_ctx(tmp_path, work))
    # 拒绝与审计解耦：摘掉 emit，拒绝仍发生。
    assert result.ok is False
    # 但审计链路断裂 ⇒ 无 audit_id、磁盘无事件（主用例的断言会因此变红）。
    assert result.audit_id is None
    assert sink.query_by_id("any-id") is None
