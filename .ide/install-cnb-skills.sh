#!/usr/bin/env bash
# ============================================================================
# 构建期安装脚本：CNB 平台能力（cnb-cli + 官方 Skills）
#
# 【为什么是本脚本、而不是复用 .ide/fetch-assets.sh】
#   两者处理的是**不同种类的资产**，不存在"同一逻辑的第二份实现"：
#     fetch-assets.sh —— 按 URL 下载单个文件（模型 / 基准）或克隆参考仓库（只读查阅）；
#     本脚本          —— 安装 **npm 全局包**，并把 Skill 落到代理可读的**两层目录**
#                        （canonical 目录 + 符号链接），逐 Skill 校验 `SKILL.md` 摘要。
#   ⚠️ 更关键的原因是**层缓存**：`fetch-assets.sh` 的 COPY 位于 Dockerfile 第 10~12 阶段
#      （GB 级资产）**之前**，改它会使模型层缓存失效、重建多花约 20~25 分钟
#      （该代价已由 .ide/Dockerfile 的注释实测记录）。把本脚本的 COPY/RUN 放在
#      第 12 阶段**之后**，前序层输入不变 ⇒ 命中缓存，重建只跑本阶段 + 自检。
#   ⚠️ 因此：**不要**把本脚本的逻辑并进 fetch-assets.sh——那会把"改一次 Skill 清单"
#      的代价从"秒级"抬高到"20 分钟级"。
#
# 【用法】
#   install-cnb-skills.sh install [仓库克隆目录]  # 安装 CLI 与 Skill（默认动作）
#   install-cnb-skills.sh verify  [仓库克隆目录]  # 只校验已装内容，不安装（构建自检 / 运行期复查）
#   install-cnb-skills.sh plan                    # 只解析清单并打印计划，不安装
#
# 【设计要点】
#   - **fail-secure**：版本 / 摘要不符立即非零退出，中断镜像构建——绝不静默接受坏资产；
#   - **幂等**：已装且校验通过则跳过，便于重复执行与本地复用；
#   - **自清理**：落点里存在但不在清单内的旧 Skill 会被移除（避免"改了清单却留着旧 Skill"）；
#   - 仓库克隆目录固定在 /opt（在 /workspace 之外）且构建后**保留**，供 `verify` 复查；
#   - 脚本**不读取任何凭据**、不写密钥、不修改系统状态（符合 SECURITY.md）。
#
# 【依赖】npm / node / python3 / git / sha256sum / ln —— 均由镜像既有阶段提供，不引入新依赖。
# ============================================================================

set -euo pipefail

readonly SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly ASSETS_DIR="${SCRIPT_DIR}/assets"
readonly MANIFEST="${ASSETS_DIR}/cnb-skills.txt"

# Skill 落点：两层结构，缺一不可（依据见清单文件头部）
readonly HOME_DIR="${HOME:-/root}"
readonly AGENTS_SKILLS="${HOME_DIR}/.agents/skills"
readonly CODEBUDDY_SKILLS="${HOME_DIR}/.codebuddy/skills"
readonly REPO_DIR_DEFAULT="/opt/cnb-skill-repo"

log() { printf '[cnb-skills] %s\n' "$*"; }
# 用 %b 解释转义，使多行错误信息（期望/实际）在构建日志中分行可读
die() { printf '[cnb-skills][ERROR] %b\n' "$*" >&2; exit 1; }

is_skippable() {
    local line="$1"
    [[ -z "${line//[[:space:]]/}" ]] && return 0
    [[ "${line#"${line%%[![:space:]]*}"}" == \#* ]] && return 0
    return 1
}

require_manifest() {
    [[ -f "${MANIFEST}" ]] || die "找不到清单文件：${MANIFEST}"
}

# 逐行解析清单，输出 **7 个以制表符分隔的字段**，顺序与清单一致。
# 固定 7 列：<类型> <名称> <附加> <钉定值> <许可证> <体积> <说明>
#   （不适用某列时清单里写 `-`；**不得增删列**）
each_entry() {
    local raw kind name extra pinned license size note
    while IFS= read -r raw || [[ -n "${raw}" ]]; do
        is_skippable "${raw}" && continue
        IFS='|' read -r kind name extra pinned license size note <<< "${raw}"
        case "${kind}" in
            npm | skill-repo | skill) : ;;
            *) die "未知的清单类型：${kind}（仅支持 npm / skill-repo / skill）" ;;
        esac
        printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
            "${kind}" "${name}" "${extra}" "${pinned}" "${license}" "${size}" "${note}"
    done < "${MANIFEST}"
}

# 已装的 npm 全局包版本（未安装时输出空串）
npm_installed_version() {
    npm ls -g --depth=0 --json 2>/dev/null | python3 -c '
import json, sys
try:
    deps = json.load(sys.stdin).get("dependencies", {})
except Exception:
    deps = {}
print(deps.get(sys.argv[1], {}).get("version", ""))
' "$1"
}

# ---------------------------------------------------------------------------
# npm 全局包：钉精确版本安装（幂等：版本已一致则跳过）
# ---------------------------------------------------------------------------
install_npm() {
    local name="$1" version="$2" current
    current="$(npm_installed_version "${name}")" || current=""
    if [[ "${current}" == "${version}" ]]; then
        log "跳过（已装且版本一致）：${name}@${version}"
        return 0
    fi
    log "安装 ${name}@${version}"
    npm install -g "${name}@${version}" || die "npm 安装失败：${name}@${version}"
    rm -rf "${HOME_DIR}/.npm" || true
}

# ---------------------------------------------------------------------------
# Skill 仓库：按完整 commit SHA 克隆（禁止分支 / 标签）
# ---------------------------------------------------------------------------
clone_repo() {
    local name="$1" url="$2" sha="$3" dest="$4" head
    if [[ -d "${dest}/.git" ]]; then
        head="$(git -C "${dest}" rev-parse HEAD 2>/dev/null || echo '')"
        if [[ "${head}" == "${sha}" ]]; then
            log "跳过（已固定在 ${sha:0:12}）：${name}"
            return 0
        fi
        log "提交不符（当前 ${head:0:12}），重新克隆：${name}"
        rm -rf "${dest}"
    fi
    log "克隆 ${name} → ${sha:0:12}"
    # 先普通克隆再 checkout：不假设远端支持"按任意 SHA 浅取"
    # （GitHub 支持 `fetch <sha>`，其它托管未必——本仓库托管在 cnb.cool）
    git clone -q --no-checkout "${url}" "${dest}" || die "克隆失败：${url}"
    git -C "${dest}" checkout -q "${sha}" || die "checkout 失败：${name} @ ${sha}"
    head="$(git -C "${dest}" rev-parse HEAD)"
    [[ "${head}" == "${sha}" ]] || die "HEAD 与钉定值不符：${name}\n  期望：${sha}\n  实际：${head}"
}

# ---------------------------------------------------------------------------
# 单个 Skill：校验 SKILL.md 摘要 → 复制到 canonical 目录 → 建符号链接
# ---------------------------------------------------------------------------
install_skill() {
    local name="$1" want_sha="$2" repo_dir="$3"
    local src="${repo_dir}/skills/${name}" actual target link

    [[ -f "${src}/SKILL.md" ]] || die "仓库内找不到该 Skill：${src}/SKILL.md"

    actual="$(sha256sum "${src}/SKILL.md" | cut -d' ' -f1)"
    [[ "${actual}" == "${want_sha}" ]] ||
        die "Skill 摘要不符：${name}\n  期望：${want_sha}\n  实际：${actual}\n  构建中止。"

    target="${AGENTS_SKILLS}/${name}"
    if [[ -f "${target}/SKILL.md" ]] &&
        [[ "$(sha256sum "${target}/SKILL.md" | cut -d' ' -f1)" == "${want_sha}" ]]; then
        log "跳过（已装且摘要一致）：${name}"
    else
        rm -rf "${target}"
        mkdir -p "${target}"
        cp -a "${src}/." "${target}/"
        log "安装 Skill：${name}"
    fi

    # 第二层：符号链接。CodeBuddy 读 ~/.codebuddy/skills/，canonical 在 ~/.agents/skills/
    link="${CODEBUDDY_SKILLS}/${name}"
    mkdir -p "${CODEBUDDY_SKILLS}"
    if [[ -L "${link}" ]]; then
        rm -f "${link}"
    elif [[ -e "${link}" ]]; then
        die "落点已被非符号链接占用（拒绝覆盖）：${link}"
    fi
    ln -s "${target}" "${link}"
}

# 移除"落点里存在、但清单里没有"的旧 Skill（避免清单瘦身后旧 Skill 继续留在镜像里）
prune_stale_skills() {
    local entry base links
    links="$(each_entry | cut -f1,2 | awk -F'\t' '$1=="skill"{print $2}')"
    local path
    for path in "${CODEBUDDY_SKILLS}"/* "${AGENTS_SKILLS}"/*; do
        [[ -e "${path}" || -L "${path}" ]] || continue
        base="$(basename "${path}")"
        if ! printf '%s\n' "${links}" | grep -qx "${base}"; then
            log "清理不在清单内的 Skill：${base}"
            rm -f "${CODEBUDDY_SKILLS:?}/${base}"
            rm -rf "${AGENTS_SKILLS:?}/${base}"
        fi
    done
}

# ---------------------------------------------------------------------------
# install：按清单安装（**单遍处理**，因此清单顺序有约束：skill-repo 先于 skill）
# ---------------------------------------------------------------------------
cmd_install() {
    require_manifest
    local repo_dir="${1:-${REPO_DIR_DEFAULT}}"
    local kind name extra pinned license size note

    mkdir -p "${AGENTS_SKILLS}" "${CODEBUDDY_SKILLS}"
    prune_stale_skills

    while IFS=$'\t' read -r kind name extra pinned license size note; do
        case "${kind}" in
            npm) install_npm "${name}" "${pinned}" ;;
            skill-repo) clone_repo "${name}" "${extra}" "${pinned}" "${repo_dir}" ;;
            skill) install_skill "${name}" "${pinned}" "${repo_dir}" ;;
        esac
    done < <(each_entry)

    log "安装完成"
}

# ---------------------------------------------------------------------------
# verify：只校验已装内容（构建自检与运行期复查共用）
# ---------------------------------------------------------------------------
cmd_verify() {
    require_manifest
    local repo_dir="${1:-${REPO_DIR_DEFAULT}}"
    local kind name extra pinned license size note
    local count_npm=0 count_skill=0 count_repo=0 actual target link

    while IFS=$'\t' read -r kind name extra pinned license size note; do
        case "${kind}" in
            npm)
                actual="$(npm_installed_version "${name}")" || actual=""
                [[ "${actual}" == "${pinned}" ]] ||
                    die "npm 包版本不符：${name}\n  期望：${pinned}\n  实际：${actual:-未安装}"
                log "OK npm   ${name}@${actual}"
                count_npm=$((count_npm + 1))
                ;;
            skill-repo)
                [[ -d "${repo_dir}/.git" ]] || die "Skill 仓库未克隆：${repo_dir}"
                [[ "$(git -C "${repo_dir}" rev-parse HEAD)" == "${pinned}" ]] ||
                    die "Skill 仓库提交不符：${repo_dir}\n  期望：${pinned}"
                log "OK repo  ${name}@${pinned:0:12}"
                count_repo=$((count_repo + 1))
                ;;
            skill)
                target="${AGENTS_SKILLS}/${name}/SKILL.md"
                [[ -f "${target}" ]] || die "Skill 缺失：${target}"
                [[ "$(sha256sum "${target}" | cut -d' ' -f1)" == "${pinned}" ]] ||
                    die "Skill 摘要不符：${name}"
                link="${CODEBUDDY_SKILLS}/${name}"
                [[ -L "${link}" ]] || die "CodeBuddy 落点不是符号链接：${link}"
                [[ -f "${link}/SKILL.md" ]] || die "符号链接不可达：${link}"
                log "OK skill ${name}"
                count_skill=$((count_skill + 1))
                ;;
        esac
    done < <(each_entry)

    log "共校验 npm 包 ${count_npm} 个、Skill 仓库 ${count_repo} 个、Skill ${count_skill} 个，全部通过"
}

# ---------------------------------------------------------------------------
# plan：只解析清单并打印，不安装
# ---------------------------------------------------------------------------
cmd_plan() {
    require_manifest
    local kind name extra pinned license size note
    while IFS=$'\t' read -r kind name extra pinned license size note; do
        case "${kind}" in
            npm) log "  npm        ${name}@${pinned}  [${license}]  ${size}" ;;
            skill-repo) log "  skill-repo ${name} @${pinned:0:12}  → /opt/cnb-skill-repo  [${license}]" ;;
            skill) log "  skill      ${name}  ${pinned:0:12}  → ${CODEBUDDY_SKILLS}/${name}" ;;
        esac
    done < <(each_entry)
}

main() {
    local cmd="${1:-install}"
    shift || true
    case "${cmd}" in
        install) cmd_install "$@" ;;
        verify) cmd_verify "$@" ;;
        plan) cmd_plan ;;
        *) die "用法：$0 {install|verify|plan} [仓库克隆目录]" ;;
    esac
}

main "$@"
