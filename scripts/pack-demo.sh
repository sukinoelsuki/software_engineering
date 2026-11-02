#!/usr/bin/env bash
# ============================================================================
# 能力包内核 · 一键演示入口（现场用）
#
# 【为什么需要这个脚本】
#   演示能否成功，主要不取决于模型，而取决于几个**与模型无关的前提**：
#     1. /tmp 下残留的旧 bundle 会让 `status` 早已是 evaluated（"跑过了"被读成"刚跑"）；
#     2. 上一次中断遗留的 llama-server 占着 8080 ⇒ 新进程起不来，现象像"模型加载失败"；
#     3. 模型路径 / 推理后端 / 依赖没装好 ⇒ 失败信息看起来像"模型能力不行"。
#   手工按手册敲 6 条命令时，任何一条出错都会被现场读成"模型不行"。
#   本脚本只做手册里**已有**的步骤，把上述前提变成 fail-fast 断言；
#   它**不改变**任何安全默认、判据或包内容。
#
# 【用法】
#   scripts/pack-demo.sh                 # 完整演示（MiniCPM5-2B，实测约 4 分钟）
#   scripts/pack-demo.sh --prepare       # 只做自检 + 构建 + 校验 + 状态（约 3 秒，不跑模型）
#   MODEL=/opt/models/Qwen3-4B-Q4_K_M.gguf scripts/pack-demo.sh   # 换模型（更慢）
#
#   BUNDLE=/tmp/xxx.bundle   覆盖输出目录（默认 /tmp/ip.bundle；只允许 /tmp 下）
#   SKIP_DOCTOR=1            跳过 `agent-sec-perf doctor`（例如用户级配置故意不完整）
#   ALLOW_BUNDLE_OUTSIDE_TMP=1  允许把 BUNDLE 指到 /tmp 之外（危险：会先删该目录）
#
# 【退出码】
#   0  成功（`pack run` 全部机检通过，status=evaluated）
#   1  `pack run` 返回失败（任务未通过机检；看终端里的机检表与工作区）
#   2  前置条件不满足（环境 / 模型 / 端口 / 路径），**没有**开始跑模型
#   3  装配期故障（案例 / 包 / 模型装配失败；看 stderr 的类型与消息）
#   其他 其他未预期故障
# ============================================================================

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CASE_DIR="${CASE_DIR:-$ROOT/examples/showcase/incident-01}"
BUNDLE="${BUNDLE:-/tmp/ip.bundle}"
MODEL="${MODEL:-/opt/models/MiniCPM5-2B-Q4_K_M.gguf}"
LLAMA_FALLBACK="/opt/llama.cpp/build/bin/llama-server"
MODE="${1:-full}"

START_SECONDS=$SECONDS

if [[ "$MODE" != "full" && "$MODE" != "--prepare" ]]; then
  printf '用法：%s [--prepare]\n' "$0" >&2
  exit 2
fi

step() {
  printf '\n\033[1;36m== %s ==\033[0m\n' "$*"
}

die() {
  printf '\n\033[1;31m[演示前置失败]\033[0m %s\n' "$*" >&2
  exit 2
}

finish() {
  local code=$?
  trap - EXIT
  if ((code != 0)); then
    printf '\n\033[1;31m演示未成功（退出码 %d）。\033[0m\n' "$code" >&2
    if pgrep -a llama-server >/dev/null 2>&1; then
      printf '残留推理进程（确认后可用 pkill -f llama-server 结束）：\n' >&2
      pgrep -a llama-server >&2 || true
    fi
    case "$code" in
      1) printf '排障：先看机检表里第一条 fail；工作区保留在 /tmp/lowspec-showcase-*/workspace。\n' >&2 ;;
      2) printf '排障：本脚本没有开始跑模型；按上面那条前置失败的提示修正环境。\n' >&2 ;;
      3) printf '排障：看 stderr 的类型与消息；不要靠改判据绕过。\n' >&2 ;;
      *) printf '排障：见 docs/engineering/pack-runbook.md §7。\n' >&2 ;;
    esac
  elif [[ "$MODE" == "--prepare" ]]; then
    printf '\n\033[1;32m准备完成：%d 秒（未跑模型）。\033[0m\n' "$((SECONDS - START_SECONDS))"
  else
    printf '\n\033[1;32m演示成功：%d 秒。\033[0m\n' "$((SECONDS - START_SECONDS))"
  fi
  exit "$code"
}
trap finish EXIT

# ---------------------------------------------------------------------------
# 0. 前置条件（全部与模型无关，必须先过）
# ---------------------------------------------------------------------------
step "0/6 前置检查"

[[ -n "$BUNDLE" && "$BUNDLE" == /* ]] || die "BUNDLE 必须是绝对路径：$BUNDLE"
case "$BUNDLE" in
  / | "$ROOT" | "$ROOT"/*) die "BUNDLE 不能指向仓库或根目录：$BUNDLE" ;;
esac
if [[ "$BUNDLE" != /tmp/* && "${ALLOW_BUNDLE_OUTSIDE_TMP:-0}" != "1" ]]; then
  die "BUNDLE 默认只允许 /tmp 下（脚本会 --force 删除该目录）：$BUNDLE"
fi

command -v uv >/dev/null 2>&1 || die "找不到 uv（先执行 make setup）"
[[ "$MODEL" == /* ]] || die "MODEL 必须是绝对路径：$MODEL"
[[ -f "$MODEL" ]] || die "模型文件不存在：$MODEL（本环境可用 /opt/models/MiniCPM5-2B-Q4_K_M.gguf）"
if ! command -v llama-server >/dev/null 2>&1; then
  [[ -x "$LLAMA_FALLBACK" ]] || die "找不到 llama-server（PATH 与 $LLAMA_FALLBACK 都没有）"
  die "llama-server 不在 PATH（产品按 PATH 解析）。可执行：export PATH=\"$(dirname "$LLAMA_FALLBACK"):\$PATH\""
fi
[[ -d "$CASE_DIR" ]] || die "案例目录不存在：$CASE_DIR"
CASE_DIR="$(cd "$CASE_DIR" && pwd)"
[[ -f "$ROOT/src/agent_sec_perf/pack/__init__.py" ]] ||
  die "当前分支没有能力包内核（src/agent_sec_perf/pack/）；先切到 feat/11-capability-pack-kernel"

port_busy() {
  if command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | awk 'NR > 1 {print $4}' | grep -qE '(:|\.)8080$'
    return
  fi
  if command -v netstat >/dev/null 2>&1; then
    netstat -ltn 2>/dev/null | awk 'NR > 2 {print $4}' | grep -qE '(:|\.)8080$'
    return
  fi
  # 兜底：镜像里可能既没有 ss 也没有 netstat（本环境实测两者都没有）。
  # 只做 connect 探测：能连上就说明 8080 有进程在听——这正是 llama-server 起不来的原因。
  python3 - <<'PY'
import socket
import sys

sock = socket.socket()
sock.settimeout(0.5)
try:
    sock.connect(("127.0.0.1", 8080))
except OSError:
    sys.exit(1)
finally:
    sock.close()
sys.exit(0)
PY
}

if port_busy; then
  pgrep -a llama-server >&2 || true
  die "8080 已被占用：先确认并结束残留 llama-server（pkill -f llama-server），再重跑"
fi

printf '分支：%s\n' "$(git -C "$ROOT" branch --show-current 2>/dev/null || echo '（非 git 仓库）')"
printf '提交：%s\n' "$(git -C "$ROOT" log --oneline -1 2>/dev/null || echo '（无）')"
printf '模型：%s\n' "$MODEL"
printf '包：  %s\n' "$BUNDLE"

if [[ "${SKIP_DOCTOR:-0}" != "1" ]]; then
  step "1/6 环境体检（agent-sec-perf doctor）"
  (cd "$ROOT" && uv run agent-sec-perf doctor) || die "doctor 报 fail（见上表；隔离 L1 是 warn，不是 fail）"
else
  printf '\n（SKIP_DOCTOR=1：跳过 doctor）\n'
fi

# ---------------------------------------------------------------------------
# 2. 构建 → 校验 → 状态
# ---------------------------------------------------------------------------
step "2/6 构建能力包（--force 保证从「还没有证据」开始）"
(cd "$ROOT" && uv run agent-sec-perf pack build "$CASE_DIR" --out "$BUNDLE" --force)

step "3/6 校验能力包（逐文件 sha256 + 判据自证）"
(cd "$ROOT" && uv run agent-sec-perf pack verify "$BUNDLE")

step "4/6 状态与修复目录（状态必须是 verified；plan 是 F1~F9 白名单）"
(cd "$ROOT" && uv run agent-sec-perf pack status "$BUNDLE")
(cd "$ROOT" && uv run agent-sec-perf pack plan)

if [[ "$MODE" == "--prepare" ]]; then
  step "prepare 完成（未跑模型）"
  printf '下一步：%s\n' "$0"
  exit 0
fi

# ---------------------------------------------------------------------------
# 5. 真跑（唯一的分钟级步骤）
# ---------------------------------------------------------------------------
step "5/6 用端侧模型跑一次（流式显示思考；约 4 分钟）"
(cd "$ROOT" && uv run agent-sec-perf pack run "$BUNDLE" \
  --model "$MODEL" \
  --model-dir "$(dirname "$MODEL")" \
  --keep)

# ---------------------------------------------------------------------------
# 6. 状态 + 产物位置
# ---------------------------------------------------------------------------
step "6/6 运行后的状态（必须是 evaluated）"
(cd "$ROOT" && uv run agent-sec-perf pack status "$BUNDLE")

WORKSPACE="$(find /tmp -maxdepth 1 -type d -name 'lowspec-showcase-*' -printf '%T@ %p\n' 2>/dev/null \
  | sort -nr | head -n 1 | cut -d' ' -f2- || true)"
EVIDENCE="$(find "$BUNDLE/evidence" -maxdepth 1 -type f -name '*.json' -printf '%T@ %p\n' 2>/dev/null \
  | sort -nr | head -n 1 | cut -d' ' -f2- || true)"

printf '\n证据：%s\n' "${EVIDENCE:-（未找到）}"
if [[ -n "$WORKSPACE" ]]; then
  printf '工作区：%s\n' "$WORKSPACE/workspace"
  printf '终稿：%s\n' "$WORKSPACE/workspace/report.md"
  for file in "$WORKSPACE"/workspace/reasoning-*.md; do
    [[ -f "$file" ]] && printf '思考：%s\n' "$file"
  done
fi

printf '\n现场口径：这是一次单案例、单次采样的通过（11 条机检），'
printf '不是"模型具备通用 agent 能力"，也不是性能结论。\n'
