"""工程基线的冒烟测试。确认测试基础设施与包导入链路可用。"""

from __future__ import annotations

import pytest

from agent_sec_perf import __version__


@pytest.mark.unit
def test_version_is_semver() -> None:
    """包应暴露符合语义化版本规范的版本号。"""
    parts = __version__.split(".")

    assert len(parts) == 3, f"版本号应为 MAJOR.MINOR.PATCH 三段。实际为 {__version__!r}"
    assert all(part.isdigit() for part in parts), f"版本号各段应为纯数字。实际为 {__version__!r}"
