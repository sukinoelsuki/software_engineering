#!/usr/bin/env bash
# ============================================================================
# 一键跑测：跑一轮 → 四道可信闸门 → 校验入库
#
# 【为什么是脚本而不是 CI】
#   组织级「云原生构建」免费额度只有 160 核时/月且按**顶级组织**共享
#   （[ADR-0023](../../docs/adr/0023-ci-downgrade-to-manual-trigger.md)）。
#   跑测改在**云原生开发环境**里人工一键触发。
#
# 【关键判据：'跑起来' ≠ '可信'】
#   跑测的**四道可信闸门一条不少地搬进本脚本**——承载位置从"流水线的 stage 结构"
#   变成"脚本里的断言"，**判据一条没变**。少跑一道，那一轮数据就不可比。
#   闸门②③④ 的断言实现在 `scripts/bench/gates.py`（判据本身仍来自生产代码）。
#
# 【用法】在云原生开发环境里（建议从 `test/<slug>` 分支拉起，见 CODEBUDDY.md §2）：
#   make bench                                  # 跑一轮并发布到 bench/data
#   BENCH_DRY_RUN=1 make bench                  # 只跑 + 校验，不发布
#   BENCH_TIERS=S BENCH_REPEATS=3 make bench    # 快速自检（约 3 分钟）
#
# 【四道闸门】
#   ① 跑前重启服务进程清 KV（第 0.5 步清场 + 轮次内每次重复一个全新进程）
#   ② 最小 token 闸门 ≥ 32 且计时行数 == 3（逐份日志复核）
#   ③ 只暴露中位数 + 极差（索引里不得出现单次采样）
#   ④ 入库前 schema 校验（复用 rounds.validate_latest，同一套实现）
# ============================================================================

set -euo pipefail

readonly BENCH_DATA_ROOT="${BENCH_DATA_ROOT:-${PWD}/.bench-data}"
readonly BENCH_TIERS="${BENCH_TIERS:-S,M,L}"
readonly BENCH_REPEATS="${BENCH_REPEATS:-10}"
readonly BENCH_THREADS="${BENCH_THREADS:-8}"
readonly BENCH_LABEL="${BENCH_LABEL:-manual}"
readonly BENCH_MODEL_DIR="${BENCH_MODEL_DIR:-/opt/models}"
readonly BENCH_DRY_RUN="${BENCH_DRY_RUN:-0}"

log() { printf '[bench] %s\n' "$*"; }
die() { printf '[bench][ERROR] %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 0. 前置检查（fail-secure：缺什么就停下，不要"跑了一半才发现"）
# ---------------------------------------------------------------------------
log "=== 0. 前置检查 ==="
command -v llama-server >/dev/null 2>&1 || die "找不到 llama-server（应已在镜像构建期预置）"
command -v uv >/dev/null 2>&1 || die "找不到 uv（开发环境应已预置）"
[ -d "${BENCH_MODEL_DIR}" ] || die "模型目录不存在：${BENCH_MODEL_DIR}"
case "${BENCH_REPEATS}" in
    ''|*[!0-9]*) die "BENCH_REPEATS 必须是非负整数：${BENCH_REPEATS}" ;;
esac
case "${BENCH_THREADS}" in
    ''|*[!0-9]*) die "BENCH_THREADS 必须是非负整数：${BENCH_THREADS}" ;;
esac
[ "${BENCH_REPEATS}" -ge 3 ] || die "重复次数必须 ≥ 3：单次采样不得用于阈值判断（ADR-0014 §2.5）"
[ "${BENCH_THREADS}" -ge 1 ] || die "线程数必须 ≥ 1：${BENCH_THREADS}"

# ---------------------------------------------------------------------------
# 0.5 闸门①-A 跑前清场：终止**本机同款** llama-server 的残留实例
#
# 为什么要清：残留进程会占住 8080 端口、并可能让新一轮复用旧的槽位/KV 状态。
# 为什么按"可执行文件一致"过滤：同名进程未必是**我们这一份** llama-server，
# 一律 pkill -f 会误伤别的进程（那是不可回退的操作）。
# ---------------------------------------------------------------------------
log "=== 闸门①-A 跑前清场（终止残留的 llama-server）==="
llama_exe="$(readlink -f "$(command -v llama-server)")"
stale_pids=""
for pid in $(pgrep -x llama-server 2>/dev/null || true); do
    pid_exe="$(readlink -f "/proc/${pid}/exe" 2>/dev/null || true)"
    if [ "${pid_exe}" = "${llama_exe}" ]; then
        stale_pids="${stale_pids} ${pid}"
    fi
done
if [ -n "${stale_pids}" ]; then
    log "发现残留进程：${stale_pids}（同一份可执行文件 ${llama_exe}）→ 终止"
    # shellcheck disable=SC2086  # 这里**需要**按空格拆分出多个 pid
    kill ${stale_pids} 2>/dev/null || true
    for _ in $(seq 1 20); do
        pgrep -x llama-server >/dev/null 2>&1 || break
        sleep 0.5
    done
    # shellcheck disable=SC2086
    for pid in ${stale_pids}; do kill -9 "${pid}" 2>/dev/null || true; done
fi
if pgrep -x llama-server >/dev/null 2>&1; then
    die "仍有 llama-server 在运行 ⇒ 端口与 KV 状态不清，拒绝开跑"
fi
log "清场完成：新一轮将从空 KV 开始"

# ---------------------------------------------------------------------------
# 1. 跑一轮（闸门①-B：轮次内**每次重复都重启服务进程**，由 rounds.py 保证）
# ---------------------------------------------------------------------------
log "=== 1. 跑一轮：档位=${BENCH_TIERS} 重复=${BENCH_REPEATS} 线程=${BENCH_THREADS} 标签=${BENCH_LABEL} ==="
make bench-round \
    BENCH_DATA_ROOT="${BENCH_DATA_ROOT}" \
    BENCH_TIERS="${BENCH_TIERS}" \
    BENCH_REPEATS="${BENCH_REPEATS}" \
    BENCH_THREADS="${BENCH_THREADS}" \
    BENCH_LABEL="${BENCH_LABEL}" \
    BENCH_MODEL_DIR="${BENCH_MODEL_DIR}"

# ---------------------------------------------------------------------------
# 2. 四道可信闸门（②③④ 在这里；① 已在第 0.5 步与本步的轮次结构里核过）
# ---------------------------------------------------------------------------
log "=== 2. 四道可信闸门（全过才允许往下）==="
PYTHONPATH="${PWD}/src" uv run python scripts/bench/gates.py \
    --data-root "${BENCH_DATA_ROOT}" \
    --label "${BENCH_LABEL}" \
    --tiers "${BENCH_TIERS}" \
    --repeats "${BENCH_REPEATS}" \
    || die "有闸门未通过 ⇒ 本轮数据不得入库（'跑起来' ≠ '可信'）"

# ---------------------------------------------------------------------------
# 3. 只暴露中位数 + 极差
#
# 报告第 1 节就是"本轮中位数 / 极差 / 基线中位数 / 差异 / 判定"，
# 单次采样不在其中（它们只留在 perf.json 作为事后复核的原件）。
# 这里**故意只打印第 1 节**：打印全份会把 §2 的资源单值与 §3 的逐任务明细
# 一起铺开，读的人容易把"某个数"当成结论。
# ---------------------------------------------------------------------------
log "=== 3. 本轮读数（只给中位数与极差）==="
grep -q '^## 1\.' "${BENCH_DATA_ROOT}/latest.md" || die "报告缺少第 1 节（性能），格式可能已变"
awk '/^## 1\./ {show=1} /^## 2\./ {show=0} show {print}' "${BENCH_DATA_ROOT}/latest.md"

# ---------------------------------------------------------------------------
# 4. 发布到数据分支（**唯一持久化出口**：不许把测试数据推到代码分支）
# ---------------------------------------------------------------------------
if [ "${BENCH_DRY_RUN}" = "1" ]; then
    log "BENCH_DRY_RUN=1：四道闸门已过，按指示跳过发布"
    exit 0
fi
log "=== 4. 发布到数据分支（唯一持久化出口）==="
BENCH_ALLOW_LOCAL=1 bash scripts/bench/publish.sh
log "完成。取数：git fetch origin bench/data && git show FETCH_HEAD:bench/latest.md"
