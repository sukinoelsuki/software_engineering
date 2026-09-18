"""模型客户端契约（L2 能力层）。

接口形态取自 ADR-0015 §5.1.2：``ModelClient.chat(messages, tools, ...) -> ModelResponse``；
并发假设为**非线程安全**（一个会话一个实例），资源生命周期为 ``close()`` 幂等。

**形状待澄清**：``ChatMessage`` / ``ModelResponse`` 的字段、``CapabilityTier`` 的成员，
以及 ``chat`` 在接口表里以 ``...`` 省略的其余参数，ADR-0015 均未规定 ⇒ 以占位形式落定，
实现者不得据此假设任何字段。完整定义属后续交付物 ``docs/design/interfaces/``。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from agent_sec_perf.contracts.tools import ToolSpec


class CapabilityTier(Enum):
    """能力档位。

    **成员待澄清**：ADR-0015 只给出类型名；具体档位（S/M/L 等）以 ADR-0010/0011 为准，
    在落到本类型之前不得假设任何成员。
    """


@dataclass(frozen=True)
class ChatMessage:
    """一次对话中的单条消息。**字段待澄清**（ADR-0015 未规定）。"""


@dataclass(frozen=True)
class ModelResponse:
    """一次模型补全的响应。**字段待澄清**（ADR-0015 未规定）。"""


class ModelClient(Protocol):
    """模型客户端的统一抽象（本地 / 云端两个实现共用）。

    Args:
        messages: 对话消息序列。
        tools: 本次暴露给模型的工具描述。

    并发假设：**非线程安全**；一个会话一个实例（llama-server 默认 ``-np 1``）。
    资源生命周期：:meth:`close` 幂等；进程型后端由 ``ExitStack`` 托管。
    """

    def chat(self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]) -> ModelResponse: ...

    def close(self) -> None: ...
