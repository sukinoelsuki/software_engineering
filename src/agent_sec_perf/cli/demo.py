"""演示案例运行器（``agent-sec-perf demo``）。

它把"一次可展示的端侧 agent 工作流"拆成两个阶段、五步工具调用，**每一步都可核对**：

1. **诊断阶段（draft）**：读 ``app.log`` → 读 ``service.toml`` → ``write_report`` 写初稿；
2. **修订阶段（revise）**：读回 ``report.md`` → ``write_report`` 覆盖，只改 ``VERDICT`` 行；
3. **机检**：终态 / ``seq`` / 读次数 / 写次数 / 落盘报告内容 / 审计回放 / 降级留痕；
4. **审计回放**：从落盘 JSONL 里按 ``audit_id`` 还原每一次工具调用。

为什么拆成两个会话：4B 级模型在"一次会话里连做五步"时容易跳步
（2026-10-26 实测：只读一个文件就编出配置值）。拆成两个工作项后，每一步的判据更窄，
失败也能定位到"是诊断错了，还是修订错了"；而"读回—修改"本身就是真实工作流的形态。

**为什么写能用而不用通用 write_file**：本案例用 ``write_report``（目标固定为工作区
``report.md``、参数只有内容），领域包把它声明为 ``low`` 是"工具更窄"；
通用 ``write_file`` 仍是 ``HIGH`` 且**不在 allowlist**，对模型等同不存在。

本模块**不**修改 ``RunRequest`` / 退出码表 / 安全默认：它只是 ``execute()`` 的调用方。
"""

from __future__ import annotations

import io
import json
import pathlib
import shutil
import tempfile
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, cast

from rich.console import Console
from rich.table import Table
from rich.text import Text

from agent_sec_perf.cli.doctor import DEFAULT_MODEL_DIR, repo_root
from agent_sec_perf.contracts.model import CapabilityTier
from agent_sec_perf.foundation.config import AppConfig, AuditConfig, LoggingConfig, PolicyConfig
from agent_sec_perf.foundation.errors import BenchError
from agent_sec_perf.foundation.logging import sanitize_for_display
from agent_sec_perf.observability.audit import JsonlAuditSink
from agent_sec_perf.security.sandbox import IsolationReport, emit_degradation_audit, probe_isolation

__all__ = [
    "DemoCase",
    "DemoOutcome",
    "StageRun",
    "VerifyCheck",
    "load_case",
    "prepare_workspace",
    "run_demo",
    "verify_stages",
]

#: 案例默认位置（相对仓库根）。
DEFAULT_CASE: Final = pathlib.Path("examples/showcase/incident-01")

#: 演示授予的能力：只读 + 写。写能力只被 ``write_report`` 使用（通用 ``write_file``
#: 不在领域包 allowlist 内，对模型不可见）。**不读用户级 config**，避免演示权限更大。
DEMO_GRANTED_CAPABILITIES: Final[tuple[str, ...]] = ("read_file", "write_file")

#: 演示使用的**模型能力档位**（不是权限）。
#:
#: ⚠️ 必须 ≥ ``STANDARD``：`trimming.TIER_CAPABILITY_BUDGET` 里 ``BASIC`` 只含 ``READ_FILE``，
#: 于是 ``write_report``（声明 ``WRITE_FILE``）**不会出现在模型可见的工具列表**里——
#: 模型只能反复读文件，直到步数耗尽。2026-10-26 实测：用默认 ``BASIC`` 跑了 7 次
#: `read_file`、0 次写，最终 ``LIMIT_REACHED``（失败原因是"档位预算不覆盖写能力"，
#: 不是"模型不会写"）。
#: 档位只决定**暴露面预算**；授权仍由 ``DEMO_GRANTED_CAPABILITIES`` 与实际策略求值决定，
#: 提高档位**不会**给模型多授予一个能力。
DEMO_CAPABILITY_TIER: Final = CapabilityTier.STANDARD

#: 阶段名（顺序固定：先诊断、后修订）。
STAGE_DRAFT: Final = "draft"
STAGE_REVISE: Final = "revise"

_LABEL_LIMIT: Final = 80
_LIVE_CONTENT_LIMIT: Final = 120


@dataclass(frozen=True)
class DemoCase:
    """从 ``case.toml`` 读到的案例声明（唯一真源）。"""

    case_id: str
    title: str
    case_dir: pathlib.Path
    scene_dir: pathlib.Path
    pack_draft_dir: pathlib.Path
    pack_revise_dir: pathlib.Path
    task_draft: str
    task_revise: str
    min_tool_calls: int
    min_read_calls: int
    min_write_calls: int
    report_file: str
    report_substrings: tuple[str, ...]
    report_forbidden: tuple[str, ...]
    report_preserved_keys: tuple[str, ...]
    report_revised_key: str
    preferred_model: str
    max_completion_tokens: int
    request_timeout_s: float
    ready_timeout_s: float


@dataclass(frozen=True)
class VerifyCheck:
    """一条机检结论。"""

    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class StageRun:
    """一个阶段（一次独立会话）的结果。"""

    stage_id: str
    exit_code: int
    events: tuple[Mapping[str, object], ...]
    session_id: str | None

    @property
    def status(self) -> object:
        """最后一条 ``TASK_FINISHED`` 的状态值（没有则 ``None``）。"""
        finished = [item for item in self.events if item.get("kind") == "task_finished"]
        return finished[-1].get("status") if finished else None


@dataclass(frozen=True)
class DemoOutcome:
    """一次演示的完整结果（供 CLI 渲染与测试断言）。"""

    case: DemoCase
    workspace: pathlib.Path
    stages: tuple[StageRun, ...]
    report_text: str
    isolation: IsolationReport
    checks: tuple[VerifyCheck, ...]

    @property
    def passed(self) -> bool:
        """全部机检通过且所有阶段退出码为 0 ⇔ 演示成功。"""
        return all(stage.exit_code == 0 for stage in self.stages) and all(
            item.ok for item in self.checks
        )


# ----------------------------------------------------------------------
# 案例加载与工作区准备
# ----------------------------------------------------------------------


def load_case(case_dir: pathlib.Path | None = None) -> DemoCase:
    """读取并校验 ``case.toml``。

    Raises:
        FileNotFoundError: 案例目录或 ``case.toml`` 缺失。
        ValueError: 必填字段缺失 / 类型不符（这是**仓库内容**问题，不是用户输入）。
    """
    base = (repo_root() / DEFAULT_CASE) if case_dir is None else case_dir
    with (base / "case.toml").open("rb") as handle:
        document = tomllib.load(handle)

    case = _section(document, "case")
    model = _section(document, "model")
    return DemoCase(
        case_id=_require_str(case, "id"),
        title=_require_str(case, "title"),
        case_dir=base,
        scene_dir=base / _require_str(case, "scene"),
        pack_draft_dir=base / _require_str(case, "pack_draft"),
        pack_revise_dir=base / _require_str(case, "pack_revise"),
        task_draft=_require_str(case, "task_draft").strip(),
        task_revise=_require_str(case, "task_revise").strip(),
        min_tool_calls=_require_positive_int(case, "min_tool_calls"),
        min_read_calls=_require_positive_int(case, "min_read_calls"),
        min_write_calls=_require_positive_int(case, "min_write_calls"),
        report_file=_require_str(case, "report_file"),
        report_substrings=_require_str_list(case, "report_substrings"),
        report_forbidden=_require_str_list(case, "report_forbidden"),
        report_preserved_keys=_require_str_list(case, "report_preserved_keys"),
        report_revised_key=_require_str(case, "report_revised_key"),
        preferred_model=_require_str(model, "preferred"),
        max_completion_tokens=_require_positive_int(model, "max_completion_tokens"),
        request_timeout_s=_require_positive_float(model, "request_timeout_s"),
        ready_timeout_s=_require_positive_float(model, "ready_timeout_s"),
    )


def prepare_workspace(case: DemoCase, workspace: pathlib.Path) -> pathlib.Path:
    """把现场复制进工作区，并写入**只授予演示所需能力**的项目级配置。

    返回工作区路径。工作区之外的文件不在 ``allowed_roots`` 内 ⇒ 读不到。
    """
    workspace.mkdir(parents=True, exist_ok=True)
    shutil.copytree(case.scene_dir, workspace, dirs_exist_ok=True)
    _remove_stale_report(workspace, case.report_file)
    granted = ", ".join(f'"{item}"' for item in DEMO_GRANTED_CAPABILITIES)
    (workspace / ".lowspec.toml").write_text(
        "# 演示自动生成：只授予本案例需要的能力（能力只能由显式配置授予）\n"
        "[policy]\n"
        f"granted_capabilities = [{granted}]\n",
        encoding="utf-8",
    )
    return workspace


def _remove_stale_report(workspace: pathlib.Path, filename: str) -> None:
    """删掉上一次运行留下的报告，让本次从"还没有报告"开始。

    为什么必须删：``--workspace`` 会被复用（``demo`` 只准备、``demo --run`` 用同一目录，
    演示脚本就是这么写的）。若不清理，修订阶段会读到**上一轮**的报告并通过机检——
    那证明的不是"模型写完又改了它"，而是"目录里恰有一个旧文件"。
    这是**可判定性**问题，不是整洁问题。

    Raises:
        BenchError: ``filename`` 不是纯文件名（拒绝拼接路径），或删除失败。
    """
    if pathlib.PurePath(filename).name != filename or filename in {".", ".."}:
        raise BenchError(f"report_file 必须是工作区内的纯文件名，收到 {filename!r}")
    target = workspace / filename
    try:
        # 符号链接也删：不跟随链接，避免"删除"变成对链接目标的写操作。
        if target.is_symlink() or target.is_file():
            target.unlink()
    except OSError as exc:
        raise BenchError(f"无法清理旧报告（{type(exc).__name__}），已停止以免误判") from exc


def run_demo(
    *,
    case: DemoCase | None = None,
    model_path: pathlib.Path | None = None,
    model_dir: pathlib.Path = DEFAULT_MODEL_DIR,
    workspace: pathlib.Path | None = None,
    keep: bool = False,
    console: Console | None = None,
) -> DemoOutcome:
    """准备 + 两阶段运行 + 机检 + 审计回放；**不修改任何安全默认**。

    Raises:
        FileNotFoundError: 案例资产或模型缺失（调用方据此返回装配期退出码 3）。
    """
    output = console if console is not None else Console()
    selected = load_case() if case is None else case
    resolved_model = _resolve_model(model_path, model_dir, selected.preferred_model)
    if resolved_model is None:
        raise FileNotFoundError(f"{model_dir} 下没有可用的 .gguf 模型")

    report = probe_isolation()
    temp_handle: tempfile.TemporaryDirectory[str] | None = None
    if workspace is None:
        if keep:
            raw = pathlib.Path(tempfile.mkdtemp(prefix="lowspec-showcase-"))
            workdir = prepare_workspace(selected, raw / "workspace")
        else:
            temp_handle = tempfile.TemporaryDirectory(prefix="lowspec-showcase-")
            workdir = prepare_workspace(selected, pathlib.Path(temp_handle.name) / "workspace")
    else:
        workdir = prepare_workspace(selected, workspace)

    _render_plan(output, selected, workdir, resolved_model, report)
    try:
        draft = _run_stage(
            STAGE_DRAFT,
            selected.task_draft,
            selected,
            selected.pack_draft_dir,
            workdir,
            resolved_model,
            output,
        )
        # 初稿快照**必须**在修订阶段覆盖之前读：否则"只改了结论行"这件事无从判定
        # （修订后的文件里已经看不到初稿长什么样）。
        draft_report = _read_report(workdir, selected.report_file)
        stages: tuple[StageRun, ...] = (draft,)
        if draft.exit_code == 0:
            stages += (
                _run_stage(
                    STAGE_REVISE,
                    selected.task_revise,
                    selected,
                    selected.pack_revise_dir,
                    workdir,
                    resolved_model,
                    output,
                ),
            )
        else:
            # 诊断阶段没成功就没有初稿可修订：继续跑只是再烧一轮推理，
            # 而且会让"修订阶段通过"掩盖"诊断阶段失败"。如实停在第一段。
            output.print(Text("诊断阶段未成功，跳过修订阶段（无初稿可改）。", style="bold red"))
        report_text = _read_report(workdir, selected.report_file)
        checks = list(
            verify_stages(stages, selected, report_text=report_text, draft_report=draft_report)
        )
        checks.extend(_audit_checks(stages))
        if report.matrix.degraded:
            checks.append(_emit_degradations(stages, report))
        outcome = DemoOutcome(
            case=selected,
            workspace=workdir,
            stages=stages,
            report_text=report_text,
            isolation=report,
            checks=tuple(checks),
        )
        _render_report(outcome, output)
        return outcome
    finally:
        if temp_handle is not None:
            temp_handle.cleanup()


# ----------------------------------------------------------------------
# 执行（与 `run` 共用装配与退出码表）
# ----------------------------------------------------------------------


def _run_stage(
    stage_id: str,
    task: str,
    case: DemoCase,
    pack_dir: pathlib.Path,
    workdir: pathlib.Path,
    model_path: pathlib.Path,
    console: Console,
) -> StageRun:
    """跑一个阶段（一次独立会话），返回事件与退出码。

    ``pack_dir`` 由调用方按阶段给出：诊断阶段只暴露写全文工具，修订阶段只暴露改行工具
    （理由见 ``examples/showcase/incident-01/pack-revise/pack.toml`` 的注释）。
    """
    # 延迟导入：app.py 需要注册本模块的命令，顶层互相 import 会成环。
    from agent_sec_perf.cli.app import OUTPUT_JSON, RunRequest, execute

    console.print(Text(f"\n=== 阶段：{stage_id} ===", style="bold cyan"))
    stream = _LiveStream(console=console)
    stderr = io.StringIO()
    request = RunRequest(
        task=task,
        working_dir=workdir,
        # 允许根只有"工作区"与"案例根"：模型读不到演示之外的文件。
        allowed_roots=(workdir, case.case_dir.parent),
        output_format=OUTPUT_JSON,
        pack_directory=pack_dir,
        # 档位决定"模型能看到哪些工具"（BASIC 只有读）⇒ 需要写就必须 ≥ STANDARD。
        capability_tier=DEMO_CAPABILITY_TIER,
        max_steps=8,
        max_consecutive_failures=3,
        tool_timeout_s=30.0,
        max_prompt_tokens=8192,
        max_completion_tokens=case.max_completion_tokens,
        model_path=model_path,
        model_log=workdir / "llama-server.log",
        model_request_timeout_s=case.request_timeout_s,
        model_ready_timeout_s=case.ready_timeout_s,
    )
    config = AppConfig(
        logging=LoggingConfig(),
        policy=PolicyConfig(granted_capabilities=DEMO_GRANTED_CAPABILITIES),
        audit=AuditConfig(),
    )
    exit_code = execute(request, stdout=stream, stderr=stderr, config=config)
    events = _parse_events(stream.getvalue())
    return StageRun(
        stage_id=stage_id,
        exit_code=exit_code,
        events=events,
        session_id=_session_id(events),
    )


class _LiveStream(io.StringIO):
    """既收集 JSONL，又把每行渲染成一行进度（不写产品 stdout 以外的内容）。"""

    def __init__(self, *, console: Console) -> None:
        super().__init__()
        self._console = console

    def write(self, text: str) -> int:
        written = super().write(text)
        for line in text.splitlines():
            if line.strip():
                self._console.print(Text(_live_line(line), style="dim"))
        return written


def _live_line(raw: str) -> str:
    """把一条事件 JSON 变成一行可读进度；解析失败只报"事件"不回显原文。"""
    try:
        payload = cast("Mapping[str, object]", json.loads(raw))
    except json.JSONDecodeError:
        return "· 事件（无法解析）"
    kind = payload.get("kind")
    tool = payload.get("tool_name")
    shown_tool = (
        sanitize_for_display(str(tool), limit=_LABEL_LIMIT) if isinstance(tool, str) else None
    )
    if kind == "tool_call" and shown_tool:
        # 只显示工具名：`TOOL_CALL` 事件在参数校验**之前**产出（loop 的"步 0"），
        # 此刻还没有可展示的 `arguments_summary`。要看读的是哪个文件，看机检的
        # 「工具集使用」与工作区产物，或按 `audit_id` 回放审计。
        return f"→ 调用 {shown_tool}"
    if kind == "tool_result" and shown_tool:
        result = payload.get("result")
        ok = isinstance(result, Mapping) and cast("Mapping[str, object]", result).get("ok") is True
        if ok:
            return f"✓ 结果 {shown_tool}"
        # 失败时显示工具给的**原因**：演示时观众能立刻看到"是格式被拒、还是路径被拒"，
        # 排障时也不必再翻审计（`error` 由工具产生，按不可信文本先净化再截断）。
        reason = None
        if isinstance(result, Mapping):
            detail_error = cast("Mapping[str, object]", result).get("error")
            if isinstance(detail_error, str) and detail_error:
                reason = sanitize_for_display(detail_error, limit=_LIVE_CONTENT_LIMIT)
        return f"✗ 结果 {shown_tool}：{reason}" if reason else f"✗ 结果 {shown_tool}"
    if kind == "policy_decision" and shown_tool:
        return f"· 策略 {shown_tool}"
    if kind == "model_response":
        content = _response_content(payload)
        if content:
            return f"· 模型：{sanitize_for_display(content, limit=_LIVE_CONTENT_LIMIT)}"
        return "· 模型：请求工具"
    if kind == "task_finished":
        return f"· 终态：{payload.get('status')}"
    return f"· {kind}"


# ----------------------------------------------------------------------
# 机检
# ----------------------------------------------------------------------


def verify_stages(
    stages: Sequence[StageRun],
    case: DemoCase,
    *,
    report_text: str,
    draft_report: str = "",
) -> tuple[VerifyCheck, ...]:
    """对两个阶段的事件流与落盘报告逐条断言（纯函数，便于单测；不读盘）。

    Args:
        stages: 实际跑过的阶段（正常为 ``draft`` → ``revise`` 两条）。
        case: 案例声明（判据的唯一来源）。
        report_text: 修订后落盘的报告全文。
        draft_report: **修订前**的初稿快照；缺省空串 ⇒「修订保真」判失败。
    """
    checks: list[VerifyCheck] = []

    expected = (STAGE_DRAFT, STAGE_REVISE)
    observed = tuple(stage.stage_id for stage in stages)
    checks.append(
        VerifyCheck(
            "阶段完整",
            observed == expected,
            f"实际 {' → '.join(observed) or '（无）'}；要求 {' → '.join(expected)}",
        )
    )

    for stage in stages:
        finished = [item for item in stage.events if item.get("kind") == "task_finished"]
        ok = (
            stage.exit_code == 0
            and len(finished) == 1
            and stage.status == "completed"
            and stage.events[-1].get("kind") == "task_finished"
        )
        checks.append(
            VerifyCheck(
                f"{stage.stage_id} 终态",
                ok,
                f"退出码={stage.exit_code}，TASK_FINISHED 条数={len(finished)}，status={stage.status}",
            )
        )
        seqs = [item.get("seq") for item in stage.events]
        checks.append(
            VerifyCheck(
                f"{stage.stage_id} seq 连续",
                seqs == list(range(len(seqs))),
                f"{len(seqs)} 条事件，区间 {_seq_span(seqs)}",
            )
        )

    calls = [item for stage in stages for item in stage.events if item.get("kind") == "tool_call"]
    names = [item.get("tool_name") for item in calls]
    read_calls = names.count("read_file")
    written = (
        names.count("write_report")
        + names.count("write_incident_report")
        + names.count("write_report_fields")
    )
    revised = names.count("revise_report")
    # "写"包含初稿与修订两个工具：案例要求的是"写了一次、又按意见改了一次"。
    write_calls = written + revised
    checks.append(
        VerifyCheck(
            "工具集使用",
            len(calls) >= case.min_tool_calls
            and read_calls >= case.min_read_calls
            and write_calls >= case.min_write_calls,
            (
                f"共 {len(calls)} 次（read_file {read_calls}，写 {written}，"
                f"revise_report {revised}；要求 ≥{case.min_tool_calls} / "
                f"≥{case.min_read_calls} / ≥{case.min_write_calls}）"
            ),
        )
    )

    results = [
        item for stage in stages for item in stage.events if item.get("kind") == "tool_result"
    ]
    executed_ok = [item for item in results if _result_ok(item)]
    denied = [item for item in results if item.get("result") is None]
    checks.append(
        VerifyCheck(
            "工具真的执行",
            len(executed_ok) == len(results) and bool(results) and not denied,
            f"成功执行 {len(executed_ok)}/{len(results)} 次，未执行/被拒 {len(denied)} 次",
        )
    )

    # 空白不承载语义：模型写"配置值 8000 大于日志值 3000"还是"配置值8000大于日志值3000"
    # 是同一句话（2026-10-26 实测：只差空格，判据却判失败）。⇒ 去掉所有空白后比较。
    normalized_report = _normalized(report_text)
    missing = [
        item for item in case.report_substrings if _normalized(item) not in normalized_report
    ]
    leftovers = [item for item in case.report_forbidden if _normalized(item) in normalized_report]
    checks.append(
        VerifyCheck(
            "落盘报告",
            not missing and not leftovers,
            (
                f"{case.report_file}：命中全部 {len(case.report_substrings)} 项"
                if not missing and not leftovers
                else f"缺少 {missing}；残留 {leftovers}"
            ),
        )
    )
    checks.append(_revision_check(case, draft_report=draft_report, final_report=report_text))
    return tuple(checks)


def _revision_check(case: DemoCase, *, draft_report: str, final_report: str) -> VerifyCheck:
    """「修订保真」：初稿里只有 ``report_revised_key`` 允许变，其余行必须逐字不变。

    为什么这条不可省：只看终稿无法区分"按意见改了结论行"与"重写了一份新报告"。
    后者同样能通过 ``report_substrings``，但它证明的能力完全不同——
    本案例要证明的是**受控修订**（改指定的那一行、其余不动），不是"再生成一遍"。
    同一 key 重复出现也算失败：它能让"旧值留着、正确值另起一行"绕过取值比较。
    """
    draft = _key_values(draft_report)
    final = _key_values(final_report)
    if not draft:
        return VerifyCheck("修订保真", False, "初稿为空或没有 KEY=VALUE 行，无法核对修订")

    problems: list[str] = []
    checked = (*case.report_preserved_keys, case.report_revised_key)
    for key in checked:
        if key not in draft:
            problems.append(f"初稿缺 {key}")
        if len(final.get(key, [])) != 1:
            problems.append(f"终稿 {key} 出现 {len(final.get(key, []))} 次（要求恰好 1 次）")
    for key in case.report_preserved_keys:
        if draft.get(key) and final.get(key) and draft[key] != final[key]:
            problems.append(f"{key} 被改动（{draft[key][0]!r} → {final[key][0]!r}）")
    revised = case.report_revised_key
    if draft.get(revised) and final.get(revised) and draft[revised] == final[revised]:
        problems.append(f"{revised} 未变化（等于没修订）")

    if problems:
        return VerifyCheck("修订保真", False, "；".join(problems))
    return VerifyCheck(
        "修订保真",
        True,
        f"{' / '.join(case.report_preserved_keys)} 逐字保留；{revised} 已变更",
    )


def _normalized(text: str) -> str:
    """去掉全部空白并转大写（判据比较用：排版差异不算内容差异）。"""
    return "".join(text.split()).upper()


def _key_values(text: str) -> dict[str, list[str]]:
    """按 ``KEY=VALUE`` 抽出报告行（key 去空白并转大写；值为去空白后的原文）。"""
    values: dict[str, list[str]] = {}
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        name = key.strip().upper()
        if separator and name:
            values.setdefault(name, []).append(value.strip())
    return values


def _result_ok(item: Mapping[str, object]) -> bool:
    """该 ``tool_result`` 是否表示"真的执行且成功"（``result`` 为 ``None`` 是未执行）。"""
    result = item.get("result")
    if not isinstance(result, Mapping):
        return False
    return cast("Mapping[str, object]", result).get("ok") is True


def _audit_checks(stages: Sequence[StageRun]) -> tuple[VerifyCheck, ...]:
    """审计回放：每个阶段每条 ``tool_result`` 的 ``audit_id`` 都要能从落盘文件还原。"""
    session_ids = {stage.session_id for stage in stages if stage.session_id is not None}
    if len(session_ids) != len(stages):
        return (VerifyCheck("审计回放", False, "有阶段缺少 session_id"),)
    try:
        sink = JsonlAuditSink(_audit_directory())
        stored = [event for event in sink.read_all() if event.session_id in session_ids]
    except (BenchError, OSError, ValueError) as exc:
        return (VerifyCheck("审计回放", False, f"读取审计失败（{type(exc).__name__}）"),)

    audit_ids = [
        item.get("audit_id")
        for stage in stages
        for item in stage.events
        if item.get("kind") == "tool_result"
    ]
    usable = [item for item in audit_ids if isinstance(item, str) and item]
    replayable = [item for item in usable if sink.query_by_id(item) is not None]
    return (
        VerifyCheck(
            "审计回放",
            bool(usable) and len(replayable) == len(usable) and bool(stored),
            f"{len(session_ids)} 个会话审计 {len(stored)} 行；{len(replayable)}/{len(usable)} 个 audit_id 可回放",
        ),
    )


def _emit_degradations(stages: Sequence[StageRun], report: IsolationReport) -> VerifyCheck:
    """给**每个会话**各写一条降级审计，并把结果汇总成**一条**机检结论。

    为什么汇总：按 `session_id` 各记一条是**证据需要**（回放时能对上会话）；
    但机检表里出现两行同名结论只会让人以为是重复执行（2026-10-26 实测出现过）。
    """
    session_ids = [stage.session_id for stage in stages if stage.session_id is not None]
    if not session_ids:
        return VerifyCheck("降级留痕", False, "没有 session_id，无法记录降级")
    try:
        sink = JsonlAuditSink(_audit_directory())
        for session_id in session_ids:
            emit_degradation_audit(sink, session_id=session_id, report=report)
        sink.flush()
    except (BenchError, OSError) as exc:
        return VerifyCheck("降级留痕", False, f"写入失败（{type(exc).__name__}）")
    return VerifyCheck(
        "降级留痕",
        True,
        f"{len(session_ids)} 个会话各记一条；tier={report.tier.value}",
    )


def _audit_directory() -> pathlib.Path:
    from agent_sec_perf.foundation.config import default_audit_directory

    return default_audit_directory()


# ----------------------------------------------------------------------
# 渲染
# ----------------------------------------------------------------------


def _render_plan(
    console: Console,
    case: DemoCase,
    workdir: pathlib.Path,
    model_path: pathlib.Path,
    report: IsolationReport,
) -> None:
    console.print(Text(f"演示案例：{case.title}（{case.case_id}）", style="bold cyan"))
    rows = Table(show_header=False, box=None)
    rows.add_column("键", style="bold", no_wrap=True)
    rows.add_column("值")
    rows.add_row(Text("工作区"), Text(str(workdir)))
    rows.add_row(Text("允许根"), Text("工作区 + 案例根（其余路径不可读）"))
    rows.add_row(
        Text("领域包"),
        Text(
            f"诊断 {case.pack_draft_dir.name} / 修订 {case.pack_revise_dir.name}（按阶段收窄暴露面）"
        ),
    )
    rows.add_row(
        Text("工具集"),
        Text("读 read_file / list_dir；写 write_incident_report；改 revise_report（目标固定）"),
    )
    rows.add_row(
        Text("档位"), Text(f"{DEMO_CAPABILITY_TIER.value}（决定工具暴露预算；不改变授权）")
    )
    rows.add_row(Text("流程"), Text("阶段 A 诊断写初稿 → 阶段 B 读回并只改结论行"))
    rows.add_row(Text("模型"), Text(str(model_path)))
    rows.add_row(Text("隔离"), Text(f"{report.tier.value}（未达到 L2 的原因会写入审计）"))
    console.print(rows)


def _render_report(outcome: DemoOutcome, console: Console) -> None:
    table = Table(title="机检结果", header_style="bold cyan")
    table.add_column("检查", style="bold", no_wrap=True)
    table.add_column("结果", no_wrap=True)
    table.add_column("证据")
    for item in outcome.checks:
        table.add_row(
            Text(item.name),
            Text("✓ pass" if item.ok else "✗ fail", style="green" if item.ok else "red"),
            Text(item.detail),
        )
    console.print(table)

    console.print(Text("最终报告（落盘内容）：", style="bold"))
    for line in outcome.report_text.splitlines():
        console.print(Text(f"  {line}"))

    if outcome.passed:
        console.print(
            Text(
                "演示成功：模型读了两份现场文件、写了报告、按复核意见只改结论行；"
                "产物与审计都可核对。",
                style="bold green",
            )
        )
    else:
        console.print(
            Text(
                "演示未通过：先看上面第一条 fail 的证据，再查 llama-server.log。", style="bold red"
            )
        )


# ----------------------------------------------------------------------
# 解析辅助
# ----------------------------------------------------------------------


def _read_report(workdir: pathlib.Path, filename: str) -> str:
    """读落盘报告；不存在返回空串（由机检判失败，而不是抛异常打断报告渲染）。"""
    try:
        return (workdir / filename).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _parse_events(raw: str) -> tuple[Mapping[str, object], ...]:
    """把 JSONL 解析成事件列表；坏行**不静默跳过**（跳过会让 seq 检查失真）。"""
    items: list[Mapping[str, object]] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, dict):
            raise ValueError("事件行不是 JSON 对象")
        items.append(cast("Mapping[str, object]", payload))
    return tuple(items)


def _session_id(events: Sequence[Mapping[str, object]]) -> str | None:
    for item in events:
        value = item.get("session_id")
        if isinstance(value, str) and value:
            return value
    return None


def _response_content(payload: Mapping[str, object]) -> str | None:
    response = payload.get("response")
    if not isinstance(response, Mapping):
        return None
    content = response.get("content")
    return content if isinstance(content, str) else None


def _seq_span(seqs: Sequence[object]) -> str:
    if not seqs:
        return "空"
    return f"{seqs[0]}..{seqs[-1]}"


def _resolve_model(
    explicit: pathlib.Path | None, model_dir: pathlib.Path, preferred: str
) -> pathlib.Path | None:
    if explicit is not None:
        return explicit if explicit.is_file() else None
    candidate = model_dir / preferred
    if candidate.is_file():
        return candidate
    try:
        available = sorted(model_dir.glob("*.gguf"))
    except OSError:
        return None
    return available[0] if available else None


def _section(document: Mapping[str, object], name: str) -> Mapping[str, object]:
    value = document.get(name)
    if not isinstance(value, dict):
        raise ValueError(f"case.toml 缺少 [{name}] 段")
    return cast("Mapping[str, object]", value)


def _require_str(section: Mapping[str, object], key: str) -> str:
    value = section.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"case.toml 的 {key} 必须是非空字符串")
    return value


def _require_positive_int(section: Mapping[str, object], key: str) -> int:
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"case.toml 的 {key} 必须是正整数")
    return value


def _require_positive_float(section: Mapping[str, object], key: str) -> float:
    value = section.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
        raise ValueError(f"case.toml 的 {key} 必须是正数")
    return float(value)


def _require_str_list(section: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = section.get(key)
    if not isinstance(value, list) or not value:
        raise ValueError(f"case.toml 的 {key} 必须是非空字符串数组")
    items: list[str] = []
    for entry in value:
        if not isinstance(entry, str) or not entry:
            raise ValueError(f"case.toml 的 {key} 含非字符串项")
        items.append(entry)
    return tuple(items)
