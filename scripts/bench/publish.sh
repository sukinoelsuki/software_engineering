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
#   3. **来源分支白名单**：默认只允许从 `bench/nightly` 或 `test/*` 运行
#      （2026-09-24 起跑测改在 `test/<slug>` 借来的云原生开发环境里人工触发，
#       见 ADR-0023）；本地演练需显式 `BENCH_ALLOW_LOCAL=1`
#      （避免在 develop/main 上误触发发布）；
#   4. **提交前校验**：调用生产代码里的同一套 schema 校验，失败即拒绝发布
#      （不发布坏数据——坏数据比没有数据更难发现）；
#   5. **凭据**：不读取、不落盘任何密钥。CI 里用运行期临时令牌（构建结束即销毁），
#      并通过 credential helper 传入，**不写进 remote URL、不写进 git config**；
#   6. **失败即非零退出**（fail-secure），不重试、不吞错。
#
# 【用法】
#   bash scripts/bench/publish.sh                 # 发布到数据分支（跑测环境）
#   DRY_RUN=1 bash scripts/bench/publish.sh       # 只演练：校验+提交，不推送
#   BENCH_DATA_ROOT=/tmp/bench-data DRY_RUN=1 BENCH_ALLOW_LOCAL=1 bash scripts/bench/publish.sh
#
# 通常不必直接调它：`make bench`（scripts/bench/run.sh）会在四道可信闸门全过之后调。
# ============================================================================

set -euo pipefail

#: 来源分支白名单（**ERE**，不是单个分支名）。2026-09-24 由"只允许 bench/nightly"
#: 扩为"bench/nightly 或 test/*"：跑测不再由 CI 触发，而是在 `test/<slug>`
#: 借来的云原生开发环境里人工触发（ADR-0023）。develop/main **仍然不在白名单里**
#: ——"人手触发"不等于"可以在主干上顺手发布数据"。
readonly CODE_BRANCH_PATTERN="${BENCH_CODE_BRANCH_PATTERN:-^bench/nightly$|^test/}"
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

if [[ ! "${CURRENT_BRANCH}" =~ ${CODE_BRANCH_PATTERN} ]]; then
    if [[ "${BENCH_ALLOW_LOCAL:-0}" == "1" ]]; then
        log "本地演练模式：当前分支 ${CURRENT_BRANCH} 不在白名单 ${CODE_BRANCH_PATTERN} 内，已放行"
    else
        die "只允许从 ${CODE_BRANCH_PATTERN} 运行（当前 ${CURRENT_BRANCH}）；本地演练请设 BENCH_ALLOW_LOCAL=1"
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
# 3. 把本轮数据**合并**进数据子目录（绝不删除历史，也绝不触碰代码）
#
# 为什么不是"整体替换"：CI 的数据根目录是全新容器里的、只有本轮，而数据分支上已经
# 有历史轮次。整体替换会删掉历史轮次的日志/产物/报告，并把 index.json 退化成只有
# 一条——数据分支于是永远只剩最新一轮，"跨夜序列"根本建立不起来。
# 2026-09-17 的首夜发布在提交 diff 里已经真实删除了 09-16 的 capability.json /
# perf.json / report.md 与全部 server.log（该次发布因 SIGPIPE 失败，数据才侥幸留存）。
# ---------------------------------------------------------------------------
readonly DAILY_SRC="${DATA_ROOT}/daily"
[[ -d "${DAILY_SRC}" ]] || die "数据根目录下没有 daily/：${DAILY_SRC}"

readonly LATEST_DAY="$(ls -1 "${DAILY_SRC}" | sort | tail -n1)"
# 目录名参与路径构造，因此必须是严格白名单格式（防目录穿越）；
# 不做"看起来像日期"的宽松判断。
[[ "${LATEST_DAY}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] \
    || die "每日目录名不是合法日期：${LATEST_DAY:-<空>}"

mkdir -p "${WORKTREE:?}/${DATA_SUBDIR:?}/daily"
rm -rf "${WORKTREE:?}/${DATA_SUBDIR:?}/daily/${LATEST_DAY}"
cp -a "${DAILY_SRC}/${LATEST_DAY}" "${WORKTREE}/${DATA_SUBDIR}/daily/"

# 索引与 latest 报告交给生产代码合并（与跑轮次共用同一套模块，不引入第二套实现）
PYTHONPATH="${PWD}/src" uv run python -m agent_sec_perf.bench.rounds \
    --data-root "${DATA_ROOT}" --merge-into "${WORKTREE}/${DATA_SUBDIR}" \
    || die "数据合并失败，拒绝发布"

# 判定中间目录不入库。它们是**模型生成的 .py**，一旦进入版本库就会被代码格式化钩子
# 改写——那等于篡改证据；产物原文已另有 artifacts/*.md 归档。
find "${WORKTREE}/${DATA_SUBDIR}" -type d -name work -prune -exec rm -rf {} +

git -C "${WORKTREE}" add "${DATA_SUBDIR}"
if git -C "${WORKTREE}" diff --cached --quiet; then
    log "没有新的数据变更，跳过提交"
    exit 0
fi

# 数据提交**不运行代码钩子**（core.hooksPath 指向空目录）：
#   钩子是为代码质量设计的（ruff 会格式化 Markdown/`.py` 里的代码块），
#   而这里提交的是模型产出与日志——它们必须字节保真，不能被"格式化"。
#   代码侧的门禁由**借来的开发环境**里的 pre-commit 与 `make check` 承担
#   （2026-09-24 起已无 CI 推送这条路径，见 ADR-0023）；此处既不重复也不适用。
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
    # 同上：不得用 `… | head`（pipefail 下会变成 SIGPIPE 141）。用 awk 打印前三行并缩进。
    awk 'NR <= 3 { printf "    %s\n", $0 }' "${WORKTREE}/sign-error.log"
    git -C "${WORKTREE}" -c core.hooksPath="${EMPTY_HOOKS_DIR}" -c commit.gpgsign=false \
        -c user.name="cnb" -c user.email="cnb@cnb.cool" \
        commit -F "${COMMIT_MSG_FILE}"
    log "数据提交完成（未签名）"
fi
rm -f "${WORKTREE}/sign-error.log"

log "本次将推送的文件："
# 不要把 git 的输出用管道接到 head：`set -o pipefail` 下 head 读完若干行即退出，
# git 后续写入会收到 SIGPIPE（退出码 141），整条管道被判为非零 ⇒ 发布被中止。
# 这个坑**只在输出超过截断行数时出现**：2026-09-17 首夜（135 个文件）必现，
# 09-16（6 个文件）不暴露。先落盘、再截断即可。
readonly STAT_FILE="$(mktemp)"
git -C "${WORKTREE}" --no-pager show --stat --oneline HEAD > "${STAT_FILE}"
head -30 "${STAT_FILE}"
rm -f "${STAT_FILE}"

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
