"""版本发布口径的机器检查（决策见 `ADR-0019`，流程见 `docs/engineering/git-workflow.md` §5）。

**为什么需要这个文件**：版本号在本项目里是**多副本**的（`pyproject.toml` 两处 + 包内
`__version__` + `uv.lock`），而"忘同步一处"从来不会报错——它只会让某个载体长期说错话。
一致性核查报告 `A-17` 就是同一形状：文档写 `0.x`、配置却会把版本推到 `1.0.0`，两边都不报错。
这里把"必须相等"与"只能取里程碑值"变成**可执行断言**，使版本号不再能悄悄漂移。

断言分五组：

1. **副本一致**：四处副本必须逐一相等；
2. **取值合法**：当前版本必须是台账里声明的里程碑之一；
3. **台账良构**：`0.x.y`、严格递增、判据非空（Phase 0 内不接受 `1.x`）；
4. **自动推导越不过 `0.x`**：`major_version_zero` 安全网在场；
5. **CHANGELOG 段落合法**：只允许已声明的里程碑版本段落，且 `[Unreleased]` 在最前。

另有对 `scripts/release_edit.py` 的**不变式回归**：每处替换必须"恰好命中预期次数"，
少一处多一处都必须失败——版本号"只改对一半"比不改更坏。
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import sys
import tomllib
from collections.abc import Iterable
from typing import Any

import pytest

from agent_sec_perf import __version__

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
LOCK_PATH = REPO_ROOT / "uv.lock"
CHANGELOG_PATH = REPO_ROOT / "CHANGELOG.md"
INIT_PATH = REPO_ROOT / "src" / "agent_sec_perf" / "__init__.py"
RELEASE_EDIT_PATH = REPO_ROOT / "scripts" / "release_edit.py"

#: Phase 0 口径：里程碑一律 `0.x.y`。越过 `0.x` 必须先改这条检查（= 一次显式决策）。
MILESTONE_VERSION_RE = re.compile(r"^0\.\d+\.\d+$")

#: CHANGELOG 的版本段落（`## [1.2.3] - date` 之类）。
_CHANGELOG_SECTION_RE = re.compile(r"^## \[(\d+\.\d+\.\d+)\]", re.MULTILINE)


def _load_toml(path: pathlib.Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _pyproject() -> dict[str, Any]:
    return _load_toml(PYPROJECT_PATH)


def _milestones() -> list[dict[str, Any]]:
    releases = _pyproject()["tool"]["lowspec"]["releases"]
    return list(releases["milestones"])


def _declared_versions() -> set[str]:
    return {str(item["version"]) for item in _milestones()}


def _version_key(version: str) -> tuple[int, ...]:
    """把 `x.y.z` 拆成可比较的整数元组（用于"严格递增"与"取下一个里程碑"）。"""
    return tuple(int(part) for part in version.split("."))


def _next_target(current: str, declared: Iterable[str]) -> str | None:
    """台账里**高于** ``current`` 的最小里程碑；不存在时返回 ``None``。

    **为什么单独抽成函数**："当前即台账最高一级"是一个**合法状态**（台账只到 `0.1.0` 时
    就该能发布 `0.1.0`），而原先内联的 ``min(...)`` 会把这一合法状态表现为
    ``ValueError: min() iterable argument is empty``——2026-09-20 的
    `make release VERSION=0.1.0` 正是被它挡下并回滚。把两个分支显式化，
    才能对"有更高一级"与"没有更高一级"**各自**写断言，而不是只覆盖其中一条路。

    参数标成 :class:`~collections.abc.Iterable` 而非 ``Sequence``：调用点传的是
    :func:`_declared_versions` 返回的 ``set``（`set` 不是 `Sequence`）。

    Returns:
        下一个里程碑版本号；``current`` 已是最高一级时返回 ``None``
        ——**不抛异常、也不返回** ``current``（那会构成"目标不大于当前版本"的非法目标）。
    """
    higher = [version for version in declared if _version_key(version) > _version_key(current)]
    if not higher:
        return None
    return min(higher, key=_version_key)


def _locked_version() -> str:
    """读出 `uv.lock` 里本项目（可编辑安装）的版本号。"""
    project_name = str(_pyproject()["project"]["name"])
    for package in _load_toml(LOCK_PATH)["package"]:
        if package["name"] == project_name:
            return str(package["version"])
    msg = f"uv.lock 中没有 `{project_name}` 的可编辑安装条目"
    raise AssertionError(msg)


def _load_release_editor() -> Any:
    """加载 `scripts/release_edit.py`（纯编辑模块）；返回模块对象，故标注为 `Any`。

    路径由本文件位置推导、固定指向仓库内文件，不涉及外部输入——
    这正是「禁止动态导入不可信来源」允许的情形。

    注意必须先登记到 ``sys.modules`` 再 ``exec_module``：被加载模块里有 ``dataclass``，
    而 dataclasses 会回头按 ``cls.__module__`` 查 ``sys.modules`` 来解析注解；
    不登记会得到一条与本题无关的 ``AttributeError: 'NoneType' object has no attribute '__dict__'``。
    """
    spec = importlib.util.spec_from_file_location("release_edit", RELEASE_EDIT_PATH)
    assert spec is not None, f"无法为 {RELEASE_EDIT_PATH} 构造模块规格"
    assert spec.loader is not None, f"{RELEASE_EDIT_PATH} 没有可用的模块加载器"
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# 1~2. 副本一致 / 取值合法
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_version_copies_are_identical() -> None:
    """四处版本号副本必须逐一相等：改了一处忘了其余，是这一族缺陷的共同形状。"""
    pyproject = _pyproject()
    copies = {
        "pyproject.toml:[project].version": str(pyproject["project"]["version"]),
        "pyproject.toml:[tool.commitizen].version": str(pyproject["tool"]["commitizen"]["version"]),
        "src/agent_sec_perf/__init__.py:__version__": __version__,
        "uv.lock:<本项目条目>.version": _locked_version(),
    }
    assert len(set(copies.values())) == 1, f"版本号副本不一致：{copies}"


@pytest.mark.unit
def test_current_version_is_a_declared_milestone() -> None:
    """当前版本**必须**是台账里声明过的里程碑——不允许"随手写一个版本号"。"""
    current = str(_pyproject()["project"]["version"])
    declared = _declared_versions()
    assert current in declared, f"当前版本 {current!r} 不在里程碑台账中：{sorted(declared)}"


# ---------------------------------------------------------------------------
# 3. 台账良构
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_milestone_ladder_is_well_formed_and_stays_within_phase_zero() -> None:
    """台账必须良构：`0.x.y`、严格递增、判据非空。

    **Phase 0 内不接受 `1.x` 里程碑**——进入 `1.0.0` 意味着"首个稳定版"，
    那必须先改这条检查（改检查 = 一次显式决策，而不是顺手加一行台账）。
    """
    milestones = _milestones()
    assert milestones, "里程碑台账为空"

    for item in milestones:
        version = str(item["version"])
        assert MILESTONE_VERSION_RE.match(version), (
            f"里程碑 {version!r} 不符合 Phase 0 的 `0.x.y` 口径"
        )
        assert str(item.get("name", "")).strip(), f"里程碑 {version!r} 缺 name"
        assert str(item.get("criteria", "")).strip(), (
            f"里程碑 {version!r} 缺 criteria —— 判据必须可核对，否则版本号又变回主观判断"
        )

    versions = [str(item["version"]) for item in milestones]
    assert len(set(versions)) == len(versions), f"里程碑版本重复：{versions}"

    def _key(text: str) -> tuple[int, ...]:
        return tuple(int(part) for part in text.split("."))

    assert versions == sorted(versions, key=_key), (
        f"里程碑必须严格递增（台账自上而下由旧到新）：{versions}"
    )


# ---------------------------------------------------------------------------
# 4. 自动推导越不过 0.x
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_commitizen_cannot_jump_to_major_one() -> None:
    """安全网在场：`major_version_zero = true`。

    缺省 `False` 时，`feat!:` / `BREAKING CHANGE` 会把 `0.x` 直接推到 `1.0.0`
    （commitizen `defaults.py` 的 `BUMP_MAP` ⇒ `MAJOR`），而本项目 Phase 0 内不得出现 `1.0.0`。
    即使有人误跑 `make bump`，这条配置也保证越不过 `0.x`。
    """
    commitizen = _pyproject()["tool"]["commitizen"]
    assert commitizen.get("major_version_zero") is True, (
        "`major_version_zero` 必须为 true：缺省 False 会让首个破坏性提交把版本推到 1.0.0"
        "（一致性报告 A-17）"
    )


@pytest.mark.unit
def test_version_files_cover_the_package_dunder_version() -> None:
    """`version_files` 必须覆盖包内 `__version__`：否则任何一次自动同步都会在 `src/` 留下旧值。"""
    version_files = [str(item) for item in _pyproject()["tool"]["commitizen"]["version_files"]]
    assert any("agent_sec_perf/__init__.py" in item for item in version_files), (
        f"`version_files` 未覆盖包内 `__version__`：{version_files}"
    )


# ---------------------------------------------------------------------------
# 5. CHANGELOG 段落合法
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_changelog_sections_only_use_declared_milestones() -> None:
    """CHANGELOG 的版本段落只能来自台账，且 `[Unreleased]` 必须位于最前。

    这条拦的是另一条路径：自动推导生成一个没进台账的版本段落，长期留在仓库里
    与本项目的发布口径脱节。
    """
    text = CHANGELOG_PATH.read_text(encoding="utf-8")

    unreleased = text.find("## [Unreleased]")
    assert unreleased != -1, "CHANGELOG 缺少 `## [Unreleased]` 段落"

    published = [match.group(1) for match in _CHANGELOG_SECTION_RE.finditer(text)]
    assert len(published) == len(set(published)), f"CHANGELOG 有重复版本段落：{published}"

    unknown = sorted(set(published) - _declared_versions())
    assert not unknown, f"CHANGELOG 含未登记的版本段落：{unknown}"

    first_published = min(
        (match.start() for match in _CHANGELOG_SECTION_RE.finditer(text)),
        default=None,
    )
    if first_published is not None:
        assert unreleased < first_published, "`[Unreleased]` 必须位于已发布段落之前"


# ---------------------------------------------------------------------------
# 回归：`scripts/release_edit.py` 的不变式（"恰好命中预期次数"）
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_release_editor_replaces_exactly_the_version_lines_in_both_sections() -> None:
    """两处 `version = "…"` 必须都被替换，且**只**替换这两处。"""
    editor = _load_release_editor()
    updated = editor.replace_project_version(PYPROJECT_PATH.read_text(encoding="utf-8"), "0.9.9")

    data = tomllib.loads(updated)
    assert data["project"]["version"] == "0.9.9"
    assert data["tool"]["commitizen"]["version"] == "0.9.9"
    # 台账不能被顺手改掉：它是版本号的来源，不是版本的副本。
    assert data["tool"]["lowspec"]["releases"]["milestones"][0]["version"] == "0.0.0"
    assert updated.count('version = "0.9.9"') == 2


@pytest.mark.unit
def test_release_editor_refuses_partial_pyproject_replacement() -> None:
    """少一个 `[tool.commitizen]` 段时必须**失败**，而不是"改一半"。

    这是本模块最要紧的一条：版本号只改对一半，比完全没改更坏——
    某个载体开始说错话，而且没有任何地方会报错。
    """
    editor = _load_release_editor()
    crippled = '[project]\nname = "x"\nversion = "0.0.0"\n'
    with pytest.raises(editor.ReleaseEditError, match="恰好替换"):
        editor.replace_project_version(crippled, "0.9.9")


@pytest.mark.unit
def test_release_editor_refuses_init_version_when_not_exactly_once() -> None:
    """包内 `__version__` 不是恰好一处时必须失败。"""
    editor = _load_release_editor()
    with pytest.raises(editor.ReleaseEditError, match="恰好出现 1 次"):
        editor.replace_init_version('__version__ = "0.0.0"\n__version__ = "0.0.0"\n', "0.9.9")


@pytest.mark.unit
def test_release_editor_inserts_changelog_section_after_unreleased() -> None:
    """新段落插在 `[Unreleased]` 之后，且 `[Unreleased]` 本身保留（供后续累积）。"""
    editor = _load_release_editor()
    text = "## [Unreleased]\n\n### Added\n\n- a\n"
    updated = editor.add_changelog_release_section(text, "0.0.1", "2026-09-19")

    assert updated.startswith("## [Unreleased]\n\n## [0.0.1] - 2026-09-19\n\n### Added")
    assert updated.count("## [Unreleased]") == 1


@pytest.mark.unit
def test_release_editor_refuses_already_released_version() -> None:
    """同一版本不得发布两次（防重复追加段落）。"""
    editor = _load_release_editor()
    text = "## [Unreleased]\n\n## [0.0.1] - 2026-09-19\n\n### Added\n"
    with pytest.raises(editor.ReleaseEditError, match="已存在"):
        editor.add_changelog_release_section(text, "0.0.1", "2026-09-20")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("target", "reason"),
    [
        ("0.5.0", "不在里程碑台账中"),
        ("0.0.0", "必须严格大于当前版本"),
        ("1.0.0", "不在里程碑台账中"),
        ("0.0.1.0", "不是 `MAJOR.MINOR.PATCH` 形状"),
    ],
)
def test_release_editor_refuses_invalid_targets(target: str, reason: str) -> None:
    """目标版本的四类非法输入都必须被拒绝（含"越到 1.0.0"）。"""
    editor = _load_release_editor()
    with pytest.raises(editor.ReleaseEditError, match=reason):
        editor.validate_target(PYPROJECT_PATH.read_text(encoding="utf-8"), target)


@pytest.mark.unit
def test_next_target_picks_the_lowest_higher_milestone() -> None:
    """分支①：存在更高一级 ⇒ 返回其中**最小**者（不是最大、不是任意一个）。"""
    declared = ["0.0.0", "0.0.1", "0.1.0", "0.2.0"]
    assert _next_target("0.0.1", declared) == "0.1.0"
    # 乱序输入也必须取最小者：台账顺序不参与判定，大小才是判据。
    assert _next_target("0.0.1", ["0.2.0", "0.1.0"]) == "0.1.0"


@pytest.mark.unit
def test_next_target_returns_none_when_current_is_the_highest_milestone() -> None:
    """分支②：当前即最高一级 ⇒ 返回 `None`，**不抛异常、不返回当前版本**。

    这条正是 2026-09-20 的漏检点：`min()` 对空序列抛 `ValueError`，
    使"落到最高一级"这一**合法状态**被判为失败（`make release VERSION=0.1.0` 因此回滚）。
    """
    declared = ["0.0.0", "0.0.1", "0.1.0"]
    assert _next_target("0.1.0", declared) is None


@pytest.mark.unit
def test_release_plan_is_consistent_and_does_not_touch_the_ladder() -> None:
    """对真实仓库算一次计划：三份文件都被改写，且改后的版本号仍自洽。

    目标版本**从台账动态取**（当前版本之上的最小者），**不写死**：写死会让这条用例
    随版本推进自行失效——2026-09-19 实测过一次：首个 `make release VERSION=0.0.1`
    正是被写死 `0.0.1` 的本用例挡下（编辑完成后门禁红，脚本按设计**回滚**、版本号未变）。
    那次失败同时证明了两件事：**门禁真的拦得住**、**回滚真的生效**。

    2026-09-20 追加记录：`make release VERSION=0.1.0` 被**另一条**路径挡下并回滚——
    本用例当时直接对"高于当前版本"的集合取 ``min``，而 `release_edit.py` 已把
    `[project].version` 改写成 `0.1.0`（= 台账**最高一级**）⇒ 该集合为空 ⇒
    ``ValueError: min() iterable argument is empty``。

    **本条口径（不得再改回去）**：**"落到最高一级"是合法状态**——台账只列到 `0.1.0` 时，
    发布 `0.1.0` 完全正当，本用例**不得因此变红**。故目标改由 :func:`_next_target` 取，
    返回 `None` 即"当前已是最新里程碑"，此时 **`pytest.skip`**（而不是失败），
    也**不得**用"假造一个目标"或"删掉断言"的方式让它变绿。

    注意 skip 的**范围只有下半段**：上面"当前版本必须在台账里"这条不变式在任何版本状态下
    都先被断言——不存在"当前版本不在台账里"还能混过去的状态。
    """
    editor = _load_release_editor()
    current = str(_pyproject()["project"]["version"])
    declared = _declared_versions()

    # 先断言一条**任何**版本状态下都成立的真实不变式（不是放宽，是新增的检查）：
    # 当前版本不在台账里时，"取下一级"这件事本身就没有定义，必须在这里就失败。
    assert current in declared, f"当前版本 {current!r} 不在里程碑台账中：{sorted(declared)}"

    target = _next_target(current, declared)
    if target is None:
        pytest.skip(
            "台账里没有高于当前版本的里程碑作目标：当前即最高一级，"
            "这是合法状态，本用例无可检验的目标"
        )

    plan = editor.build_plan(version=target, release_date="2026-09-19")

    assert plan.previous_version == current
    assert set(plan.files) == {PYPROJECT_PATH, INIT_PATH, CHANGELOG_PATH}, (
        "计划应恰好涉及三份文件（uv.lock 由 `uv lock` 另行同步，不由本模块改写）"
    )

    updated_pyproject = tomllib.loads(plan.files[PYPROJECT_PATH])
    assert updated_pyproject["project"]["version"] == target
    assert updated_pyproject["tool"]["commitizen"]["version"] == target
    assert updated_pyproject["tool"]["lowspec"]["releases"]["milestones"] == _milestones()

    init_text = plan.files[INIT_PATH]
    assert f'__version__ = "{target}"' in init_text
    assert f"## [{target}] - 2026-09-19" in plan.files[CHANGELOG_PATH]
