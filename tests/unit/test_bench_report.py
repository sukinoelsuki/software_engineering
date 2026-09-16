"""报告与可比性判定。

重点：**比较只能在同签名、同指标键的前提下进行**。索引条目里的键名一旦与记录中的
指标名不一致（或签名不一致），基线就会静默取不到——那会让"超阈告警"永远不触发，
比误报更危险。
"""

from __future__ import annotations

from typing import Any

import pytest
from conftest import make_record

from agent_sec_perf.bench.report import (
    build_report,
    comparable_history,
    comparison_signature,
    index_entry,
)


def _index_of(record: dict[str, Any]) -> dict[str, Any]:
    return index_entry(record=record, report_path="daily/2026-09-17/report.md", core_hours=4.0)


@pytest.mark.unit
def test_signature_depends_on_measurement_parameters() -> None:
    """协议或测量参数变了，就不是同一条序列。"""
    base = make_record()
    other = make_record(threads=4)

    assert comparison_signature(base["env"]) != comparison_signature(other["env"])
    assert comparison_signature(base["env"]) == comparison_signature(make_record()["env"])


@pytest.mark.unit
def test_index_entries_carry_comparable_metric_keys() -> None:
    """索引里的指标键必须与记录中的指标名一致，否则基线取不到。"""
    entry = _index_of(make_record())

    assert entry["perf"]["S"]["prefill_tok_per_s"] == pytest.approx(100.0)
    assert entry["perf"]["S"]["gen_tok_per_s"] == pytest.approx(20.0)
    assert entry["signature"] == comparison_signature(make_record()["env"])


@pytest.mark.unit
def test_history_filtering_uses_signature_and_window() -> None:
    """只取同签名、最近 window 轮。"""
    entries = [_index_of(make_record()) for _ in range(3)]
    entries.append(_index_of(make_record(threads=4)))

    matched = comparable_history(
        entries, signature=comparison_signature(make_record()["env"]), window=2
    )

    assert len(matched) == 2


@pytest.mark.unit
def test_report_marks_over_threshold_against_baseline() -> None:
    """与可比基线差异超过阈值时必须告警。"""
    history = [_index_of(make_record())]
    faster = make_record(prefill_values=[130.0, 131.0, 129.0])

    text = build_report(record=faster, history=history, threshold_pct=10.0, window=7)

    assert "超阈，需查明原因" in text
    assert "需处理" in text


@pytest.mark.unit
def test_report_without_comparable_history_builds_baseline() -> None:
    """没有可比历史时明确写"建立基线"，而不是拿不可比的数据去比。"""
    text = build_report(record=make_record(), history=[], threshold_pct=10.0, window=7)

    assert "建立基线" in text
    assert "需处理" not in text


@pytest.mark.unit
def test_report_flags_budget_exhaustion_separately() -> None:
    """`finish_reason=length` 必须被标成"预算耗尽"，不得当作能力不足。"""
    record = make_record(finish_reasons=["stop", "length", "stop"])

    text = build_report(record=record, history=[], threshold_pct=10.0, window=7)

    assert "预算耗尽" in text


@pytest.mark.unit
def test_report_flags_unisolated_execution() -> None:
    """以 root 隔离跑出来的轮次不得用于安全断言，必须显式提示。"""
    record = make_record(isolation="root")

    text = build_report(record=record, history=[], threshold_pct=10.0, window=7)

    assert "未隔离" in text


@pytest.mark.unit
def test_report_flags_partial_round() -> None:
    """半份数据必须被标注出来。"""
    record = make_record(status="partial")

    text = build_report(record=record, history=[], threshold_pct=10.0, window=7)

    assert "轮次未完成" in text
