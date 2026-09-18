"""spawn-env 静态守卫：所有 ``spawn(...)`` 调用点必须显式传 ``env=``。

背景（与 T-08 同根）：``proc.spawn`` 默认 ``env=dict(os.environ)``，即全量继承父环境。
一旦某个调用点**忘记传 env**，就会无声地泄漏凭据（``bench/runner.py:122`` 就是先例）。
本用例把所有 ``spawn(...)`` 调用点变成机器检查：每个调用都必须显式出现 ``env=`` 实参。

与 T-08 的区别：T-08 是**运行时** canary（凭据确实泄漏）；本用例是**静态**纪律检查，
确保调用点显式声明环境——即使未来 ``spawn`` 默认被改回继承，调用点也已自带最小 env。

当前状态：全仓唯一的调用点 ``bench/runner.py:122`` 未传 ``env=``，即同一缺陷的静态表现，
故本用例以 ``xfail(strict=True)`` 钉住，待缺陷修复后翻正。

变异验证（开发期手动，仓库保持干净）：

* 修复后（``runner.py:122`` 显式传 ``env=``）⇒ 本用例通过 ⇒ xfail(strict) 翻红，强制翻正；
* 修复后若再删掉某调用点的 ``env=`` ⇒ 本用例失败（预期内），证明它能抓住回归。
"""

from __future__ import annotations

import pathlib
import re

import pytest

SRC_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src"

# 匹配所有 spawn( 调用，但排除函数定义的 def spawn(。
_CALL_RE = re.compile(r"(?<!def )spawn\(")


def _extract_call_args(source: str) -> list[str]:
    """返回所有 ``spawn(...)`` 调用的实参文本（跨行，括号配平）。"""
    args: list[str] = []
    for match in _CALL_RE.finditer(source):
        open_paren = match.end() - 1  # '(' 的位置
        depth = 0
        i = open_paren
        n = len(source)
        while i < n:
            ch = source[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    args.append(source[open_paren + 1 : i])
                    break
            i += 1
    return args


@pytest.mark.security
@pytest.mark.xfail(
    strict=True,
    reason=(
        "bench/runner.py:122 未显式传 env=，spawn 默认全量继承 os.environ"
        "（与 T-08 同根缺陷，待修复后翻正）"
    ),
)
def test_all_spawn_calls_pass_env_explicitly() -> None:
    """src/ 中每个 ``spawn(...)`` 调用都必须显式传 ``env=`` 实参。"""
    offenders: list[str] = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "spawn(" not in text:
            continue
        for call_args in _extract_call_args(text):
            if "env=" not in call_args:
                rel = path.relative_to(SRC_ROOT)
                offenders.append(f"{rel}: 缺少显式 env= 的 spawn 调用")
    assert offenders == [], (
        "以下 spawn 调用未显式传 env=，会退化为全量继承父环境（凭据泄漏风险）：\n"
        + "\n".join(offenders)
    )
