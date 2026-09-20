"""基础设施层：跨层共享的地基。

承载**跨层共享的基础设施**，由 ``bench/`` 中已被验证的三件地基提升而来
（决策见 ``docs/adr/0015-layering-and-reuse-boundary.md`` §5.4.1）：

``errors`` → 全项目共享的异常层次
``paths``  → 全项目唯一的路径白名单校验入口
``proc``   → 全项目唯一的子进程封装层（隔离 + rlimit + 最小环境）

已实现（ADR-0015 §5.4.1）：``errors`` / ``paths`` / ``proc`` / ``config`` / ``logging``；
未实现：无。
本层只允许依赖标准库与 ``contracts``，不得依赖任何业务包（ADR-0015 §7.1 R1/R4）。
"""

from __future__ import annotations
