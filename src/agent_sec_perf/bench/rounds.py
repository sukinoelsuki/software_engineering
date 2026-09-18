"""一轮基准的编排与命令行入口。

一轮 = 对每个档位：启动 llama-server → 重复 R 次（每次跑三个任务）→ 客观判定
→ 汇总统计 → 落盘 → 生成报告 → 追加索引。

两条纪律：

* **失败不掩盖**：某档出错时记录错误、继续跑其余档位，并把整轮标记为 ``partial``
  （进程退出码非零）。半份数据比没有数据好，但必须让人知道它是半份；
* **不清理证据**：服务端日志与模型产物原样留在 ``daily/<日期>/`` 下，
  何时删除由保留策略决定——日志是性能数字的唯一原件。

用法（``make bench-round`` 是同一入口的薄封装）::

    PYTHONPATH=src python -m agent_sec_perf.bench.rounds --data-root /tmp/bench --repeats 3
"""

from __future__ import annotations

import argparse
import logging
import os
import pathlib
import platform
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from agent_sec_perf.bench import report as report_mod
from agent_sec_perf.bench import runner, store
from agent_sec_perf.bench.assets import (
    DEFAULT_MODELS_MANIFEST,
    ModelAsset,
    read_model_manifest,
    sha256_of,
)
from agent_sec_perf.bench.evaluate import Evaluator, TaskVerdict
from agent_sec_perf.bench.protocol import (
    DEFAULT_MODEL_DIR,
    EXPECTED_TIMING_LINES_PER_REPEAT,
    MIN_PREFILL_TOKENS,
    PROTOCOL_VERSION,
    TASK_IDS,
    TIERS,
    RunParams,
    build_prompts,
    model_path_for,
)
from agent_sec_perf.bench.stats import success_rate, summarize
from agent_sec_perf.foundation import proc
from agent_sec_perf.foundation.errors import BenchError, PathNotAllowedError, ProtocolError
from agent_sec_perf.foundation.paths import resolve_within

LOGGER = logging.getLogger("bench.rounds")

DEFAULT_KEEP_DAYS = 30
#: 保留期只清理这些重型子目录：聚合数据（JSON、报告）长期保留。
#: ``work`` 是判定的中间工作目录（模型产物已另有归档），一并按保留期清理。
DEFAULT_DATA_ROOT_SUBDIRS: tuple[str, ...] = ("logs", "artifacts", "work")


@dataclass
class TierOutcome:
    """一个档位的产出（档位失败时两个字段都为空）。"""

    tier: str
    perf: dict[str, object]
    capability: dict[str, object]


def configure_logging() -> None:
    """把日志输出到 stdout。

    这是功能需求而不是排版偏好：CNB 流水线对"长时间无输出"的任务有 10 分钟超时，
    一轮基准必须持续产出进度行。
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# 编排
# ---------------------------------------------------------------------------


def run_round(
    *,
    params: RunParams,
    data_root: pathlib.Path,
    model_dir: pathlib.Path = DEFAULT_MODEL_DIR,
    manifest: pathlib.Path = DEFAULT_MODELS_MANIFEST,
    verify_assets: bool = False,
    keep_days: int = DEFAULT_KEEP_DAYS,
    report_window: int = report_mod.DEFAULT_HISTORY_WINDOW,
    threshold_pct: float = report_mod.DEFAULT_THRESHOLD_PCT,
) -> dict[str, object]:
    """跑完一轮并落盘，返回整轮记录。"""
    params.validate()
    started_at = store.now_iso()
    started_monotonic = time.monotonic()
    date = store.today_local()
    round_id = f"{date}-{params.label}"
    daily_dir = data_root / store.DAILY_DIRNAME / date
    daily_dir.mkdir(parents=True, exist_ok=True)

    assets = read_model_manifest(manifest)
    if verify_assets:
        verify_models(model_dir, manifest, tiers=params.tiers)
    binary = proc.resolve_binary("llama-server")
    LOGGER.info(
        "轮次 %s 开始：档位=%s 重复=%d 线程=%d 隔离=%s",
        round_id,
        params.tiers,
        params.repeats,
        params.threads,
        params.isolation,
    )

    errors: list[str] = []
    evaluator = Evaluator(root=daily_dir / "work", isolation=params.isolation)
    prompts = build_prompts()
    perf: dict[str, object] = {}
    capability: dict[str, object] = {}

    for tier in params.tiers:
        asset = _asset_for(assets, tier)
        try:
            outcome = _run_tier(
                tier=tier,
                params=params,
                model_dir=model_dir,
                asset=asset,
                evaluator=evaluator,
                prompts=prompts,
                daily_dir=daily_dir,
            )
        except BenchError as exc:
            errors.append(f"{tier} 档失败：{exc}")
            LOGGER.error("[%s] 档位失败：%s", tier, exc)
            continue
        perf[tier] = outcome.perf
        capability[tier] = outcome.capability

    if not perf:
        msg = f"所有档位都失败，未产出任何可入库数据：{errors}"
        raise BenchError(msg)

    duration_s = time.monotonic() - started_monotonic
    env = _env_fingerprint(
        params=params,
        assets=assets,
        model_dir=model_dir,
        binary=binary,
        round_id=round_id,
        started_at=started_at,
        duration_s=duration_s,
        errors=errors,
    )
    record: dict[str, object] = {"env": env, "perf": perf, "capability": capability}
    store.validate_round(record)

    store.write_json(daily_dir / "env.json", {"env": env})
    store.write_json(daily_dir / "perf.json", {"perf": perf})
    store.write_json(daily_dir / "capability.json", {"capability": capability})

    history = store.load_index(data_root)
    report_text = report_mod.build_report(
        record=record, history=history, threshold_pct=threshold_pct, window=report_window
    )
    store.write_text(daily_dir / "report.md", report_text)
    store.write_text(data_root / store.LATEST_REPORT_FILENAME, report_text)

    core_hours = _core_hours(env, duration_s)
    entry = report_mod.index_entry(
        record=record,
        report_path=f"{store.DAILY_DIRNAME}/{date}/report.md",
        core_hours=core_hours,
    )
    store.append_index(data_root, entry)
    removed = store.prune_daily(data_root, keep_days=keep_days, subdirs=DEFAULT_DATA_ROOT_SUBDIRS)

    LOGGER.info(
        "轮次 %s 结束：status=%s 耗时 %.0fs 约 %.2f 核时",
        round_id,
        env["status"],
        duration_s,
        core_hours,
    )
    if removed:
        LOGGER.info("按保留期清理：%s", "、".join(removed))
    return record


def _run_tier(
    *,
    tier: str,
    params: RunParams,
    model_dir: pathlib.Path,
    asset: ModelAsset,
    evaluator: Evaluator,
    prompts: dict[str, str],
    daily_dir: pathlib.Path,
) -> TierOutcome:
    """跑单个档位：R 次重复，每次跑三个任务，并汇总统计。

    **每次重复都重启服务进程**。原因不是洁癖：同一进程内重复发送同一提示词时，
    服务端会复用该槽位的 KV 前缀，日志给出的是"1 个 token 的 eval 时间"，
    那不是 prefill 测量（2026-09-16 实测：由此得到一条极差 326% 的假序列）。
    重启后每次重复的 KV 皆空，R 次重复才构成"同一条件"下的独立测量。
    """
    model_path = _resolve_model(model_dir, asset, tier)
    binary = proc.resolve_binary("llama-server")

    per_repeat_prefill: list[float] = []
    per_repeat_gen: list[float] = []
    per_repeat_elapsed: list[float] = []
    per_repeat_load: list[float] = []
    per_repeat_peak: list[float] = []
    verdicts: dict[str, list[TaskVerdict]] = {task: [] for task in TASK_IDS}
    finish_reasons: dict[str, list[str]] = {task: [] for task in TASK_IDS}
    server_info: dict[str, object] = {"n_slots": None, "n_ctx_slot": None}
    model_load_seconds_log: float | None = None

    for repeat in range(1, params.repeats + 1):
        log_path = daily_dir / "logs" / f"{tier}_r{repeat:02d}.server.log"
        with runner.LlamaServer(
            params=params, binary=binary, model_path=model_path, log_path=log_path
        ) as server:
            repeat_elapsed = 0.0
            for task in TASK_IDS:
                tag = f"{tier}_r{repeat:02d}_{task}"
                chat_result = runner.chat(params, prompts[task])
                store.write_text(daily_dir / "artifacts" / f"{tag}.md", chat_result.content)
                finish_reasons[task].append(chat_result.finish_reason)
                if not chat_result.content.strip():
                    LOGGER.warning(
                        "[%s] %s 输出为空（finish_reason=%s）：先怀疑预算耗尽，再怀疑能力",
                        tier,
                        tag,
                        chat_result.finish_reason,
                    )
                verdict = evaluator.evaluate(task, chat_result.content, tag=tag)
                verdicts[task].append(verdict)
                repeat_elapsed += chat_result.elapsed_s
                LOGGER.info(
                    "[%s] r%02d %s：%.1fs prompt=%d out=%d finish=%s 判定=%s",
                    tier,
                    repeat,
                    task,
                    chat_result.elapsed_s,
                    chat_result.prompt_tokens,
                    chat_result.completion_tokens,
                    chat_result.finish_reason,
                    "通过" if verdict.passed else "未通过",
                )
            peak_memory_gib = server.peak_memory_gib()
            load_seconds = server.load_seconds

        prefill, gen, ok = _repeat_timings(log_path)
        if ok and prefill is not None and gen is not None:
            per_repeat_prefill.append(prefill)
            per_repeat_gen.append(gen)
        else:
            LOGGER.warning(
                "[%s] 第 %d 次重复的计时不可用（行数不符或疑似缓存命中），不计入统计",
                tier,
                repeat,
            )
        per_repeat_elapsed.append(repeat_elapsed)
        per_repeat_load.append(load_seconds)
        if peak_memory_gib is not None:
            per_repeat_peak.append(peak_memory_gib)
        if repeat == 1:
            first_log = log_path.read_text(encoding="utf-8", errors="replace")
            server_info = runner.parse_server_info(first_log)
            model_load_seconds_log = runner.parse_model_load_seconds(first_log)
        LOGGER.info(
            "[%s] r%02d 完成：prefill=%s gen=%s 有效速率样本=%d/%d",
            tier,
            repeat,
            f"{prefill:.2f}" if prefill is not None else "—",
            f"{gen:.2f}" if gen is not None else "—",
            len(per_repeat_prefill),
            repeat,
        )

    if not per_repeat_prefill or not per_repeat_gen:
        msg = f"{tier} 档没有任何可用的服务端计时（日志目录：{daily_dir / 'logs'}）"
        raise ProtocolError(msg)

    load_series = summarize(per_repeat_load)
    perf: dict[str, object] = {
        "repeats": params.repeats,
        "prefill_tok_per_s": summarize(per_repeat_prefill).to_json(),
        "gen_tok_per_s": summarize(per_repeat_gen).to_json(),
        "request_elapsed_s": summarize(per_repeat_elapsed).to_json(),
        "load_seconds": round(load_series.median, 2),
        "load_spread_pct": round(load_series.spread_pct, 2),
        "model_load_seconds_log": model_load_seconds_log,
        "peak_memory_gib": round(max(per_repeat_peak), 2) if per_repeat_peak else None,
        "valid_prefill_repeats": len(per_repeat_prefill),
        "server": server_info,
    }
    capability = _capability(verdicts, finish_reasons)
    return TierOutcome(tier=tier, perf=perf, capability=capability)


def _repeat_timings(log_path: pathlib.Path) -> tuple[float | None, float | None, bool]:
    """解析**单次重复**的服务端计时（每次重复一份日志，因此可以严格校验）。

    返回 ``(prefill 均值, 生成均值, 是否可用)``。两类情况判为不可用：

    * 行数与 ``EXPECTED_TIMING_LINES_PER_REPEAT`` 不符——通常意味着日志格式或
      请求数变了，属于"口径可能已变"，必须暴露而不是硬凑一个数；
    * prefill 行的被评估 token 数低于 ``MIN_PREFILL_TOKENS``——那是缓存命中，
      不是 prefill 测量。
    """
    if not log_path.is_file():
        return None, None, False
    timings = runner.parse_timings(log_path.read_text(encoding="utf-8", errors="replace"))
    expected = EXPECTED_TIMING_LINES_PER_REPEAT
    if len(timings.prefill) != expected or len(timings.gen) != expected:
        return None, None, False
    if any(item.tokens < MIN_PREFILL_TOKENS for item in timings.prefill):
        return None, None, False
    prefill = sum(item.tok_per_s for item in timings.prefill) / expected
    gen = sum(item.tok_per_s for item in timings.gen) / expected
    return prefill, gen, True


def _capability(
    verdicts: dict[str, list[TaskVerdict]],
    finish_reasons: dict[str, list[str]],
) -> dict[str, object]:
    """汇总能力判定。"""
    tasks: dict[str, object] = {}
    flat: list[bool] = []
    for task in TASK_IDS:
        task_verdicts = verdicts[task]
        passed = sum(1 for verdict in task_verdicts if verdict.passed)
        tasks[task] = {
            "passed": passed,
            "n": len(task_verdicts),
            "rate": round(success_rate([verdict.passed for verdict in task_verdicts]), 4),
            "finish_reasons": finish_reasons[task],
            "verdicts": [verdict.to_json() for verdict in task_verdicts],
        }
        flat.extend(verdict.passed for verdict in task_verdicts)
    return {"round_pass_rate": round(success_rate(flat), 4), "tasks": tasks}


def _asset_for(assets: dict[str, ModelAsset], tier: str) -> ModelAsset:
    """取档位对应的清单条目。

    Raises:
        ProtocolError: 清单里没有该档位的模型文件。
    """
    filename = model_path_for(tier, pathlib.Path()).name
    asset = assets.get(filename)
    if asset is None:
        msg = f"模型清单中没有 {tier} 档所需的 {filename}"
        raise ProtocolError(msg)
    return asset


def _resolve_model(model_dir: pathlib.Path, asset: ModelAsset, tier: str) -> pathlib.Path:
    """解析并校验模型路径（必须落在模型目录内，且文件存在）。

    Raises:
        PathNotAllowedError: 路径逃出模型目录。
        ProtocolError: 文件不存在。
    """
    try:
        model_path = resolve_within(model_dir / asset.filename, [model_dir], what=f"{tier} 档模型")
    except PathNotAllowedError:
        raise
    if not model_path.is_file():
        msg = f"{tier} 档模型文件不存在：{model_path}（应在镜像构建期预置）"
        raise ProtocolError(msg)
    return model_path


def _env_fingerprint(
    *,
    params: RunParams,
    assets: dict[str, ModelAsset],
    model_dir: pathlib.Path,
    binary: str,
    round_id: str,
    started_at: str,
    duration_s: float,
    errors: list[str],
) -> dict[str, object]:
    """环境指纹：没有它，跨轮数据无法判断是否可比。"""
    models: dict[str, object] = {}
    for tier in TIERS:
        asset = assets.get(model_path_for(tier, pathlib.Path()).name)
        if asset is None:
            continue
        path = model_dir / asset.filename
        models[tier] = {
            "file": asset.filename,
            "sha256": asset.sha256,
            "quant": asset.quant,
            "size": asset.size,
            "present": path.is_file(),
        }
    version = proc.run_inherit_env([binary, "--version"], cwd=model_dir, timeout_s=60.0)
    return {
        "protocol": PROTOCOL_VERSION,
        "round_id": round_id,
        "started_at": started_at,
        "finished_at": store.now_iso(),
        "duration_s": round(duration_s, 2),
        "status": "partial" if errors else "complete",
        "errors": errors,
        "isolation": params.isolation,
        "params": params.to_json(),
        "trigger": {
            "event": os.environ.get("CNB_EVENT", "local"),
            "branch": os.environ.get("CNB_BRANCH", "local"),
            "commit": os.environ.get("CNB_COMMIT", "unknown"),
            "is_cron": os.environ.get("CNB_IS_CRONEVENT", "false"),
            "build_id": os.environ.get("CNB_BUILD_ID", "local"),
            "build_url": os.environ.get("CNB_BUILD_WEB_URL", ""),
        },
        "runner": {
            "cpus": os.environ.get("CNB_CPUS") or str(os.cpu_count() or 0),
            "memory_gib": os.environ.get("CNB_MEMORY") or _total_memory_gib(),
            "nproc": os.cpu_count(),
            # CPU 型号是"这台机器"的标识：同一份协议的 CI 数据与开发环境数据
            # 实测吞吐差 25% 以上（2026-09-16），不加区分地混比就是口径错误。
            "cpu_model": _cpu_model(),
        },
        "llama": {
            "binary": binary,
            "version": (version.stdout or version.stderr).strip().splitlines()[0]
            if (version.stdout or version.stderr).strip()
            else "unknown",
        },
        "python": platform.python_version(),
        "models": models,
    }


def _cpu_model() -> str:
    """从 ``/proc/cpuinfo`` 读取 CPU 型号（取第一条 ``model name``）。

    读不到时返回 ``unknown``——不编造。
    """
    try:
        for line in pathlib.Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            key, _, value = line.partition(":")
            if key.strip() == "model name":
                return value.strip() or "unknown"
    except OSError:
        return "unknown"
    return "unknown"


def _total_memory_gib() -> str:
    """从 ``/proc/meminfo`` 读取物理内存（GiB）。"""
    try:
        for line in pathlib.Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return f"{int(line.split()[1]) / (1024 * 1024):.1f}"
    except OSError:
        return "unknown"
    return "unknown"


def _core_hours(env: dict[str, object], duration_s: float) -> float:
    """估算本轮消耗的核时（核数乘以时长）。"""
    runner_info = cast("dict[str, object]", env.get("runner") or {})
    raw = runner_info.get("cpus")
    try:
        cpus = int(str(raw))
    except ValueError:
        cpus = os.cpu_count() or 0
    return cpus * duration_s / 3600.0


# ---------------------------------------------------------------------------
# 命令行入口
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="跑一轮基准并落盘（含报告与索引）")
    parser.add_argument("--data-root", required=True, help="数据根目录（数据分支上的 bench/）")
    parser.add_argument(
        "--model-dir", default=str(DEFAULT_MODEL_DIR), help="模型目录（默认 /opt/models）"
    )
    parser.add_argument("--manifest", default=str(DEFAULT_MODELS_MANIFEST), help="模型清单路径")
    parser.add_argument("--tiers", default=",".join(TIERS), help="档位，逗号分隔（默认 S,M,L）")
    parser.add_argument("--repeats", type=int, default=10, help="每档重复次数（≥3）")
    parser.add_argument("--threads", type=int, default=8, help="llama-server 线程数（-t/-tb）")
    parser.add_argument("--ctx", type=int, default=4096, help="上下文长度（-c）")
    parser.add_argument("--max-tokens", type=int, default=2048, help="单次生成上限")
    parser.add_argument("--label", default="nightly", help="轮次标签（nightly/deep/push 等）")
    parser.add_argument(
        "--isolation", choices=("user", "root"), default="user", help="判定执行隔离模式"
    )
    parser.add_argument(
        "--keep-days", type=int, default=DEFAULT_KEEP_DAYS, help="日志与产物的保留天数"
    )
    parser.add_argument(
        "--report-window",
        type=int,
        default=report_mod.DEFAULT_HISTORY_WINDOW,
        help="基线取最近 N 轮",
    )
    parser.add_argument(
        "--threshold-pct",
        type=float,
        default=report_mod.DEFAULT_THRESHOLD_PCT,
        help="超阈阈值（%）",
    )
    parser.add_argument(
        "--verify-assets", action="store_true", help="本轮重算模型 sha256 并与清单比对"
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="只校验最新一轮产出是否符合 schema（供发布脚本在提交前调用）",
    )
    parser.add_argument(
        "--merge-into",
        default=None,
        metavar="已发布数据根",
        help="把本轮产出合并进该目录（发布阶段用）：索引按 round_id 合并、latest 报告覆盖",
    )
    return parser.parse_args(argv)


def validate_latest(data_root: pathlib.Path) -> bool:
    """校验最新一轮的产出（发布前的最后一道闸门）。

    与跑轮次时用的是**同一个** :func:`agent_sec_perf.bench.store.validate_round`，
    因此不存在"发布脚本另有一套校验"的分叉。

    Returns:
        是否通过校验（不通过时给出明确日志，而不是静默放行）。
    """
    daily_root = data_root / store.DAILY_DIRNAME
    if not daily_root.is_dir():
        LOGGER.error("没有可校验的数据目录：%s", daily_root)
        return False
    days = sorted(entry.name for entry in daily_root.iterdir() if entry.is_dir())
    if not days:
        LOGGER.error("数据目录为空：%s", daily_root)
        return False
    latest = daily_root / days[-1]
    record: dict[str, object] = {
        "env": store.load_json(latest / "env.json").get("env"),
        "perf": store.load_json(latest / "perf.json").get("perf"),
        "capability": store.load_json(latest / "capability.json").get("capability"),
    }
    try:
        store.validate_round(record)
    except BenchError as exc:
        LOGGER.error("最新一轮（%s）校验失败，拒绝发布：%s", days[-1], exc)
        return False
    LOGGER.info("最新一轮（%s）通过 schema 校验", days[-1])
    return True


def merge_into_published(*, data_root: pathlib.Path, published_root: pathlib.Path) -> int:
    """把本轮产出合并进**已发布的**数据根目录（发布阶段专用）。

    只处理两个聚合文件，语义都是"合并 / 最新"：

    * ``index.json``：本轮条目按 ``round_id`` 并进已发布索引——**不丢弃历史轮次**；
    * ``latest.md``：用本轮报告覆盖——它本来就表示"最新一轮"。

    每日目录的复制由调用方完成（``publish.sh`` 用 ``cp -a`` 保留字节）。之所以不让
    发布脚本"整体替换"数据子目录：CI 的数据根目录只有本轮，替换会删掉历史轮次，
    数据分支于是永远只剩最新一轮（2026-09-17 的首夜发布真实发生过，见 devlog 0012）。

    Returns:
        进程退出码（0 = 成功）。
    """
    merged = store.merge_index_files(
        published_root / store.INDEX_FILENAME, data_root / store.INDEX_FILENAME
    )
    latest = data_root / store.LATEST_REPORT_FILENAME
    if latest.is_file():
        store.write_text(
            published_root / store.LATEST_REPORT_FILENAME, latest.read_text(encoding="utf-8")
        )
    LOGGER.info(
        "已把本轮并进已发布索引：共 %d 轮（%s）", len(merged), published_root / store.INDEX_FILENAME
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口：0=完成，1=部分完成，2=硬失败。"""
    args = _parse_args(argv)
    configure_logging()
    if args.validate_only:
        return 0 if validate_latest(pathlib.Path(str(args.data_root))) else 2
    if args.merge_into:
        try:
            return merge_into_published(
                data_root=pathlib.Path(str(args.data_root)),
                published_root=pathlib.Path(str(args.merge_into)),
            )
        except BenchError as exc:
            LOGGER.error("合并进已发布数据失败：%s", exc)
            return 2
    params = RunParams(
        ctx=args.ctx,
        threads=args.threads,
        repeats=args.repeats,
        tiers=tuple(part.strip() for part in str(args.tiers).split(",") if part.strip()),
        max_tokens=args.max_tokens,
        label=str(args.label),
        isolation=str(args.isolation),
    )
    try:
        record = run_round(
            params=params,
            data_root=pathlib.Path(str(args.data_root)),
            model_dir=pathlib.Path(str(args.model_dir)),
            manifest=pathlib.Path(str(args.manifest)),
            verify_assets=bool(args.verify_assets),
            keep_days=int(args.keep_days),
            report_window=int(args.report_window),
            threshold_pct=float(args.threshold_pct),
        )
    except BenchError as exc:
        LOGGER.error("轮次硬失败：%s", exc)
        return 2
    env = cast("dict[str, object]", record["env"])
    return 0 if env.get("status") == "complete" else 1


def verify_models(
    model_dir: pathlib.Path, manifest: pathlib.Path, *, tiers: Sequence[str] = TIERS
) -> None:
    """重算模型 sha256 并与清单比对，作为可选的运行期复查。

    Raises:
        ProtocolError: 摘要不符（fail-secure：不符即不得用于测量）。
    """
    assets = read_model_manifest(manifest)
    for tier in tiers:
        asset = _asset_for(assets, tier)
        path = model_dir / asset.filename
        if not path.is_file():
            msg = f"{tier} 档模型缺失：{path}"
            raise ProtocolError(msg)
        actual = sha256_of(path)
        if actual != asset.sha256:
            msg = f"{tier} 档模型摘要不符：{path}\n  期望：{asset.sha256}\n  实际：{actual}"
            raise ProtocolError(msg)
        LOGGER.info("[%s] 模型摘要校验通过：%s", tier, asset.filename)


if __name__ == "__main__":  # pragma: no cover - 入口分支
    raise SystemExit(main())
