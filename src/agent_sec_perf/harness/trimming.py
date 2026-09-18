"""工具裁剪（``REQ-HARNESS-03``，L3 编排层）。

模型一次能看到多少工具，同时决定三件事：弱模型的选择难度（SRS §4.1 引用的实证：
工具越多，弱模型越乱）、上下文占用（``REQ-PERF-03``），以及**攻击面**。
本模块按**硬件档位**给出一份**能力预算**，只把"能力声明落在预算内"的工具暴露给模型。

三条设计取向：

1. **默认少暴露**。预算随档位单调（``S ⊆ M ⊆ L``），且**三档都不含**
   ``NETWORK_OUTBOUND``：出站是 default-deny（``interfaces/README.md`` C6），
   任何档位都不得自动放开——"档位高"不是"可以联网"的理由。
2. **纯函数、确定性、只读**。同一输入必得同一输出：返回的元组按工具名排序，
   因此与输入顺序无关；不修改任何 :class:`ToolSpec`（其本身是 ``frozen dataclass``）；
   只返回**描述**（``ToolSpec``），不返回可执行句柄——与 ``ToolRegistry.specs()`` 的口径一致。
3. **不猜**。工具**没有声明任何能力**属声明缺陷（``PolicyRequest.requested`` 必须非空，
   见 ``contracts/policy.py``）⇒ 本函数**不暴露**它，而不是替它猜一个能力；
   档位不是 :class:`HardwareTier` 成员 ⇒ 抛 ``ValueError``，**不回落**到某个档位的预算。

**裁剪 ≠ 授权，也 ≠ 拦截**：本函数只决定"暴露什么"。被裁剪掉的工具，其名字对模型而言
应**等同于未知工具**——调用点必须据此**默认拒绝 + 审计**（``REQ-SEC-01``，与
``ToolRegistry.resolve()`` 返回 ``None`` 的处置一致），:func:`exposed_tool_names`
就是给该判定用的名字集合。真正的授权判定仍在 ``security.PolicyEngine.decide()``
（横切层），本模块不承担它。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from types import MappingProxyType
from typing import Final

from agent_sec_perf.contracts.model import HardwareTier
from agent_sec_perf.contracts.policy import Capability
from agent_sec_perf.contracts.tools import ToolSpec

__all__ = [
    "TIER_CAPABILITY_BUDGET",
    "exposed_tool_names",
    "select_tools",
]

#: 档位 → **可暴露工具的能力预算**（只读；工具声明的能力必须是它的子集才会被暴露）。
#:
#: * ``S``（≤4 GiB / 2B 级）：只读——读文件、列目录；
#: * ``M``（8 GiB / 4B 级）：只读 + 写文件；
#: * ``L``（16 GiB / 8B 级）：再加命令执行。
#:
#: ``NETWORK_OUTBOUND`` **不在任何档位**内：出站只能由会话显式授权后在策略层放行。
TIER_CAPABILITY_BUDGET: Final[Mapping[HardwareTier, frozenset[Capability]]] = MappingProxyType(
    {
        HardwareTier.S: frozenset({Capability.READ_FILE}),
        HardwareTier.M: frozenset({Capability.READ_FILE, Capability.WRITE_FILE}),
        HardwareTier.L: frozenset(
            {Capability.READ_FILE, Capability.WRITE_FILE, Capability.EXECUTE_COMMAND}
        ),
    }
)


def _budget_for(tier: HardwareTier) -> frozenset[Capability]:
    """取该档位的能力预算。"""
    budget = TIER_CAPABILITY_BUDGET.get(tier)
    if budget is None:
        msg = (
            f"未知的硬件档位：{tier!r}；只接受 HardwareTier 的 S / M / L。"
            "档位不确定时不得猜测，应先由硬件探测（REQ-PERF-06）确定。"
        )
        raise ValueError(msg)
    return budget


def select_tools(specs: Iterable[ToolSpec], tier: HardwareTier) -> tuple[ToolSpec, ...]:
    """选出该档位**允许暴露**的工具描述。

    入选条件（两条同时满足）：

    1. 工具**声明了至少一个能力**（空 ``capabilities`` 属声明缺陷 ⇒ 不暴露）；
    2. 工具声明的全部能力**落在该档位的能力预算内**（声明了预算外能力的工具整体不暴露，
       而不是"把超出的能力裁掉再暴露"——后者会让模型看到一个它其实用不了的工具）。

    Args:
        specs: 候选工具描述（如 ``ToolRegistry.specs()`` 的返回）。
        tier: 硬件档位。

    Returns:
        按 ``name`` 排序的元组（**确定性**：与 ``specs`` 的顺序无关）。
        没有工具入选时返回空元组（empty 是合法结果，不是错误）。

    Raises:
        ValueError: ``tier`` 不是 :class:`HardwareTier` 的已知成员。
    """
    budget = _budget_for(tier)
    selected = (spec for spec in specs if spec.capabilities and spec.capabilities <= budget)
    return tuple(sorted(selected, key=lambda spec: spec.name))


def exposed_tool_names(specs: Iterable[ToolSpec], tier: HardwareTier) -> frozenset[str]:
    """该档位下**允许暴露**的工具名集合。

    调用点用它把"被裁剪掉的工具名"判定为**未知工具** ⇒ **默认拒绝 + 审计**
    （``REQ-SEC-01``）；不要用注册表的全量名字做这个判定。
    """
    return frozenset(spec.name for spec in select_tools(specs, tier))
