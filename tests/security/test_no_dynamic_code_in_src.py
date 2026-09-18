"""T-12（威胁模型 T-12 动态代码执行）：src/ 中禁止动态代码执行原语。

背景：安全基线禁止 ``eval`` / ``exec`` / ``importlib`` / ``__import__`` 这类动态执行原语
（不可信内容可能借此变成指令）。``src/`` 当前 grep 为 **0 命中**。本用例把它变成
机器检查：任何新增的 ``eval(`` / ``exec(`` / ``importlib`` / ``__import__`` 都会
让用例失败，防止未来回归。

变异验证（开发期手动）：临时在某 src 文件注入一行 ``eval("1")``，本用例失败；
``git restore`` 后 grep 恢复 0 命中，仓库干净。
"""

from __future__ import annotations

import pathlib
import re

import pytest

SRC_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src"

# 需禁止的动态执行原语（词边界，避免误伤 "execute" 等正常词）。
_FORBIDDEN_PATTERNS = [
    re.compile(r"\beval\("),
    re.compile(r"\bexec\("),
    re.compile(r"\bimportlib\b"),
    re.compile(r"__import__"),
]


@pytest.mark.security
def test_no_dynamic_code_execution_in_src() -> None:
    """src/ 下不得出现 eval/exec/importlib/__import__ 这类动态执行原语。"""
    offenders: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for pattern in _FORBIDDEN_PATTERNS:
            for match in pattern.finditer(text):
                rel = path.relative_to(SRC_ROOT)
                line_no = text.count("\n", 0, match.start()) + 1
                offenders.append(f"{rel}:{line_no}: {match.group()}")
    assert offenders == [], (
        "src/ 中出现被禁止的动态执行原语（eval/exec/importlib/__import__）：\n"
        + "\n".join(offenders)
    )
