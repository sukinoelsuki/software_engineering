"""``agent-sec-perf pack`` 的冒烟测试：一条命令链路能跑通，且 JSON 可被总控解析。"""

from __future__ import annotations

import json
import pathlib

from typer.testing import CliRunner

from agent_sec_perf.cli.app import app

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CASE_DIR = REPO_ROOT / "examples" / "showcase" / "incident-01"

runner = CliRunner()


def test_pack_build_verify_status_roundtrip(tmp_path: pathlib.Path) -> None:
    bundle = tmp_path / "bundle"

    built = runner.invoke(app, ["pack", "build", str(CASE_DIR), "--out", str(bundle), "--json"])
    assert built.exit_code == 0, built.output
    built_payload = json.loads(built.stdout)
    assert built_payload["ok"] is True
    assert built_payload["selftest"]["ok"] is True

    verified = runner.invoke(app, ["pack", "verify", str(bundle), "--json"])
    assert verified.exit_code == 0, verified.output
    assert json.loads(verified.stdout)["problems"] == []

    status = runner.invoke(app, ["pack", "status", str(bundle), "--json"])
    assert status.exit_code == 0, status.output
    status_payload = json.loads(status.stdout)
    assert status_payload["stage"] == "verified"
    assert [item["id"] for item in status_payload["next_actions"]]


def test_pack_verify_rejects_tampering_with_exit_code(tmp_path: pathlib.Path) -> None:
    bundle = tmp_path / "bundle"
    assert runner.invoke(app, ["pack", "build", str(CASE_DIR), "--out", str(bundle)]).exit_code == 0

    target = bundle / "case" / "case.toml"
    target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    verified = runner.invoke(app, ["pack", "verify", str(bundle), "--json"])

    assert verified.exit_code == 1
    assert json.loads(verified.stdout)["ok"] is False

    status = runner.invoke(app, ["pack", "status", str(bundle), "--json"])
    assert status.exit_code == 1
    assert json.loads(status.stdout)["stage"] == "broken"


def test_pack_plan_lists_all_failure_modes() -> None:
    result = runner.invoke(app, ["pack", "plan", "--json"])

    assert result.exit_code == 0, result.output
    catalog = json.loads(result.stdout)["catalog"]
    assert [item["code"] for item in catalog] == [f"F{index}" for index in range(1, 9)]
