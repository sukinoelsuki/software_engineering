"""把版本号落定到里程碑——**纯编辑逻辑**（不做任何进程调用、不碰 git）。

为什么单独成文件
----------------

``scripts/release.sh`` 负责进程编排（分支检查 / ``uv lock`` / ``make check`` / 回滚 / 提交），
本文件只做**纯文本变换与校验**。这样切分的两个理由：

1. **纯函数才能被单测覆盖**：``tests/unit/test_release_policy.py`` 直接加载本模块并逐一
   验证"恰好替换预期次数"等不变式；
2. **不引入额外安全面**：脚本里一旦出现 ``subprocess``，就要为 ``S603`` 走安全豁免两步写
   并登记 ADR——而这件事本可以不必发生。

三条不变式（每条都有回归用例）
------------------------------

1. **目标版本必须来自台账**：出现在 ``pyproject.toml`` 的
   ``[[tool.lowspec.releases.milestones]]`` 里，且**严格大于**当前版本
   ⇒ "随手写一个版本号"这条路被关闭；
2. **每处替换必须恰好命中预期次数**：多一处、少一处都直接失败，**绝不"部分成功"**
   （版本号有 4 处副本，只改对一半是比不改更坏的状态——某处会长期说错话且不报错）；
3. **失败时不写任何文件**：先把全部新内容算好（:func:`build_plan`），再一起落盘；
   校验失败时磁盘保持原样。

完整口径见 ``docs/engineering/git-workflow.md`` §5，决策见
``docs/adr/0019-release-and-version-policy.md``。
"""

from __future__ import annotations

import argparse
import difflib
import pathlib
import re
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

#: 仓库根（本文件位于 ``<root>/scripts/``）。
REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
INIT_PATH = REPO_ROOT / "src" / "agent_sec_perf" / "__init__.py"
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"

#: 版本号落在哪些 TOML 段里（段名与 ``[section]`` 头部逐字对应）。
VERSION_SECTIONS: tuple[str, ...] = ("project", "tool.commitizen")

#: 版本号形状：SemVer 三段。Phase 0 的 `0.x` 约束由台账检查（``tests/unit``）承担。
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")

_SECTION_RE = re.compile(r"^\[+(?P<name>[^\[\]]+)\]+$")
_VERSION_LINE_RE = re.compile(r'^version = "[^"]*"$')
_INIT_VERSION_RE = re.compile(r'^__version__ = "[^"]*"$', re.MULTILINE)
_UNRELEASED_RE = re.compile(r"^## \[Unreleased\]$", re.MULTILINE)


class ReleaseEditError(Exception):
    """本次发布无法按规则落定（调用方**不得**回退为"先改了再说"）。"""


@dataclass(frozen=True)
class Milestone:
    """台账里的一个里程碑。"""

    version: str
    name: str
    criteria: str


@dataclass(frozen=True)
class ReleasePlan:
    """一次发布的完整计划：目标版本、里程碑、以及每个文件的新内容。"""

    version: str
    milestone: Milestone
    previous_version: str
    files: Mapping[pathlib.Path, str]


# ---------------------------------------------------------------------------
# 读取与校验
# ---------------------------------------------------------------------------


def _version_key(version: str) -> tuple[int, ...]:
    """把版本号拆成可比较的整数元组（三段数字，由 :data:`VERSION_RE` 保证形状）。"""
    return tuple(int(part) for part in version.split("."))


def read_milestones(pyproject_text: str) -> dict[str, Milestone]:
    """读出里程碑台账（``version -> Milestone``）。

    Raises:
        ReleaseEditError: 台账缺失、为空，或某项缺 ``version`` / ``name`` / ``criteria``。
    """
    data = tomllib.loads(pyproject_text)
    raw = data.get("tool", {}).get("lowspec", {}).get("releases", {}).get("milestones")
    if not raw:
        msg = "pyproject.toml 缺少 [[tool.lowspec.releases.milestones]] 台账（版本号无法落定）"
        raise ReleaseEditError(msg)

    milestones: dict[str, Milestone] = {}
    for item in raw:
        version = str(item.get("version", ""))
        name = str(item.get("name", "")).strip()
        criteria = str(item.get("criteria", "")).strip()
        if not version or not name or not criteria:
            msg = f"里程碑条目不完整（version/name/criteria 均必填）：{item!r}"
            raise ReleaseEditError(msg)
        milestones[version] = Milestone(version=version, name=name, criteria=criteria)
    return milestones


def read_current_version(pyproject_text: str) -> str:
    """读出 ``[project].version``。"""
    data = tomllib.loads(pyproject_text)
    current = str(data.get("project", {}).get("version", ""))
    if not current:
        msg = "pyproject.toml 的 [project].version 缺失"
        raise ReleaseEditError(msg)
    return current


def validate_target(pyproject_text: str, target: str) -> tuple[str, Milestone]:
    """校验目标版本合法：形状正确、在台账里、且**严格大于**当前版本。

    Returns:
        ``(当前版本, 目标里程碑)``。

    Raises:
        ReleaseEditError: 任一条件不满足。
    """
    current = read_current_version(pyproject_text)
    if not VERSION_RE.match(target):
        msg = f"目标版本 {target!r} 不是 `MAJOR.MINOR.PATCH` 形状"
        raise ReleaseEditError(msg)

    milestones = read_milestones(pyproject_text)
    if target not in milestones:
        declared = ", ".join(sorted(milestones, key=_version_key))
        msg = (
            f"目标版本 {target!r} 不在里程碑台账中。"
            f"允许的值：{declared}。"
            "—— 版本号不表达「改了多少提交」，只表达「到达了哪个里程碑」；"
            "新增里程碑必须先给出可核对的判据（见 git-workflow.md §5）"
        )
        raise ReleaseEditError(msg)

    if _version_key(target) <= _version_key(current):
        msg = f"目标版本 {target!r} 必须严格大于当前版本 {current!r}（版本号不可回退）"
        raise ReleaseEditError(msg)

    return current, milestones[target]


# ---------------------------------------------------------------------------
# 文本变换（每个函数都必须"恰好命中预期次数"）
# ---------------------------------------------------------------------------


def replace_project_version(pyproject_text: str, version: str) -> str:
    """把 :data:`VERSION_SECTIONS` 里每段的 ``version = "…"`` 全部替换为 ``version``。

    **逐行 + 按段名判定**，而不是全局正则：``version = "x"`` 在 ``[project]`` 与
    ``[tool.commitizen]`` 两处**逐字相同**，全局替换无法区分，也无法发现"某段漏了"。

    Raises:
        ReleaseEditError: 命中次数不等于段数（多一处或少一处都直接失败）。
    """
    lines = pyproject_text.splitlines(keepends=True)
    section = ""
    replaced = 0

    for index, line in enumerate(lines):
        stripped = line.rstrip("\n")
        header = _SECTION_RE.match(stripped)
        if header is not None:
            section = header.group("name")
            continue
        if section in VERSION_SECTIONS and _VERSION_LINE_RE.match(stripped):
            lines[index] = f'version = "{version}"\n'
            replaced += 1

    if replaced != len(VERSION_SECTIONS):
        msg = (
            f"pyproject.toml 的版本号应恰好替换 {len(VERSION_SECTIONS)} 处"
            f"（{', '.join(VERSION_SECTIONS)}），实际命中 {replaced} 处——"
            "不允许部分成功，请检查文件结构是否被改动"
        )
        raise ReleaseEditError(msg)

    return "".join(lines)


def replace_init_version(init_text: str, version: str) -> str:
    """替换包内 ``__version__``（恰好一处）。

    Raises:
        ReleaseEditError: 命中次数不等于 1。
    """
    matches = list(_INIT_VERSION_RE.finditer(init_text))
    if len(matches) != 1:
        msg = f"src/agent_sec_perf/__init__.py 的 `__version__` 应恰好出现 1 次，实际 {len(matches)} 次"
        raise ReleaseEditError(msg)
    return _INIT_VERSION_RE.sub(lambda _match: f'__version__ = "{version}"', init_text, count=1)


def add_changelog_release_section(changelog_text: str, version: str, release_date: str) -> str:
    """在 ``## [Unreleased]`` 之后插入 ``## [version] - date`` 段落。

    **保留** ``[Unreleased]``（供后续累积），新段落紧随其后——这是 Keep a Changelog 的常规形态：
    已发布的内容归新段落，``[Unreleased]`` 重新从空开始。

    Raises:
        ReleaseEditError: ``[Unreleased]`` 缺失或出现多次；该版本段落已存在（防重复发布）。
    """
    if f"## [{version}]" in changelog_text:
        msg = f"CHANGELOG.md 已存在 `## [{version}]` 段落（该版本已发布过？）"
        raise ReleaseEditError(msg)

    anchors = list(_UNRELEASED_RE.finditer(changelog_text))
    if len(anchors) != 1:
        msg = f"CHANGELOG.md 的 `## [Unreleased]` 应恰好出现 1 次，实际 {len(anchors)} 次"
        raise ReleaseEditError(msg)

    anchor = anchors[0]
    insertion = f"\n\n## [{version}] - {release_date}"
    return changelog_text[: anchor.end()] + insertion + changelog_text[anchor.end() :]


# ---------------------------------------------------------------------------
# 计划与落盘
# ---------------------------------------------------------------------------


def build_plan(*, version: str, release_date: str, root: pathlib.Path = REPO_ROOT) -> ReleasePlan:
    """算出本次发布的全部新内容——**不写任何文件**。

    Raises:
        ReleaseEditError: 校验失败或任一变换未恰好命中预期次数。
    """
    pyproject_path = root / "pyproject.toml"
    init_path = root / "src" / "agent_sec_perf" / "__init__.py"
    changelog_path = root / "CHANGELOG.md"

    pyproject_text = pyproject_path.read_text(encoding="utf-8")
    init_text = init_path.read_text(encoding="utf-8")
    changelog_text = changelog_path.read_text(encoding="utf-8")

    previous, milestone = validate_target(pyproject_text, version)

    files: dict[pathlib.Path, str] = {
        pyproject_path: replace_project_version(pyproject_text, version),
        init_path: replace_init_version(init_text, version),
        changelog_path: add_changelog_release_section(changelog_text, version, release_date),
    }
    return ReleasePlan(
        version=version,
        milestone=milestone,
        previous_version=previous,
        files=files,
    )


def write_plan(plan: ReleasePlan) -> None:
    """把计划落盘（调用方应在门禁失败时自行回滚，见 ``scripts/release.sh``）。"""
    for path, text in plan.files.items():
        path.write_text(text, encoding="utf-8")


def describe_changes(plan: ReleasePlan) -> str:
    """生成便于人工复核的统一 diff。"""
    chunks: list[str] = []
    for path, new_text in plan.files.items():
        old_text = path.read_text(encoding="utf-8")
        relative = path.relative_to(REPO_ROOT)
        diff = difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
        )
        chunks.append("".join(diff))
    return "".join(chunks)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """命令行入口：``release_edit.py <version> [--date YYYY-MM-DD] [--dry-run]``。"""
    parser = argparse.ArgumentParser(
        prog="release_edit.py",
        description="把版本号落定到里程碑台账中的目标版本（纯编辑，不做进程调用）。",
    )
    parser.add_argument("version", help="目标版本，必须出现在里程碑台账中")
    # 取**本地**日期（发布记录面向人），同时用 tz-aware 写法：
    # ruff 的 DTZ011 禁止 `date.today()`（未指定时区），故先取 UTC 再转本地时区。
    parser.add_argument(
        "--date",
        default=datetime.now(UTC).astimezone().date().isoformat(),
        help="发布日期（默认本地今天）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印将要做的改动，不写文件")
    args = parser.parse_args(argv)

    try:
        plan = build_plan(version=args.version, release_date=args.date)
    except ReleaseEditError as exc:
        print(f"[release] 拒绝：{exc}", file=sys.stderr)
        return 1

    print(f"[release] 目标里程碑：{plan.version}「{plan.milestone.name}」")
    print(f"[release] 当前版本：{plan.previous_version}")
    print(f"[release] 判据（**需人工逐条核对**）：{plan.milestone.criteria}")

    if args.dry_run:
        print("[release] --dry-run：以下改动不会写入")
        print(describe_changes(plan))
        return 0

    write_plan(plan)
    print("[release] 已写入：" + "、".join(str(p.relative_to(REPO_ROOT)) for p in plan.files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
