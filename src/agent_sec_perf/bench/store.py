"""轮次数据的 schema 校验、落盘与索引维护。

数据是**机器写入**、无人复核即进入版本库的，因此入库前必须校验：一份坏数据比
没有数据更糟——它会污染时间序列，而且很难事后发现。校验失败即拒绝提交
（fail-secure）。
"""

from __future__ import annotations

import json
import pathlib
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import cast

from agent_sec_perf.bench.errors import SchemaError

INDEX_FILENAME = "index.json"
LATEST_REPORT_FILENAME = "latest.md"
DAILY_DIRNAME = "daily"

#: 索引保留的最大轮次数（超出后丢弃最旧的）。500 轮 ≈ 一年半的每日轮次。
INDEX_LIMIT = 500

_ROUND_STATUSES = ("complete", "partial")


def now_iso() -> str:
    """当前时间（带时区偏移的 ISO 8601 字符串）。

    带偏移量是刻意的：跨夜、跨时区的数据必须能还原成绝对时刻。
    """
    return datetime.now(tz=UTC).astimezone().isoformat(timespec="seconds")


def today_local() -> str:
    """本地日期（CNB 定时任务使用 Asia/Shanghai 时区）。"""
    return datetime.now(tz=UTC).astimezone().strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------


def _require_mapping(value: object, *, where: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        msg = f"{where} 应为对象，实际是 {type(value).__name__}"
        raise SchemaError(msg)
    return cast("Mapping[str, object]", value)


def _require_sequence(value: object, *, where: str) -> Sequence[object]:
    if isinstance(value, str) or not isinstance(value, Sequence):
        msg = f"{where} 应为数组，实际是 {type(value).__name__}"
        raise SchemaError(msg)
    return cast("Sequence[object]", value)


def _require_str(value: object, *, where: str) -> str:
    if not isinstance(value, str) or not value:
        msg = f"{where} 应为非空字符串"
        raise SchemaError(msg)
    return value


def _require_number(value: object, *, where: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        msg = f"{where} 应为数值，实际是 {type(value).__name__}"
        raise SchemaError(msg)
    return float(value)


def _require_int(value: object, *, where: str, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{where} 应为整数，实际是 {type(value).__name__}"
        raise SchemaError(msg)
    if minimum is not None and value < minimum:
        msg = f"{where} 应 ≥ {minimum}，实际是 {value}"
        raise SchemaError(msg)
    return value


def _validate_series(value: object, *, where: str) -> None:
    """校验一个重复测量的汇总量。"""
    series = _require_mapping(value, where=where)
    n = _require_int(series.get("n"), where=f"{where}.n", minimum=1)
    median = _require_number(series.get("median"), where=f"{where}.median")
    minimum = _require_number(series.get("min"), where=f"{where}.min")
    maximum = _require_number(series.get("max"), where=f"{where}.max")
    spread = _require_number(series.get("spread_pct"), where=f"{where}.spread_pct")
    if not minimum <= median <= maximum:
        msg = f"{where} 的 min/median/max 不满足 min ≤ median ≤ max"
        raise SchemaError(msg)
    if spread < 0:
        msg = f"{where}.spread_pct 不应为负：{spread}"
        raise SchemaError(msg)
    expected = 0.0 if median == 0 else (maximum - minimum) / median * 100.0
    if abs(expected - spread) > 0.5:
        msg = f"{where}.spread_pct 与 min/max/median 不一致：{spread} vs 计算值 {expected:.2f}"
        raise SchemaError(msg)
    if len(_require_sequence(series.get("values"), where=f"{where}.values")) != n:
        msg = f"{where}.values 的长度与 n 不一致"
        raise SchemaError(msg)


def validate_round(record: Mapping[str, object]) -> None:
    """校验一轮记录的完整结构。

    Raises:
        SchemaError: 结构不符合约定（拒绝入库）。
    """
    env = _require_mapping(record.get("env"), where="env")
    _require_str(env.get("protocol"), where="env.protocol")
    _require_str(env.get("round_id"), where="env.round_id")
    _require_str(env.get("started_at"), where="env.started_at")
    status = _require_str(env.get("status"), where="env.status")
    if status not in _ROUND_STATUSES:
        msg = f"env.status 应为 {_ROUND_STATUSES} 之一，实际是 {status!r}"
        raise SchemaError(msg)
    _require_mapping(env.get("params"), where="env.params")
    _require_str(env.get("isolation"), where="env.isolation")

    models = _require_mapping(env.get("models"), where="env.models")
    for tier, entry in models.items():
        asset = _require_mapping(entry, where=f"env.models.{tier}")
        digest = _require_str(asset.get("sha256"), where=f"env.models.{tier}.sha256")
        if len(digest) != 64:
            msg = f"env.models.{tier}.sha256 长度非法：{len(digest)}"
            raise SchemaError(msg)

    perf = _require_mapping(record.get("perf"), where="perf")
    if not perf:
        msg = "perf 为空：没有任何档位数据"
        raise SchemaError(msg)
    for tier, entry in perf.items():
        tier_perf = _require_mapping(entry, where=f"perf.{tier}")
        _require_int(tier_perf.get("repeats"), where=f"perf.{tier}.repeats", minimum=1)
        for metric in ("prefill_tok_per_s", "gen_tok_per_s", "request_elapsed_s"):
            _validate_series(tier_perf.get(metric), where=f"perf.{tier}.{metric}")
        for optional in (
            "load_seconds",
            "load_spread_pct",
            "model_load_seconds_log",
            "peak_memory_gib",
        ):
            if tier_perf.get(optional) is not None:
                _require_number(tier_perf[optional], where=f"perf.{tier}.{optional}")

    capability = _require_mapping(record.get("capability"), where="capability")
    for tier, entry in capability.items():
        tier_cap = _require_mapping(entry, where=f"capability.{tier}")
        _require_number(tier_cap.get("round_pass_rate"), where=f"capability.{tier}.round_pass_rate")
        tasks = _require_mapping(tier_cap.get("tasks"), where=f"capability.{tier}.tasks")
        if not tasks:
            msg = f"capability.{tier}.tasks 为空"
            raise SchemaError(msg)
        for task, verdict in tasks.items():
            entry_task = _require_mapping(verdict, where=f"capability.{tier}.tasks.{task}")
            passed = _require_int(
                entry_task.get("passed"), where=f"{tier}.{task}.passed", minimum=0
            )
            total = _require_int(entry_task.get("n"), where=f"{tier}.{task}.n", minimum=1)
            if passed > total:
                msg = f"capability.{tier}.tasks.{task} 的 passed({passed}) > n({total})"
                raise SchemaError(msg)


# ---------------------------------------------------------------------------
# 落盘
# ---------------------------------------------------------------------------


def write_json(path: pathlib.Path, payload: Mapping[str, object]) -> None:
    """写入 JSON（缩进、保留中文、末尾换行——便于 diff）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=False)
    path.write_text(f"{text}\n", encoding="utf-8")


def write_text(path: pathlib.Path, text: str) -> None:
    """写入文本（末尾保证一个换行）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else f"{text}\n", encoding="utf-8")


def load_json(path: pathlib.Path) -> dict[str, object]:
    """读取 JSON 对象；文件不存在时返回空对象。

    Raises:
        SchemaError: 内容不是合法的 JSON 对象。
    """
    if not path.is_file():
        return {}
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"{path} 不是合法 JSON：{exc}"
        raise SchemaError(msg) from exc
    if not isinstance(parsed, dict):
        msg = f"{path} 的顶层应为对象"
        raise SchemaError(msg)
    return cast("dict[str, object]", parsed)


def load_index(data_root: pathlib.Path) -> list[dict[str, object]]:
    """读取数据根目录下的轮次索引（不存在则为空列表）。"""
    return load_index_file(data_root / INDEX_FILENAME)


def load_index_file(path: pathlib.Path) -> list[dict[str, object]]:
    """从**具体文件**读取轮次索引（不存在则为空列表）。

    与 :func:`load_index` 的分工：后者接数据根目录（跑轮次时用），前者接文件路径
    （发布阶段在数据分支的工作副本上合并索引时用）。
    """
    payload = load_json(path)
    entries = payload.get("rounds")
    if entries is None:
        return []
    sequence = _require_sequence(entries, where=f"{path.name}.rounds")
    return [
        cast("dict[str, object]", _require_mapping(item, where="rounds[]")) for item in sequence
    ]


def append_index(
    data_root: pathlib.Path,
    entry: Mapping[str, object],
    *,
    limit: int = INDEX_LIMIT,
) -> list[dict[str, object]]:
    """追加一条索引记录（按时间升序保存，超出上限丢弃最旧的）。"""
    entries = load_index(data_root)
    round_id = _require_str(entry.get("round_id"), where="index entry.round_id")
    entries = [item for item in entries if item.get("round_id") != round_id]
    entries.append(dict(entry))
    entries = entries[-limit:]
    write_json(data_root / INDEX_FILENAME, {"rounds": entries})
    return entries


def merge_index_files(
    target: pathlib.Path,
    incoming: pathlib.Path,
    *,
    limit: int = INDEX_LIMIT,
) -> list[dict[str, object]]:
    """把 ``incoming`` 的轮次索引合并进 ``target``（按 ``round_id`` 去重，保持升序）。

    为什么需要"合并"而不是"覆盖"：发布阶段拿到的数据根目录在 CI 里是全新容器中的、
    只有本轮，而数据分支上已经有历史轮次。直接覆盖会让 ``index.json`` 退化成只有一条，
    数据分支于是永远只剩最新一轮——**跨夜序列无从建立**
    （2026-09-17 的首夜发布在提交 diff 里已经真实删除了 09-16 的记录，见 devlog 0012）。

    Returns:
        合并后的条目列表（已写入 ``target``）。
    """
    merged = load_index_file(target)
    for entry in load_index_file(incoming):
        round_id = _require_str(entry.get("round_id"), where="index entry.round_id")
        merged = [item for item in merged if item.get("round_id") != round_id]
        merged.append(entry)
    merged = merged[-limit:]
    write_json(target, {"rounds": merged})
    return merged


def prune_daily(data_root: pathlib.Path, *, keep_days: int, subdirs: Sequence[str]) -> list[str]:
    """按保留期清理每日目录下的重型子目录（保留 JSON 与报告）。

    聚合数据（JSON、report.md）体积很小，长期保留；日志与模型产物只留最近若干天。
    """
    removed: list[str] = []
    daily_root = data_root / DAILY_DIRNAME
    if not daily_root.is_dir():
        return removed
    cutoff = datetime.now(tz=UTC).astimezone().date() - timedelta(days=keep_days)
    for day_dir in sorted(daily_root.iterdir()):
        if not day_dir.is_dir():
            continue
        try:
            day = date.fromisoformat(day_dir.name)
        except ValueError:
            continue
        if day >= cutoff:
            continue
        for subdir in subdirs:
            target = day_dir / subdir
            if target.is_dir():
                for item in sorted(target.rglob("*"), reverse=True):
                    item.unlink(missing_ok=True)
                target.rmdir()
                removed.append(str(target.relative_to(data_root)))
    return removed


__all__ = [
    "INDEX_FILENAME",
    "INDEX_LIMIT",
    "LATEST_REPORT_FILENAME",
    "append_index",
    "load_index",
    "load_index_file",
    "load_json",
    "merge_index_files",
    "now_iso",
    "prune_daily",
    "today_local",
    "validate_round",
    "write_json",
    "write_text",
]
