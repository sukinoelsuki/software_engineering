"""L2 能力层：本地 / 云端统一模型抽象与能力探测（MODEL）。

统一模型客户端、路由与降级、能力探针、GGUF 资产发现与校验。
规划模块（ADR-0015 §5.4.1，**尚未实现**）：``client`` / ``router`` / ``probe`` / ``assets``。
本包当前只有骨架，不含任何业务实现。

依赖约束（ADR-0015 §7.1 R1）：只向下依赖 ``foundation``，并可依赖 ``contracts`` 与横切层。
"""

from __future__ import annotations
