"""架构约束：子进程只能从唯一的封装层启动。

安全基线要求"所有子进程调用必须位于统一封装层"。这条约定只是文档里的文字时，
它迟早会被绕过；因此这里把它变成**机器检查**：任何直接使用 ``subprocess``
的模块（除封装层自身外）都会让测试失败，``shell=True`` 更是直接禁止。

配套的豁免（ruff S404/S603、bandit B404/B603）**只**出现在封装层，
登记位置见 ``docs/adr/0014-benchmark-automation.md``。
"""

from __future__ import annotations

import ast
import pathlib

import pytest

SRC_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src"
#: 唯一的子进程封装层：判定依据是**相对路径**而不只是文件名。
#: 只按 ``path.name == "proc.py"`` 判定的话，任何目录下出现同名文件都能绕过；
#: ADR-0015 §7.1 要求把判定升级为"位于 ``foundation/`` 下的 ``proc.py``"。
ENCAPSULATION_MODULE = pathlib.Path("agent_sec_perf") / "foundation" / "proc.py"


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
