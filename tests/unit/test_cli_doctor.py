"""体检项的纯函数测试（不跑隔离探针、不读真实模型目录）。"""

from __future__ import annotations

from pathlib import Path

from agent_sec_perf.cli.doctor import _check_models, _check_showcase, _mark


def test_missing_models_is_hard_failure(tmp_path: Path) -> None:
    result = _check_models(tmp_path)

    assert result.failed is True
    assert result.name == "端侧模型"
    assert "--model-dir" in result.detail


def test_present_models_are_ok(tmp_path: Path) -> None:
    (tmp_path / "Qwen3-4B-Q4_K_M.gguf").write_bytes(b"not-a-real-model")

    result = _check_models(tmp_path)

    assert result.status == "ok"
    assert "Qwen3-4B" in result.detail


def test_showcase_assets_exist_in_repo() -> None:
    result = _check_showcase()

    assert result.status == "ok"
    assert "incident-01" in result.detail


def test_mark_maps_three_states() -> None:
    assert _mark("ok")[0].startswith("✓")
    assert _mark("warn")[0].startswith("!")
    assert _mark("fail")[0].startswith("✗")
