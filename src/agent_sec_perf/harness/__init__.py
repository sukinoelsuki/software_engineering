"""L3 编排层：能力自适应 Harness（核心论点）。

ReAct 循环、工具裁剪、提示分级、会话状态与检查点、错误分级、上下文效率引擎、领域包加载。
已实现（ADR-0015 §5.4.1 清单）：``session`` / ``loop`` / ``prompts`` / ``trimming`` /
``checkpoint`` / ``errors`` / ``context/`` / ``domain_pack``。
未实现：无。清单外的 ``arguments``（ADR-0020）亦已实现。

依赖约束（ADR-0015 §7.1 R1/R5）：向下依赖 ``model`` / ``tools`` / ``foundation``；领域包
**只加载声明式配置**（TOML/JSON），**禁止加载其中的 Python 代码**。
"""

from __future__ import annotations
