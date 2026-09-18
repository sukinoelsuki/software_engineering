"""工具裁剪（``REQ-HARNESS-03``，L3 编排层）。

模型一次能看到多少工具，同时决定三件事：弱模型的选择难度（SRS §4.1 引用的实证：
工具越多，弱模型越乱）、上下文占用（``REQ-PERF-03``），以及**攻击面**。
本模块按**模型能力档位**给出一份**能力预算**，只把"能力声明落在预算内"的工具暴露给模型；
再叠加调用方给出的名字白名单（领域包的 ``tools.allowlist``）**进一步收窄**。

三条设计取向：

1. **默认少暴露**。预算随档位单调（``BASIC ⊆ STANDARD ⊆ ADVANCED``），且**三档都不含**
   ``NETWORK_OUTBOUND``：出站是 default-deny（``interfaces/README.md`` C6），
   任何档位都不得自动放开——"档位高"不是"可以联网"的理由。
2. **纯函数、确定性、只读**。同一输入必得同一输出：返回的元组按工具名排序，
   因此与输入顺序无关；不修改任何 :class:`ToolSpec`（其本身是 ``frozen dataclass``）；
   只返回**描述**（``ToolSpec``），不返回可执行句柄——与 ``ToolRegistry.specs()`` 的口径一致。
3. **不猜**。工具**没有声明任何能力**属声明缺陷（``PolicyRequest.requested`` 必须非空，
   见 ``contracts/policy.py``）⇒ 本函数**不暴露**它，而不是替它猜一个能力；
   档位不是 :class:`CapabilityTier` 成员 ⇒ 抛 ``ValueError``，**不回落**到某个档位的预算。

**档位轴是 ``CapabilityTier``（模型能力），不是 ``HardwareTier``（硬件）**：
两条轴正交且契约明令不得互相转换（``contracts/model.py``）。用硬件轴意味着
"同一模型换更强硬件就多给工具"，与 ``REQ-HARNESS-03`` 要防的事直接冲突
（契约 §7.1 第 1 项、``R-1`` 的裁决）。

**``allowlist`` 只能收窄、不能放宽**：名字不在 ``allowlist`` 里一律不暴露，即便它落在
能力预算内、即便 ``allowlist`` 为空。空 ``allowlist`` ⇒ 空结果（"什么都不给"是**可表达**
的取值，而"漏写"必须表现为**失败**，两者的分工见契约 §4.2）。任何把 ``allowlist`` 与
预算**并集**或"默认全放"的写法都是 fail-open。

**裁剪 ≠ 授权，也 ≠ 拦截**：本函数只决定"暴露什么"。被裁剪掉的工具，其名字对模型而言
应**等同于未知工具**——调用点必须据此**默认拒绝 + 审计**（``REQ-SEC-01``，与
``ToolRegistry.resolve()`` 返回 ``None`` 的处置一致），:func:`exposed_tool_names`
就是给该判定用的名字集合。真正的授权判定仍在 ``security.PolicyEngine.decide()``
（横切层），本模块不承担它。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Final

from agent_sec_perf.contracts.model import CapabilityTier
from agent_sec_perf.contracts.policy import Capability
from agent_sec_perf.contracts.tools import ToolSpec

__all__ = [
    "TIER_CAPABILITY_BUDGET",
    "exposed_tool_names",
    "select_tools",
]

#: 档位 → **可暴露工具的能力预算**（只读；工具声明的能力必须是它的子集才会被暴露）。
#:
#: * ``BASIC``（能力最受限的模型）：只读——读文件、列目录；
#: * ``STANDARD``：只读 + 写文件；
#: * ``ADVANCED``：再加命令执行。
#:
#: ``NETWORK_OUTBOUND`` **不在任何档位**内：出站只能由会话显式授权后在策略层放行。
TIER_CAPABILITY_BUDGET: Final[Mapping[CapabilityTier, frozenset[Capability]]] = MappingProxyType(
    {
        CapabilityTier.BASIC: frozenset({Capability.READ_FILE}),
        CapabilityTier.STANDARD: frozenset({Capability.READ_FILE, Capability.WRITE_FILE}),
        CapabilityTier.ADVANCED: frozenset(
            {Capability.READ_FILE, Capability.WRITE_FILE, Capability.EXECUTE_COMMAND}
        ),
    }
)


def _budget_for(tier: CapabilityTier) -> frozenset[Capability]:
    """取该档位的能力预算。"""
    budget = TIER_CAPABILITY_BUDGET.get(tier)
    if budget is None:
        msg = (
            f"未知的能力档位：{tier!r}；只接受 CapabilityTier 的成员。"
            "档位不确定时不得猜测，应显式取最保守的 BASIC。"
        )
        raise ValueError(msg)
    return budget


def select_tools(
    specs: Sequence[ToolSpec], *, tier: CapabilityTier, allowlist: frozenset[str]
) -> tuple[ToolSpec, ...]:
    """选出该档位 + 该名字白名单下**允许暴露**的工具描述。

    入选条件（三条同时满足）：

    1. 工具**名字在 ``allowlist`` 内**（名字白名单只能收窄暴露面）；
    2. 工具**声明了至少一个能力**（空 ``capabilities`` 属声明缺陷 ⇒ 不暴露）；
    3. 工具声明的全部能力**落在该档位的能力预算内**（声明了预算外能力的工具整体不暴露，
       而不是"把超出的能力裁掉再暴露"——后者会让模型看到一个它其实用不了的工具）。

    Args:
        specs: 候选工具描述（如 ``ToolRegistry.specs()`` 的返回）。
        tier: 模型能力档位（:class:`CapabilityTier`）。**不接受** ``HardwareTier``。
        allowlist: 允许暴露的工具名。**无默认值**：默认值会让"忘了传"退化为"不限制"，
            而白名单**不得**来自猜测（与 ``audit.md`` §2.5 的 ``P1`` 同一取向）。

    Returns:
        按 ``name`` 排序的元组（**确定性**：与 ``specs`` 的顺序无关）。
        没有工具入选时返回空元组（empty 是合法结果，不是错误）。

    Raises:
        ValueError: ``tier`` 不是 :class:`CapabilityTier` 的已知成员。
    """
    budget = _budget_for(tier)
    selected = (
        spec
        for spec in specs
        if spec.name in allowlist and spec.capabilities and spec.capabilities <= budget
    )
    return tuple(sorted(selected, key=lambda spec: spec.name))


def exposed_tool_names(
    specs: Sequence[ToolSpec], *, tier: CapabilityTier, allowlist: frozenset[str]
) -> frozenset[str]:
    """该档位 + 该白名单下**允许暴露**的工具名集合（``harness.md`` §3.1）。

    **必须由** :func:`select_tools` **派生**（避免第二份档位预算判定：两份判定必然漂移，
    而漂移的后果是"暴露面"与"解析域"不一致——契约 §3.1 明令）。

    调用点用它把"被裁剪掉的工具名"判定为**未暴露** ⇒ **默认拒绝 + 审计**
    （``REQ-SEC-01``）；不要用注册表的全量名字做这个判定。
    """
    return frozenset(spec.name for spec in select_tools(specs, tier=tier, allowlist=allowlist))
