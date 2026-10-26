"""演示案例的加载、工作区准备与机检断言（不调用模型）。"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from agent_sec_perf.cli.demo import (
    DEMO_CAPABILITY_TIER,
    StageRun,
    load_case,
    prepare_workspace,
    verify_stages,
)
from agent_sec_perf.cli.doctor import repo_root
from agent_sec_perf.contracts.policy import Capability
from agent_sec_perf.harness.domain_pack import load_pack
from agent_sec_perf.harness.trimming import TIER_CAPABILITY_BUDGET

GOOD_REPORT = (
    "CODE=E42\n"
    "LOG_TIMEOUT_MS=3000\n"
    "CONFIG_TIMEOUT_MS=8000\n"
    "VERDICT=配置值 8000 大于日志值 3000，日志侧超时更短"
)

#: 诊断阶段的初稿（「修订保真」的比对基线）。
DRAFT_REPORT = "CODE=E42\nLOG_TIMEOUT_MS=3000\nCONFIG_TIMEOUT_MS=8000\nVERDICT=待复核\n"


def _stage(
    stage_id: str,
    tools: Sequence[str],
    *,
    denied: bool = False,
    exit_code: int = 0,
) -> StageRun:
    """构造一个结构合法的事件流（seq 从 0 连续）。"""
    items: list[Mapping[str, object]] = []
    seq = 0
    for index, name in enumerate(tools):
        items.append({"kind": "tool_call", "seq": seq, "tool_name": name})
        seq += 1
        items.append(
            {
                "kind": "tool_result",
                "seq": seq,
                "tool_name": name,
                "audit_id": f"{stage_id}-{index}",
                "result": None if denied else {"ok": True},
            }
        )
        seq += 1
    items.append({"kind": "model_response", "seq": seq, "response": {"content": "OK"}})
    seq += 1
    items.append({"kind": "task_finished", "seq": seq, "status": "completed"})
    return StageRun(
        stage_id=stage_id,
        exit_code=exit_code,
        events=tuple(items),
        session_id=f"session-{stage_id}",
    )


def _stages(
    *, draft_tools: Sequence[str] | None = None, revise_tools: Sequence[str] | None = None
) -> tuple[StageRun, StageRun]:
    draft = draft_tools or ("read_file", "read_file", "write_report")
    revise = revise_tools or ("read_file", "write_report")
    return (_stage("draft", draft), _stage("revise", revise))


def test_case_declares_two_stages_and_scoped_write() -> None:
    case = load_case()

    assert case.case_id == "incident-01"
    assert case.min_tool_calls == 5
    assert case.min_write_calls == 2
    assert case.task_draft and case.task_revise
    assert "待复核" in case.report_forbidden

    known = frozenset(
        {
            "read_file",
            "list_dir",
            "write_file",
            "run_command",
            "write_report",
            "write_incident_report",
            "revise_report",
        }
    )
    draft_pack = load_pack(case.pack_draft_dir, roots=(repo_root(),), known_tools=known)
    revise_pack = load_pack(case.pack_revise_dir, roots=(repo_root(),), known_tools=known)

    assert draft_pack.capabilities_allowlist == frozenset(
        {Capability.READ_FILE, Capability.WRITE_FILE}
    )
    # 结构化写：格式由工具生成，模型只填四个字段（自由文本写法对 4B 模型不稳定）。
    assert "write_incident_report" in draft_pack.tool_allowlist
    assert "write_report" not in draft_pack.tool_allowlist, "不并列两种写法，避免模型选错"
    assert "write_file" not in draft_pack.tool_allowlist, "通用写工具不得进入演示包暴露面"
    assert "run_command" not in draft_pack.tool_allowlist, "执行工具不得进入演示包暴露面"
    # 分阶段收窄：诊断阶段看不到修订工具（否则模型会在报告还不存在时误调它，
    # 2026-10-26 实测过：连续三次失败 ⇒ 整段 FAILED）。
    assert "revise_report" not in draft_pack.tool_allowlist

    assert revise_pack.capabilities_allowlist == frozenset(
        {Capability.READ_FILE, Capability.WRITE_FILE}
    )
    assert "revise_report" in revise_pack.tool_allowlist
    # 修订阶段不暴露写全文工具 ⇒ "重写整份报告"这个动作不存在。
    assert "write_report" not in revise_pack.tool_allowlist
    assert "write_file" not in revise_pack.tool_allowlist


def test_demo_tier_budget_exposes_write_report() -> None:
    """档位预算必须覆盖写能力。

    `trimming.TIER_CAPABILITY_BUDGET[BASIC]` 只含 `READ_FILE` ⇒ `write_report`
    （声明 `WRITE_FILE`）不会出现在模型可见工具里，模型只能反复读文件直到步数耗尽
    （2026-10-26 实测：7 次 `read_file`、0 次写、`LIMIT_REACHED`）。
    这条用例把"演示档位至少要能看见写"钉死，防止有人把它改回 `BASIC`。
    """
    budget = TIER_CAPABILITY_BUDGET[DEMO_CAPABILITY_TIER]

    assert Capability.READ_FILE in budget
    assert Capability.WRITE_FILE in budget


def test_prepare_workspace_grants_only_required_capabilities(tmp_path) -> None:
    case = load_case()
    workspace = prepare_workspace(case, tmp_path / "workspace")

    assert (workspace / "app.log").is_file()
    assert (workspace / "service.toml").is_file()
    config = (workspace / ".lowspec.toml").read_text(encoding="utf-8")
    assert '"read_file"' in config
    assert '"write_file"' in config
    assert "execute_command" not in config


def test_verify_passes_on_full_workflow() -> None:
    case = load_case()

    checks = verify_stages(_stages(), case, report_text=GOOD_REPORT, draft_report=DRAFT_REPORT)

    failed = [item for item in checks if not item.ok]
    assert failed == [], failed


def test_verify_rejects_unrevised_report() -> None:
    """只写了初稿、没完成修订 ⇒ 报告里残留"待复核"，机检必须失败。"""
    case = load_case()
    unrevised = GOOD_REPORT.replace("配置值 8000 大于日志值 3000，日志侧超时更短", "待复核")

    checks = verify_stages(_stages(), case, report_text=unrevised, draft_report=DRAFT_REPORT)

    report = next(item for item in checks if item.name == "落盘报告")
    assert report.ok is False
    assert "待复核" in report.detail


def test_verify_ignores_whitespace_in_expected_phrases() -> None:
    """结论句只差空格也算命中：排版差异不是内容差异（2026-10-26 实测踩过）。"""
    case = load_case()
    spaced = GOOD_REPORT.replace("配置值 8000 大于日志值 3000", "配置值8000大于日志值3000")

    checks = verify_stages(_stages(), case, report_text=spaced, draft_report=DRAFT_REPORT)

    report = next(item for item in checks if item.name == "落盘报告")
    assert report.ok is True, report.detail


def test_verify_rejects_rewritten_facts() -> None:
    """修订阶段顺手改了取证值 ⇒ 「修订保真」失败（这正是"只改结论行"的判据）。"""
    case = load_case()
    rewritten = GOOD_REPORT.replace("LOG_TIMEOUT_MS=3000", "LOG_TIMEOUT_MS=3500")

    checks = verify_stages(_stages(), case, report_text=rewritten, draft_report=DRAFT_REPORT)

    fidelity = next(item for item in checks if item.name == "修订保真")
    assert fidelity.ok is False
    assert "LOG_TIMEOUT_MS" in fidelity.detail


def test_verify_rejects_duplicate_key() -> None:
    """同一 key 写两行（旧值留着、正确值另起一行）⇒ 必须失败，不能被取值比较放过。"""
    case = load_case()
    duplicated = f"{DRAFT_REPORT}{GOOD_REPORT}\n"

    checks = verify_stages(_stages(), case, report_text=duplicated, draft_report=DRAFT_REPORT)

    fidelity = next(item for item in checks if item.name == "修订保真")
    assert fidelity.ok is False
    assert "恰好 1 次" in fidelity.detail


def test_verify_requires_both_stages() -> None:
    """诊断阶段失败时只跑了一段（不跑修订）⇒ 「阶段完整」失败，不得算演示成功。"""
    case = load_case()
    draft_only = (_stage("draft", ("read_file", "read_file", "write_report")),)

    checks = verify_stages(draft_only, case, report_text=GOOD_REPORT, draft_report=DRAFT_REPORT)

    completeness = next(item for item in checks if item.name == "阶段完整")
    assert completeness.ok is False
    assert "revise" in completeness.detail


def test_scene_log_is_first_error_case(tmp_path) -> None:
    """现场日志必须随仓库交付，且第一条 ERROR 是 E42/3000（防止案例资产再次被忽略）。"""
    case = load_case()
    workspace = prepare_workspace(case, tmp_path / "workspace")
    lines = (workspace / "app.log").read_text(encoding="utf-8").splitlines()

    errors = [line for line in lines if "ERROR" in line]
    assert errors, "案例日志里没有 ERROR 行"
    assert "code=E42" in errors[0] and "timeout_ms=3000" in errors[0]
    assert any("code=E99" in line for line in errors[1:]), (
        "缺少第二条干扰行，无法验证首条 ERROR 约束"
    )


def test_prepare_workspace_removes_stale_report(tmp_path) -> None:
    """复用工作区时必须清掉上一轮的 report.md，否则修订阶段会读到旧报告而"假通过"。"""
    case = load_case()
    workspace = prepare_workspace(case, tmp_path / "workspace")
    (workspace / case.report_file).write_text("CODE=OLD\n", encoding="utf-8")

    prepare_workspace(case, workspace)

    assert not (workspace / case.report_file).exists()


def test_verify_rejects_single_write_and_denied_tool() -> None:
    case = load_case()
    stages = (
        _stage("draft", ("read_file", "read_file", "write_report")),
        _stage("revise", ("read_file",), denied=True),
    )

    checks = verify_stages(stages, case, report_text=GOOD_REPORT)

    tools = next(item for item in checks if item.name == "工具集使用")
    executed = next(item for item in checks if item.name == "工具真的执行")
    assert tools.ok is False
    assert executed.ok is False
