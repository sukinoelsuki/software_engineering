#!/usr/bin/env bash
# 里程碑发布编排：校验 → 编辑 → 同步锁文件 → 门禁 → 提交（失败即回滚）。
#
# 口径见 docs/engineering/git-workflow.md §5，决策见 docs/adr/0019-release-and-version-policy.md。
#
# ⚠️ 本脚本**不做远端写入**：不推送、不打标签。`main` 是受保护发布线
#    （远端授权 B 类），开 PR / 合并 / 打标签**均为所有者动作**。
#
# 用法：
#   make release VERSION=0.0.1          # 落定到里程碑 0.0.1
#   DRY_RUN=1 make release VERSION=0.0.1  # 只打印将要做的改动
set -euo pipefail

VERSION="${1:-}"
if [[ -z "${VERSION}" ]]; then
  echo ">> 用法：make release VERSION=x.y.z（例如 make release VERSION=0.0.1）" >&2
  exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

# 版本号的四处副本所在文件；回滚时按这份清单还原。
# uv.lock 的那一份由 `uv lock` 生成，因此也必须在回滚范围内。
TRACKED_PATHS=(
  pyproject.toml
  uv.lock
  CHANGELOG.md
  src/agent_sec_perf/__init__.py
)

branch="$(git rev-parse --abbrev-ref HEAD)"
if [[ "${branch}" != "develop" ]]; then
  echo ">> 拒绝：发布动作在 develop 上进行（当前为 ${branch}）。" >&2
  echo ">> 理由：main 是受保护发布线，写入需事先批准；发布先落在工作主干，再经 PR 合并。" >&2
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo ">> 拒绝：工作树不干净。请先提交或清理——否则无关改动会被卷进发布提交。" >&2
  git status --short >&2
  exit 1
fi

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  uv run python scripts/release_edit.py "${VERSION}" --dry-run
  echo ">> DRY_RUN=1：未写入任何文件。"
  exit 0
fi

echo ">> 1/4 编辑版本号（台账校验失败即中止，且不写任何文件）"
uv run python scripts/release_edit.py "${VERSION}"

echo ">> 2/4 同步 uv.lock（第 4 处副本）"
uv lock

echo ">> 3/4 跑门禁（红则回滚——绝不提交未经门禁的发布）"
if ! make check; then
  echo ">> 门禁未通过：已回滚到执行前状态，版本号未变。" >&2
  git checkout -- "${TRACKED_PATHS[@]}"
  exit 1
fi

echo ">> 4/4 本地提交（不含推送、不含打标签）"
if ! git commit -o -m "chore(release): ${VERSION}" -- "${TRACKED_PATHS[@]}"; then
  echo ">> 提交失败（可能是钩子拦下）。改动**保留**在工作树中，请人工处理后重试。" >&2
  echo ">> 未提交即未发布：没有任何内容被推出。" >&2
  exit 1
fi

cat <<EOF
>> 本地发布提交已完成。**后续均为所有者动作**（远端授权 B 类，代理不得代办）：
     1) 开 PR：develop → main（Merge commit，保留发布节点）
     2) CI 全绿后合并到 main
     3) 在 main 上打带注释标签：git tag -a v${VERSION} -m "Release v${VERSION}"
     4) 将 main 回合 develop：git switch develop && git merge --ff-only origin/main
EOF
