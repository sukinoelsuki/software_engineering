"""远端写入授权分级的「五处口径一致」机器检查。

背景（[ADR-0016] §5.2 / §5.8 / §8 待办，2026-09-18）：

远端写入不再"一刀切"，授权级别由**操作自身的可回退性**决定，分级集合为 **A~F**。
ADR-0016 §5.8 第 1 / 3 / 4 / 6 / 7 条要求把这套分级同时写进 **5 处载体**：

1. `CODEBUDDY.md` §2 —— 常驻结论
2. `AGENTS.md` §2 —— 常驻结论（与 `CODEBUDDY.md` 内容等价，项目既有惯例）
3. `.codebuddy/rules/git-workflow/RULE.mdc` —— 常驻摘要（每次会话自动加载）
4. `docs/engineering/git-workflow.md` §4 —— **权威定义**
5. `CONTRIBUTING.md` §8 —— 面向"受委托的代理"的表述

5 处**定位不同、不是互为副本**（摘要允许省略细则，见 `git-workflow.md` §2 分工表），
但**分级集合必须逐项一致**。ADR-0016 §11 只做过一次**手工** md5 自查；
手工自查与"写进文档"同族——都可能在后续编辑中悄悄失配，且**不会有人发现**。
本文件把它换成机器检查，消掉"同一事实两份真源"这一根因
（与 `tests/unit/test_commit_message_contract.py` 同一模式）。

检查口径（**与被检文件的措辞解耦**）：

不在测试里写死任何一份文件的原文，而是从每处**解析出**分级映射
`{类别: (范围, 规则)}`，再断言 5 份映射逐项相等。这样"改文档时顺手调整了措辞/排版"
不会造成假红——否则检查会退化成"改文档的人被迫改测试"，而不是"两份真源被钉在一起"。

覆盖两种既有写法（两处表格 + 三处条目列表），两条解析路径都必须命中：
前者见 `docs/engineering/git-workflow.md` §4 与 `.codebuddy/rules/git-workflow/RULE.mdc`，
后者见 `CODEBUDDY.md` / `AGENTS.md` / `CONTRIBUTING.md`。

非恒过性由**变异探针**证明（见文件末尾）：在 `/tmp` 的文件副本上改一处载体，
检查必须报红并指明"哪两处、哪一类、差在哪"。探针不触碰工作树（不污染他人产出域）。
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: 分级类别（ADR-0016 §5.1）
CLASSES = ("A", "B", "C", "D", "E", "F")

#: 承载 A~F 分级的 5 处载体，相对仓库根。**权威定义**是 `docs/engineering/git-workflow.md`，
#: 但比对是"两两相等"而非"以某一份为准"，因此这里只维护一份清单。
SITE_PATHS = (
    "CODEBUDDY.md",
    "AGENTS.md",
    ".codebuddy/rules/git-workflow/RULE.mdc",
    "docs/engineering/git-workflow.md",
    "CONTRIBUTING.md",
)

#: 表格写法：``| **A** | 范围 | 规则 |``
_TABLE_ROW = re.compile(r"^\|\s*\*\*(?P<cls>[A-F])\*\*\s*\|(?P<rest>.+)\|\s*$")

#: 条目列表写法：``- **A 类**：范围 ⇒ 规则``（允许缩进）
_BULLET_ITEM = re.compile(r"^\s*-\s*\*\*(?P<cls>[A-F])\s*类\*\*[：:](?P<rest>.+)$")


# ---------------------------------------------------------------------------
# 解析层：从一处载体抽出 `{类别: (范围, 规则)}`
# ---------------------------------------------------------------------------


def _normalize(cell: str) -> str:
    """把 markdown 片段归一化成可比较的纯文本（去掉加粗、反引号、链接与多余空白）。

    归一化的目的正是"只比语义、不比排版"：载体之间允许写法不同
    （表格 vs 列表、加粗 vs 反引号、空格多少），只要文字内核相同即视为一致。
    """
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cell)
    text = text.replace("`", "").replace("**", "")
    return " ".join(text.split())


def _split_table_cells(rest: str, line: str, label: str) -> tuple[str, str]:
    cells = [cell.strip() for cell in rest.split("|")]
    if len(cells) != 2:
        msg = f"{label}: 分级表行不是「类 | 范围 | 规则」三列，无法解析：{line!r}"
        raise AssertionError(msg)
    return cells[0], cells[1]


def _split_bullet(rest: str, line: str, label: str) -> tuple[str, str]:
    scope, arrow, rule = rest.partition("⇒")
    if not arrow:
        msg = f"{label}: 分级条目缺少「⇒ 规则」分隔符，无法解析：{line!r}"
        raise AssertionError(msg)
    return scope, rule


def _parse_grading(label: str, path: pathlib.Path) -> dict[str, tuple[str, str]]:
    """解析一处载体的分级映射；同一类别重复声明即报错（无法判定以哪一处为准）。"""
    grading: dict[str, tuple[str, str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        table = _TABLE_ROW.match(line)
        bullet = _BULLET_ITEM.match(line)
        if table is not None:
            cls = table.group("cls")
            scope, rule = _split_table_cells(table.group("rest"), line, label)
        elif bullet is not None:
            cls = bullet.group("cls")
            scope, rule = _split_bullet(bullet.group("rest"), line, label)
        else:
            continue
        if cls in grading:
            msg = f"{label}: 类别 {cls} 被声明了两次，无法判定以哪一处为准：{line!r}"
            raise AssertionError(msg)
        grading[cls] = (_normalize(scope), _normalize(rule))
    return grading


def _collect(repo_root: pathlib.Path) -> dict[str, dict[str, tuple[str, str]]]:
    """按 `SITE_PATHS` 解析全部载体；`repo_root` 可指定，供变异探针在副本上运行。"""
    return {rel: _parse_grading(rel, repo_root / rel) for rel in SITE_PATHS}


# ---------------------------------------------------------------------------
# 断言层：口径一致性（失败信息必须能直接看出"哪两处、差在哪"）
# ---------------------------------------------------------------------------


def _assert_consistent(repo_root: pathlib.Path) -> None:
    sites = _collect(repo_root)

    # 第一步：每处都必须列全 A~F。
    # 缺项本身就是**口径缺陷**，按 ADR-0016 §5.8 必须补齐，**不得**为此放宽断言
    # （"某处只说了一部分"正是本检查要暴露的状态）。
    for label, grading in sites.items():
        missing = [cls for cls in CLASSES if cls not in grading]
        extra = sorted(cls for cls in grading if cls not in CLASSES)
        assert not missing and not extra, (
            f"{label} 未完整声明 A~F 分级集合：缺 {missing}、多 {extra}。\n"
            "分级集合必须在 5 处逐项一致（ADR-0016 §5.8 第 1/3/4/6/7 条）；"
            "请补齐该文件的 A~F 分级，不要把断言放宽成「缺了也算一致」。"
        )

    # 第二步：两两相等（以清单中第一处为基准，逐项比对并打印差异）。
    reference_label, reference = next(iter(sites.items()))
    for label, grading in sites.items():
        if grading == reference:
            continue
        raise AssertionError(_diff_message(reference_label, reference, label, grading))


def _diff_message(
    reference_label: str,
    reference: dict[str, tuple[str, str]],
    label: str,
    grading: dict[str, tuple[str, str]],
) -> str:
    """构造可读的失败信息：基准文件、分歧文件、逐类差异（范围 / 规则两栏分别给出）。"""
    lines = [
        "A~F 远端写入授权分级在 5 处载体之间不一致（同一事实不得两份真源）：",
        f"  基准：{reference_label}",
        f"  分歧：{label}",
    ]
    for cls in CLASSES:
        if grading[cls] != reference[cls]:
            ref_scope, ref_rule = reference[cls]
            got_scope, got_rule = grading[cls]
            lines.append(
                f"  {cls} 类：\n"
                f"    {reference_label}：范围={ref_scope!r}，规则={ref_rule!r}\n"
                f"    {label}：范围={got_scope!r}，规则={got_rule!r}"
            )
    lines.append(
        "措辞/排版可以不同，但「范围」与「规则」两栏的语义必须逐项相同。"
        "若确为有意的口径调整，请在同一次改动里同步全部 5 处"
        "（并相应更新 ADR-0016 §5.8 的联动清单）。"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 1. 事实：5 处口径当前一致（这是本任务要钉住的对象）
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_remote_write_grading_is_identical_across_all_five_sites() -> None:
    """5 处载体解析出的 A~F 分级映射必须逐项相等。

    替掉 ADR-0016 §11 的**手工** md5 自查：手工自查不会在"下一次编辑漏改一处"时报错。
    """
    _assert_consistent(REPO_ROOT)


@pytest.mark.unit
def test_every_site_yields_a_full_and_non_empty_grading() -> None:
    """解析器本身不得恒过：每处都要解析出 A~F 六项，且「范围」「规则」非空。

    若解析规则与载体的排版漂移（例如把表格换成另一种语法），本用例会以"解析出的集合
    不完整 / 内容为空"报红——而不是让上面的比对在**空集合上悄悄通过**。
    """
    sites = _collect(REPO_ROOT)

    assert len(sites) == len(SITE_PATHS), "有载体未能读到（清单与文件不匹配）"
    for label, grading in sites.items():
        assert set(grading) == set(CLASSES), (
            f"{label} 解析出的分级集合不完整：{sorted(grading)}；预期 {sorted(CLASSES)}"
        )
        for cls, (scope, rule) in grading.items():
            assert scope, f"{label} 的 {cls} 类没有解析出「范围」"
            assert rule, f"{label} 的 {cls} 类没有解析出「规则」"


# ---------------------------------------------------------------------------
# 2. 变异探针：证明上面的检查不是恒过（在 /tmp 副本上做，不触碰工作树）
# ---------------------------------------------------------------------------


def _copy_sites_to(root: pathlib.Path) -> None:
    """把 5 份载体按相对路径复制到 `root` 下（副本目录由 pytest 的 tmp_path 管理）。"""
    for rel in SITE_PATHS:
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((REPO_ROOT / rel).read_text(encoding="utf-8"), encoding="utf-8")


def _mutate(victim: pathlib.Path, old: str, new: str) -> None:
    """对副本做一次替换，并**断言替换真的命中**（探针自己失效时必须报错，而不是静默）。"""
    original = victim.read_text(encoding="utf-8")
    mutated = original.replace(old, new)
    assert mutated != original, f"变异探针失效：替换目标未命中 {victim.name}：{old!r}"
    victim.write_text(mutated, encoding="utf-8")


@pytest.mark.unit
def test_probe_untouched_copy_passes(tmp_path: pathlib.Path) -> None:
    """探针的对照组：未变异的副本必须通过，否则"变异后报红"不构成非恒过的证据。"""
    root = tmp_path / "repo"
    _copy_sites_to(root)

    _assert_consistent(root)


@pytest.mark.unit
def test_probe_mutating_one_sites_scope_is_detected(tmp_path: pathlib.Path) -> None:
    """变异探针 ①：某处改「范围」⇒ 报红，且指出是哪两处、哪一类、差了哪一栏。"""
    root = tmp_path / "repo"
    _copy_sites_to(root)
    _mutate(
        root / "CONTRIBUTING.md",
        "- **E 类**：`bench/data` ⇒",
        "- **E 类**：`bench/data-branch` ⇒",
    )

    with pytest.raises(AssertionError) as excinfo:
        _assert_consistent(root)

    message = str(excinfo.value)
    assert "CONTRIBUTING.md" in message, f"失败信息未指出分歧文件：\n{message}"
    assert "CODEBUDDY.md" in message, f"失败信息未指出基准文件：\n{message}"
    assert "E 类" in message, f"失败信息未指出分歧的类别：\n{message}"
    assert "bench/data-branch" in message, f"失败信息未给出实际抽到的内容：\n{message}"


@pytest.mark.unit
def test_probe_mutating_one_sites_rule_is_detected(tmp_path: pathlib.Path) -> None:
    """变异探针 ②：某处改「规则」（表格写法）⇒ 报红。

    与探针 ① 互补：① 走条目列表、② 走表格，"两条解析路径都不是装饰"。
    """
    root = tmp_path / "repo"
    _copy_sites_to(root)
    _mutate(
        root / ".codebuddy/rules/git-workflow/RULE.mdc",
        "| **B** | `main` | **事先批准** |",
        "| **B** | `main` | **推送后报告** |",
    )

    with pytest.raises(AssertionError) as excinfo:
        _assert_consistent(root)

    message = str(excinfo.value)
    assert "RULE.mdc" in message, f"失败信息未指出分歧文件：\n{message}"
    assert "B 类" in message, f"失败信息未指出分歧的类别：\n{message}"
    assert "推送后报告" in message, f"失败信息未给出实际抽到的规则：\n{message}"


@pytest.mark.unit
def test_probe_removing_a_class_is_detected(tmp_path: pathlib.Path) -> None:
    """变异探针 ③：某处少列一类 ⇒ 报红（而不是退化成"缺项也算一致"）。"""
    root = tmp_path / "repo"
    _copy_sites_to(root)
    victim = root / "AGENTS.md"
    original = victim.read_text(encoding="utf-8")
    mutated = "\n".join(
        line for line in original.splitlines() if not line.strip().startswith("- **E 类**")
    )
    assert mutated != original, "变异探针失效：AGENTS.md 的 E 类条目未被移除"
    victim.write_text(mutated + "\n", encoding="utf-8")

    with pytest.raises(AssertionError) as excinfo:
        _assert_consistent(root)

    message = str(excinfo.value)
    assert "AGENTS.md" in message, f"失败信息未指出缺项的文件：\n{message}"
    assert "'E'" in message, f"失败信息未指出缺失的类别：\n{message}"
