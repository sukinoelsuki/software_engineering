"""L2 能力层：本地 / 云端统一模型抽象与能力探测（MODEL）。

统一模型客户端、路由与降级、能力探针、GGUF 资产发现与校验。
已实现（ADR-0015 §5.4.1）：``client``（``LocalLlamaClient``，本地 ``llama-server``；``G7``）。
未实现（ADR-0015 §5.4.1）：``router`` / ``probe`` / ``assets``。

依赖约束（ADR-0015 §7.1 R1）：只向下依赖 ``foundation``，并可依赖 ``contracts`` 与横切层。
"""

from __future__ import annotations

from agent_sec_perf.model.client import LocalLlamaClient

__all__ = ["LocalLlamaClient"]
