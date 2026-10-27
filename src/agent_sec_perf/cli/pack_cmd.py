"""``agent-sec-perf pack``：能力包内核的命令面。

五个命令对应内核的五步，**每一步都可单独跑**（这正是"能被大模型驱动"的前提——
总控读上一步的 JSON，决定下一步，而不是一次调用里跑完全程）::

    pack build   案例目录 ──► bundle（校验 + 判据自证 + 摘要）
    pack verify  bundle ──► 完整性 + 判据自证（不执行模型）
    pack status  bundle ──► 状态 + 允许的下一步（机器可读）
    pack plan    ──► 失效模式与允许的结构性修复（机器可读的白名单）
    pack run     bundle + 模型 ──► 一次真实执行 + evidence.json

**职责边界**：本模块只做参数解析、渲染与退出码；判定逻辑全在
:mod:`agent_sec_perf.pack`（可单测、不依赖 Typer）。内核不替大模型生成 prompt，
也不改代码——大模型只产出/修改**声明式**文件，由 ``pack build`` 重新校验。
"""

from __future__ import annotations

import datetime
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, cast

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from agent_sec_perf.cli.demo import DemoOutcome, StageRun, run_demo
from agent_sec_perf.cli.doctor import DEFAULT_MODEL_DIR
from agent_sec_perf.cli.exit_codes import EXIT_ASSEMBLY, EXIT_OK, EXIT_TASK_FAILED
from agent_sec_perf.foundation.logging import sanitize_for_display
from agent_sec_perf.pack.bundle import (
    MANIFEST_NAME,
    BundleError,
    build_bundle,
    verify_bundle,
)
from agent_sec_perf.pack.case import CaseSpecError, load_case_spec
from agent_sec_perf.pack.criteria import SelftestResult
from agent_sec_perf.pack.plan import catalog_as_json
from agent_sec_perf.pack.state import inspect_bundle

__all__ = ["pack_app"]

pack_app = typer.Typer(
    add_completion=False,
    help="能力包内核：构建 / 校验 / 状态 / 修复目录 / 用端侧模型跑一次。",
)

_JSON_INDENT = 2


@pack_app.command("build")
def build(
    case_dir: Annotated[Path, typer.Argument(help="案例目录（含 case.toml 与 [selftest]）")],
    out: Annotated[Path, typer.Option("--out", help="输出目录（bundle）")],
    version: Annotated[str, typer.Option("--version", help="包版本（写入清单）")] = "0.1.0",
    stamp: Annotated[
        str | None,
        typer.Option("--stamp", help="可选的构建标记（如提交哈希）；省略则产物完全确定"),
    ] = None,
    force: Annotated[bool, typer.Option("--force", help="覆盖非空输出目录")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="输出机器可读 JSON")] = False,
) -> None:
    """把案例编译成能力包：校验案例 → 判据自证 → 计算摘要 → 写清单。"""
    try:
        result = build_bundle(case_dir, out, version=version, stamp=stamp, force=force)
    except (BundleError, CaseSpecError, OSError) as exc:
        _fail(exc, as_json=as_json)
        raise typer.Exit(code=EXIT_ASSEMBLY) from None

    if as_json:
        _emit_json(
            {
                "ok": True,
                "bundle": str(result.bundle_dir),
                "name": result.manifest.name,
                "version": result.manifest.version,
                "files": len(result.manifest.files),
                "selftest": _selftest_payload(result.selftest),
            }
        )
        return

    console = Console()
    console.print(Text(f"已构建能力包：{result.bundle_dir}", style="bold green"))
    console.print(
        Text(
            f"文件 {len(result.manifest.files)} 个；判据自证通过"
            f"（覆盖 {len(result.selftest.covered)} 项，未覆盖 {len(result.selftest.uncovered)} 项）"
        )
    )
    console.print(Text("下一步：agent-sec-perf pack verify <目录> --json", style="bold"))


@pack_app.command("verify")
def verify(
    bundle_dir: Annotated[Path, typer.Argument(help="能力包目录")],
    as_json: Annotated[bool, typer.Option("--json", help="输出机器可读 JSON")] = False,
) -> None:
    """校验摘要与判据自证（不执行模型）。篡改 / 缺文件 / 多文件都会被拒。"""
    try:
        result = verify_bundle(bundle_dir)
    except BundleError as exc:
        _fail(exc, as_json=as_json)
        raise typer.Exit(code=EXIT_ASSEMBLY) from None

    payload = {
        "ok": result.ok,
        "bundle": str(result.bundle_dir),
        "problems": list(result.problems),
        "selftest": None if result.selftest is None else _selftest_payload(result.selftest),
        "files": 0 if result.manifest is None else len(result.manifest.files),
        "covered": [] if result.manifest is None else list(result.manifest.covered),
        "uncovered": [] if result.manifest is None else list(result.manifest.uncovered),
    }
    if as_json:
        _emit_json(payload)
    else:
        console = Console()
        style = "bold green" if result.ok else "bold red"
        console.print(Text("校验通过" if result.ok else "校验未通过", style=style))
        for problem in result.problems:
            console.print(Text(f"  ✗ {problem}", style="red"))
        if result.selftest is not None:
            console.print(
                Text(
                    "  判据自证："
                    f"正例全过={result.selftest.positive_ok}，"
                    f"反例至少失败一项={result.selftest.negative_ok}"
                )
            )
    raise typer.Exit(code=EXIT_OK if result.ok else EXIT_TASK_FAILED)


@pack_app.command("status")
def status(
    bundle_dir: Annotated[Path, typer.Argument(help="能力包目录")],
    as_json: Annotated[bool, typer.Option("--json", help="输出机器可读 JSON（给总控读）")] = False,
) -> None:
    """报告当前状态与**允许的下一步**；只读，不执行模型。"""
    result = inspect_bundle(bundle_dir)
    payload = {
        "ok": result.ok,
        "bundle": str(result.bundle_dir),
        "stage": result.stage.value,
        "blockers": list(result.blockers),
        "evidence": list(result.evidence_files),
        "next_actions": [
            {
                "id": action.id,
                "title": action.title,
                "command": action.command,
                "requires": action.requires,
            }
            for action in result.next_actions
        ],
    }
    if as_json:
        _emit_json(payload)
    else:
        console = Console()
        table = Table(show_header=False, box=None)
        table.add_column("键", style="bold", no_wrap=True)
        table.add_column("值")
        table.add_row(Text("阶段"), Text(result.stage.value))
        table.add_row(Text("证据"), Text("、".join(result.evidence_files) or "（无）"))
        table.add_row(Text("阻塞"), Text("；".join(result.blockers) or "（无）"))
        for action in result.next_actions:
            table.add_row(Text(f"→ {action.id}"), Text(action.command))
        console.print(table)
    raise typer.Exit(code=EXIT_OK if result.ok else EXIT_TASK_FAILED)


@pack_app.command("plan")
def plan(
    bundle_dir: Annotated[
        Path | None, typer.Argument(help="能力包目录（可选；给出则附带当前状态）")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="输出机器可读 JSON")] = False,
) -> None:
    """列出失效模式 `F1`~`F8` 与**允许的**结构性修复（机器可读白名单）。"""
    current = None if bundle_dir is None else inspect_bundle(bundle_dir)
    payload: dict[str, object] = {"catalog": catalog_as_json()}
    if current is not None:
        payload["stage"] = current.stage.value
        payload["blockers"] = list(current.blockers)

    if as_json:
        _emit_json(payload)
        return

    console = Console()
    table = Table(title="失效模式 → 允许的结构性修复", header_style="bold cyan")
    table.add_column("编号", no_wrap=True)
    table.add_column("失效")
    table.add_column("观测信号")
    table.add_column("允许的修复")
    table.add_column("禁止")
    for item in catalog_as_json():
        table.add_row(
            Text(item["code"], style="bold"),
            Text(item["failure"]),
            Text(item["signal"]),
            Text(item["action"]),
            Text(item["forbidden"], style="red"),
        )
    console.print(table)
    if current is not None:
        console.print(Text(f"当前阶段：{current.stage.value}", style="bold"))


@pack_app.command("run")
def run(
    bundle_dir: Annotated[Path, typer.Argument(help="能力包目录")],
    model: Annotated[Path | None, typer.Option("--model", help="GGUF 路径；省略按案例首选")] = None,
    model_dir: Annotated[
        Path, typer.Option("--model-dir", help="模型目录（省略按案例首选查找）")
    ] = DEFAULT_MODEL_DIR,
    workspace: Annotated[
        Path | None, typer.Option("--workspace", help="显式工作区（省略用临时目录）")
    ] = None,
    keep: Annotated[bool, typer.Option("--keep", help="保留临时工作区")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="输出机器可读 JSON")] = False,
) -> None:
    """用端侧模型跑一次案例，把结果写成 ``evidence/<...>.json``（需要模型）。"""
    base = Path(bundle_dir)
    case_root = base / "case"
    try:
        case = load_case_spec(case_root)
        outcome = run_demo(
            case=case,
            model_path=model,
            model_dir=model_dir,
            workspace=workspace,
            keep=keep,
            # JSON 模式下 stdout 只允许出 JSON：内核的进度/表格一律走 stderr。
            console=Console(stderr=True) if as_json else None,
        )
    except (BundleError, CaseSpecError, FileNotFoundError, OSError, ValueError) as exc:
        _fail(exc, as_json=as_json)
        raise typer.Exit(code=EXIT_ASSEMBLY) from None

    evidence = _write_evidence(base, outcome=outcome, model_path=model)
    payload = {
        "ok": outcome.passed,
        "bundle": str(base),
        "evidence": str(evidence),
        "checks": [
            {"name": item.name, "ok": item.ok, "detail": item.detail} for item in outcome.checks
        ],
        "workspace": str(outcome.workspace),
        "isolation_tier": outcome.isolation.tier.value,
    }
    if as_json:
        _emit_json(payload)
    else:
        console = Console()
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
        console.print(Text(f"证据已写入：{evidence}"))
        console.print(
            Text(
                "下一步：agent-sec-perf pack plan <目录> --json（按证据选一个结构性修复）",
                style="bold",
            )
        )
    raise typer.Exit(code=EXIT_OK if outcome.passed else EXIT_TASK_FAILED)


# ---------------------------------------------------------------------------
# 内部：JSON 与证据
# ---------------------------------------------------------------------------


def _emit_json(payload: Mapping[str, object]) -> None:
    typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=_JSON_INDENT))


def _fail(exc: Exception, *, as_json: bool) -> None:
    """把故障渲染给调用方；消息可能含来自 bundle 的路径，**先净化再显示**。"""
    message = sanitize_for_display(str(exc), limit=400)
    if as_json:
        _emit_json({"ok": False, "error": type(exc).__name__, "message": message})
        return
    typer.echo(f"无法完成：{message}", err=True)


def _selftest_payload(selftest: SelftestResult) -> dict[str, object]:
    """把 ``SelftestResult`` 转成 JSON（字段名只在这一处定义）。"""
    return {
        "ok": selftest.ok,
        "positive_ok": selftest.positive_ok,
        "negative_ok": selftest.negative_ok,
        "covered": list(selftest.covered),
        "uncovered": list(selftest.uncovered),
    }


def _write_evidence(bundle_dir: Path, *, outcome: DemoOutcome, model_path: Path | None) -> Path:
    """把一次运行写成证据 JSON；证据**不参与**完整性校验（它是产物，不是包内容）。"""
    directory = bundle_dir / "evidence"
    directory.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    path = directory / f"{outcome.case.case_id}-{timestamp}.json"
    manifest_path = bundle_dir / MANIFEST_NAME
    manifest_digest = _sha256(manifest_path) if manifest_path.is_file() else ""

    payload: dict[str, object] = {
        "schema": "capability-pack/evidence/v1",
        "case_id": outcome.case.case_id,
        "bundle_manifest_sha256": manifest_digest,
        "model": {"path": "" if model_path is None else str(model_path)},
        "isolation": {"tier": outcome.isolation.tier.value},
        "checks": [
            {"name": item.name, "ok": item.ok, "detail": item.detail} for item in outcome.checks
        ],
        "stages": [_stage_payload(stage) for stage in outcome.stages],
        "report": outcome.report_text,
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=_JSON_INDENT) + "\n",
        encoding="utf-8",
    )
    return path


def _stage_payload(stage: StageRun) -> dict[str, object]:
    """一个阶段的事件摘要（只保留判据用得上的字段）。"""
    calls = [item for item in stage.events if item.get("kind") == "tool_call"]
    results = [item for item in stage.events if item.get("kind") == "tool_result"]
    return {
        "id": stage.stage_id,
        "exit_code": stage.exit_code,
        "status": stage.status,
        "tool_calls": [item.get("tool_name") for item in calls],
        "tool_results": [
            {
                "tool": item.get("tool_name"),
                "executed": item.get("result") is not None,
                "ok": _result_ok(item),
            }
            for item in results
        ],
    }


def _result_ok(item: Mapping[str, object]) -> bool:
    result = item.get("result")
    if not isinstance(result, Mapping):
        return False
    return cast("Mapping[str, object]", result).get("ok") is True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
