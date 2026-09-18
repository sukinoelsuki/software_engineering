"""接缝完整性（ADR-0015 §7.1）：路径校验的唯一入口是 ``foundation/paths.py``。

安全基线要求「只有 ``foundation/paths.py`` 能校验路径」。这条约定若只停留在文档，
迟早会有模块另开第二条白名单（那是越权 / 穿越的绕过点）。本节把它变成机器检查：
源码中 ``raise PathNotAllowedError`` 只应出现在 ``foundation/paths.py``。

这是 S2 的结构性前提——若其它模块也开始 raise / 实现路径白名单，S2 的「唯一入口」
保证就被破坏了，对抗性用例也会被绕过。
"""

from __future__ import annotations

import pathlib

import pytest

from agent_sec_perf.foundation import paths

SRC_ROOT = pathlib.Path(__file__).resolve().parents[2] / "src"
PATHS_MODULE = pathlib.Path("agent_sec_perf") / "foundation" / "paths.py"


@pytest.mark.security
def test_only_paths_layer_raises_path_not_allowed() -> None:
    """除 ``foundation/paths.py`` 外，源码中不得有其它地方 ``raise PathNotAllowedError``。"""
    offenders = [
        str(path.relative_to(SRC_ROOT))
        for path in sorted(SRC_ROOT.rglob("*.py"))
        if "raise PathNotAllowedError" in path.read_text(encoding="utf-8")
        and path.relative_to(SRC_ROOT) != PATHS_MODULE
    ]
    assert offenders == [], f"以下模块绕过了唯一路径校验入口：{offenders}"


@pytest.mark.security
def test_paths_layer_exposes_resolve_within_entrypoint() -> None:
    """保活：唯一入口 ``resolve_within`` 必须存在且可导入，防止重构误删该接缝。"""
    assert hasattr(paths, "resolve_within")
