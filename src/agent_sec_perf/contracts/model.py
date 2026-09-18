"""模型客户端契约（L2 能力层）。

字段级定义见 ``docs/design/interfaces/model.md``（本模块是它的唯一实现）；
边界处签名与语义的上游决策见 ADR-0015 §5.1.2。
``ModelUnavailableError`` / ``ModelProtocolError`` 归属 ``foundation/errors.py``
（单一异常层次，Q4），不在本零行为契约层。
本模块只依赖标准库与 ``contracts`` 内部模块（ADR-0015 §7.1 R3）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from agent_sec_perf.contracts.tools import ToolCallRequest, ToolSpec


class Role(StrEnum):
    """消息来源；决定**信任**与装配位置。

    ``SYSTEM`` 是我方生成的系统指令（可信）；``USER`` / ``ASSISTANT`` / ``TOOL``
    **一律不可信**，只能作为**数据**进入上下文（``REQ-SEC-03``）。
    """

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True)
class ChatMessage:
    """一次对话中的单条消息（**不设** ``trusted`` 字段，信任由装配层按 ``role`` 判定）。

    不变式（由生产者 / HARNESS 保证）：``role in {SYSTEM, USER}`` ⇒ ``tool_calls == ()``
    且 ``tool_call_id is None``；``role == TOOL`` ⇒ ``tool_call_id`` 非 ``None``；
    ``role == ASSISTANT`` ⇒ ``content`` 与 ``tool_calls`` 至少一个非空。
    """

    role: Role
    content: str | None = None
    tool_calls: tuple[ToolCallRequest, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True)
class TokenUsage:
    """一次模型调用的 token 计量（服务端 ``usage`` 回执，``REQ-PERF-02`` 的可信口径）。"""

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class CapabilityTier(StrEnum):
    """**模型能力**档位；与**硬件档位** S/M/L 正交，二者不可互相推导、不得混用。

    【待定】档数与成员名尚未决策（权威来源 SRS ``Q-3``、验收见 ``REQ-MODEL-06``）；
    以下成员为**占位**，名称可在决策后经 ADR 变更。**在决策前任何代码不得把
    ``CapabilityTier`` 与 S/M/L 硬件档位相互转换。**
    见 ``docs/design/interfaces/model.md`` §2.3 与 ``docs/design/interfaces/README.md`` §6 的 U1。
    """

    BASIC = "basic"
    STANDARD = "standard"
    ADVANCED = "advanced"


class HardwareTier(StrEnum):
    """**硬件档位**（可用硬件预算的分级）。

    成员与语义由 ``ADR-0011 §5.1`` 定义（**非本契约新造**）；``REQ-PERF-06`` 要求会话启动时
    探测一次并映射到固定 3 档预设配置（决定模型规模与量化档、上下文长度、线程数与批量）。

    与 :class:`CapabilityTier`（**模型能力**档位）是**两条正交的轴**
    （``ADR-0010 §5.2`` / ``SRS §14``）：**同一模型换到更强硬件，能力档位不变，但配置必须变**。

    **硬禁令**：任何代码不得把 ``HardwareTier`` 与 ``CapabilityTier`` 相互转换；
    ``S`` / ``M`` / ``L`` **只属于**本类型，``CapabilityTier`` 的成员**不得**取名为它们。
    """

    S = "s"  # ≤ 4 GiB：移动端代表性验证（MiniCPM5-2B-Q4_K_M）
    M = "m"  # 8 GiB：目标设备下限 / 通用基线（Qwen3-4B-Q4_K_M）
    L = "l"  # 16 GiB：笔记本 / 个人 PC（Qwen3-8B-Q4_K_M）


class FinishReason(StrEnum):
    """生成终止原因。

    fail-safe 取向：无法识别时落 ``UNKNOWN``，**不得**默认成 ``STOP``（否则截断会被当成成功）。
    """

    STOP = "stop"
    LENGTH = "length"
    TOOL_CALLS = "tool_calls"
    CONTENT_FILTER = "content_filter"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ModelResponse:
    """一次模型补全的响应。

    不变式：``content`` 与 ``tool_calls`` **至少一个非空**（否则响应不符合协议 ⇒
    ``ModelProtocolError``）；``finish_reason == TOOL_CALLS`` ⇒ ``tool_calls`` 非空；
    ``tool_calls`` 内的 ``arguments_json`` 仍是**原始 JSON 文本**，解析与校验**只能**
    发生在 HARNESS 侧的信任边界。
    """

    content: str | None
    tool_calls: tuple[ToolCallRequest, ...]
    finish_reason: FinishReason
    usage: TokenUsage
    model_id: str


class ModelClient(Protocol):
    """模型客户端的统一抽象（本地 ``llama-server`` 与云端 OpenAI 兼容端点共用）。

    并发假设：**非线程安全**；一个会话一个实例（``llama-server`` 默认 ``-np 1``）。
    资源生命周期：:meth:`close` 幂等；进程型后端由 ``ExitStack`` 托管。
    错误语义：抛 ``ModelUnavailableError``（路由降级）/ ``ModelProtocolError``（重试一次后
    回喂）；``chat`` 内**不得**吞掉异常后返回空响应。``tools=None`` 表示**不暴露任何工具**
    （default-deny）。
    """

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        timeout_s: float = 60.0,
    ) -> ModelResponse: ...

    def close(self) -> None: ...
