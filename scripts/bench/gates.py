"""跑测的四道可信闸门：把 CI 的 stage 结构**迁移为脚本里的断言**。

为什么需要这个文件：[ADR-0023](../../docs/adr/0023-ci-downgrade-to-manual-trigger.md)
把跑测从"CI 全自动"降级为"环境内人工一键"。降级的**唯一正当性**是
"**闸门从流水线的结构迁移到脚本的检查**"——而不是"少跑一道也没关系"。
因此这四道闸门必须**逐条**落在这里；少一道，那一轮数据就不可比。

四道闸门（判据与实现均在**生产代码**里，本文件只做**断言与报告**，不另写第二套口径）：

| # | 闸门 | 判据 | 判据的真源 |
| --- | --- | --- | --- |
| ① | 跑前重启服务进程清 KV | 本轮每次重复各自一份**全新** `llama-server` 日志；`valid_prefill_repeats == repeats` | `bench/rounds.py::_run_tier` 的 `with runner.LlamaServer(...)` 循环 |
| ② | 最小 token 闸门（≥32）且计时行数 == 3 | 每份日志的 prefill / gen 计时行**恰好** `EXPECTED_TIMING_LINES_PER_REPEAT`；每个 prefill 行的被评估 token 数 **≥** `MIN_PREFILL_TOKENS` | `bench/protocol.py` 的常量 + `bench/rounds.py::_repeat_timings` |
| ③ | 只暴露中位数 + 极差 | `index.json` 的条目里**不得**出现单次采样（`values` 数组）；对外读数只给中位数与极差 | `bench/stats.py::Series`、`bench/report.py::index_entry` |
| ④ | 入库前 schema 校验 | `store.validate_round` 不通过即拒绝入库 | `bench/store.py::validate_round`（经 `rounds.validate_latest` 调用，**同一套实现**） |

用法（`make bench` 会调它，通常不必手敲）::

    PYTHONPATH=src uv run python scripts/bench/gates.py \\
        --data-root .bench-data --label manual --repeats 3

退出码：0 = 四道全过；1 = 有闸门未通过（**此时不得发布**）。
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from collections.abc import Iterator, Mapping, Sequence
from typing import cast

from agent_sec_perf.bench import rounds, runner, store
from agent_sec_perf.bench.protocol import (
    EXPECTED_TIMING_LINES_PER_REPEAT,
    MIN_PREFILL_TOKENS,
    TIERS,
)

#: 闸门名（也是 `--gate` 的取值；`all` 表示全跑）
GATES: tuple[str, ...] = ("kv", "tokens", "exposure", "schema")

_GATE_LABELS = {"kv": "①", "tokens": "②", "exposure": "③", "schema": "④"}


def _ok(gate: str, message: str) -> None:
    print(f"[gate {_GATE_LABELS[gate]}][OK] {message}")


def _fail(gate: str, message: str) -> None:
    print(f"[gate {_GATE_LABELS[gate]}][FAIL] {message}", file=sys.stderr)


def _latest_round(data_root: pathlib.Path) -> tuple[str, pathlib.Path, dict[str, object]]:
    """取数据根目录下最新一轮的目录与记录（与发布脚本读的是同一批文件）。

    Raises:
        SystemExit: 没有可校验的数据目录（fail-secure：宁可停下，也不放过）。
    """
    daily_root = data_root / store.DAILY_DIRNAME
    if not daily_root.is_dir():
        raise SystemExit(f"[gates][FATAL] 没有可校验的数据目录：{daily_root}（先跑一轮）")
    days = sorted(entry.name for entry in daily_root.iterdir() if entry.is_dir())
    if not days:
        raise SystemExit(f"[gates][FATAL] 数据目录为空：{daily_root}")
    latest = daily_root / days[-1]
    record: dict[str, object] = {
        "env": store.load_json(latest / "env.json").get("env"),
        "perf": store.load_json(latest / "perf.json").get("perf"),
        "capability": store.load_json(latest / "capability.json").get("capability"),
    }
    env = cast("Mapping[str, object]", record["env"])
    return str(env.get("round_id") or days[-1]), latest, record


# ---------------------------------------------------------------------------
# 闸门 ①：跑前重启服务进程清 KV
# ---------------------------------------------------------------------------


def gate_kv(latest: pathlib.Path, record: Mapping[str, object], *, repeats: int) -> bool:
    """① 每次重复都必须是**全新服务进程**（否则日志里混进"1 token 的 prefill"）。

    「跑前清场」（把上一轮残留的 `llama-server` 杀干净）由 `run.sh` 在第 0 步做；
    本闸门核的是**轮次内**的结构性证据：本轮**每一个档位、每一次重复**都留下
    一份独立日志，且通过可选计数的重复数等于请求的重复数。
    """
    perf = cast("Mapping[str, object]", record.get("perf") or {})
    passed = True
    for tier in sorted(perf):
        entry = perf.get(tier)
        if not isinstance(entry, Mapping):
            _fail("kv", f"{tier} 档数据缺失")
            passed = False
            continue
        valid = cast("Mapping[str, object]", entry).get("valid_prefill_repeats")
        logs = sorted((latest / "logs").glob(f"{tier}_r*.server.log"))
        if valid != repeats or len(logs) != repeats:
            _fail(
                "kv",
                f"{tier} 档：有效速率样本 {valid}/{repeats}、日志 {len(logs)}/{repeats} 份"
                "——不等于请求的重复数，说明有重复被丢弃（缓存命中或口径已变）",
            )
            passed = False
            continue
        _ok("kv", f"{tier} 档：{repeats}/{repeats} 次重复各自一份全新服务进程日志（KV 全空）")
    return passed


# ---------------------------------------------------------------------------
# 闸门 ②：最小 token 闸门（≥32）且计时行数 == 3
# ---------------------------------------------------------------------------


def gate_tokens(latest: pathlib.Path, *, repeats: int, tiers: Sequence[str]) -> bool:
    """② 逐份日志复核：计时行数**恰好** 3 条，且 prefill 的被评估 token 数 ≥ 32。

    这是**第二道**防线（第一道是"每次重复重启进程"）：万一缓存行为变化、
    或日志格式/请求数变了，这里立刻把它暴露成"不可用"，而不是硬凑一个数。
    行数与 token 数任一不符 ⇒ 该次重复**不计入统计**，且本闸门判失败。
    """
    passed = True
    expected = EXPECTED_TIMING_LINES_PER_REPEAT
    for tier in tiers:
        for repeat in range(1, repeats + 1):
            log_path = latest / "logs" / f"{tier}_r{repeat:02d}.server.log"
            if not log_path.is_file():
                _fail("tokens", f"{tier} r{repeat:02d}：日志缺失（{log_path.name}）")
                passed = False
                continue
            timings = runner.parse_timings(log_path.read_text(encoding="utf-8", errors="replace"))
            if len(timings.prefill) != expected or len(timings.gen) != expected:
                _fail(
                    "tokens",
                    f"{tier} r{repeat:02d}：计时行数 prefill={len(timings.prefill)} / "
                    f"gen={len(timings.gen)}，期望各 {expected} —— 口径可能已变，拒绝入库",
                )
                passed = False
                continue
            smallest = min(item.tokens for item in timings.prefill)
            if smallest < MIN_PREFILL_TOKENS:
                _fail(
                    "tokens",
                    f"{tier} r{repeat:02d}：prefill 被评估 token 数最小 {smallest} < "
                    f"{MIN_PREFILL_TOKENS} —— 这是缓存命中，不是 prefill 测量",
                )
                passed = False
                continue
            _ok(
                "tokens",
                f"{tier} r{repeat:02d}：计时行 {expected}/{expected}，"
                f"prefill token 最小 {smallest} ≥ {MIN_PREFILL_TOKENS}",
            )
    return passed


# ---------------------------------------------------------------------------
# 闸门 ③：只暴露中位数 + 极差
# ---------------------------------------------------------------------------


def _single_sample_paths(value: object, path: str = "") -> Iterator[str]:
    """递归找出**单次采样**的落点（键名为 ``values`` 的数组）。

    为什么只找 ``values``：那是 `stats.Series.to_json` 里唯一盛放逐次原始值的键。
    `perf.json` **刻意**保留它（事后复核的唯一原件），但 `index.json` 里**不得**出现
    ——索引是"对外读数"，只允许中位数与极差。
    """
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            if key == "values":
                yield child_path
            yield from _single_sample_paths(child, child_path)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, child in enumerate(value):
            yield from _single_sample_paths(child, f"{path}[{index}]")


def gate_exposure(data_root: pathlib.Path, round_id: str, record: Mapping[str, object]) -> bool:
    """③ 对外读数只含**中位数 + 极差**；单次采样不得进入索引。

    ⚠️ 口径必须说准：本闸门**不禁** `perf.json` 里的逐次原始值（那是刻意留证的），
    它禁的是"把单次采样当成**可用的读数**发布出去"——索引里出现 `values`
    就等于把"某个数"和"中位数"摆在同一层级，读的人迟早会拿它下结论。
    """
    entries = [item for item in store.load_index(data_root) if item.get("round_id") == round_id]
    if len(entries) != 1:
        _fail("exposure", f"索引里 {round_id} 的条目数应为 1，实际 {len(entries)}")
        return False
    leaks = sorted(_single_sample_paths(entries[0]))
    if leaks:
        _fail(
            "exposure",
            f"索引条目含单次采样（{len(leaks)} 处，如 {leaks[0]}）——对外读数只允许中位数与极差",
        )
        return False

    perf = cast("Mapping[str, object]", record.get("perf") or {})
    metrics = (
        ("prefill_tok_per_s", "prefill"),
        ("gen_tok_per_s", "gen"),
        ("request_elapsed_s", "elapsed"),
    )
    for tier in sorted(perf):
        tier_perf = cast("Mapping[str, object]", perf[tier])
        parts: list[str] = []
        for metric, label in metrics:
            series = cast("Mapping[str, object]", tier_perf[metric])
            parts.append(
                f"{label} 中位数 {float(cast('float', series['median'])):.2f}"
                f"（极差 {float(cast('float', series['spread_pct'])):.1f}%）"
            )
        _ok("exposure", f"{tier} 档：" + "；".join(parts))
    _ok("exposure", "索引条目仅含中位数与极差，未泄漏任何单次采样")
    return True


# ---------------------------------------------------------------------------
# 闸门 ④：入库前 schema 校验
# ---------------------------------------------------------------------------


def gate_schema(data_root: pathlib.Path) -> bool:
    """④ 入库前 schema 校验——**复用生产代码的同一套实现**，不在这里另写一遍。

    调用的是 `rounds.validate_latest`，与 `make bench-publish` 里
    `rounds --validate-only` 走的是同一个函数 ⇒ 不存在"发布脚本另有一套校验"的分叉。
    """
    if not rounds.validate_latest(data_root):
        _fail("schema", "最新一轮未通过 schema 校验，拒绝入库（坏数据比没有数据更难发现）")
        return False
    _ok("schema", "schema 校验通过（store.validate_round）")
    return True


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="跑测的四道可信闸门（全过才允许发布）")
    parser.add_argument("--data-root", required=True, help="数据根目录（跑测落盘处）")
    parser.add_argument(
        "--label",
        default=None,
        help="轮次标签；给了就要求与最新一轮一致（防止核错了别的轮次）",
    )
    parser.add_argument("--tiers", default=",".join(TIERS), help="档位，逗号分隔")
    parser.add_argument("--repeats", type=int, required=True, help="本轮请求的重复次数")
    parser.add_argument("--gate", choices=(*GATES, "all"), default="all", help="只跑某一道闸门")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """0 = 四道全过；1 = 有闸门未通过（必须停下，不得发布）。"""
    args = _parse_args(argv)
    rounds.configure_logging()

    data_root = pathlib.Path(str(args.data_root))
    tiers = tuple(part.strip() for part in str(args.tiers).split(",") if part.strip())
    repeats = int(args.repeats)
    round_id, latest, record = _latest_round(data_root)

    if args.label is not None:
        env = cast("Mapping[str, object]", record["env"])
        params = cast("Mapping[str, object]", env.get("params") or {})
        if params.get("label") != args.label:
            _fail("schema", f"标签不符：最新一轮是 {params.get('label')!r}，期望 {args.label!r}")
            return 1

    print(f"[gates] 校验轮次 {round_id}（目录 {latest}）；重复 {repeats}；档位 {list(tiers)}")

    wanted = set(GATES) if args.gate == "all" else {args.gate}
    results: dict[str, bool] = {}
    if "kv" in wanted:
        results["kv"] = gate_kv(latest, record, repeats=repeats)
    if "tokens" in wanted:
        results["tokens"] = gate_tokens(latest, repeats=repeats, tiers=tiers)
    if "exposure" in wanted:
        results["exposure"] = gate_exposure(data_root, round_id, record)
    if "schema" in wanted:
        results["schema"] = gate_schema(data_root)

    failed = [name for name, passed in results.items() if not passed]
    if failed:
        print(
            f"[gates][FAIL] {len(failed)}/{len(results)} 道闸门未通过：{failed}"
            " —— 本轮数据不得入库（'跑起来' ≠ '可信'）",
            file=sys.stderr,
        )
        return 1
    print(f"[gates][OK] 四道可信闸门全部通过（共 {len(results)} 道）：{sorted(results)}")
    return 0


if __name__ == "__main__":  # pragma: no cover - 入口分支
    raise SystemExit(main())
