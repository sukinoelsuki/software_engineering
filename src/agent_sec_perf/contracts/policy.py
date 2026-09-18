"""安全策略契约（横切 SEC 层）。

ADR-0015 §5.4.1 只给出类型名，未规定字段/成员 ⇒ ``Capability`` / ``RiskLevel`` 与
``PolicyRequest`` 以占位形式落定；``PolicyDecision`` 的两个字段取自 §5.1.2 明确写出的
``allow=False, requires_confirmation=True``（fail-secure 错误语义）。

``PolicyEngine.decide()`` 属 ``security/`` 的实现，不在本零行为契约层。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Capability(Enum):
    """能力（权限模型的原子单位，default-deny）。

    **成员待澄清**：ADR-0015 §7.2 提及 ``WRITE_FILE`` 等能力，但未给出完整成员集合。
    """


class RiskLevel(Enum):
    """风险分级。**成员待澄清**（ADR-0015 未规定）。"""


@dataclass(frozen=True)
class PolicyRequest:
    """一次策略求值的输入。**字段待澄清**（ADR-0015 未规定）。"""


@dataclass(frozen=True)
class PolicyDecision:
    """一次策略求值的输出。

    错误语义（ADR-0015 §5.1.2，fail-secure）：``decide()`` 内部任何异常都**不得**逃逸为
    allow；失败时返回 ``allow=False`` 且 ``requires_confirmation=True``。字段名即取自该表。

    **是否还有其它字段（如拒绝理由、审计关联）待澄清**。
    """

    allow: bool
    requires_confirmation: bool
