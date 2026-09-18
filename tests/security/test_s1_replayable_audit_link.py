"""对抗性验证：``REQ-SEC-06`` 的**可回放审计链路**（``ToolResult.audit_id`` → ``query_by_id``）。

依据（独立验证，不听实现者的解释）：

* ``docs/adr/0015-layering-and-reuse-boundary.md`` §7.2 的 ``S1``；
* ``docs/design/interfaces/tools.md`` §1：真正的"是否放行"决策点在 **Harness 侧**
  （schema 校验 → ``decide()`` → ``invoke()``），``src/agent_sec_perf/harness/`` 尚**未开工**。

本文件**只验证系统真实存在的行为**，不为"凑一条用例"自建替身 Harness：

* 已落地、**可独立验证**的：工具层授权调用的审计**可回放链路**——
  ``invoke()`` 成功时把 ``audit_tool_call`` 返回的 ``event_id`` 作为 ``ToolResult.audit_id``
  回传，且 ``JsonlAuditSink.query_by_id(audit_id)`` 端到端能还原该事件（落盘后从文件读回）。

* **阻塞**（本文件记录、不在替身里假过）：``S1`` 完整语义"**能力未授权**工具调用被拒且审计可回放"
  的**能力层 deny + 留痕**发生在 Harness 侧——能力授权判定点不在工具层
  （``docs/design/interfaces/tools.md`` §1：``decide()`` 先于 ``invoke()``），
  ``src/agent_sec_perf/harness/`` 尚未开工。工具层自身的**路径越权**拒绝已在本文件验证为
  既被拒、也带可回放 ``audit_id``；但"能力未授权"这一判定缺 Harness 而无法端到端验证，
  须待其落盘后由验证工程师闭环补全，详见本轮回报的"遗留/需领导裁决"。
"""

from __future__ import annotations

import pathlib

import pytest

from agent_sec_perf.contracts.audit import AuditEventKind
from agent_sec_perf.contracts.tools import ExecutionContext, ToolResult
from agent_sec_perf.observability.audit import JsonlAuditSink
from agent_sec_perf.tools import files as files_module
from agent_sec_perf.tools.files import ReadFileTool


@pytest.mark.security
def test_authorized_tool_call_audit_is_replayable(tmp_path: pathlib.Path) -> None:
    """``REQ-SEC-06`` 链路：授权调用后 ``ToolResult.audit_id`` 端到端可被 ``query_by_id`` 还原。

    使用真实 ``JsonlAuditSink``（``roots=(tmp_path,)`` 仅作测试落盘根，不改动 src），
    真实 ``ReadFileTool``，不搭任何替身。
    """
    sink = JsonlAuditSink(tmp_path, roots=(tmp_path,))
    tool = ReadFileTool(sink=sink)

    work = tmp_path / "work"
    work.mkdir()
    secret = work / "hello.txt"
    secret.write_text("WORKDIR_CONTENT", encoding="utf-8")

    ctx = ExecutionContext(
        session_id="sess-1",
        call_id="call-1",
        working_dir=work,
        allowed_roots=(work,),
        timeout_s=5.0,
    )

    result = tool.invoke({"path": "hello.txt"}, ctx=ctx)
    assert isinstance(result, ToolResult)
    assert result.ok is True
    assert result.content == "WORKDIR_CONTENT"

    # 可回放链路：audit_id 指向一条落盘事件，且 query_by_id 能从文件端到端还原。
    assert result.audit_id is not None
    event = sink.query_by_id(result.audit_id)
    assert event is not None
    assert event.event_id == result.audit_id
    assert event.kind is AuditEventKind.TOOL_CALL
    assert event.call_id == "call-1"


@pytest.mark.security
def test_denied_tool_call_is_audited_and_replayable(tmp_path: pathlib.Path) -> None:
    """工具层**路径越权**调用被拒，且**带可回放的 audit_id**（落盘后 query_by_id 可还原）。

    这证明"审计可回放"在**工具层**对失败调用也成立——``invoke`` 的异常路径仍走
    ``audit_tool_call`` 并写回 ``audit_id``，不是"成功才审计"。

    注意（``S1`` 真正缺口）：本例是**路径越权**（工具层能判定），与 ``S1`` 的
    "**能力未授权**调用被拒"不是同一件事。能力授权判定点在 **Harness 侧**
    （``decide()`` 先于 ``invoke()``，``docs/design/interfaces/tools.md`` §1），
    ``src/agent_sec_perf/harness/`` 尚未开工 ⇒ **能力层 deny + 留痕**仍属阻塞，
    须待 Harness 落盘后由验证工程师闭环（见本轮回报"遗留/需领导裁决"）。
    """
    sink = JsonlAuditSink(tmp_path, roots=(tmp_path,))
    tool = ReadFileTool(sink=sink)

    work = tmp_path / "work"
    work.mkdir()

    ctx = ExecutionContext(
        session_id="sess-1",
        call_id="call-2",
        working_dir=work,
        allowed_roots=(work,),
        timeout_s=5.0,
    )

    # 指向 allowed_roots 之外 ⇒ 工具层拒绝（解析失败）。
    result = tool.invoke({"path": "../forbidden.txt"}, ctx=ctx)
    assert result.ok is False
    assert result.error is not None  # 越权被拒（路径不在允许根内）

    # 失败调用同样有可回放审计记录（不是"成功才审计"）。
    assert result.audit_id is not None
    event = sink.query_by_id(result.audit_id)
    assert event is not None
    assert event.event_id == result.audit_id
    assert event.kind is AuditEventKind.TOOL_CALL
    assert event.call_id == "call-2"


# ---------------------------------------------------------------------------
# 变异探针（证明上述两条用例非恒过：移除真实保护后，原本应绿的行为会翻红）
#
# 每个探针通过 ``monkeypatch`` 在测试结束时自动还原实现，不留在共享工作树。
# 对照组即上方 2 条正常用例（未变异时全绿）；探针只断言"变异确实命中"，
# 从而证明这些用例抓住的是真实回归、而非恒过。
# ---------------------------------------------------------------------------


@pytest.mark.security
def test_authorized_call_audit_id_wired_depends_on_audit_tool_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """变异探针：若 ``audit_tool_call`` 未把 event_id 写回 ``ToolResult.audit_id``（返回 None）

    ⇒ 授权调用的可回放链路断裂 ⇒ 证明 ``test_authorized_tool_call_audit_is_replayable`` 非恒过。
    """
    monkeypatch.setattr(files_module, "audit_tool_call", lambda *a, **k: None)

    sink = JsonlAuditSink(tmp_path, roots=(tmp_path,))
    tool = ReadFileTool(sink=sink)
    work = tmp_path / "work"
    work.mkdir()
    secret = work / "hello.txt"
    secret.write_text("WORKDIR_CONTENT", encoding="utf-8")
    ctx = ExecutionContext(
        session_id="sess-1",
        call_id="call-probe-1",
        working_dir=work,
        allowed_roots=(work,),
        timeout_s=5.0,
    )
    result = tool.invoke({"path": "hello.txt"}, ctx=ctx)
    # 保护缺失 ⇒ audit_id 未写入（原本用例期望非 None 且可 query_by_id 还原）。
    assert result.audit_id is None


@pytest.mark.security
def test_denied_call_audit_id_wired_depends_on_audit_tool_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path
) -> None:
    """变异探针：若 ``audit_tool_call`` 未把 event_id 写回 ``ToolResult.audit_id``（返回 None）

    ⇒ 失败调用也失去可回放审计 ⇒ 证明 ``test_denied_tool_call_is_audited_and_replayable`` 非恒过。
    """
    monkeypatch.setattr(files_module, "audit_tool_call", lambda *a, **k: None)

    sink = JsonlAuditSink(tmp_path, roots=(tmp_path,))
    tool = ReadFileTool(sink=sink)
    work = tmp_path / "work"
    work.mkdir()
    ctx = ExecutionContext(
        session_id="sess-1",
        call_id="call-probe-2",
        working_dir=work,
        allowed_roots=(work,),
        timeout_s=5.0,
    )
    result = tool.invoke({"path": "../forbidden.txt"}, ctx=ctx)
    # 保护缺失 ⇒ 失败调用同样没有可回放的 audit_id（原本用例期望非 None）。
    assert result.audit_id is None
