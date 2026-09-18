"""判定工具的行为约束。

`parse_pytest_counts` 是"退出码不可信"这条判据修正的落地：退出码是复合信号，
"测试没跑起来"与"测试发现缺陷"都会非零。2026-09-15 的 S 档就因此被误判过。
"""

from __future__ import annotations

import pytest

from agent_sec_perf.bench.evaluate import extract_code, parse_pytest_counts


@pytest.mark.unit
def test_extract_code_takes_longest_fenced_block() -> None:
    """输出里有多个代码块时取最长的一段。"""
    text = "先说明：\n```python\nx = 1\n```\n然后是正式答案：\n```python\nprint(1)\nprint(2)\n```\n"

    code, fenced = extract_code(text)

    assert fenced is True
    assert "print(2)" in code
    assert "x = 1" not in code


@pytest.mark.unit
def test_extract_code_without_fence_returns_raw_text() -> None:
    """没按格式输出时返回原文，并把"无围栏"作为一条可记录的信息。"""
    code, fenced = extract_code("def f(): pass")

    assert fenced is False
    assert code == "def f(): pass"


@pytest.mark.unit
def test_parse_pytest_counts_extracts_structured_results() -> None:
    """从输出中解析 passed / failed / errors。"""
    counts = parse_pytest_counts("1 failed, 2 passed, 1 error in 0.12s")

    assert counts == {"passed": 2, "failed": 1, "errors": 1, "no_tests": 0}


@pytest.mark.unit
def test_parse_pytest_counts_detects_no_tests_ran() -> None:
    """`no tests ran` 必须被识别——否则会被当成"检出力"（本轮曾误判）。"""
    counts = parse_pytest_counts("no tests ran in 0.01s")

    assert counts["no_tests"] == 1
    assert counts["passed"] == 0
    assert counts["failed"] == 0


@pytest.mark.unit
def test_parse_pytest_counts_on_empty_output() -> None:
    """空输出不应抛错，而是全零——由调用方按"没跑起来"处理。"""
    counts = parse_pytest_counts("")

    assert counts == {"passed": 0, "failed": 0, "errors": 0, "no_tests": 0}
