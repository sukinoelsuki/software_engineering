"""重复测量的统计汇总。

为什么必须有这一层：单次采样与判据阈值（10%）同量级，直接比较会得出错误结论。
本项目 2026-09-16 实测到同机同参数的 prefill 极差达 8.4%，论证见
``docs/notes/evaluation-pitfalls.md`` 的「情形四：把噪声当成信号」。

因此对外**只暴露汇总量**（中位数、极值、极差百分比），不暴露"某个数"。
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass

from agent_sec_perf.foundation.errors import ProtocolError


@dataclass(frozen=True)
class Series:
    """一组重复测量的汇总。"""

    n: int
    median: float
    minimum: float
    maximum: float
    spread_pct: float
    values: tuple[float, ...]

    def to_json(self) -> dict[str, object]:
        """转为可写入 JSON 的字典（保留原始值以便事后复核）。"""
        return {
            "n": self.n,
            "median": round(self.median, 4),
            "min": round(self.minimum, 4),
            "max": round(self.maximum, 4),
            "spread_pct": round(self.spread_pct, 2),
            "values": [round(value, 4) for value in self.values],
        }


def summarize(values: Sequence[float]) -> Series:
    """汇总一组重复测量。

    Raises:
        ProtocolError: 输入为空（没有重复就不该产出"汇总"）。
    """
    if not values:
        msg = "没有任何重复测量值，无法汇总；请检查轮次编排"
        raise ProtocolError(msg)
    ordered = sorted(values)
    median = statistics.median(values)
    spread_pct = 0.0 if median == 0 else (ordered[-1] - ordered[0]) / median * 100.0
    return Series(
        n=len(values),
        median=median,
        minimum=ordered[0],
        maximum=ordered[-1],
        spread_pct=spread_pct,
        values=tuple(values),
    )


def success_rate(passes: Sequence[bool]) -> float:
    """通过率（0~1）。

    Raises:
        ProtocolError: 输入为空。
    """
    if not passes:
        msg = "没有任何判定结果，无法计算通过率"
        raise ProtocolError(msg)
    return sum(1 for item in passes if item) / len(passes)


def relative_delta_pct(current: float, baseline: float) -> float:
    """相对基线变化的百分比（current 相对 baseline）。

    Raises:
        ProtocolError: 基线为 0（无法计算相对变化，而不是返回无穷大）。
    """
    if baseline == 0:
        msg = "基线为 0，无法计算相对变化"
        raise ProtocolError(msg)
    return (current - baseline) / baseline * 100.0
