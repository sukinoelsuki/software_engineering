"""横切可观测层（OBS）：结构化日志与审计。

结构化日志与脱敏管线、审计事件的落盘与查询、可选追踪钩子。
已实现（ADR-0015 §5.4.1）：``audit``。
未实现（ADR-0015 §5.4.1）：``tracing``。

依赖约束（ADR-0015 §7.1 R2）：本层**不得** import ``harness`` / ``tools`` / ``model`` / ``cli``；
只允许依赖 ``foundation``、``contracts`` 与标准库。
"""

from __future__ import annotations
