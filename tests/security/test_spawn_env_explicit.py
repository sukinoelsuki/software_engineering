"""spawn-env 静态守卫：所有 ``spawn(...)`` 调用点必须显式传 ``env=``。

背景（与 T-08 同根）：修复前 ``proc.spawn`` 默认 ``env=dict(os.environ)``，即全量继承父环境。
一旦某个调用点**忘记传 env**，就会无声地泄漏凭据（``bench/runner.py:122`` 就是先例）。
本用例把所有 ``spawn(...)`` 调用点变成机器检查：每个调用都必须显式出现 ``env=`` 实参。

与 T-08 的区别：T-08 是**运行时** canary（凭据确实泄漏）；本用例是**静态**纪律检查，
确保调用点显式声明环境——即使未来 ``spawn`` 默认被改回继承，调用点也已自带最小 env。

历史与本用例的当前形态：修复前，全仓唯一的调用点 ``bench/runner.py:122`` 未传 ``env=``，
即同一缺陷的静态表现，本用例曾以 ``xfail(strict=True)`` 钉住它（**曾以 xfail 钉住**这件事
本身就是证据：该缺陷当时确实存在）。2026-09-18 随修复（``runner.py`` 显式传最小 env，
且 ``proc.spawn`` 的默认值改为最小环境）**翻正为常态断言**，`xfail` 标记已移除。

变异验证（修复后手动，仓库保持干净）：

* 删掉任一调用点的 ``env=`` ⇒ 本用例失败（预期内），证明它能抓住回归；
* 反向变异（把 ``spawn`` 默认改回继承父环境）不会让本用例变红——那由**运行时**
  canary ``test_spawn_credentials_canary.py`` 负责，二者刻意互补。
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
