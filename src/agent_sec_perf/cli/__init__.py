"""L4 表现层：CLI 与交互（UX）。

Typer 应用与子命令、交互 / 非交互（JSON）输出、权限确认交互、中文优先。
规划模块（ADR-0015 §5.4.1，**尚未实现**）：``app`` / ``render`` / ``approval``。
本包当前只有骨架，不含任何业务实现。

依赖约束（ADR-0015 §7.1 R1）：只向下依赖 ``harness`` 等层；Typer / Rich 的细节限制在本层内。
"""

from __future__ import annotations
