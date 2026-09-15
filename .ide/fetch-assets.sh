#!/usr/bin/env bash
# ============================================================================
# 构建期资产获取脚本：端侧模型 + 开源 Harness 参考资料
#
# 【为什么做成脚本，而不是把命令直接写进 Dockerfile】
#   1. **可独立验证**：开发环境无法内省镜像构建过程，把逻辑放进脚本后，
#      可以在容器里直接跑一遍确认它对，而不是"改完等重建才知道"；
#   2. **数据与逻辑分离**：增删模型/参考仓库只改 assets/*.txt，不动逻辑。
#
# 【用法】
#   fetch-assets.sh models     [目标目录]   # 下载并校验模型（默认 /opt/models）
#   fetch-assets.sh references [目标目录]   # 按固定提交浅克隆参考仓库
#   fetch-assets.sh verify     [模型目录]   # 只校验已有模型，不下载（用于构建自检与运行期复查）
#
# 【设计要点】
#   - **fail-secure**：校验和不符立即非零退出，中断镜像构建——绝不静默接受坏资产；
#   - **幂等**：已存在且校验通过的文件直接跳过，便于重复执行与本地复用；
#   - 下载先写 `<file>.part` 再原子改名，避免半成品被当成完整文件；
#   - 参考仓库固定到**具体提交 SHA**（不是分支、不是标签），保证可复现；
#   - 脚本不读取任何凭据，不写入密钥，不修改系统状态（符合 SECURITY.md）。
#
# 【依赖】curl / sha256sum / git —— 均由基础镜像提供，不引入新依赖。
# ============================================================================

set -euo pipefail

readonly ASSETS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/assets"
readonly MODELS_MANIFEST="${ASSETS_DIR}/models.txt"
readonly REFERENCES_MANIFEST="${ASSETS_DIR}/references.txt"

log() { printf '[assets] %s\n' "$*"; }
# 用 %b 解释转义，使多行错误信息（期望/实际校验值）在构建日志中分行可读
die() { printf '[assets][ERROR] %b\n' "$*" >&2; exit 1; }

# 跳过注释行与纯空白行
is_skippable() {
    local line="$1"
    [[ -z "${line//[[:space:]]/}" ]] && return 0
    [[ "${line#"${line%%[![:space:]]*}"}" == \#* ]] && return 0
    return 1
}

require_file() {
    [[ -f "$1" ]] || die "找不到清单文件：$1"
}

# ---------------------------------------------------------------------------
# models：下载并校验模型权重
# ---------------------------------------------------------------------------
cmd_models() {
    local dest_dir="${1:-/opt/models}"
    require_file "${MODELS_MANIFEST}"
    mkdir -p "${dest_dir}"

    local raw sha name url quant size note path actual
    # 先整行读入再切分：注释行的字段数与数据行不同，直接按 `|` 读会错位
    while IFS= read -r raw || [[ -n "${raw}" ]]; do
        is_skippable "${raw}" && continue
        IFS='|' read -r sha name url quant size note <<< "${raw}"
        path="${dest_dir}/${name}"

        if [[ -f "${path}" ]]; then
            actual="$(sha256sum "${path}" | cut -d' ' -f1)"
            if [[ "${actual}" == "${sha}" ]]; then
                log "跳过（已存在且校验通过）：${name}"
                continue
            fi
            log "校验不符，重新下载：${name}"
            rm -f "${path}"
        fi

        log "下载 ${name}（${quant}，${size}）—— 大文件可能需要数分钟"
        log "  来源：${url}"
        # -f：HTTP 错误即失败；-L：跟随 HF 的 302 到 CDN；--retry：网络抖动可自愈
        # --silent --show-error：抑制进度条（构建日志里是噪音），但保留错误输出
        curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 30 \
            --output "${path}.part" "${url}" \
            || die "下载失败：${name}"

        actual="$(sha256sum "${path}.part" | cut -d' ' -f1)"
        if [[ "${actual}" != "${sha}" ]]; then
            rm -f "${path}.part"
            die "校验和不符：${name}\n  期望：${sha}\n  实际：${actual}\n  已删除该文件，构建中止。"
        fi
        mv "${path}.part" "${path}"
        log "  校验通过：${name}"
    done < "${MODELS_MANIFEST}"
}

# ---------------------------------------------------------------------------
# references：按固定提交浅克隆参考资料
# ---------------------------------------------------------------------------
cmd_references() {
    local dest_dir="${1:-/opt/references/harness}"
    require_file "${REFERENCES_MANIFEST}"
    mkdir -p "${dest_dir}"

    local raw name url commit license tier size note target
    while IFS= read -r raw || [[ -n "${raw}" ]]; do
        is_skippable "${raw}" && continue
        IFS='|' read -r name url commit license tier size note <<< "${raw}"
        target="${dest_dir}/${name}"

        if [[ -d "${target}/.git" ]]; then
            local head
            head="$(git -C "${target}" rev-parse HEAD 2>/dev/null || echo '')"
            if [[ "${head}" == "${commit}" ]]; then
                log "跳过（已固定在 ${commit:0:12}）：${name}"
                continue
            fi
            log "提交不符（当前 ${head:0:12}），重新拉取：${name}"
            rm -rf "${target}"
        fi

        log "克隆 ${name}（${license}，${tier}）→ ${commit:0:12}"
        mkdir -p "${target}"
        # 按 SHA 浅克隆：GitHub 支持按对象名 fetch，因此无需拉取完整历史
        git init -q "${target}"
        git -C "${target}" remote add origin "${url}"
        git -C "${target}" fetch -q --depth 1 origin "${commit}" \
            || die "拉取失败：${name} @ ${commit}"
        git -C "${target}" checkout -q FETCH_HEAD
    done < "${REFERENCES_MANIFEST}"
}

# ---------------------------------------------------------------------------
# verify：只校验已有模型，不下载（构建自检与运行期复查共用）
# ---------------------------------------------------------------------------
cmd_verify() {
    local dest_dir="${1:-/opt/models}"
    require_file "${MODELS_MANIFEST}"

    local raw sha name url quant size note path actual count=0
    while IFS= read -r raw || [[ -n "${raw}" ]]; do
        is_skippable "${raw}" && continue
        IFS='|' read -r sha name url quant size note <<< "${raw}"
        path="${dest_dir}/${name}"
        [[ -f "${path}" ]] || die "模型缺失：${path}"
        actual="$(sha256sum "${path}" | cut -d' ' -f1)"
        [[ "${actual}" == "${sha}" ]] || die "模型校验失败：${path}"
        log "OK ${name}（$(du -h "${path}" | cut -f1)）"
        count=$((count + 1))
    done < "${MODELS_MANIFEST}"

    log "共校验 ${count} 个模型，全部通过"
}

main() {
    local cmd="${1:-}"
    shift || true
    case "${cmd}" in
        models)     cmd_models "$@" ;;
        references) cmd_references "$@" ;;
        verify)     cmd_verify "$@" ;;
        *) die "用法：$0 {models|references|verify} [目标目录]" ;;
    esac
}

main "$@"
