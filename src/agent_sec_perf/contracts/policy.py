"""安全策略契约（横切 SEC 层）。

字段级定义见 ``docs/design/interfaces/policy.md``（本模块是它的唯一实现）；
``PolicyEngine.decide()`` 的**实现**仍在 ``security/policy.py``，本零行为契约层只放 Protocol。
本模块只依赖标准库（ADR-0015 §7.1 R3）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class Capability(StrEnum):
    """能力（权限模型的原子单位，default-deny）。

    刻意的**粗粒度**：能力回答"这类操作是否被授权"，细粒度危险度由 :class:`RiskLevel`
    表达。扩展成员改变权限模型面积，需 ADR。
    """

    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    EXECUTE_COMMAND = "execute_command"
    NETWORK_OUTBOUND = "network_outbound"


class RiskLevel(StrEnum):
    """风险等级；等级 → 处置的映射见 ``docs/design/interfaces/policy.md`` §2.2。

    由 ``PolicyEngine`` **逐次求值**得出，不是静态挂在工具上的属性。
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class PolicyRequest:
    """一次策略求值的输入（由 HARNESS 在完成参数校验后构造）。

    ``arguments`` 是**已校验**的结构化参数，但仍按**数据**处理：策略不得把它们拼接进
    命令、路径、正则或表达式求值（``REQ-SEC-03``）；需要路径时经
    ``foundation.paths.resolve_within`` 再比较。

    ``requested`` **必须非空**（前置条件）：工具未声明任何能力属**声明缺陷**，应在构造**之前**
    拒绝，不得构造出空集请求。注意该前置条件**只是调用方约定**——``frozenset[Capability]``
    在类型层面表达不了"非空"，而本层是零行为契约层（不得加 ``__post_init__``）⇒ 空集的兜底
    由 :class:`PolicyEngine` 的实现负责（硬拒绝，见其 docstring 与
    ``docs/design/interfaces/policy.md`` §2.5「补充规定」）。
    """

    session_id: str
    call_id: str
    tool_name: str
    arguments: Mapping[str, object]
    requested: frozenset[Capability]
    domain_pack: str | None = None


@dataclass(frozen=True)
class PolicyDecision:
    """一次策略求值的输出（决策是**返回值**，不是异常）。

    四种 ``allow`` 与 ``requires_confirmation`` 的组合（自动放行 / 须人工确认 / 可升级拒绝 /
    硬拒绝）的含义见 ``docs/design/interfaces/policy.md`` §2.4，实现与测试必须逐一覆盖。
    三个非布尔字段均**无默认值**，强制调用点显式给出（避免"忘了填理由"通过）。
    """

    allow: bool
    requires_confirmation: bool
    risk_level: RiskLevel
    reason: str
    audit_id: str


class PolicyEngine(Protocol):
    """策略引擎（被 ``harness/`` 消费；实现见 ``security/policy.py``）。

    并发：无状态、纯函数式，可多线程调用。
    fail-secure：**求值阶段**任何异常都收敛为
    ``allow=False, requires_confirmation=True, risk_level=CRITICAL``，**禁止**逃逸为 allow；
    而 ``AuditSink.emit()`` 的异常**必须原样冒泡**，不得被上述收敛吞掉（二者处置相反）。

    ``PolicyRequest.requested`` 为空集（上游构造缺陷）⇒ **硬拒绝**：``allow=False``、
    ``requires_confirmation=False``、``risk_level=CRITICAL``，且**仍须** ``emit`` 一条
    ``POLICY_DECISION`` 事件（``capability=None``，由 ``detail["requested"] == []`` 表达）。
    多元素时 ``capability`` 取**确定性代表**（缺失集合非空则取其名字最小者，否则取请求中最小者）；
    完整集合写入 ``detail["requested"]``。规则与理由见
    ``docs/design/interfaces/policy.md`` §2.5「补充规定」。
    """

    def decide(self, request: PolicyRequest) -> PolicyDecision: ...
