"""提交信息的「文档 ↔ 门禁」一致性：把两份真源钉在一起。

背景（一致性报告 **A-16**，2026-09-18 实测）：
文档（`CONTRIBUTING.md` / `CODEBUDDY.md` / `AGENTS.md` /
`docs/engineering/git-workflow.md` / `.codebuddy/rules/git-workflow/RULE.mdc`）把 `security`
列为合法 `type`，而门禁（`.pre-commit-config.yaml` → `cz check`）**拒绝**它——
`cz_conventional_commits` 的类型集**硬编码**在插件源码里
（`commitizen/cz/conventional_commits/conventional_commits.py` 的 `schema_pattern()`），
既不包含 `security`，也**无法通过 `pyproject.toml` 配置扩展**。
⇒ 按文档写 `security(ci): …` 会被门禁挡下（实测 `exit code 14`）。

本文件的作用就是**消掉"两份真源"这个根因**（不只是改一次文案）：
文档列出的每个 type 都必须被门禁**实测接受**，`security` 必须被**实测拒绝**，
且门禁接受的完整集合被钉住（上游若新增/删除类型会报红，需人复核）。

**为什么用子进程实测、而不是读插件内部**：断言的对象是"**门禁**接受什么"，
不是"某个类的常量是什么"。实测走的是与提交时**同一条路径**（`cz check`）。
已知代价：每个类型一次子进程调用（用 `pytest.mark.parametrize` 交给 xdist 并行）。
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: 列出提交 type 的 5 处文档（A-16 的全部副本）
DOC_SITES = (
    REPO_ROOT / "CONTRIBUTING.md",
    REPO_ROOT / "CODEBUDDY.md",
    REPO_ROOT / "AGENTS.md",
    REPO_ROOT / "docs" / "engineering" / "git-workflow.md",
    REPO_ROOT / ".codebuddy" / "rules" / "git-workflow" / "RULE.mdc",
)

#: 门禁接受、但**有意不写进文档**的类型（commitizen 的内置成员）：
#: 项目不使用它们（`bump` 由 `cz bump` 生成、`style` 从未使用），但门禁不会拒绝，
#: 因此如实登记，避免"文档类型集 == 门禁类型集"这一更强断言在这里假红。
GATE_ONLY_TYPES = ("bump", "style")

#: 每个文档站点里定位 type 列表的那一行
_SITE_LINE_PATTERNS = (
    re.compile(r"^-\s*\*\*type\*\*"),  # CONTRIBUTING.md
    re.compile(r"^-?\s*`type` ∈"),  # CODEBUDDY.md / AGENTS.md（无前缀）/ RULE.mdc（列表项）
    re.compile(r"^\|\s*`type`\s*\|"),  # git-workflow.md 的表格行
)


def _documented_types(path: pathlib.Path) -> set[str]:
    """从一处文档里取出它列出的 type 集合。

    兼容三种写法（各行只取"类型列表所在的那一行"，避免把后续说明文字里的
    `security` / `fix(security)` 之类的反引号内容误当类型）：

    1. ``- **type**：`feat` `fix` …``（逐个反引号包裹）
    2. ``` `type` ∈ `feat fix …` ```（一个反引号包裹、空格分隔）
    3. ``| `type` | `feat` `fix` … |``（表格行）
    """
    for line in path.read_text(encoding="utf-8").splitlines():
        if not any(pattern.match(line) for pattern in _SITE_LINE_PATTERNS):
            continue
        # 截断到首个全角括号：其后是说明文字，不属于类型列表
        head = re.split(r"[（(]", line, maxsplit=1)[0]
        tokens: set[str] = set()
        for span in re.findall(r"`([^`]*)`", head):
            for token in span.split():
                if re.fullmatch(r"[a-z]+", token) and token != "type":
                    tokens.add(token)
        return tokens
    msg = f"{path} 中未找到 type 列表所在的行（本测试的口径必须与该行格式一致）"
    raise AssertionError(msg)


def _check(message: str) -> subprocess.CompletedProcess[str]:
    """用与提交时同一条路径校验一条提交信息（`cz check`）。"""
    return subprocess.run(
        [sys.executable, "-m", "commitizen", "check", "-m", message],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _sample_message(commit_type: str) -> str:
    return f"{commit_type}: 契约用例的占位提交信息"


# ---------------------------------------------------------------------------
# 1. 文档侧：5 处副本必须一致（防"改了一处漏了四处"）
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_documented_type_lists_are_identical_across_all_sites() -> None:
    """5 处文档列出的 type 集合必须完全相同。"""
    per_site = {path: _documented_types(path) for path in DOC_SITES}
    reference_site, reference = next(iter(per_site.items()))

    for path, types in per_site.items():
        assert types == reference, (
            f"{path.relative_to(REPO_ROOT)} 与 {reference_site.relative_to(REPO_ROOT)} 的 type 集不一致："
            f"仅前者有 {sorted(types - reference)}；仅后者有 {sorted(reference - types)}"
        )


@pytest.mark.unit
def test_security_is_not_documented_as_a_type() -> None:
    """`security` 不得再出现在任何一份文档的 type 列表里（A-16 的回归守卫）。

    它曾被写进 5 处文档，而门禁从不接受它——"按文档做就会被拦"。
    安全类改动的正确写法是 `fix(security): …` / `feat(security): …`（scope 承载语义）。
    """
    for path in DOC_SITES:
        assert "security" not in _documented_types(path), (
            f"{path.relative_to(REPO_ROOT)} 又把 `security` 列为 type 了；"
            "门禁不接受它，且无法通过配置扩展（见 git-workflow.md §3 表注）"
        )


# ---------------------------------------------------------------------------
# 2. 门禁侧：文档列的每个 type 都必须被实测接受
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("commit_type", sorted(_documented_types(DOC_SITES[0])))
def test_documented_type_is_accepted_by_the_gate(commit_type: str) -> None:
    """文档承诺的每个 type 都必须真的能过门禁（A-16 的直接根因）。"""
    result = _check(_sample_message(commit_type))

    assert result.returncode == 0, (
        f"文档列出了 `{commit_type}`，但门禁拒绝了它：\n{result.stdout}\n{result.stderr}"
    )


@pytest.mark.unit
def test_security_type_is_rejected_by_the_gate() -> None:
    """`security` 会被门禁拒绝——这条断言让"文档与门禁不一致"永不再静默发生。

    若哪天真的决定放开 `security`（需要换掉 commitizen 插件并连带改 bump/changelog），
    本用例会报红，从而强制那次变更显式地同时更新文档与本测试。
    """
    result = _check(_sample_message("security"))

    assert result.returncode != 0, (
        "门禁竟然接受了 `security`：说明类型集变了，文档与本测试必须同步更新"
    )


@pytest.mark.unit
def test_gate_type_set_is_pinned() -> None:
    """门禁接受的完整 type 集被钉住，防上游升级静默改变它。

    口径 = **文档列出的类型** 与 **已知"门禁接受但项目不用"的类型** 的**并集**
    （后者见 `GATE_ONLY_TYPES`）。
    实现方式：故意提交一条非法类型，从**门禁打印的 pattern** 里取出类型的候选列表。
    （选择"读门禁的输出"而不是"读插件源码常量"，是为了让断言对象始终是"门禁行为"；
    门禁若改了报错格式，本用例会**以完整输出报红**，而不是静默通过。）
    """
    result = _check(_sample_message("not-a-real-type"))

    assert result.returncode != 0
    match = re.search(r"\(\?s\)\(([a-z|]+)\)", f"{result.stdout}\n{result.stderr}")
    assert match is not None, f"未能从门禁输出中解析出 pattern：\n{result.stdout}\n{result.stderr}"

    gate_types = set(match.group(1).split("|"))
    expected = _documented_types(DOC_SITES[0]) | set(GATE_ONLY_TYPES)

    assert gate_types == expected, (
        "门禁接受的 type 集发生变化：\n"
        f"  新增（门禁有、文档+已知白名单没有）：{sorted(gate_types - expected)}\n"
        f"  消失（文档列了、门禁不再接受）：{sorted(expected - gate_types)}\n"
        "请同步文档与 tests/unit/test_commit_message_contract.py（若为上游升级所致，一并复核）。"
    )
