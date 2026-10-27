"""能力包内核：构建、摘要、篡改检测与状态机（不执行模型）。"""

from __future__ import annotations

import json
import pathlib

import pytest

from agent_sec_perf.pack.bundle import BundleError, build_bundle, verify_bundle
from agent_sec_perf.pack.plan import REPAIR_CATALOG, catalog_as_json
from agent_sec_perf.pack.state import BundleStage, inspect_bundle

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CASE_DIR = REPO_ROOT / "examples" / "showcase" / "incident-01"


def test_build_then_verify_is_clean(tmp_path: pathlib.Path) -> None:
    result = build_bundle(CASE_DIR, tmp_path / "bundle")

    assert result.manifest.schema == "capability-pack/v1"
    assert result.manifest.case_id == "incident-01"
    assert result.selftest.ok
    assert (tmp_path / "bundle" / "manifest.json").is_file()
    assert (tmp_path / "bundle" / "README.md").is_file()

    verified = verify_bundle(tmp_path / "bundle")
    assert verified.ok
    assert verified.problems == ()


def test_build_is_deterministic(tmp_path: pathlib.Path) -> None:
    build_bundle(CASE_DIR, tmp_path / "a")
    build_bundle(CASE_DIR, tmp_path / "b")

    first = (tmp_path / "a" / "manifest.json").read_bytes()
    second = (tmp_path / "b" / "manifest.json").read_bytes()

    assert first == second


def test_tampered_file_is_reported(tmp_path: pathlib.Path) -> None:
    build_bundle(CASE_DIR, tmp_path / "bundle")
    target = tmp_path / "bundle" / "case" / "scene" / "app.log"
    target.write_text(target.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")

    result = verify_bundle(tmp_path / "bundle")

    assert not result.ok
    assert any("案例/scene" in problem or "app.log" in problem for problem in result.problems)


def test_extra_file_is_reported(tmp_path: pathlib.Path) -> None:
    build_bundle(CASE_DIR, tmp_path / "bundle")
    (tmp_path / "bundle" / "case" / "extra.txt").write_text("x", encoding="utf-8")

    result = verify_bundle(tmp_path / "bundle")

    assert not result.ok
    assert any("多余文件" in problem for problem in result.problems)


def test_manifest_path_traversal_is_rejected(tmp_path: pathlib.Path) -> None:
    bundle = tmp_path / "evil"
    bundle.mkdir()
    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "capability-pack/v1",
                "name": "evil",
                "version": "1.0.0",
                "case_id": "evil",
                "model": "m.gguf",
                "criteria": {"covered": [], "uncovered": []},
                "files": [{"path": "../escape", "sha256": "0" * 64, "size": 0}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(BundleError):
        verify_bundle(bundle)


def test_non_empty_output_requires_force(tmp_path: pathlib.Path) -> None:
    build_bundle(CASE_DIR, tmp_path / "bundle")

    with pytest.raises(BundleError):
        build_bundle(CASE_DIR, tmp_path / "bundle")

    again = build_bundle(CASE_DIR, tmp_path / "bundle", force=True)
    assert again.manifest.case_id == "incident-01"


def test_status_unbuilt_verified_broken(tmp_path: pathlib.Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert inspect_bundle(empty).stage is BundleStage.UNBUILT

    build_bundle(CASE_DIR, tmp_path / "bundle")
    status = inspect_bundle(tmp_path / "bundle")
    assert status.stage is BundleStage.VERIFIED
    assert status.ok
    assert any(action.id == "run" for action in status.next_actions)

    target = tmp_path / "bundle" / "case" / "case.toml"
    target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    broken = inspect_bundle(tmp_path / "bundle")
    assert broken.stage is BundleStage.BROKEN
    assert not broken.ok


def test_repair_catalog_covers_all_failure_modes() -> None:
    codes = [option.code for option in REPAIR_CATALOG]

    assert codes == [f"F{index}" for index in range(1, 9)]
    assert all(option.action and option.forbidden for option in REPAIR_CATALOG)
    assert [item["code"] for item in catalog_as_json()] == codes
