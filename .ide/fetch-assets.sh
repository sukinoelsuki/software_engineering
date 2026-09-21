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
#   fetch-assets.sh benchmarks [目标目录]   # 下载并校验基准任务集（默认 /opt/benchmarks）
#   fetch-assets.sh references [目标目录]   # 按固定提交浅克隆参考仓库
#   fetch-assets.sh verify     [模型目录] [基准目录]  # 只校验已有资产，不下载（构建自检与运行期复查）
#   fetch-assets.sh plan                            # 只解析清单并打印计划，不下载
#
# 【设计要点】
#   - **fail-secure**：校验和不符立即非零退出，中断镜像构建——绝不静默接受坏资产；
#   - **幂等**：已存在且校验通过的文件直接跳过，便于重复执行与本地复用；
#   - 下载先写 `<file>.part` 再原子改名，避免半成品被当成完整文件；
#   - 参考仓库固定到**具体提交 SHA**（不是分支、不是标签），保证可复现；
#   - **一切网络访问都必须有界**（超时 + 重试）：平台会在"Job 连续 10 分钟无输出"
#     时杀掉整个构建，无界等待等于自杀。理由与实测见下面
#     【为什么 fetch 必须有界】，以及 `cmd_references` 上方的说明。
#   - 脚本不读取任何凭据，不写入密钥，不修改系统状态（符合 SECURITY.md）。
#
# 【依赖】curl / sha256sum / git / timeout（coreutils）—— 均由基础镜像提供，不引入新依赖。
# ============================================================================

set -euo pipefail

readonly ASSETS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/assets"
readonly MODELS_MANIFEST="${ASSETS_DIR}/models.txt"
readonly BENCHMARKS_MANIFEST="${ASSETS_DIR}/benchmarks.txt"
readonly REFERENCES_MANIFEST="${ASSETS_DIR}/references.txt"

# 浅克隆的**有界**参数（可用环境变量覆盖，便于本地调参/验证）。
# 取值依据见下面【为什么 fetch 必须有界】：单次上限必须**显著小于**平台的
# 10 分钟"无输出超时"，且要给正常的慢速克隆留余量（正常情况下 9 个仓库
# 合计 447 MB 全部克隆只需 40.5 s）。
readonly GIT_FETCH_TIMEOUT_S="${GIT_FETCH_TIMEOUT_S:-300}"
readonly GIT_FETCH_ATTEMPTS="${GIT_FETCH_ATTEMPTS:-3}"
readonly GIT_FETCH_BACKOFF_S="${GIT_FETCH_BACKOFF_S:-5}"

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
# 共用：按清单下载 + sha256 校验（模型与基准任务集同构，共用一份逻辑）
#   清单行格式：<sha256>|<文件名>|<URL>|<格式>|<体积>|<说明>
# ---------------------------------------------------------------------------
fetch_manifest() {
    local manifest="$1" dest_dir="$2" label="$3"
    require_file "${manifest}"
    mkdir -p "${dest_dir}"

    local raw sha name url fmt size note path actual
    # 先整行读入再切分：注释行的字段数与数据行不同，直接按 `|` 读会错位
    while IFS= read -r raw || [[ -n "${raw}" ]]; do
        is_skippable "${raw}" && continue
        IFS='|' read -r sha name url fmt size note <<< "${raw}"
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

        log "下载${label} ${name}（${fmt}，${size}）"
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
    done < "${manifest}"
}

# ---------------------------------------------------------------------------
# models：下载并校验模型权重
# ---------------------------------------------------------------------------
cmd_models() {
    fetch_manifest "${MODELS_MANIFEST}" "${1:-/opt/models}" ""
}

# ---------------------------------------------------------------------------
# benchmarks：获取并校验基准任务集
#   支持两种来源（格式见 assets/benchmarks.txt）：
#     file     —— 直接下载一个文件
#     hf-rows  —— 经 HF datasets-server 的 rows API 取全部行落为 JSONL
#                （HF 数据集多为 parquet，读它需 pyarrow；rows API 返回 JSON，
#                 用标准库即可处理，**不引入新依赖**）
# ---------------------------------------------------------------------------
fetch_hf_rows() {
    local dataset="$1" config="$2" split="$3" out="$4"
    python3 - "$dataset" "$config" "$split" "$out" <<'PYEOF'
import json
import sys
import urllib.parse
import urllib.request

dataset, config, split, out = sys.argv[1:5]
base = "https://datasets-server.huggingface.co/rows"
offset, written, total = 0, 0, None
with open(out, "w", encoding="utf-8") as fh:
    while True:
        query = urllib.parse.urlencode(
            {
                "dataset": dataset,
                "config": config,
                "split": split,
                "offset": offset,
                "length": 100,
            }
        )
        with urllib.request.urlopen(f"{base}?{query}", timeout=180) as resp:
            data = json.load(resp)
        rows = data.get("rows") or []
        if total is None:
            total = data.get("num_rows_total", len(rows))
        for item in rows:
            # 行序按 offset 递增、字段按 key 排序 → 生成结果确定，摘要可复现
            fh.write(json.dumps(item["row"], ensure_ascii=False, sort_keys=True) + "\n")
            written += 1
        offset += len(rows)
        if not rows or written >= (total or 0):
            break
print(f"  rows API：{dataset} {split} 取回 {written} / {total} 行")
PYEOF
}

cmd_benchmarks() {
    local dest_dir="${1:-/opt/benchmarks}"
    require_file "${BENCHMARKS_MANIFEST}"
    mkdir -p "${dest_dir}"

    local raw kind sha name src config split size note path actual
    while IFS= read -r raw || [[ -n "${raw}" ]]; do
        is_skippable "${raw}" && continue
        IFS='|' read -r kind sha name src config split size note <<< "${raw}"
        path="${dest_dir}/${name}"

        if [[ -f "${path}" ]]; then
            actual="$(sha256sum "${path}" | cut -d' ' -f1)"
            if [[ "${actual}" == "${sha}" ]]; then
                log "跳过（已存在且校验通过）：${name}"
                continue
            fi
            log "摘要不符，重新获取：${name}"
            rm -f "${path}"
        fi

        log "获取基准 ${name}（${size}）"
        case "${kind}" in
            file)
                log "  来源：${src}"
                curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 30 \
                    --output "${path}.part" "${src}" \
                    || die "下载失败：${name}"
                ;;
            hf-rows)
                log "  来源：HF 数据集 ${src}（config=${config}, split=${split}）"
                fetch_hf_rows "${src}" "${config}" "${split}" "${path}.part" \
                    || die "rows API 获取失败：${name}"
                ;;
            *)
                die "未知的基准来源类型：${kind}（仅支持 file / hf-rows）"
                ;;
        esac

        actual="$(sha256sum "${path}.part" | cut -d' ' -f1)"
        if [[ "${actual}" != "${sha}" ]]; then
            rm -f "${path}.part"
            die "摘要不符：${name}\n  期望：${sha}\n  实际：${actual}\n  已删除该文件，构建中止。"
        fi
        mv "${path}.part" "${path}"
        log "  校验通过：${name}"
    done < "${BENCHMARKS_MANIFEST}"
}

# ---------------------------------------------------------------------------
# references：按固定提交浅克隆参考资料
#
# 【为什么 fetch 必须有界（2026-09-21 一次真实构建失败换来的）】
#   平台规则：**Job 连续 10 分钟无任何输出即触发超时**，超时后整个镜像构建被
#   SIGKILL。日志表现是 `#24 CANCELED` + `failed to solve: Canceled: context canceled`
#   + `exit code: -1, signal: 9` —— 看上去像"Dockerfile 写错了"，实际是**被平台杀掉**。
#   （出处：https://docs.cnb.cool/zh/build/timeout.md 「无输出超时」）
#
#   故障：`git fetch` **自身没有任何超时**。GitHub 侧连接 stall 时 git 会一直等
#   （内核 TCP 重传可拖十几分钟），而这期间脚本一行都不打印 ⇒ 正好把 10 分钟填满。
#   实测（`cnb-rjo-1k3263rlg`，2026-09-21）：最后一次输出是 00:09:16 的
#   "克隆 openharness"，随后**静默 10m01s** 被杀；而正常情况下这 9 个仓库
#   （合计 447 MB）**全部克隆只花 40.5 s**、openharness（23 MB）只需 1.7 s
#   （对照 `cnb-j4r-1k2qgnopp`，2026-09-18）。
#
#   处置（四条缺一不可）：
#     ① `timeout` 兜底单次上限 ⇒ 最长静默有界；
#     ② `http.lowSpeedLimit/lowSpeedTime`：速率掉到 1 KB/s 持续 30 s 即中止
#        ——针对"连接还在、字节不动"的 stall（比整体超时更早失败）；
#     ③ 重试 + 每次尝试前后都打日志 ⇒ 最长静默 ≈ 300 s + 退避 5 s，远低于 10 分钟；
#     ④ 最终失败走 `die`：**明确失败**并指出是哪个仓库，而不是被平台悄悄杀掉。
#
#   为什么不加 `--progress` 让进度条充当 keep-alive：447 MB 会产生大量噪声行，
#   而本项目对构建日志噪音有明确取舍（见文件头 curl 的 `--silent --show-error`）
#   ⇒ 用**有界超时**解决静默，不用日志噪音解决。
# ---------------------------------------------------------------------------
fetch_commit_shallow() {
    local name="$1" url="$2" commit="$3" target="$4"
    local attempt=1

    git init -q "${target}"
    git -C "${target}" remote add origin "${url}"

    while [[ "${attempt}" -le "${GIT_FETCH_ATTEMPTS}" ]]; do
        log "  拉取 ${name} @ ${commit:0:12}（第 ${attempt}/${GIT_FETCH_ATTEMPTS} 次，单次上限 ${GIT_FETCH_TIMEOUT_S}s）"
        # GIT_TERMINAL_PROMPT=0：凭据缺失时立即失败，而不是等一个永远不会有人回答的输入
        if GIT_TERMINAL_PROMPT=0 timeout "${GIT_FETCH_TIMEOUT_S}" \
            git -C "${target}" \
                -c http.lowSpeedLimit=1000 \
                -c http.lowSpeedTime=30 \
                fetch --depth 1 --no-tags origin "${commit}"; then
            git -C "${target}" checkout -q FETCH_HEAD \
                || die "检出失败：${name} @ ${commit}"
            return 0
        fi
        log "  第 ${attempt} 次拉取失败（超时或被中断）"
        attempt=$((attempt + 1))
        if [[ "${attempt}" -le "${GIT_FETCH_ATTEMPTS}" ]]; then
            sleep "${GIT_FETCH_BACKOFF_S}"
        fi
    done

    die "拉取失败：${name} @ ${commit}（已重试 ${GIT_FETCH_ATTEMPTS} 次，单次上限 ${GIT_FETCH_TIMEOUT_S}s）"
}

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
        fetch_commit_shallow "${name}" "${url}" "${commit}" "${target}"
    done < "${REFERENCES_MANIFEST}"
}

# ---------------------------------------------------------------------------
# verify：只校验已有资产，不下载（构建自检与运行期复查共用）
# ---------------------------------------------------------------------------
verify_manifest() {
    local manifest="$1" dest_dir="$2" label="$3"
    require_file "${manifest}"

    local raw sha name url fmt size note path actual count=0
    while IFS= read -r raw || [[ -n "${raw}" ]]; do
        is_skippable "${raw}" && continue
        IFS='|' read -r sha name url fmt size note <<< "${raw}"
        path="${dest_dir}/${name}"
        [[ -f "${path}" ]] || die "${label}缺失：${path}"
        actual="$(sha256sum "${path}" | cut -d' ' -f1)"
        [[ "${actual}" == "${sha}" ]] || die "${label}校验失败：${path}"
        log "OK ${name}（$(du -h "${path}" | cut -f1)）"
        count=$((count + 1))
    done < "${manifest}"

    log "共校验 ${count} 个${label}，全部通过"
}

verify_benchmarks() {
    local dest_dir="${1:-/opt/benchmarks}"
    require_file "${BENCHMARKS_MANIFEST}"

    local raw kind sha name src config split size note path actual count=0
    while IFS= read -r raw || [[ -n "${raw}" ]]; do
        is_skippable "${raw}" && continue
        IFS='|' read -r kind sha name src config split size note <<< "${raw}"
        path="${dest_dir}/${name}"
        [[ -f "${path}" ]] || die "基准文件缺失：${path}"
        actual="$(sha256sum "${path}" | cut -d' ' -f1)"
        [[ "${actual}" == "${sha}" ]] || die "基准文件校验失败：${path}"
        log "OK ${name}（$(du -h "${path}" | cut -f1)）"
        count=$((count + 1))
    done < "${BENCHMARKS_MANIFEST}"

    log "共校验 ${count} 个基准文件，全部通过"
}

cmd_verify() {
    verify_manifest "${MODELS_MANIFEST}" "${1:-/opt/models}" "模型"
    verify_benchmarks "${2:-/opt/benchmarks}"
}

# ---------------------------------------------------------------------------
# plan：只解析清单并打印将获取什么，不下载、不克隆
#   用途：① 清单纯文本改动后可在**不重建镜像**的前提下确认解析正确；
#        ② 新增模型/参考资料时先看一眼总量是否可接受。
# ---------------------------------------------------------------------------
cmd_plan() {
    local raw sha name url quant size note rest
    local total=0

    log "模型（目标：/opt/models）"
    while IFS= read -r raw || [[ -n "${raw}" ]]; do
        is_skippable "${raw}" && continue
        IFS='|' read -r sha name url quant size note <<< "${raw}"
        log "  ${name}  [${quant}]  ${size}"
    done < "${MODELS_MANIFEST}"

    log "基准任务集（目标：/opt/benchmarks）"
    while IFS= read -r raw || [[ -n "${raw}" ]]; do
        is_skippable "${raw}" && continue
        IFS='|' read -r kind sha name src config split size note <<< "${raw}"
        log "  ${name}  [${kind}]  ${size}  ← ${src}"
    done < "${BENCHMARKS_MANIFEST}"

    log "参考资料（目标：/opt/references/harness）"
    while IFS= read -r raw || [[ -n "${raw}" ]]; do
        is_skippable "${raw}" && continue
        IFS='|' read -r name url commit license rest <<< "${raw}"
        log "  ${name}  @${commit:0:12}  [${license}]"
    done < "${REFERENCES_MANIFEST}"
}

main() {
    local cmd="${1:-}"
    shift || true
    case "${cmd}" in
        models)     cmd_models "$@" ;;
        benchmarks) cmd_benchmarks "$@" ;;
        references) cmd_references "$@" ;;
        verify)     cmd_verify "$@" ;;
        plan)       cmd_plan ;;
        *) die "用法：$0 {models|benchmarks|references|verify|plan} [目标目录]" ;;
    esac
}

main "$@"
