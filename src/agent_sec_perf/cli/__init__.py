"""L4 表现层：CLI 与交互（UX）。

Typer 应用与子命令、交互 / 非交互（JSON）输出、权限确认交互、中文优先。
ADR-0015 §5.4.1 规划的三件**均已实现**：

* ``app``：最小入口——参数解析（Typer）· §5.1 的装配顺序 · §5.2 的事件渲染与序列化 ·
  退出码表（``0``/``1``/``2``/``3``/``4``/``5``，见 ``harness.md`` §5.2）；
* ``render``：§5.2 的渲染分支表与 §2.6 的 JSONL 载荷序列化——模型 / 工具来的字符串写终端前
  一律经 ``foundation.logging.sanitize_for_display``；
* ``approval``：交互式人工确认（``ApprovalGate`` 的实现）——fail-secure：无 TTY / 超时 /
  非预期输入一律拒绝（``harness.md`` §2.5.5 的 ``R2`` / ``R5``）。

依赖约束（ADR-0015 §7.1 R1）：只向下依赖 ``harness`` 等层；Typer / Rich 的细节限制在本层内。
（渲染输出纯文本行，**不**把 Rich 的 markup 用在不可信文本上——理由见 ``cli/render.py``。）
"""

from __future__ import annotations
