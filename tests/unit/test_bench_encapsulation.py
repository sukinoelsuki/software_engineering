"""架构约束：子进程只能从唯一的封装层启动。

安全基线要求"所有子进程调用必须位于统一封装层"。这条约定只是文档里的文字时，
它迟早会被绕过；因此这里把它变成**机器检查**：任何直接使用 ``subprocess``
的模块（除封装层自身外）都会让测试失败，``shell=True`` 更是直接禁止。

配套的豁免（ruff S404/S603、bandit B404/B603）**只**出现在封装层，
登记位置见 ``docs/adr/0014-benchmark-automation.md`` §2.9。

另按该 ADR §2.9.1 的 **E4** 增设一条"反静默过度豁免"检查：``# nosec`` / ``# noqa``
注释**只允许写规则号**，且每行的 id 集合**恰好**等于预期集合。理由：bandit 会把
``# nosec`` 之后的自由文本当作规则号候选，理由里出现某规则 ID 即**静默豁免**该规则
（§2.9.1 有探针实测）。
"""

from __future__ import annotations

import ast
import pathlib
import re
from collections import Counter

import pytest

SRC_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src"
#: 唯一的子进程封装层：判定依据是**相对路径**而不只是文件名。
#: 只按 ``path.name == "proc.py"`` 判定的话，任何目录下出现同名文件都能绕过；
#: ADR-0015 §7.1 要求把判定升级为"位于 ``foundation/`` 下的 ``proc.py``"。
ENCAPSULATION_MODULE = pathlib.Path("agent_sec_perf") / "foundation" / "proc.py"

#: E4 期望的豁免标记集合（``ADR-0014 §2.9.1``）：标记内**只**允许出现这些规则号，
#: 且每一行的 id 集合必须**恰好**是该行的预期集合（故用"恰好相等"而非"包含"断言）。
_EXPECTED_MARKERS = Counter(
    {
        ("nosec", ("B404",)): 1,  # import subprocess：封装层的职责本身
        ("nosec", ("B603",)): 3,  # run / run_inherit_env / spawn 三处调用
        ("noqa", ("S603",)): 3,  # 同上三处调用（ruff 侧）
    }
)

_NOSEC_RE = re.compile(r"#\s*nosec(?P<rest>.*)$")
_NOQA_RE = re.compile(r"#\s*noqa(?P<rest>.*)$")
#: 标记之后**只**允许规则号（可逗号分隔）；出现任何其它文本即违规（见模块 docstring 的 E4）。
_IDS_ONLY_RE = re.compile(r"^\s*:?\s*(?P<ids>[A-Z]+[0-9]+(?:\s*,\s*[A-Z]+[0-9]+)*)\s*$")


def _source_files() -> list[pathlib.Path]:
    return sorted(SRC_ROOT.rglob("*.py"))


@pytest.mark.unit
def test_subprocess_is_used_only_in_encapsulation_layer() -> None:
    """除封装层外，不得直接使用 subprocess。"""
    offenders = [
        str(path.relative_to(SRC_ROOT))
        for path in _source_files()
        if "subprocess" in path.read_text(encoding="utf-8")
        and path.relative_to(SRC_ROOT) != ENCAPSULATION_MODULE
    ]

    assert offenders == [], f"以下模块绕过了子进程封装层：{offenders}"


@pytest.mark.unit
def test_no_shell_execution_anywhere_in_source() -> None:
    """任何地方都不得用 shell 执行命令。"""
    offenders = [
        str(path.relative_to(SRC_ROOT))
        for path in _source_files()
        if "shell=True" in path.read_text(encoding="utf-8")
    ]

    assert offenders == [], f"以下模块使用了 shell 执行：{offenders}"


@pytest.mark.unit
def test_bench_modules_do_not_print() -> None:
    """src 下不得残留 print（日志一律走 logging；CI 需要持续输出不是例外）。

    用 AST 而不是字符串匹配：``print(`` 也会出现在字符串字面量与文档里
    （例如判定脚本片段），字符串匹配会把它们误判成违规。
    """
    offenders = [
        str(path.relative_to(SRC_ROOT))
        for path in _source_files()
        if _has_print_call(path.read_text(encoding="utf-8"))
    ]

    assert offenders == [], f"以下模块使用了 print：{offenders}"


def _has_print_call(text: str) -> bool:
    """源码里是否存在真正的 ``print(...)`` 调用。"""
    for node in ast.walk(ast.parse(text)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ):
            return True
    return False


def _exemption_markers() -> list[tuple[str, int, str, tuple[str, ...]]]:
    """列出 ``src/`` 下所有 ``# nosec`` / ``# noqa`` 标记及其声明的规则号。

    返回 ``(相对路径, 行号, kind, ids)``；**违规行**（裸标记、或标记后含自由文本）的
    ``ids`` 为空元组，由 :func:`test_exemption_markers_declare_exactly_the_expected_rules`
    判定为失败。
    """
    markers: list[tuple[str, int, str, tuple[str, ...]]] = []
    for path in _source_files():
        relative = str(path.relative_to(SRC_ROOT))
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for kind, pattern in (("nosec", _NOSEC_RE), ("noqa", _NOQA_RE)):
                match = pattern.search(line)
                if match is None:
                    continue
                ids_match = _IDS_ONLY_RE.match(match.group("rest"))
                ids = (
                    tuple(part.strip() for part in ids_match.group("ids").split(","))
                    if ids_match is not None
                    else ()
                )
                markers.append((relative, lineno, kind, ids))
    return markers


@pytest.mark.unit
def test_exemption_markers_declare_exactly_the_expected_rules() -> None:
    """E4：豁免标记只写规则号，且 id 集合**恰好**等于预期（防静默过度豁免）。

    ``# nosec`` 之后的自由文本会被 bandit 当作规则号候选：理由里出现某个规则 ID，
    该规则就会被**静默豁免**（``ADR-0014 §2.9.1`` 的探针实测）。因此逐行断言
    "标记后只有规则号 + 该行 id 集合恰好是预期集合"，而不是只断言"标记存在"。
    """
    markers = _exemption_markers()

    wrong_place = [
        f"{relative}:{lineno}"
        for relative, lineno, _kind, _ids in markers
        if relative != str(ENCAPSULATION_MODULE)
    ]
    free_text = [
        f"{relative}:{lineno} ({kind})" for relative, lineno, kind, ids in markers if not ids
    ]
    observed = Counter((kind, ids) for _relative, _lineno, kind, ids in markers)

    assert wrong_place == [], f"豁免标记出现在封装层之外：{wrong_place}"
    assert free_text == [], f"豁免标记后含非规则号文本（可致静默过度豁免）：{free_text}"
    assert observed == _EXPECTED_MARKERS, (
        f"豁免标记与预期不符（防过度豁免）：实际 {dict(observed)}，预期 {dict(_EXPECTED_MARKERS)}"
    )
