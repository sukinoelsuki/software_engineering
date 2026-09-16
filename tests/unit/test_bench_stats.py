"""重复测量统计的行为约束。

这些断言的来源是 2026-09-16 的实测：同一机器、同一权重、同一参数下 prefill
极差达 8.4%，与"10% 阈值"同量级 ⇒ 单次采样不得用于判据。
"""

from __future__ import annotations

import pytest

from agent_sec_perf.bench.errors import ProtocolError
from agent_sec_perf.bench.stats import relative_delta_pct, success_rate, summarize


@pytest.mark.unit
def test_summarize_reports_median_and_spread() -> None:
    """汇总应给出中位数、极值与极差百分比。"""
    series = summarize([108.69, 117.77, 116.16])

    assert series.n == 3
    assert series.median == pytest.approx(116.16)
    assert series.minimum == pytest.approx(108.69)
    assert series.maximum == pytest.approx(117.77)
    assert series.spread_pct == pytest.approx((117.77 - 108.69) / 116.16 * 100, rel=1e-6)


@pytest.mark.unit
def test_summarize_single_sample_has_zero_spread() -> None:
    """单次采样的极差为 0——它不代表稳定，只代表"没测过第二次"。"""
    series = summarize([42.0])

    assert series.n == 1
    assert series.spread_pct == 0.0


@pytest.mark.unit
def test_summarize_rejects_empty_input() -> None:
    """没有重复就不该产出"汇总"。"""
    with pytest.raises(ProtocolError):
        summarize([])


@pytest.mark.unit
def test_success_rate_averages_passes() -> None:
    """通过率按次数平均。"""
    assert success_rate([True, True, False, False]) == pytest.approx(0.5)


@pytest.mark.unit
def test_success_rate_rejects_empty_input() -> None:
    """没有判定结果时不得给出"通过率"。"""
    with pytest.raises(ProtocolError):
        success_rate([])


@pytest.mark.unit
def test_relative_delta_is_signed() -> None:
    """相对变化带方向：更慢为正、更快为负。"""
    assert relative_delta_pct(120.0, 100.0) == pytest.approx(20.0)
    assert relative_delta_pct(80.0, 100.0) == pytest.approx(-20.0)


@pytest.mark.unit
def test_relative_delta_rejects_zero_baseline() -> None:
    """基线为 0 时不得返回无穷大，必须显式失败。"""
    with pytest.raises(ProtocolError):
        relative_delta_pct(1.0, 0.0)
