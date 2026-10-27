"""能力包内核：案例契约与判据自证（不执行模型）。

这三类断言对应内核的三个前提：

1. **案例能被严格解析**（缺字段/越界一律拒绝）；
2. **判据能自证**（正例必过、反例必败）；
3. **归一化不等于放宽**（空白不算内容，缺值仍然算缺值）。
"""

from __future__ import annotations

import pathlib

import pytest

from agent_sec_perf.foundation.errors import BenchError
from agent_sec_perf.pack.case import CaseSpecError, load_case_spec
from agent_sec_perf.pack.criteria import check_report, normalize, run_selftest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CASE_DIR = REPO_ROOT / "examples" / "showcase" / "incident-01"

_CASE_TEMPLATE = """
[case]
id = "toy"
title = "玩具案例"
scene = "scene"
pack_draft = "pack"
pack_revise = "pack-revise"
min_tool_calls = 1
min_read_calls = 1
min_write_calls = 1
report_file = "report.md"
report_substrings = ["E42"]
report_forbidden = ["待复核"]
report_preserved_keys = ["CODE"]
report_revised_key = "VERDICT"
task_draft = "做第一段"
task_revise = "改结论"

{selftest}

[model]
preferred = "toy.gguf"
max_completion_tokens = 128
request_timeout_s = 10
ready_timeout_s = 10
"""


def test_case_declares_two_stages_and_selftest() -> None:
    case = load_case_spec(CASE_DIR)

    assert case.case_id == "incident-01"
    assert case.selftest is not None
    assert case.selftest.positive.is_file()
    assert case.selftest.negative.is_file()
    assert case.content_criteria_declared


def test_selftest_positive_passes_and_negative_fails() -> None:
    case = load_case_spec(CASE_DIR)

    result = run_selftest(case)

    assert result.ok
    assert result.positive_ok
    assert result.negative_ok
    # 覆盖范围必须如实：内容判据已自证，事件流判据**没有**。
    assert "报告必含项" in result.covered
    assert "工具真的执行" in result.uncovered


def test_whitespace_is_not_semantic_but_missing_value_is() -> None:
    case = load_case_spec(CASE_DIR)
    compact = (
        "CODE=E42\nLOG_TIMEOUT_MS=3000\nCONFIG_TIMEOUT_MS=8000\nVERDICT=配置值8000大于日志值3000\n"
    )
    # 注意：**必含项是子串判据**，写在别的行里也会命中（"8000" 若出现在 VERDICT 里，
    # CONFIG 写错也能通过）。所以这里让缺失值在整个报告里都不出现——
    # 这也是"子串判据只能证伪明显错误，不能证明取值正确"的诚实边界。
    missing = "CODE=E42\nLOG_TIMEOUT_MS=3000\nCONFIG_TIMEOUT_MS=5000\nVERDICT=配置值未知\n"

    compact_checks = {item.name: item.ok for item in check_report(case, compact)}
    missing_checks = {item.name: item.ok for item in check_report(case, missing)}

    assert compact_checks["报告必含项"] is True
    assert missing_checks["报告必含项"] is False
    assert normalize("a b\nc") == "ABC"


def test_missing_selftest_is_rejected(tmp_path: pathlib.Path) -> None:
    case_dir = _make_case(tmp_path, selftest="")

    with pytest.raises(CaseSpecError):
        run_selftest(load_case_spec(case_dir))


def test_selftest_path_must_stay_inside_case(tmp_path: pathlib.Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("CODE=E42\n", encoding="utf-8")
    case_dir = _make_case(
        tmp_path,
        selftest='[selftest]\npositive = "../outside.md"\nnegative = "negative.md"\n',
    )

    with pytest.raises(BenchError):
        load_case_spec(case_dir)


def test_missing_case_toml_is_rejected(tmp_path: pathlib.Path) -> None:
    with pytest.raises(CaseSpecError):
        load_case_spec(tmp_path)


def _make_case(root: pathlib.Path, *, selftest: str) -> pathlib.Path:
    case_dir = root / "case"
    case_dir.mkdir()
    for name in ("scene", "pack", "pack-revise"):
        (case_dir / name).mkdir()
    (case_dir / "selftest").mkdir()
    (case_dir / "selftest" / "positive.md").write_text(
        "CODE=E42\nVERDICT=已复核\n", encoding="utf-8"
    )
    (case_dir / "selftest" / "negative.md").write_text("VERDICT=待复核\n", encoding="utf-8")
    (case_dir / "case.toml").write_text(_CASE_TEMPLATE.format(selftest=selftest), encoding="utf-8")
    return case_dir
