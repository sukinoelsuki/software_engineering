"""能力与权限模型（``REQ-SEC-01``，横切 SEC 层，default-deny）。

能力回答"**这类操作是否被授权**"；"**这一次该不该让人确认**"由 ``RiskLevel`` 回答
（``docs/design/interfaces/policy.md`` §2.1 的粒度选择）。本模块只做前者，且**纯内存、无 I/O**。

default-deny 的落法**不是**一句口号，而是两条结构性约束：

1. :class:`CapabilitySet` 的默认构造是**空集**——"什么都没授予"，不是"全授予"。
   构造方要放行任何能力，必须**显式**把该能力写进去（``docs/design/interfaces/README.md`` C6）。
2. 声明式来源（配置文件、领域包）里的能力名解析（:func:`parse_capabilities`）遇到
   ``Capability`` 里不存在的名字**即拒绝**。反过来"未知名字跳过"会把
   ``write_file`` 误写成 ``write_files`` 变成一次**静默降权**：系统照常启动、
   该能力却没授予，而调用方以为已授予——这正是 default-deny 最容易被绕开的地方。

本模块依赖 ``contracts``（能力枚举）与 ``foundation``（异常基类），不依赖任何业务层
（ADR-0015 §7.1 的 R2：横切层不得反向依赖 ``harness`` / ``tools`` / ``model`` / ``cli``）。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from agent_sec_perf.contracts.policy import Capability
from agent_sec_perf.foundation.errors import BenchError
from agent_sec_perf.foundation.logging import sanitize_for_display

__all__ = [
    "DENY_ALL",
    "CapabilitySet",
    "UnknownCapabilityError",
    "narrow_granted",
    "parse_capabilities",
]


class UnknownCapabilityError(BenchError):
    """声明了 :class:`Capability` 中不存在的能力名（fail-secure：拒绝，不静默丢弃）。"""


@dataclass(frozen=True)
class CapabilitySet:
    """一次会话/一次求值可用的能力集合（不可变，可安全共享与多线程读取）。

    ``granted`` 默认空集 ⇒ **未显式授予即拒绝**。集合语义用 ``frozenset``：
    与 ``frozen=True`` 一致，且不会被调用方就地篡改（``docs/design/interfaces/README.md`` C4）。
    """

    granted: frozenset[Capability] = field(default_factory=frozenset)

    def allows(self, capability: Capability) -> bool:
        """该能力是否已授予。"""
        return capability in self.granted

    def missing(self, requested: Iterable[Capability]) -> frozenset[Capability]:
        """``requested`` 中**未**被授予的能力（default-deny 的判定入口）。

        返回"缺哪些"而不是布尔值：调用方需要把缺的能力写进理由与审计
        （``REQ-SEC-06`` 的可回放要求能回答"被拒是因为缺什么"）。
        """
        return frozenset(requested) - self.granted

    def allows_all(self, requested: Iterable[Capability]) -> bool:
        """``requested`` 是否**全部**已授予（任一缺失即为假）。"""
        return not self.missing(requested)

    def as_names(self) -> tuple[str, ...]:
        """排序后的能力名，供审计与日志（顺序稳定 ⇒ 跨轮可比对）。"""
        return tuple(sorted(member.value for member in self.granted))


#: 空授予集合：default-deny 的显式表示（构造方想不出该给什么时，就给这个）。
DENY_ALL = CapabilitySet()


def parse_capabilities(names: Iterable[str]) -> CapabilitySet:
    """把**声明式**来源（配置文件 / 领域包）里的能力名解析成授予集合。

    Args:
        names: 能力名序列，如 ``["read_file", "execute_command"]``。

    Returns:
        仅包含这些能力的授予集合（重复项自然去重）。

    Raises:
        UnknownCapabilityError: 出现 ``Capability`` 中不存在的名字。
            错误信息中的名字经 :func:`sanitize_for_display` 处理：它是不可信文本，
            直接回显会让日志行被换行/转义序列伪造。
    """
    granted: set[Capability] = set()
    for name in names:
        try:
            granted.add(Capability(name))
        except ValueError as exc:
            shown = sanitize_for_display(name, limit=32)
            msg = (
                f"未知的能力名：{shown!r}（允许的名字见 "
                f"docs/design/interfaces/policy.md §2.1；扩展成员需 ADR）"
            )
            raise UnknownCapabilityError(msg) from exc
    return CapabilitySet(granted=frozenset(granted))


def narrow_granted(granted: CapabilitySet, allowlist: frozenset[Capability]) -> CapabilitySet:
    """把授予集合**收窄**到 ``allowlist`` 之内（只能减小，绝不放大）。

    生效授予 = ``granted ∩ allowlist``。用于领域包（``REQ-HARNESS-08``）：pack 的
    ``security.capabilities`` **只能收窄**用户配置的授予，**不得**取并集，
    **不得**把"pack 未声明"当成"全授予"（``docs/design/interfaces/harness.md`` §4.4 第 1 条）。

    实现选**交集**而不是"按 ``allowlist`` 重建集合"，理由是可结算的：重建的形式下，
    ``allowlist`` 一旦含 ``granted`` 之外的能力就会**扩权**（fail-open）；交集在构造上
    保证 ``结果 ⊆ granted``，无论调用方传什么。

    Args:
        granted: 用户配置的授予集合。
        allowlist: 允许保留的能力（来自领域包等**声明式**配置）。

    Returns:
        ``granted ∩ allowlist``。交换律与幂等由交集直接给出，无需额外保持。
    """
    return CapabilitySet(granted=granted.granted & allowlist)
