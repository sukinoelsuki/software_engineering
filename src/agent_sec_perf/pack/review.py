"""能力审查报告：把一次或多次运行变成**可读的模型能力证据**。

``pack run`` 只回答"这次过没过"。但"过没过"不能指导下一轮：同样的 11/11，
可能是模型真的会做题，也可能是判据太宽、只跑了一次、或者预算刚好卡在边界。
本模块从 ``evidence/*.json`` 里抽出可核对的观测，再按维度给出判定与下一轮动作。

三条边界：

1. **只做汇总，不重新判分**：check 的 ``ok`` 由 ``pack run`` 产生；本模块不重跑模型，
   也不把"没观察到"写成"已具备"。
2. **单次样本不证明能力**：``samples=1`` 时，重复性维度明确写 ``not_assessed``；
   2~4 次只写 ``preliminary``；不给出一个看起来精确的"能力分"。
3. **失败归因是启发式**：``F1``~``F9`` 的映射依据是 check 名称、工具名与错误文本。
   报告写"疑似"与证据，不把启发式写成确定结论；最终修法仍由总控按 ``pack plan`` 选择。

产物：``reports/<case>-<stamp>-review.md`` 与 ``.json``。``reports/`` 与 ``evidence/``
一样是运行时产物，不参与 bundle 完整性校验。
"""

from __future__ import annotations

import hashlib
import json
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

from agent_sec_perf.foundation.errors import BenchError
from agent_sec_perf.pack.bundle import EVIDENCE_DIR_NAME, MANIFEST_NAME, verify_bundle
from agent_sec_perf.pack.case import CaseSpec, CaseSpecError, load_case_spec
from agent_sec_perf.pack.plan import REPAIR_CATALOG

__all__ = [
    "REPORT_DIR_NAME",
    "REVIEW_SCHEMA",
    "ReviewArtifacts",
    "ReviewError",
    "write_review",
]

REPORT_DIR_NAME: Final = "reports"
REVIEW_SCHEMA: Final = "capability-pack/review/v1"

_EVIDENCE_SCHEMAS: Final = frozenset({"capability-pack/evidence/v1", "capability-pack/evidence/v2"})

#: 写类工具名。用于判断"allowlist 里有、但一次都没选"（``F9``）。
_WRITE_TOOLS: Final = frozenset(
    {"write_report", "write_incident_report", "write_report_fields", "revise_report"}
)
_WRITE_ONLY_TOOLS: Final = frozenset(
    {"write_report", "write_incident_report", "write_report_fields"}
)


class ReviewError(BenchError, ValueError):
    """证据不可读、bundle 未通过校验，或报告无法生成。"""


@dataclass(frozen=True)
class CheckRecord:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class ToolResultRecord:
    tool: str
    executed: bool
    ok: bool
    error: str | None


@dataclass(frozen=True)
class StageRecord:
    stage_id: str
    exit_code: int
    status: str
    tool_calls: tuple[str, ...]
    results: tuple[ToolResultRecord, ...]
    events: int | None


@dataclass(frozen=True)
class Sample:
    """一次运行的证据（v1 与 v2 都归一成这个形状）。"""

    evidence: Path
    schema: str
    checks: tuple[CheckRecord, ...]
    stages: tuple[StageRecord, ...]
    report: str
    model_path: str
    isolation_tier: str
    isolation_summary: str
    duration_s: float | None
    settings: Mapping[str, object]

    @property
    def passed(self) -> bool:
        return (
            bool(self.checks)
            and all(item.ok for item in self.checks)
            and all(stage.exit_code == 0 for stage in self.stages)
        )


@dataclass(frozen=True)
class SampleMetrics:
    sample: Sample
    checks_passed: int
    checks_total: int
    failed_checks: tuple[str, ...]
    tool_calls: int
    read_calls: int
    write_calls: int
    revise_calls: int
    rejections: int
    not_executed: int
    duplicate_writes: int
    rejection_errors: tuple[str, ...]
    missing_targets: tuple[str, ...]
    stage_allowlists: Mapping[str, frozenset[str]]
    min_tool_calls: int


@dataclass(frozen=True)
class Dimension:
    id: str
    name: str
    verdict: str
    observation: str
    evidence: str


@dataclass(frozen=True)
class Finding:
    code: str
    confidence: str
    evidence: str
    allowed_action: str
    forbidden: str


@dataclass(frozen=True)
class NextAction:
    priority: int
    action: str
    why: str
    verify: str
    forbidden: str


@dataclass(frozen=True)
class Review:
    case: CaseSpec
    generated_at: str
    manifest_sha256: str
    samples: tuple[Sample, ...]
    metrics: tuple[SampleMetrics, ...]
    dimensions: tuple[Dimension, ...]
    findings: tuple[Finding, ...]
    next_actions: tuple[NextAction, ...]
    boundaries: tuple[str, ...]

    @property
    def passed_samples(self) -> int:
        return sum(1 for item in self.metrics if item.sample.passed)

    @property
    def pass_rate(self) -> float:
        return self.passed_samples / len(self.metrics) if self.metrics else 0.0

    def to_json(self) -> dict[str, object]:
        return {
            "schema": REVIEW_SCHEMA,
            "generated_at": self.generated_at,
            "case": {"id": self.case.case_id, "title": self.case.title},
            "bundle_manifest_sha256": self.manifest_sha256,
            "summary": {
                "samples": len(self.samples),
                "passed": self.passed_samples,
                "pass_rate": round(self.pass_rate, 4),
                "checks": _check_summary(self.metrics),
                "models": sorted(
                    {item.sample.model_path for item in self.metrics if item.sample.model_path}
                ),
                "configurations": len(
                    {_settings_key(item.sample) for item in self.metrics if item.sample.settings}
                ),
                "missing_settings": sum(1 for item in self.metrics if not item.sample.settings),
            },
            "samples": [_sample_json(item) for item in self.metrics],
            "dimensions": [
                {
                    "id": item.id,
                    "name": item.name,
                    "verdict": item.verdict,
                    "observation": item.observation,
                    "evidence": item.evidence,
                }
                for item in self.dimensions
            ],
            "findings": [
                {
                    "code": item.code,
                    "confidence": item.confidence,
                    "evidence": item.evidence,
                    "allowed_action": item.allowed_action,
                    "forbidden": item.forbidden,
                }
                for item in self.findings
            ],
            "next_actions": [
                {
                    "priority": item.priority,
                    "action": item.action,
                    "why": item.why,
                    "verify": item.verify,
                    "forbidden": item.forbidden,
                }
                for item in self.next_actions
            ],
            "boundaries": list(self.boundaries),
        }

    def to_markdown(self) -> str:
        lines: list[str] = [
            f"# 能力审查报告：{self.case.title}（{self.case.case_id}）",
            "",
            f"- 生成时间：{self.generated_at}",
            f"- 样本：{len(self.samples)} 次，通过 {self.passed_samples} 次"
            f"（{_pct(self.pass_rate)}{'；单样本，不作通过率结论' if len(self.samples) == 1 else ''}）",
            f"- bundle manifest：`{self.manifest_sha256}`",
            f"- 模型：`{_first_model(self.metrics)}`",
            f"- 隔离：{_first_isolation(self.metrics)}",
            "",
        ]
        lines.extend(_conclusion_lines(self))
        lines.extend(["", "## 1. 能力维度", ""])
        lines.append("判定表示：在这套判据与这些样本下观察到；它不是模型能力标签。")
        lines.append("")
        lines.extend(_dimension_table(self.dimensions))
        lines.extend(["", "## 2. 样本", ""])
        lines.extend(_sample_table(self.metrics))
        lines.extend(["", "## 3. 失效信号与归因", ""])
        lines.extend(_finding_lines(self.findings))
        lines.extend(["", "## 4. 下一轮动作", ""])
        lines.extend(_action_table(self.next_actions))
        lines.extend(["", "## 5. 边界", ""])
        lines.extend(f"- {item}" for item in self.boundaries)
        lines.extend(
            [
                "",
                "## 6. 复现",
                "",
                "```bash",
                f"agent-sec-perf pack review {self.case.case_dir.parent} --all --json",
                "```",
                "",
                "> `pack review` 只读 evidence，不跑模型；判定仍以 `checks` 与 `F1`~`F9` 为准。",
                "",
            ]
        )
        return "\n".join(lines)


@dataclass(frozen=True)
class ReviewArtifacts:
    review: Review
    markdown_path: Path
    json_path: Path


def write_review(
    bundle_dir: Path,
    *,
    evidence_path: Path | None = None,
    all_evidence: bool = False,
    out_dir: Path | None = None,
) -> ReviewArtifacts:
    """从 bundle 的证据生成审查报告（Markdown + JSON）。

    Args:
        bundle_dir: 能力包目录。
        evidence_path: 指定一份证据；省略时取最新一份。
        all_evidence: 汇总 ``evidence/*.json`` 全部样本；与 ``evidence_path`` 互斥。
        out_dir: 报告目录；省略用 ``bundle/reports``。

    Raises:
        ReviewError: bundle 校验不通过、证据缺失/不可读、案例或 pack 声明不可读。
    """
    base = Path(bundle_dir)
    result = verify_bundle(base)
    if not result.ok:
        problems = "；".join(result.problems) or "判据自证未通过"
        raise ReviewError(f"bundle 未通过校验，拒绝生成报告：{problems}")
    try:
        case = load_case_spec(base / "case")
    except CaseSpecError as exc:
        raise ReviewError(f"案例不可读（{type(exc).__name__}）") from exc

    selected = _select_evidence(base, evidence_path=evidence_path, all_evidence=all_evidence)
    samples = tuple(_load_sample(path) for path in selected)
    metrics = tuple(_metrics(sample, case=case) for sample in samples)
    generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    review = Review(
        case=case,
        generated_at=generated_at,
        manifest_sha256=_sha256(base / MANIFEST_NAME),
        samples=samples,
        metrics=metrics,
        dimensions=_dimensions(metrics),
        findings=_findings(metrics),
        next_actions=_next_actions(metrics),
        boundaries=_boundaries(case, metrics),
    )

    directory = Path(out_dir) if out_dir is not None else base / REPORT_DIR_NAME
    directory.mkdir(parents=True, exist_ok=True)
    stem = _report_stem(case=case, samples=samples, all_evidence=all_evidence, now=generated_at)
    markdown_path = directory / f"{stem}-review.md"
    json_path = directory / f"{stem}-review.json"
    markdown_path.write_text(review.to_markdown(), encoding="utf-8")
    json_path.write_text(
        json.dumps(review.to_json(), ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return ReviewArtifacts(review=review, markdown_path=markdown_path, json_path=json_path)


# ---------------------------------------------------------------------------
# 证据读取
# ---------------------------------------------------------------------------


def _select_evidence(
    base: Path, *, evidence_path: Path | None, all_evidence: bool
) -> tuple[Path, ...]:
    if evidence_path is not None and all_evidence:
        raise ReviewError("--evidence 与 --all 互斥：只能选一份或全部")
    directory = base / EVIDENCE_DIR_NAME
    if evidence_path is not None:
        path = Path(evidence_path)
        try:
            path = path.resolve()
            path.relative_to(base.resolve())
        except (OSError, ValueError) as exc:
            raise ReviewError(f"证据文件必须位于 bundle 内：{evidence_path}") from exc
        if not path.is_file():
            raise ReviewError(f"证据文件不存在：{evidence_path}")
        return (path,)

    if not directory.is_dir():
        raise ReviewError("bundle/evidence 不存在：先运行 pack run")
    files = sorted(directory.glob("*.json"))
    if not files:
        raise ReviewError("bundle/evidence 下没有证据：先运行 pack run")
    if all_evidence:
        return tuple(files)
    return (max(files, key=lambda item: item.stat().st_mtime),)


def _load_sample(path: Path) -> Sample:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReviewError(f"证据不可读（{path.name}）：{type(exc).__name__}") from exc
    payload = _mapping(raw, f"证据 {path.name}")
    schema = _text(payload.get("schema"), f"{path.name}.schema")
    if schema not in _EVIDENCE_SCHEMAS:
        raise ReviewError(f"未知的证据 schema：{schema}")

    checks_raw = _sequence(payload.get("checks"), f"{path.name}.checks")
    checks = tuple(_check(item, path) for item in checks_raw)
    stages_raw = _sequence(payload.get("stages"), f"{path.name}.stages")
    stages = tuple(_stage(item, path) for item in stages_raw)

    model = payload.get("model")
    model_path = ""
    if isinstance(model, dict):
        value = cast("Mapping[str, object]", model).get("path")
        if isinstance(value, str):
            model_path = value

    isolation = payload.get("isolation")
    tier = "unknown"
    summary = "unknown"
    if isinstance(isolation, dict):
        table = cast("Mapping[str, object]", isolation)
        tier_value = table.get("tier")
        if isinstance(tier_value, str):
            tier = tier_value
        summary_value = table.get("summary")
        if isinstance(summary_value, str):
            summary = summary_value

    run = payload.get("run")
    duration: float | None = None
    settings: Mapping[str, object] = {}
    if isinstance(run, dict):
        table = cast("Mapping[str, object]", run)
        raw_duration = table.get("duration_s")
        if isinstance(raw_duration, (int, float)) and not isinstance(raw_duration, bool):
            duration = float(raw_duration)
        raw_settings = table.get("settings")
        if isinstance(raw_settings, dict):
            settings = cast("Mapping[str, object]", raw_settings)

    report = payload.get("report")
    return Sample(
        evidence=path,
        schema=schema,
        checks=checks,
        stages=stages,
        report=report if isinstance(report, str) else "",
        model_path=model_path,
        isolation_tier=tier,
        isolation_summary=summary,
        duration_s=duration,
        settings=settings,
    )


def _check(value: object, path: Path) -> CheckRecord:
    table = _mapping(value, f"{path.name}.checks[]")
    name = _text(table.get("name"), f"{path.name}.checks[].name")
    ok = table.get("ok")
    if not isinstance(ok, bool):
        raise ReviewError(f"{path.name} 的 check.ok 必须是布尔值")
    detail = table.get("detail")
    return CheckRecord(name=name, ok=ok, detail=detail if isinstance(detail, str) else "")


def _stage(value: object, path: Path) -> StageRecord:
    table = _mapping(value, f"{path.name}.stages[]")
    stage_id = _text(table.get("id"), f"{path.name}.stages[].id")
    exit_code = table.get("exit_code")
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise ReviewError(f"{path.name} 的 stage.exit_code 必须是整数")
    status = table.get("status")
    calls_raw = table.get("tool_calls")
    calls: tuple[str, ...] = ()
    if isinstance(calls_raw, list):
        calls = tuple(item for item in calls_raw if isinstance(item, str))

    results_raw = table.get("tool_results")
    results: list[ToolResultRecord] = []
    if isinstance(results_raw, list):
        for item in results_raw:
            if not isinstance(item, dict):
                continue
            row = cast("Mapping[str, object]", item)
            tool = row.get("tool")
            executed = row.get("executed")
            ok = row.get("ok")
            error = row.get("error")
            results.append(
                ToolResultRecord(
                    tool=tool if isinstance(tool, str) else "",
                    executed=executed is True,
                    ok=ok is True,
                    error=error if isinstance(error, str) else None,
                )
            )
    events = table.get("events")
    return StageRecord(
        stage_id=stage_id,
        exit_code=exit_code,
        status=status if isinstance(status, str) else "",
        tool_calls=calls,
        results=tuple(results),
        events=events if isinstance(events, int) and not isinstance(events, bool) else None,
    )


# ---------------------------------------------------------------------------
# 指标、维度、归因、下一轮
# ---------------------------------------------------------------------------


def _metrics(sample: Sample, *, case: CaseSpec) -> SampleMetrics:
    calls = tuple(name for stage in sample.stages for name in stage.tool_calls)
    results = tuple(item for stage in sample.stages for item in stage.results)
    passed_checks = sum(1 for item in sample.checks if item.ok)
    failed = tuple(item.name for item in sample.checks if not item.ok)
    rejections = sum(1 for item in results if item.executed and not item.ok)
    not_executed = sum(1 for item in results if not item.executed)
    write_calls = sum(1 for name in calls if name in _WRITE_ONLY_TOOLS)
    revise_calls = sum(1 for name in calls if name == "revise_report")
    errors = tuple(item.error for item in results if item.error)

    allowlists = _stage_allowlists(case)
    missing: list[str] = []
    for stage in sample.stages:
        allowed = allowlists.get(stage.stage_id, frozenset())
        targets = allowed & _WRITE_TOOLS
        if targets and not (targets & set(stage.tool_calls)):
            missing.extend(f"{stage.stage_id}:{name}" for name in sorted(targets))

    return SampleMetrics(
        sample=sample,
        checks_passed=passed_checks,
        checks_total=len(sample.checks),
        failed_checks=failed,
        tool_calls=len(calls),
        read_calls=sum(1 for name in calls if name == "read_file"),
        write_calls=write_calls,
        revise_calls=revise_calls,
        rejections=rejections,
        not_executed=not_executed,
        duplicate_writes=max(0, write_calls - 1),
        rejection_errors=errors,
        missing_targets=tuple(missing),
        stage_allowlists=allowlists,
        min_tool_calls=case.min_tool_calls,
    )


def _stage_allowlists(case: CaseSpec) -> Mapping[str, frozenset[str]]:
    return {
        "draft": _read_allowlist(case.pack_draft_dir),
        "revise": _read_allowlist(case.pack_revise_dir),
    }


def _read_allowlist(directory: Path) -> frozenset[str]:
    path = directory / "pack.toml"
    try:
        document = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ReviewError(f"领域包不可读（{path.name}）：{type(exc).__name__}") from exc
    tools = document.get("tools")
    if not isinstance(tools, dict):
        raise ReviewError(f"领域包缺少 [tools]：{path}")
    allowlist = cast("Mapping[str, object]", tools).get("allowlist")
    if not isinstance(allowlist, list) or not all(isinstance(item, str) for item in allowlist):
        raise ReviewError(f"领域包 allowlist 非法：{path}")
    return frozenset(cast("list[str]", allowlist))


def _dimensions(metrics: Sequence[SampleMetrics]) -> tuple[Dimension, ...]:
    total = len(metrics)
    passed = sum(1 for item in metrics if item.sample.passed)
    all_revision = _all_check(metrics, "修订保真")
    all_report = _all_check(metrics, "落盘报告")
    all_tools = _all_check(metrics, "工具真的执行")
    missing_targets = tuple(target for item in metrics for target in item.missing_targets)
    rejections = sum(item.rejections for item in metrics)
    duplicates = sum(item.duplicate_writes for item in metrics)

    completion = (
        "demonstrated"
        if passed == total and total > 0
        else "partial"
        if passed > 0
        else "not_demonstrated"
    )
    selection = (
        "not_demonstrated" if missing_targets else ("demonstrated" if all_tools else "partial")
    )
    grounding_rejections = sum(
        1
        for item in metrics
        for error in item.rejection_errors
        if "value_not_in_evidence" in error or "不在这两份现场文件里" in error
    )
    grounding = (
        "demonstrated"
        if all_report and grounding_rejections == 0
        else "partial"
        if all_report
        else "not_demonstrated"
    )
    format_rejections = sum(
        1
        for item in metrics
        for error in item.rejection_errors
        if "invalid_report_format" in error or "占位符" in error
    )
    structured = "demonstrated" if all_tools and format_rejections == 0 else "partial"
    revision = "demonstrated" if all_revision else "not_demonstrated"
    recovery = (
        "not_assessed"
        if rejections == 0
        else "demonstrated"
        if passed == total
        else "not_demonstrated"
    )
    budget = (
        "redundant"
        if duplicates
        else "tight"
        if all(item.tool_calls <= item.min_tool_calls + 1 for item in metrics)
        else "sufficient"
    )
    repeatability = (
        "not_assessed"
        if total == 1
        else "preliminary"
        if total < 5
        else "demonstrated"
        if passed == total
        else "partial"
    )
    calls = [item.tool_calls for item in metrics]
    reads = sum(item.read_calls for item in metrics)
    writes = sum(item.write_calls for item in metrics)
    revises = sum(item.revise_calls for item in metrics)
    results = sum(len(stage.results) for item in metrics for stage in item.sample.stages)
    not_executed = sum(item.not_executed for item in metrics)
    report_detail = _check_detail(metrics, "落盘报告")
    revision_detail = _check_detail(metrics, "修订保真")

    return (
        Dimension(
            "task_completion",
            "任务完成",
            completion,
            f"{passed}/{total} 个样本通过；每个样本 {metrics[0].checks_total if metrics else 0} 条 check",
            "checks 与两阶段终态",
        ),
        Dimension(
            "tool_selection",
            "工具选择",
            selection,
            f"read {reads} / write {writes} / revise {revises}；"
            f"目标写工具缺失：{', '.join(missing_targets) or '无'}",
            "每个阶段的 allowlist 与 tool_calls",
        ),
        Dimension(
            "evidence_grounding",
            "证据接地",
            grounding,
            f"证据拒绝 {grounding_rejections} 次；报告判据：{report_detail}",
            "落盘报告判据与工具拒绝信息",
        ),
        Dimension(
            "structured_output",
            "结构化输出",
            structured,
            f"工具结果 {results} 条；未执行 {not_executed}；格式拒绝 {format_rejections}",
            "工具真的执行与错误文本",
        ),
        Dimension(
            "controlled_revision",
            "受控修订",
            revision,
            f"修订保真：{revision_detail}",
            "修订保真",
        ),
        Dimension(
            "recovery",
            "拒绝后恢复",
            recovery,
            f"拒绝 {rejections} 次；样本通过 {passed}/{total}",
            "工具结果与任务终态",
        ),
        Dimension(
            "budget",
            "调用预算",
            budget,
            f"每样本调用 {min(calls) if calls else 0}~{max(calls) if calls else 0}；"
            f"重复写 {duplicates}；最小要求 {metrics[0].min_tool_calls if metrics else 0}",
            "tool_calls 与最小调用要求",
        ),
        Dimension(
            "repeatability",
            "重复性",
            repeatability,
            f"样本 {total}；通过 {passed}；通过率 {_pct(passed / total if total else 0.0)}",
            "evidence 样本数与通过率",
        ),
    )


def _all_check(metrics: Sequence[SampleMetrics], name: str) -> bool:
    found = False
    for item in metrics:
        for check in item.sample.checks:
            if check.name == name:
                found = True
                if not check.ok:
                    return False
    return found


def _check_detail(metrics: Sequence[SampleMetrics], name: str) -> str:
    for item in metrics:
        for check in item.sample.checks:
            if check.name == name:
                return check.detail or "no detail"
    return "not found"


def _findings(metrics: Sequence[SampleMetrics]) -> tuple[Finding, ...]:
    by_code: dict[str, list[str]] = {}
    for item in metrics:
        for target in item.missing_targets:
            by_code.setdefault("F9", []).append(
                f"{item.sample.evidence.name}: 目标工具未选中 {target}"
            )
        for error in item.rejection_errors:
            if "value_not_in_evidence" in error or "不在这两份现场文件里" in error:
                by_code.setdefault("F6", []).append(f"{item.sample.evidence.name}: {error}")
            if "invalid_report_format" in error or "占位符" in error:
                by_code.setdefault("F2", []).append(f"{item.sample.evidence.name}: {error}")
        if item.rejections and not item.sample.passed:
            by_code.setdefault("F3", []).append(
                f"{item.sample.evidence.name}: 拒绝 {item.rejections} 次后任务未通过"
            )
        if item.duplicate_writes:
            by_code.setdefault("F7", []).append(
                f"{item.sample.evidence.name}: 写调用 {item.write_calls} 次"
            )
        if item.not_executed and not item.sample.passed:
            by_code.setdefault("F1/F4", []).append(
                f"{item.sample.evidence.name}: {item.not_executed} 次工具未执行"
            )
        for check in item.sample.checks:
            if not check.ok and ("空白" in check.detail or "排版" in check.detail):
                by_code.setdefault("F8", []).append(f"{item.sample.evidence.name}: {check.detail}")

    catalog = {item.code: item for item in REPAIR_CATALOG}
    findings: list[Finding] = []
    for code, evidence in sorted(by_code.items()):
        if code == "F1/F4":
            findings.append(
                Finding(
                    code=code,
                    confidence="low",
                    evidence="；".join(evidence[:3]),
                    allowed_action="查审计区分 F1（未暴露）与 F4（阶段串味），再调 allowlist 或拆阶段",
                    forbidden="提高全局权限；在提示里写'不要用 X'",
                )
            )
            continue
        option = catalog.get(code)
        if option is None:
            continue
        findings.append(
            Finding(
                code=code,
                confidence="high" if code in {"F6", "F7", "F9"} else "medium",
                evidence="；".join(evidence[:3]),
                allowed_action=option.action,
                forbidden=option.forbidden,
            )
        )
    return tuple(findings)


def _next_actions(metrics: Sequence[SampleMetrics]) -> tuple[NextAction, ...]:
    findings = _findings(metrics)
    actions: list[NextAction] = []
    priority = 1
    for finding in findings:
        actions.append(
            NextAction(
                priority=priority,
                action=f"{finding.code}：{finding.allowed_action}",
                why=finding.evidence,
                verify="改声明式文件后重新 pack build / pack run；同一 check 应消失",
                forbidden=finding.forbidden,
            )
        )
        priority += 1

    total = len(metrics)
    if not findings:
        actions.extend(
            [
                NextAction(
                    priority=priority,
                    action="把样本补到 5 次以上，再用 pack review --all 生成聚合报告",
                    why=f"当前 {total} 次只够写 preliminary，不能估计通过率",
                    verify="看 samples / pass_rate / 重复性维度",
                    forbidden="拿单次通过当能力结论",
                ),
                NextAction(
                    priority=priority + 1,
                    action="做第二个案例（如配置核对），用同一内核跑一次",
                    why="单案例无法验证方法论可迁移；这是当前最大的缺口",
                    verify="新案例 build 自证通过、run 至少 1 次",
                    forbidden="把同一案例换模型当成新案例",
                ),
                NextAction(
                    priority=priority + 2,
                    action="在 1.7B / 0.6B 上跑同一包，记录首个失败点",
                    why="下界探测能给出能力边界的负向证据",
                    verify="失败应能归因到 F6 / F9，而不是环境故障",
                    forbidden="把失败说成模型完全不可用；也不要现场下载顶替",
                ),
                NextAction(
                    priority=priority + 3,
                    action="把 max_completion_tokens 降一档（1536→1024）再跑",
                    why="找预算边界，避免把'预算刚好够'误读成模型能力",
                    verify="同一 checks 是否仍通过；失败时看是否 tool_calls 为空",
                    forbidden="通过放宽判据来换通过",
                ),
                NextAction(
                    priority=priority + 4,
                    action="给 7 条事件流判据补一次通过/失败的事件流自证",
                    why="当前自证只覆盖 4 条内容判据；manifest 已如实标 uncovered",
                    verify="covered 增加，uncovered 减少，且反例仍失败",
                    forbidden="把'未自证'写成'已验证'",
                ),
            ]
        )
    if any(item.tool_calls <= item.min_tool_calls for item in metrics):
        actions.append(
            NextAction(
                priority=len(actions) + 1,
                action="构造一次值不在现场文件里的反例，验证工具拒绝与模型恢复",
                why="当前调用数正好等于最小要求，且没有触发过拒绝；F3/F6 的恢复路径仍未评估",
                verify="工具应拒绝并点名字段；模型下一轮改正；若直接放弃则判 F3",
                forbidden="为了通过而放宽证据校验",
            )
        )
    return tuple(actions)


def _boundaries(case: CaseSpec, metrics: Sequence[SampleMetrics]) -> tuple[str, ...]:
    model = _first_model(metrics) or "未知模型"
    settings = metrics[0].sample.settings if metrics else {}
    tokens = settings.get("max_completion_tokens", case.max_completion_tokens)
    models = sorted({item.sample.model_path for item in metrics if item.sample.model_path})
    known_configurations = {_settings_key(item.sample) for item in metrics if item.sample.settings}
    missing_settings = sum(1 for item in metrics if not item.sample.settings)
    lines = [
        f"单案例：{case.case_id}；结论只对这个案例、这个 pack、这个模型成立",
        f"模型：{model}；目标档是 4B~14B，本报告不把 2B 的结果外推到更大或更小模型",
        f"预算：max_completion_tokens={tokens}；墙钟与调用数不是性能基准",
        "隔离：当前只到 L1，报告不把应用级拦截写成沙箱",
        "样本：1 次标记 not_assessed，2~4 次标记 preliminary；通过率不能当稳定性结论",
        "判据：内容判据 4 条已自证；事件流与审计 7 条仍需真实运行证据",
    ]
    if len(models) > 1:
        lines.append(f"样本使用了多个模型：{'、'.join(models)}；这些样本不能合并成一条通过率")
    if len(known_configurations) > 1:
        lines.append("样本的 token / 超时 / 步数配置不一致；先分组，再比较通过率")
    elif missing_settings and known_configurations:
        lines.append(
            f"有 {missing_settings} 份样本缺少配置记录（旧版 v1 证据）；"
            "其余样本的配置一致，但不要据此推断缺记录样本的配置"
        )
    return tuple(lines)


def _settings_key(sample: Sample) -> str:
    if not sample.settings:
        return ""
    return json.dumps(dict(sample.settings), ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------------------
# 渲染辅助
# ---------------------------------------------------------------------------


def _conclusion_lines(review: Review) -> list[str]:
    total = len(review.metrics)
    passed = review.passed_samples
    if total == 0:
        return ["没有可用样本。"]
    if passed == total:
        return [
            "## 结论",
            "",
            f"在 {total} 次运行中，{passed} 次通过全部机检。这个结论的适用范围是"
            f" `{review.case.case_id}` + 当前 pack + 当前模型；它不证明模型具备通用 agent 能力。",
        ]
    return [
        "## 结论",
        "",
        f"在 {total} 次运行中，{passed} 次通过。先看下面的失效信号，再按 `F1`~`F9`"
        " 选结构性修法；不要先改提示或换更大的模型。",
    ]


def _dimension_table(dimensions: Sequence[Dimension]) -> list[str]:
    lines = [
        "| 维度 | 判定 | 观测 | 依据 |",
        "| --- | --- | --- | --- |",
    ]
    for item in dimensions:
        lines.append(
            f"| {_cell(item.name)} | `{item.verdict}` | {_cell(item.observation)} | "
            f"{_cell(item.evidence)} |"
        )
    return lines


def _sample_table(metrics: Sequence[SampleMetrics]) -> list[str]:
    lines = [
        "| 证据 | 结果 | checks | 工具调用 | 拒绝 | 未执行 | 重复写 | 墙钟 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in metrics:
        duration = "n/a" if item.sample.duration_s is None else f"{item.sample.duration_s:.1f}s"
        lines.append(
            f"| `{item.sample.evidence.name}` | {'pass' if item.sample.passed else 'fail'} | "
            f"{item.checks_passed}/{item.checks_total} | {item.tool_calls} | {item.rejections} | "
            f"{item.not_executed} | {item.duplicate_writes} | {duration} |"
        )
    return lines


def _finding_lines(findings: Sequence[Finding]) -> list[str]:
    if not findings:
        return ["未观察到 `F1`~`F9` 的信号。这个结论只在当前证据范围内成立。"]
    lines = [
        "| 编号 | 置信度 | 证据 | 允许的修法 | 禁止 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in findings:
        lines.append(
            f"| `{item.code}` | {item.confidence} | {_cell(item.evidence)} | "
            f"{_cell(item.allowed_action)} | {_cell(item.forbidden)} |"
        )
    return lines


def _action_table(actions: Sequence[NextAction]) -> list[str]:
    if not actions:
        return ["暂无建议。"]
    lines = [
        "| 优先级 | 动作 | 为什么 | 验证 | 禁止 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in actions:
        lines.append(
            f"| {item.priority} | {_cell(item.action)} | {_cell(item.why)} | "
            f"{_cell(item.verify)} | {_cell(item.forbidden)} |"
        )
    return lines


def _sample_json(item: SampleMetrics) -> dict[str, object]:
    return {
        "evidence": item.sample.evidence.name,
        "schema": item.sample.schema,
        "passed": item.sample.passed,
        "checks_passed": item.checks_passed,
        "checks_total": item.checks_total,
        "failed_checks": list(item.failed_checks),
        "tool_calls": item.tool_calls,
        "read_calls": item.read_calls,
        "write_calls": item.write_calls,
        "revise_calls": item.revise_calls,
        "rejections": item.rejections,
        "not_executed": item.not_executed,
        "duplicate_writes": item.duplicate_writes,
        "duration_s": item.sample.duration_s,
        "model": item.sample.model_path,
        "isolation": item.sample.isolation_tier,
    }


def _check_summary(metrics: Sequence[SampleMetrics]) -> list[dict[str, object]]:
    names: list[str] = []
    for item in metrics:
        for check in item.sample.checks:
            if check.name not in names:
                names.append(check.name)
    summary: list[dict[str, object]] = []
    for name in names:
        ok = sum(
            1
            for item in metrics
            if any(check.name == name and check.ok for check in item.sample.checks)
        )
        summary.append({"name": name, "passed": ok, "total": len(metrics)})
    return summary


def _first_model(metrics: Sequence[SampleMetrics]) -> str:
    for item in metrics:
        if item.sample.model_path:
            return item.sample.model_path
    return ""


def _first_isolation(metrics: Sequence[SampleMetrics]) -> str:
    for item in metrics:
        if item.sample.isolation_summary != "unknown":
            return item.sample.isolation_summary
    for item in metrics:
        if item.sample.isolation_tier != "unknown":
            return item.sample.isolation_tier
    return "unknown"


def _report_stem(*, case: CaseSpec, samples: Sequence[Sample], all_evidence: bool, now: str) -> str:
    stamp = now.replace("-", "").replace(":", "")
    if all_evidence:
        return f"{case.case_id}-all-{stamp}"
    return samples[0].evidence.stem


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"


def _cell(value: str, *, limit: int = 180) -> str:
    text = " ".join(value.split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text.replace("|", "\\|")


def _mapping(value: object, what: str) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise ReviewError(f"{what} 必须是对象")
    return cast("Mapping[str, object]", value)


def _sequence(value: object, what: str) -> Sequence[object]:
    if not isinstance(value, list):
        raise ReviewError(f"{what} 必须是数组")
    return value


def _text(value: object, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise ReviewError(f"{what} 必须是非空字符串")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
