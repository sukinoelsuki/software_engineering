#!/usr/bin/env bash
# ============================================================================
# 基准数据发布：把本轮产出提交并推送到**数据分支**
#
# 为什么是独立脚本而不是写在 .cnb.yml 里：
#   1. 可以在开发环境里以 DRY_RUN 形式走一遍全流程（推送前验证逻辑）；
#   2. 安全约束集中在一处，便于审计（推送范围、force、凭据）。
#
# 【安全约束】（逐条对应 SECURITY.md 的硬性规则）
#   1. **只推数据分支**：分支名来自 BENCH_DATA_BRANCH（默认 bench/data），
#      推送形式固定为 `HEAD:${BENCH_DATA_BRANCH}`，**不提供**推其它分支的入口；
#   2. **禁止 force**：不使用 --force / --force-with-lease；
#   3. **来源分支白名单**：默认只允许从 bench/nightly 运行；本地演练需显式
#      BENCH_ALLOW_LOCAL=1（避免在 develop/main 上误触发发布）；
#   4. **提交前校验**：调用生产代码里的同一套 schema 校验，失败即拒绝发布
#      （不发布坏数据——坏数据比没有数据更难发现）；
#   5. **凭据**：不读取、不落盘任何密钥。CI 里用运行期临时令牌（构建结束即销毁），
#      并通过 credential helper 传入，**不写进 remote URL、不写进 git config**；
#   6. **失败即非零退出**（fail-secure），不重试、不吞错。
#
# 【用法】
#   bash scripts/bench/publish.sh                 # 发布到数据分支（CI）
#   DRY_RUN=1 bash scripts/bench/publish.sh       # 只演练：校验+提交，不推送
#   BENCH_DATA_ROOT=/tmp/bench-data DRY_RUN=1 BENCH_ALLOW_LOCAL=1 bash scripts/bench/publish.sh
# ============================================================================

set -euo pipefail

readonly CODE_BRANCH="${BENCH_CODE_BRANCH:-bench/nightly}"
readonly DATA_BRANCH="${BENCH_DATA_BRANCH:-bench/data}"
readonly DATA_SUBDIR="${BENCH_DATA_SUBDIR:-bench}"
readonly DATA_ROOT="${BENCH_DATA_ROOT:-${PWD}/.bench-data}"
readonly DRY_RUN="${DRY_RUN:-0}"
readonly CURRENT_BRANCH="${CNB_BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"
readonly REPO_URL="${CNB_REPO_URL_HTTPS:-}"

log()  { printf '[bench-publish] %s\n' "$*"; }
die()  { printf '[bench-publish][ERROR] %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------------------
# 0. 前置守卫
# ---------------------------------------------------------------------------
[[ -d "${DATA_ROOT}" ]] || die "数据根目录不存在：${DATA_ROOT}（先跑 make bench-round）"

if [[ "${CURRENT_BRANCH}" != "${CODE_BRANCH}" ]]; then
    if [[ "${BENCH_ALLOW_LOCAL:-0}" == "1" ]]; then
        log "本地演练模式：当前分支 ${CURRENT_BRANCH} 不等于 ${CODE_BRANCH}，已放行"
    else
        die "只允许从 ${CODE_BRANCH} 运行（当前 ${CURRENT_BRANCH}）；本地演练请设 BENCH_ALLOW_LOCAL=1"
    fi
fi

case "${DATA_BRANCH}" in
    main|master|develop) die "数据分支不得是受保护/主干分支：${DATA_BRANCH}" ;;
esac

# ---------------------------------------------------------------------------
# 1. 提交前校验（复用生产代码里的同一套 schema 校验）
# ---------------------------------------------------------------------------
log "校验 ${DATA_ROOT} 的最新一轮产出"
PYTHONPATH="${PWD}/src" uv run python -m agent_sec_perf.bench.rounds \
    --data-root "${DATA_ROOT}" --validate-only \
    || die "产出未通过 schema 校验，拒绝发布"

# ---------------------------------------------------------------------------
# 2. 准备数据分支的工作副本
# ---------------------------------------------------------------------------
readonly WORKTREE="$(mktemp -d)"
cleanup() {
    git worktree remove --force "${WORKTREE}" 2>/dev/null || rm -rf "${WORKTREE}"
}
trap cleanup EXIT

if git ls-remote --exit-code --heads origin "${DATA_BRANCH}" >/dev/null 2>&1; then
    log "获取已有数据分支 ${DATA_BRANCH}"
    git fetch --depth 1 origin "${DATA_BRANCH}"
    git worktree add --detach "${WORKTREE}" FETCH_HEAD
else
    log "数据分支 ${DATA_BRANCH} 尚不存在，将在本次发布中创建"
    git worktree add --detach "${WORKTREE}"
fi

# ---------------------------------------------------------------------------
# 3. 只同步数据子目录（绝不触碰代码）
# ---------------------------------------------------------------------------
mkdir -p "${WORKTREE}/${DATA_SUBDIR}"
rm -rf "${WORKTREE:?}/${DATA_SUBDIR:?}"
cp -a "${DATA_ROOT}" "${WORKTREE}/${DATA_SUBDIR}"

# 判定中间目录不入库。它们是**模型生成的 .py**，一旦进入版本库就会被代码格式化钩子
# 改写——那等于篡改证据；产物原文已另有 artifacts/*.md 归档。
find "${WORKTREE}/${DATA_SUBDIR}" -type d -name work -prune -exec rm -rf {} +

readonly LATEST_DAY="$(ls -1 "${WORKTREE}/${DATA_SUBDIR}/daily" | sort | tail -n1)"
[[ -n "${LATEST_DAY}" ]] || die "数据目录里没有每日记录"

git -C "${WORKTREE}" add "${DATA_SUBDIR}"
if git -C "${WORKTREE}" diff --cached --quiet; then
    log "没有新的数据变更，跳过提交"
    exit 0
fi

# 数据提交**不运行代码钩子**（core.hooksPath 指向空目录）：
#   钩子是为代码质量设计的（ruff 会格式化 Markdown/`.py` 里的代码块），
#   而这里提交的是模型产出与日志——它们必须字节保真，不能被"格式化"。
#   代码侧的门禁在 bench/nightly 推送时已经执行过，此处既不重复也不适用。
#   该豁免已登记：docs/adr/0014-benchmark-automation.md。
readonly EMPTY_HOOKS_DIR="$(mktemp -d)"
readonly COMMIT_MSG_FILE="$(mktemp)"
{
    printf 'chore(bench-data): 发布 %s 轮次数据\n\n' "${LATEST_DAY}"
    printf '来源分支：%s；来源提交：%s\n' \
        "${CURRENT_BRANCH}" "$(git -C "${WORKTREE}" rev-parse --short HEAD)"
} > "${COMMIT_MSG_FILE}"

# 先按平台默认（/etc/gitconfig 里 commit.gpgsign=true + cnb-gpgsign）尝试签名提交；
# 签名不可用时**显式**降级为未签名（平台签名助手依赖会话上下文，在流水线里可能不可用），
# 并把降级事实打进日志——不静默降级。登记位置：docs/adr/0014-benchmark-automation.md。
if git -C "${WORKTREE}" -c core.hooksPath="${EMPTY_HOOKS_DIR}" \
        -c user.name="cnb" -c user.email="cnb@cnb.cool" \
        commit -F "${COMMIT_MSG_FILE}" 2>"${WORKTREE}/sign-error.log"; then
    log "数据提交完成（已签名）"
else
    log "签名提交不可用，显式降级为未签名提交："
    sed 's/^/    /' "${WORKTREE}/sign-error.log" | head -3
    git -C "${WORKTREE}" -c core.hooksPath="${EMPTY_HOOKS_DIR}" -c commit.gpgsign=false \
        -c user.name="cnb" -c user.email="cnb@cnb.cool" \
        commit -F "${COMMIT_MSG_FILE}"
    log "数据提交完成（未签名）"
fi
rm -f "${WORKTREE}/sign-error.log"

log "本次将推送的文件："
git -C "${WORKTREE}" --no-pager show --stat --oneline HEAD | head -30

# ---------------------------------------------------------------------------
# 4. 推送（DRY_RUN 时到此为止）
# ---------------------------------------------------------------------------
if [[ "${DRY_RUN}" == "1" ]]; then
    log "DRY_RUN=1：跳过推送（提交已在临时工作副本中完成）"
    exit 0
fi

[[ -n "${REPO_URL}" ]] || die "缺少 CNB_REPO_URL_HTTPS（CI 由平台注入）"
[[ -n "${CNB_TOKEN:-}" ]] || die "缺少运行期令牌 CNB_TOKEN（CI 由平台注入，禁止手工写死）"

# 凭据通过 credential helper 传入：不写进 remote URL，也不写进 git config。
# 目标必须写成**全限定引用名** `refs/heads/<branch>`：数据分支首次创建时远端没有
# 同名引用，`HEAD:bench/data` 会被 git 拒绝
# （`error: The destination you provided is not a full refname`，
#  提示即为 `HEAD:refs/heads/bench/data`）。2026-09-16 首次真实运行即因此失败。
git -C "${WORKTREE}" \
    -c credential.helper='!f() { echo username=cnb; echo "password=${CNB_TOKEN}"; }; f' \
    push origin "HEAD:refs/heads/${DATA_BRANCH}"

log "已推送到 ${DATA_BRANCH}（来源 ${CURRENT_BRANCH}，日期 ${LATEST_DAY}）"
