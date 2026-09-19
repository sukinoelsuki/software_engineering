"""对抗性验证：``REQ-SEC-06`` 的**可回放审计链路**（``ToolResult.audit_id`` → ``query_by_id``）。

依据（独立验证，不听实现者的解释）：

* ``docs/adr/0015-layering-and-reuse-boundary.md`` §7.2 的 ``S1``；
* ``docs/design/interfaces/tools.md`` §1：真正的"是否放行"决策点在 **Harness 侧**
  （schema 校验 → ``decide()`` → ``invoke()``）；``src/agent_sec_perf/harness/`` 已**实现落地**
  （``Session.run`` 走真实 ``TaskLoop`` 决策序列；见威胁模型 §0 结论 1 与 §6）。

本文件**只验证系统真实存在的行为**，不为"凑一条用例"自建替身 Harness：

* 已落地、**可独立验证**的：工具层授权调用的审计**可回放链路**——
  ``invoke()`` 成功时把 ``audit_tool_call`` 返回的 ``event_id`` 作为 ``ToolResult.audit_id``
  回传，且 ``JsonlAuditSink.query_by_id(audit_id)`` 端到端能还原该事件（落盘后从文件读回）。

* **S1 完整语义"能力未授权调用被拒且审计可回放"的 Harness 侧闭环已解除阻塞**
  （原 docstring 记录的两条阻塞现已消除，由验证工程师独立核实：读 ``harness/`` 见
  ``session.py``/``loop.py`` 等已落地；``tests/security/test_harness_s1_authorization.py``
  用真实 ``Session.run`` 跑通、本文件用例复跑全绿）：
  - ``src/agent_sec_perf/harness/`` 已实现 ⇒ "能力授权判定点"不再是缺位；
  - ``S1`` 授权文件已入库 ⇒ "能力未授权调用被拒 + ``TOOL_CALL/DENY`` 与 ``POLICY_DECISION``
    审计 + ``ToolResult.audit_id`` 对应 DENY 事件_id"已由真实 ``Session.run`` 行为级验证
    （策略/工具/注册表/模型以协议替身注入，决策逻辑由替身返回；对照用例证明拒绝非恒过）。

  本文件的定位与 ``.S1`` 授权文件**互补**：``.S1`` 用注入的 ``PolicyEngine``/``Tool`` 替身断言
  **判定与审计关联**（真实 sink 未参与）；本文件用**真实** ``JsonlAuditSink`` + ``ReadFileTool``
  断言**落盘后 ``query_by_id`` 可端到端还原**（授权路径 + 工具层路径越权拒绝路径两条）。
  两处合起来覆盖"S1 的可回放审计链路"这一行为层证据；仍**未覆盖**的缺口（属本验证工程师
  对 ``T-11`` 的独立裁决，见回报）：① 真实 ``PolicyEngine`` 的求值逻辑
  （``.S1`` 用的是 fake，攻击路径 3"策略求值出错却返回 allow"未断言）；
  ② ``T-11`` 名义范围的"工具滥用"半（已授权工具超范围使用），其载体不在 ``S1`` 内。
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

    注意（边界澄清，非阻塞）：本例是**路径越权**（工具层能判定），与 ``S1`` 的
    "**能力未授权**调用被拒"不是同一件事；后者由 ``tests/security/test_harness_s1_authorization.py``
    用真实 ``Session.run`` 覆盖（``src/agent_sec_perf/harness/`` 已落地，能力授权判定点
    在 Harness 侧，``decide()`` 先于 ``invoke()``，``docs/design/interfaces/tools.md`` §1）。
    本文件仍只证明"审计可回放"对**工具层失败调用**成立，不替代 ``.S1`` 对能力层 deny 的断言；
    两处互补，共同构成 ``S1`` 可回放审计链路的行为层证据（缺口见模块 docstring 末段）。
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
