"""能力审查报告：从 evidence 生成维度、归因与下一轮动作。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_sec_perf.cli.app import app
from agent_sec_perf.pack.bundle import build_bundle
from agent_sec_perf.pack.review import ReviewError, write_review

REPO_ROOT = Path(__file__).resolve().parents[2]
CASE_DIR = REPO_ROOT / "examples" / "showcase" / "incident-01"
runner = CliRunner()


def _build(tmp_path: Path) -> Path:
    bundle = tmp_path / "bundle"
    build_bundle(CASE_DIR, bundle, force=True)
    return bundle


def _checks(*, report_ok: bool = True) -> list[dict[str, object]]:
    return [
        {"name": "工具真的执行", "ok": True, "detail": "执行 5/5 次"},
        {"name": "落盘报告", "ok": report_ok, "detail": "命中全部 4 项" if report_ok else "缺少"},
        {"name": "修订保真", "ok": True, "detail": "三个 KEY 逐字保留"},
    ]


def _stages(*, draft_calls: tuple[str, ...] = ("read_file", "read_file", "write_incident_report")):
    return [
        {
            "id": "draft",
            "exit_code": 0,
            "status": "completed",
            "events": 8,
            "tool_calls": list(draft_calls),
            "tool_results": [
                {"tool": name, "executed": True, "ok": True, "error": None} for name in draft_calls
            ],
        },
        {
            "id": "revise",
            "exit_code": 0,
            "status": "completed",
            "events": 4,
            "tool_calls": ["read_file", "revise_report"],
            "tool_results": [
                {"tool": "read_file", "executed": True, "ok": True, "error": None},
                {"tool": "revise_report", "executed": True, "ok": True, "error": None},
            ],
        },
    ]


def _write_evidence(
    bundle: Path,
    name: str,
    *,
    checks: list[dict[str, object]] | None = None,
    stages: list[dict[str, object]] | None = None,
    schema: str = "capability-pack/evidence/v2",
    report: str = "CODE=E42\nLOG_TIMEOUT_MS=3000\nCONFIG_TIMEOUT_MS=8000\n"
    "VERDICT=配置值 8000 大于日志值 3000，日志侧超时更短\n",
) -> Path:
    path = bundle / "evidence" / name
    payload = {
        "schema": schema,
        "case_id": "incident-01",
        "model": {"path": "/opt/models/MiniCPM5-2B-Q4_K_M.gguf"},
        "isolation": {"tier": "l1", "summary": "l1（3/8 个维度有负向证据）"},
        "run": {
            "duration_s": 255.0,
            "settings": {"max_completion_tokens": 1536},
        },
        "checks": _checks() if checks is None else checks,
        "stages": _stages() if stages is None else stages,
        "report": report,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_review_passing_samples(tmp_path: Path) -> None:
    bundle = _build(tmp_path)
    _write_evidence(bundle, "a.json")
    _write_evidence(bundle, "b.json")

    artifacts = write_review(bundle, all_evidence=True)

    payload = artifacts.review.to_json()
    assert payload["summary"]["samples"] == 2
    assert payload["summary"]["passed"] == 2
    assert payload["findings"] == []
    assert artifacts.markdown_path.is_file()
    assert "## 1. 能力维度" in artifacts.markdown_path.read_text(encoding="utf-8")


def test_review_flags_f9_f6_and_f3(tmp_path: Path) -> None:
    bundle = _build(tmp_path)
    _write_evidence(
        bundle,
        "failed.json",
        checks=_checks(report_ok=False),
        stages=[
            {
                "id": "draft",
                "exit_code": 1,
                "status": "failed",
                "events": 3,
                "tool_calls": ["read_file"],
                "tool_results": [
                    {"tool": "read_file", "executed": True, "ok": True, "error": None},
                    {
                        "tool": "write_incident_report",
                        "executed": True,
                        "ok": False,
                        "error": "字段 log_timeout_ms 的值 ':1000' 不在这两份现场文件里",
                    },
                ],
            },
            {
                "id": "revise",
                "exit_code": 1,
                "status": "failed",
                "events": 1,
                "tool_calls": [],
                "tool_results": [],
            },
        ],
        report="",
    )

    review = write_review(bundle, all_evidence=True).review
    codes = {item.code for item in review.findings}

    assert {"F9", "F6", "F3"} <= codes
    assert any("F9" in item.action for item in review.next_actions)


def test_review_rejects_evidence_outside_bundle(tmp_path: Path) -> None:
    bundle = _build(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")

    with pytest.raises(ReviewError):
        write_review(bundle, evidence_path=outside)


def test_review_cli_json(tmp_path: Path) -> None:
    bundle = _build(tmp_path)
    _write_evidence(bundle, "a.json")

    result = runner.invoke(app, ["pack", "review", str(bundle), "--all", "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["schema"] == "capability-pack/review/v1"
    assert payload["artifacts"]["markdown"]
