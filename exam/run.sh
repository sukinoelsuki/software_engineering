#!/usr/bin/env bash
# AI coding 笔试 · 一键启动
#
# 用法：
#   bash exam/run.sh
#
# 它做四件事：
#   1. **前置检查**：uv / gcc / llama-server / 模型文件，缺什么就直说缺什么；
#   2. **新建隔离工作区**：每次运行都是全新目录（`~/ai-coding-exam-runs/<时间戳>/workspace`），
#      上下轮之间**不共享任何文件**；能力授予配置随目录一起放入；
#   3. **固定线程数**：llama-server 默认按物理机核数开线程（本机 384 核 → 在 8 核容器里
#      严重超订，实测慢 3~4 倍）。这里用一层包装把它钉住（理由见 ADR-0033 §性能）；
#   4. **启动交互式会话**：`agent-sec-perf exam`（多轮对话，`/new` 清空上下文重开一轮）。
#
# 可覆盖的环境变量：
#   EXAM_MODEL            GGUF 模型路径            默认自动挑 /opt/models 下的 Qwen3
#   EXAM_LLAMA_SERVER     llama-server 可执行文件  默认 /opt/llama.cpp/build/bin/llama-server
#   EXAM_THREADS          生成线程数               默认 min(本机核数, 8)（再大反而更慢）
#   EXAM_WORKSPACE_ROOT   工作区根目录             默认 $HOME/ai-coding-exam-runs
#   EXAM_MAX_STEPS        单轮模型往返上限         默认 16

set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/.." && pwd)

EXAM_LLAMA_SERVER=${EXAM_LLAMA_SERVER:-/opt/llama.cpp/build/bin/llama-server}
EXAM_WORKSPACE_ROOT=${EXAM_WORKSPACE_ROOT:-$HOME/ai-coding-exam-runs}
EXAM_MAX_STEPS=${EXAM_MAX_STEPS:-16}

fail() {
    echo "" >&2
    echo "启动失败：$1" >&2
    shift
    for line in "$@"; do echo "  $line" >&2; done
    exit 2
}

# ---------------------------------------------------------------- 前置检查
command -v uv >/dev/null 2>&1 || fail "找不到 uv" \
    "安装：https://docs.astral.sh/uv/  （或 pip install uv）"
command -v gcc >/dev/null 2>&1 || fail "找不到 gcc" \
    "笔试题要编译 C 程序，请先安装 gcc（Debian/Ubuntu：apt-get install -y gcc）"
[ -x "$EXAM_LLAMA_SERVER" ] || fail "llama-server 不可执行：$EXAM_LLAMA_SERVER" \
    "可用 EXAM_LLAMA_SERVER=/path/to/llama-server 指定。"

if [ -z "${EXAM_MODEL:-}" ]; then
    if [ -f /opt/models/Qwen3-8B-Q4_K_M.gguf ]; then
        EXAM_MODEL=/opt/models/Qwen3-8B-Q4_K_M.gguf
    else
        EXAM_MODEL=$(ls -1 /opt/models/*.gguf 2>/dev/null | head -1 || true)
    fi
fi
[ -n "${EXAM_MODEL:-}" ] && [ -f "$EXAM_MODEL" ] || fail "找不到模型文件（EXAM_MODEL=${EXAM_MODEL:-<未设置>}）" \
    "用 EXAM_MODEL=/path/to/model.gguf 指定一个 GGUF 模型。"

# ---------------------------------------------------------------- 线程数
if [ -z "${EXAM_THREADS:-}" ]; then
    CORES=$(nproc 2>/dev/null || echo 4)
    if [ "$CORES" -gt 8 ]; then EXAM_THREADS=8; else EXAM_THREADS=$CORES; fi
fi
case "$EXAM_THREADS" in
    ''|*[!0-9]*) fail "EXAM_THREADS 必须是正整数（收到：$EXAM_THREADS）" ;;
esac
[ "$EXAM_THREADS" -ge 1 ] || fail "EXAM_THREADS 必须 ≥ 1"

# ---------------------------------------------------------------- 隔离工作区
TS=$(date +%Y%m%d-%H%M%S)
RUN_DIR="$EXAM_WORKSPACE_ROOT/$TS"
WORKSPACE="$RUN_DIR/workspace"
mkdir -p "$WORKSPACE"
# 777：`run_command` 以非特权用户（nobody）执行，它要能在工作目录里写编译产物。
chmod 777 "$WORKSPACE"
cp "$SCRIPT_DIR/config/lowspec.toml" "$WORKSPACE/.lowspec.toml"

# 线程包装脚本：本产品的 CLI 不暴露 llama-server 的附加参数，而线程数是本场景的**关键性能项**，
# 因此用一层最薄的包装把它钉住（只做 exec，不改变其它任何行为）。
WRAPPER="$RUN_DIR/llama-server-t$EXAM_THREADS"
cat > "$WRAPPER" <<WRAPPER_EOF
#!/bin/sh
exec "$EXAM_LLAMA_SERVER" -t $EXAM_THREADS "\$@"
WRAPPER_EOF
chmod +x "$WRAPPER"

# ---------------------------------------------------------------- 开工
cat <<BANNER
============================================================
 AI coding 笔试 · 环境
 本机核数    ：$(nproc 2>/dev/null || echo '?')（生成线程固定为 $EXAM_THREADS）
 模型        ：$EXAM_MODEL
 服务        ：$EXAM_LLAMA_SERVER
 题目        ：$SCRIPT_DIR/PROBLEM.md
 本次工作区  ：$WORKSPACE
 能力授予    ：$WORKSPACE/.lowspec.toml（读 / 写 / 执行）
============================================================
判分  ：bash $SCRIPT_DIR/verify.sh "$WORKSPACE"
============================================================

BANNER

cd "$WORKSPACE"
set +e
uv run --project "$REPO_ROOT" agent-sec-perf exam \
    --pack "$SCRIPT_DIR/pack" \
    --allowed-root "$WORKSPACE" --allowed-root "$SCRIPT_DIR/pack" \
    --working-dir "$WORKSPACE" \
    --model-binary "$WRAPPER" \
    --model-path "$EXAM_MODEL" \
    --max-steps "$EXAM_MAX_STEPS"
EXIT_CODE=$?
set -e

cat <<SUMMARY

============================================================
 本次笔试结束（会话退出码：$EXIT_CODE）
 工作区     ：$WORKSPACE
 产物       ：$WORKSPACE/student.c
 判分       ：bash $SCRIPT_DIR/verify.sh "$WORKSPACE"
 模型服务日志：$WORKSPACE/llama-server.log（生成速度与截断情况都在里面）
 审计记录   ：~/.local/state/lowspec/audit/audit.jsonl（只追加，按 session_id 可回放）
============================================================
SUMMARY
