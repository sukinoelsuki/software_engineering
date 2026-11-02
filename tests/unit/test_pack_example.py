"""内置示例案例的定位与复制：安装包不依赖仓库根。"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_sec_perf.foundation.errors import BenchError
from agent_sec_perf.pack.case import load_case_spec
from agent_sec_perf.pack.example import copy_example, example_case_dir


def test_example_case_is_valid() -> None:
    case = load_case_spec(example_case_dir())

    assert case.case_id == "incident-01"
    assert case.selftest is not None


def test_copy_example_to_empty_dir(tmp_path: Path) -> None:
    target = copy_example(tmp_path / "case")

    assert (target / "case.toml").is_file()
    assert load_case_spec(target).case_id == "incident-01"


def test_copy_example_accepts_existing_empty_dir(tmp_path: Path) -> None:
    target = tmp_path / "case"
    target.mkdir()

    result = copy_example(target)

    assert (result / "case.toml").is_file()


def test_copy_example_refuses_non_empty_without_force(tmp_path: Path) -> None:
    target = tmp_path / "case"
    target.mkdir()
    (target / "keep.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(BenchError, match="非空"):
        copy_example(target)


def test_copy_example_force_overwrites_without_deleting_others(tmp_path: Path) -> None:
    target = tmp_path / "case"
    target.mkdir()
    (target / "keep.txt").write_text("keep", encoding="utf-8")

    result = copy_example(target, force=True)

    assert (result / "case.toml").is_file()
    assert (result / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_copy_example_refuses_source_overlap() -> None:
    source = example_case_dir()

    with pytest.raises(BenchError, match="重叠"):
        copy_example(source, force=True)
    with pytest.raises(BenchError, match="重叠"):
        copy_example(source.parent, force=True)
