"""轮次报告生成（人读）。

报告只做三件事：

1. 给出本轮数字**与极差**——单次采样不得用于判据；
2. 与"同协议、同参数"的最近若干轮中位数比较，标记超阈项（默认 10%）；
3. 把**不能用于判据**的项显式列出（预算耗尽、隔离降级、轮次 partial）。

比较只在同一"比较签名"内进行：协议版本、上下文长度、线程数、max_tokens 与
档位集合任一不同，都不构成可比序列（否则"改进"与"口径变了"无法区分）。
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence
from typing import cast

from agent_sec_perf.bench.stats import relative_delta_pct

DEFAULT_THRESHOLD_PCT = 10.0
DEFAULT_HISTORY_WINDOW = 7

_METRICS: tuple[tuple[str, str], ...] = (
    ("prefill_tok_per_s", "prefill (tok/s)"),
    ("gen_tok_per_s", "生成 (tok/s)"),
    ("request_elapsed_s", "三任务总耗时 (s)"),
)


def comparison_signature(env: Mapping[str, object]) -> str:
    """生成可比性签名：签名相同才允许放在同一条序列上比较。"""
    params = cast("Mapping[str, object]", env.get("params") or {})
    tiers = cast("Sequence[object]", params.get("tiers") or [])
    return "|".join(
        [
            str(env.get("protocol")),
            f"ctx={params.get('ctx')}",
            f"threads={params.get('threads')}",
            f"max_tokens={params.get('max_tokens')}",
            "tiers=" + ",".join(str(tier) for tier in tiers),
        ]
    )


def _median_of(entries: Sequence[Mapping[str, object]], tier: str, metric: str) -> float | None:
    """从历史索引中取某档某指标的基线（中位数）。"""
    values: list[float] = []
    for entry in entries:
        perf = cast("Mapping[str, object]", entry.get("perf") or {})
        tier_entry = perf.get(tier)
        if not isinstance(tier_entry, Mapping):
            continue
        raw = cast("Mapping[str, object]", tier_entry).get(metric)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            values.append(float(raw))
    return statistics.median(values) if values else None


def comparable_history(
    history: Sequence[Mapping[str, object]],
    *,
    signature: str,
    window: int,
) -> list[Mapping[str, object]]:
    """挑出签名相同、最近 ``window`` 轮的历史记录（按索引顺序，旧的在前）。"""
    matched = [entry for entry in history if entry.get("signature") == signature]
    return matched[-window:]


def build_report(
    *,
    record: Mapping[str, object],
    history: Sequence[Mapping[str, object]],
    threshold_pct: float = DEFAULT_THRESHOLD_PCT,
    window: int = DEFAULT_HISTORY_WINDOW,
) -> str:
    """生成 ``report.md``（含超阈标记与告警清单）。"""
    env = cast("Mapping[str, object]", record["env"])
    perf = cast("Mapping[str, object]", record["perf"])
    capability = cast("Mapping[str, object]", record["capability"])
    signature = comparison_signature(env)
    baseline_entries = comparable_history(history, signature=signature, window=window)

    alerts: list[str] = []
    lines: list[str] = []
    lines.append(f"# 基准轮次报告 {env.get('round_id')}")
    lines.append("")
    lines.append(f"- 状态：`{env.get('status')}`")
    lines.append(f"- 触发：`{_trigger_text(env)}`")
    lines.append(
        f"- 开始 / 结束：`{env.get('started_at')}` → `{env.get('finished_at')}`"
        f"（{_number(env.get('duration_s')):.0f} s）"
    )
    lines.append(f"- 协议版本：`{env.get('protocol')}`（比较签名：`{signature}`）")
    lines.append(f"- 环境：`{_llama_text(env)}`")
    lines.append(f"- 参数：`{_params_text(env)}`；隔离：`{env.get('isolation')}`")
    lines.append(f"- 运行节点：{_runner_text(env)}")
    lines.append("")

    lines.append("## 1. 性能（中位数 ± 极差）")
    lines.append("")
    header = "| 档 | 指标 | 本轮中位数 | 极差 | 基线中位数 | 差异 | 判定 |"
    lines.append(header)
    lines.append("| --- | --- | --- | --- | --- | --- | --- |")
    for tier in sorted(perf):
        tier_perf = cast("Mapping[str, object]", perf[tier])
        for metric, label in _METRICS:
            series = cast("Mapping[str, object]", tier_perf[metric])
            median = _number(series.get("median"))
            spread = _number(series.get("spread_pct"))
            baseline = _median_of(baseline_entries, tier, metric)
            if baseline is None:
                delta_text, verdict = "—（无可比历史）", "建立基线"
            else:
                delta = relative_delta_pct(median, baseline)
                delta_text = f"{delta:+.1f}%"
                over = abs(delta) > threshold_pct
                verdict = "超阈，需查明原因" if over else "在阈内"
                if over:
                    alerts.append(
                        f"{tier} 档 {label} 与可比基线差 {delta:+.1f}%"
                        f"（阈值 ±{threshold_pct:.0f}%，基线取自 {len(baseline_entries)} 轮）"
                    )
            lines.append(
                f"| {tier} | {label} | {median:.2f} | {spread:.1f}% | "
                f"{'—' if baseline is None else f'{baseline:.2f}'} | {delta_text} | {verdict} |"
            )
    lines.append("")

    lines.append("## 2. 资源")
    lines.append("")
    lines.append("| 档 | 加载 (s) | 常驻峰值 (GiB) | 重复次数 | 槽位口径 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for tier in sorted(perf):
        tier_perf = cast("Mapping[str, object]", perf[tier])
        server = cast("Mapping[str, object]", tier_perf.get("server") or {})
        load_spread = tier_perf.get("load_spread_pct")
        spread_text = (
            f"（极差 {_optional_number(load_spread)}%）" if load_spread is not None else ""
        )
        lines.append(
            f"| {tier} | {_optional_number(tier_perf.get('load_seconds'))}{spread_text} | "
            f"{_optional_number(tier_perf.get('peak_memory_gib'))} | "
            f"{tier_perf.get('repeats')} | n_slots={server.get('n_slots')} |"
        )
    lines.append("")

    lines.append("## 3. 能力（逐任务通过率）")
    lines.append("")
    for tier in sorted(capability):
        tier_cap = cast("Mapping[str, object]", capability[tier])
        tasks = cast("Mapping[str, object]", tier_cap["tasks"])
        parts = [f"{task}={_rate(tasks[task])}" for task in sorted(tasks)]
        lines.append(
            f"- **{tier} 档**：综合通过率 "
            f"{_number(tier_cap.get('round_pass_rate')) * 100:.0f}%；{'；'.join(parts)}"
        )
        for task in sorted(tasks):
            task_entry = cast("Mapping[str, object]", tasks[task])
            exhausted = _count_finish_reason(task_entry, "length")
            if exhausted:
                alerts.append(
                    f"{tier} 档 {task} 有 {exhausted} 次 `finish_reason=length`："
                    "属于**预算耗尽**，不得据此判定模型能力不足"
                )
    lines.append("")

    if env.get("status") != "complete":
        errors = cast("Sequence[object]", env.get("errors") or [])
        for item in errors:
            alerts.append(f"轮次未完成：{item}")
    if env.get("isolation") != "user":
        alerts.append(
            f"隔离模式为 `{env.get('isolation')}`：模型产物在**未隔离**的进程里执行，"
            "该轮不得用于安全断言"
        )

    lines.append("## 4. 告警与判定")
    lines.append("")
    if alerts:
        for item in alerts:
            lines.append(f"- [需处理] {item}")
    else:
        lines.append("- 无告警：全部指标在阈内，轮次完整。")
    lines.append("")
    lines.append(
        "> 判据说明：单次采样不得用于阈值判断；本报告的每个数字都来自"
        f"至少 3 次重复（本轮各档重复 {_repeats_text(perf)} 次）。"
        "阈值命中只作**提示**，是否成立需人工结合极差与同签名历史判断。"
    )
    lines.append("")
    return "\n".join(lines)


def index_entry(
    *,
    record: Mapping[str, object],
    report_path: str,
    core_hours: float,
) -> dict[str, object]:
    """生成索引条目（供后续报告计算基线与成本核算）。"""
    env = cast("Mapping[str, object]", record["env"])
    perf = cast("Mapping[str, object]", record["perf"])
    capability = cast("Mapping[str, object]", record["capability"])
    return {
        "round_id": env.get("round_id"),
        "date": str(env.get("round_id") or "")[:10],
        "protocol": env.get("protocol"),
        "label": cast("Mapping[str, object]", env.get("params") or {}).get("label"),
        "signature": comparison_signature(env),
        "status": env.get("status"),
        "isolation": env.get("isolation"),
        "git_commit": cast("Mapping[str, object]", env.get("trigger") or {}).get("commit"),
        "core_hours": round(core_hours, 3),
        "perf": {
            tier: {
                # 键名与记录中的指标名保持一致：基线比较直接按同一套键取值。
                metric: _median(perf, tier, metric)
                for metric, _label in _METRICS
            }
            | {
                "repeats": cast("Mapping[str, object]", perf[tier]).get("repeats"),
                "load_seconds": cast("Mapping[str, object]", perf[tier]).get("load_seconds"),
                "peak_memory_gib": cast("Mapping[str, object]", perf[tier]).get("peak_memory_gib"),
            }
            for tier in perf
        },
        "capability": {
            tier: {
                "round_pass_rate": cast("Mapping[str, object]", capability[tier]).get(
                    "round_pass_rate"
                )
            }
            for tier in capability
        },
        "report": report_path,
    }


def _median(perf: Mapping[str, object], tier: str, metric: str) -> float | None:
    tier_perf = perf.get(tier)
    if not isinstance(tier_perf, Mapping):
        return None
    series = cast("Mapping[str, object]", tier_perf).get(metric)
    if not isinstance(series, Mapping):
        return None
    value = cast("Mapping[str, object]", series).get("median")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _rate(task_entry: object) -> str:
    entry = cast("Mapping[str, object]", task_entry)
    passed = _number(entry.get("passed"))
    total = _number(entry.get("n"))
    return f"{passed:.0f}/{total:.0f}"


def _count_finish_reason(task_entry: Mapping[str, object], reason: str) -> int:
    reasons = task_entry.get("finish_reasons")
    if not isinstance(reasons, Sequence) or isinstance(reasons, str):
        return 0
    return sum(1 for item in reasons if item == reason)


def _number(value: object) -> float:
    return float(cast("float", value)) if isinstance(value, (int, float)) else 0.0


def _optional_number(value: object) -> str:
    if value is None:
        return "—"
    return f"{_number(value):.2f}"


def _repeats_text(perf: Mapping[str, object]) -> str:
    repeats = {cast("Mapping[str, object]", entry).get("repeats") for entry in perf.values()}
    return "/".join(str(item) for item in sorted(str(value) for value in repeats))


def _trigger_text(env: Mapping[str, object]) -> str:
    trigger = cast("Mapping[str, object]", env.get("trigger") or {})
    return (
        f"event={trigger.get('event')} branch={trigger.get('branch')} "
        f"commit={str(trigger.get('commit'))[:8]}"
    )


def _llama_text(env: Mapping[str, object]) -> str:
    llama = cast("Mapping[str, object]", env.get("llama") or {})
    return str(llama.get("version") or "unknown").strip()


def _params_text(env: Mapping[str, object]) -> str:
    params = cast("Mapping[str, object]", env.get("params") or {})
    return ", ".join(f"{key}={params[key]}" for key in ("ctx", "threads", "max_tokens", "label"))


def _runner_text(env: Mapping[str, object]) -> str:
    runner = cast("Mapping[str, object]", env.get("runner") or {})
    return (
        f"cpus={runner.get('cpus')} memory={runner.get('memory_gib')} GiB "
        f"nproc={runner.get('nproc')}"
    )


__all__ = [
    "DEFAULT_HISTORY_WINDOW",
    "DEFAULT_THRESHOLD_PCT",
    "build_report",
    "comparable_history",
    "comparison_signature",
    "index_entry",
]
