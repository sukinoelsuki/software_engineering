#!/usr/bin/env bash
# ============================================================================
# 分支卫生自检：找出"已经产出、但还没合入基准分支（默认 develop）"的工作
#
# 【为什么需要它】
#   本项目的开发环境每次从 develop 拉起，且重启即清空上下文。
#   因此"留在未合入分支上的工作"在下一次会话里**等于不存在**——
#   2026-09-16 就因此丢过一批内容（见 ADR-0013 与 devlog 0011）。
#   这个脚本把"是否有未合入的工作"从**记性问题**变成**机器检查**。
#
# 【用法】
#   check-branch-hygiene.sh [--base <分支>] [--offline] [--strict]
#     --base     基准分支，默认 develop（本地分支优先，其次 origin/<base>）
#     --offline  只检查本地分支，不访问远端
#     --strict   存在未合入工作**或状态未知**时退出码 1（供 CI 选择是否阻断）
#
# 【退出码】
#   0  没有未合入的工作（或仅报告模式下的默认返回）
#   1  存在未合入的工作，或状态无法判定（仅 --strict）
#   2  用法错误 / 不在 git 仓库 / 找不到基准分支
#
# 【安全约束】
#   - **只读**：不写工作区、不改引用、不 fetch、不 push；
#   - **不使用任何凭据**：开放 PR 通过 `git ls-remote` 读取 `refs/pull/*/head`
#     （CNB 公开暴露该引用），不调用需要 token 的 API；
#   - **不输出远端 URL**（其中可能含令牌），只输出远端名；
#   - **fail-secure**：网络不可用导致无法判定时，状态记为"未知"而非"干净"，
#     `--strict` 下按失败处理。
# ============================================================================

set -euo pipefail

readonly BASE_DEFAULT="develop"
readonly REMOTE_NAME="origin"
# 发布线与基准天然分叉（main 只接受 develop 的发布合并），不参与"未合入"判定，
# 否则每次都会有一条恒真的告警，反而掩盖真正的问题。
readonly IGNORED_BRANCHES="main"

BASE="${BASE_DEFAULT}"
OFFLINE=0
STRICT=0
PENDING=0
UNKNOWN=0

log() { printf '[hygiene] %s\n' "$*"; }
warn() { printf '[hygiene][WARN] %s\n' "$*" >&2; }
die() { printf '[hygiene][ERROR] %s\n' "$*" >&2; exit 2; }

usage() {
    cat <<'USAGE'
用法：check-branch-hygiene.sh [--base <分支>] [--offline] [--strict]

  --base <分支>   基准分支，默认 develop
  --offline       只检查本地分支（不访问远端）
  --strict        有未合入工作或状态未知时退出码 1
  -h, --help      显示本帮助
USAGE
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --base)
            [[ $# -ge 2 ]] || die "--base 缺少取值"
            BASE="$2"
            shift 2
            ;;
        --offline) OFFLINE=1; shift ;;
        --strict) STRICT=1; shift ;;
        -h | --help) usage; exit 0 ;;
        *) die "未知参数：$1" ;;
    esac
done

git rev-parse --git-dir >/dev/null 2>&1 || die "当前目录不是 git 仓库"

# ---------------------------------------------------------------------------
# 基准分支：本地优先，其次 origin/<base>
# ---------------------------------------------------------------------------
base_ref=""
if git show-ref --verify --quiet "refs/heads/${BASE}"; then
    base_ref="refs/heads/${BASE}"
elif git show-ref --verify --quiet "refs/remotes/${REMOTE_NAME}/${BASE}"; then
    base_ref="refs/remotes/${REMOTE_NAME}/${BASE}"
else
    die "找不到基准分支 ${BASE}（本地与 ${REMOTE_NAME} 均无）"
fi

current_branch="$(git symbolic-ref --quiet --short HEAD 2>/dev/null || echo '(detached)')"

log "基准分支：${base_ref}    当前分支：${current_branch}"
echo

# ---------------------------------------------------------------------------
# 相对基准的领先/落后提交数
#   输出 "<落后> <领先>"；无法计算时输出空串（例如远端分支本地无该对象）
# ---------------------------------------------------------------------------
ahead_behind() {
    local ref="$1" pair
    pair="$(git rev-list --left-right --count "${base_ref}...${ref}" 2>/dev/null || true)"
    printf '%s' "${pair}"
}

report_row() {
    # <名称> <领先> <落后> <备注>
    printf '  %-52s %6s %6s   %s\n' "$1" "$2" "$3" "$4"
}

echo "  本地分支"
printf '  %-52s %6s %6s   %s\n' "分支" "领先" "落后" "状态"

local_branches=()
while IFS= read -r line; do
    [[ -n "${line}" ]] && local_branches+=("${line}")
done < <(git for-each-ref --format='%(refname:short)' refs/heads)

for br in "${local_branches[@]}"; do
    if [[ "${br}" == "${BASE}" ]]; then
        report_row "${br}" "-" "-" "基准分支"
        continue
    fi
    if [[ "${br}" == "${IGNORED_BRANCHES}" ]]; then
        report_row "${br}" "-" "-" "发布线（不参与判定）"
        continue
    fi
    pair="$(ahead_behind "refs/heads/${br}")"
    if [[ -z "${pair}" ]]; then
        UNKNOWN=$((UNKNOWN + 1))
        report_row "${br}" "?" "?" "未知（无法与基准比较）"
        continue
    fi
    # rev-list 的输出以**制表符**分隔，用 read 按 IFS 切分，不能按空格切
    read -r behind ahead <<<"${pair}"
    if [[ "${ahead}" == "0" ]]; then
        report_row "${br}" "${ahead}" "${behind}" "已合入"
    else
        PENDING=$((PENDING + 1))
        report_row "${br}" "${ahead}" "${behind}" "⚠ 未合入基准"
    fi
done

# ---------------------------------------------------------------------------
# 远端：只在非 --offline 时检查
# ---------------------------------------------------------------------------
if [[ "${OFFLINE}" -eq 1 ]]; then
    echo
    log "已跳过远端检查（--offline）"
else
    remote_heads="$(git ls-remote --heads "${REMOTE_NAME}" 2>/dev/null || true)"
    if [[ -z "${remote_heads}" ]]; then
        echo
        warn "无法读取远端 ${REMOTE_NAME} 的分支（离线或权限不足）→ 远端状态未知"
        UNKNOWN=$((UNKNOWN + 1))
    else
        echo
        echo "  远端分支（${REMOTE_NAME}）"
        printf '  %-52s %6s %6s   %s\n' "分支" "领先" "落后" "状态"
        while read -r sha refname; do
            [[ -n "${refname}" ]] || continue
            br="${refname#refs/heads/}"
            [[ "${br}" == "${BASE}" || "${br}" == "HEAD" || "${br}" == "${IGNORED_BRANCHES}" ]] && continue
            # 已在本地分支列表里出现过的，不重复报告
            if git show-ref --verify --quiet "refs/heads/${br}"; then
                continue
            fi
            if git cat-file -e "${sha}^{commit}" 2>/dev/null; then
                pair="$(ahead_behind "${sha}")"
                if [[ -z "${pair}" ]]; then
                    UNKNOWN=$((UNKNOWN + 1))
                    report_row "${br}" "?" "?" "未知（无法与基准比较）"
                else
                    read -r behind ahead <<<"${pair}"
                    if [[ "${ahead}" == "0" ]]; then
                        report_row "${br}" "${ahead}" "${behind}" "已合入"
                    else
                        PENDING=$((PENDING + 1))
                        report_row "${br}" "${ahead}" "${behind}" "⚠ 未合入基准"
                    fi
                fi
            else
                UNKNOWN=$((UNKNOWN + 1))
                report_row "${br}" "-" "-" "未知（本地无该提交对象）"
            fi
        done <<<"${remote_heads}"

        echo
        echo "  开放合并请求（refs/pull/*/head）"
        pr_refs="$(git ls-remote "${REMOTE_NAME}" 'refs/pull/*/head' 2>/dev/null || true)"
        if [[ -z "${pr_refs}" ]]; then
            warn "  无法读取合并请求引用 → 状态未知"
            UNKNOWN=$((UNKNOWN + 1))
        else
            while read -r sha refname; do
                [[ -n "${refname}" ]] || continue
                num="${refname#refs/pull/}"
                num="${num%/head}"
                if git cat-file -e "${sha}^{commit}" 2>/dev/null; then
                    if git merge-base --is-ancestor "${sha}" "${base_ref}" 2>/dev/null; then
                        printf '  PR #%-6s %s  已合入\n' "${num}" "${sha:0:8}"
                    else
                        PENDING=$((PENDING + 1))
                        printf '  PR #%-6s %s  ⚠ 未合入\n' "${num}" "${sha:0:8}"
                    fi
                else
                    UNKNOWN=$((UNKNOWN + 1))
                    printf '  PR #%-6s %s  未知（本地无该提交对象）\n' "${num}" "${sha:0:8}"
                fi
            done <<<"${pr_refs}"
        fi
    fi
fi

# ---------------------------------------------------------------------------
# 结论
# ---------------------------------------------------------------------------
echo
log "未合入：${PENDING} 处；状态未知：${UNKNOWN} 处"

if [[ "${PENDING}" -eq 0 && "${UNKNOWN}" -eq 0 ]]; then
    log "✅ 没有遗留的未合入工作"
    exit 0
fi

if [[ "${PENDING}" -gt 0 ]]; then
    warn "存在未合入的工作：重启环境后它们将不可见，请合回 ${BASE} 或登记到 devlog §7"
fi
if [[ "${UNKNOWN}" -gt 0 ]]; then
    warn "存在无法判定的条目：不假设它们安全，请连网后复查（fail-secure）"
fi

if [[ "${STRICT}" -eq 1 ]]; then
    exit 1
fi
exit 0
