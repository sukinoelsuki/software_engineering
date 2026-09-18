"""横切安全层（SEC）：能力边界感知的安全层。

能力/权限模型（default-deny）、策略求值、审批门、沙箱后端与逐维度探针、能力边界拒答。
规划模块（ADR-0015 §5.4.1，**尚未实现**）：``capabilities`` / ``policy`` / ``sandbox/`` /
``refusal``。本包当前只有骨架，不含任何业务实现。

依赖约束（ADR-0015 §7.1 R2）：本层**不得** import ``harness`` / ``tools`` / ``model`` / ``cli``；
只允许依赖 ``foundation``、``contracts`` 与标准库。
"""

from __future__ import annotations
