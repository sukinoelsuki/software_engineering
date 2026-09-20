"""上下文装配（``REQ-HARNESS-05`` 的上下文侧；L3 编排层）。

契约：``docs/design/interfaces/harness.md`` §3.1（``ContextBudget`` / ``assemble``）
与 §4.4 第 2 条（领域包片段**永不进 SYSTEM 位置**）。
本模块是**纯函数**模块：不读时钟、不读环境、不写文件、只依赖 ``contracts``，
因此同一输入必得同一输出，可被单测钉住。

装配顺序（契约 §3.1 写死）：

    [SYSTEM] + data_context + [USER(task)] + history

**结构性守卫（本模块最重要的一条）**：``data_context`` 的每一条消息**必须**是
``role is USER``，否则 :class:`ValueError`。这不是"调用方记得不要拼系统提示"的**纪律**，
而是"包内容永不进 SYSTEM 位置"的**结构性质**（契约 §4.4 第 2 条，对抗性验收 ``S-new-6``）：
领域包片段只经 ``prompts.pack_context_message`` 装配成 ``role=USER`` 的数据消息，
本函数只接受数据位消息；指令位（SYSTEM）**只**由 ``system`` 形参这一条产生。
把"不可信内容不进指令位"从纪律变成签名性质，正是 ``prompts`` 拒收外部内容参数的同一取向。

**预算口径（诚实标注：这是基线实现，不是最终效率引擎）**：契约 §1 明确把
"检索 / 压缩算法"排除在本轮范围之外，只把两件事写死——装配顺序，以及超预算时
**不得切断** ``ASSISTANT(tool_calls)`` 与 ``TOOL(tool_call_id)`` 的配对（验收 ``H-6``）。
因此这里采用一条**最小、确定性**的策略：

* ``SYSTEM`` / ``data_context`` / 任务消息**永不**被丢弃（它们是本次任务不可省的载荷）；
* 历史从**最旧**一端按"**原子组**"整体丢弃，直到装入预算 —— 一个原子组是
  "一条 ``ASSISTANT`` 及其 ``tool_calls`` 对应的全部 ``TOOL`` 回执"；
  组内要么全留、要么全丢 ⇒ 配对永不被切断（``model.md`` §2.1 的回指不变式）。

**为什么按字符数估 token 而不引入分词器**：``REQ-PERF-*`` 的度量机制与分词器依赖
均不在本轮范围（契约 §1「本轮范围声明」）。字符数是 token 的**保守上界**
（英文约 4 字符/token，中文约 1 字符/token）⇒ **高估** ⇒ 更早触发裁剪 ⇒
fail-secure（宁可少给上下文，也不冒超出模型上下文被服务端截断的风险）。
真实分词口径随性能工作另行落地，届时只替换 :func:`_estimate_message_tokens`。

依赖：只依赖 ``contracts``（契约 §3.1 的 ``context`` 段）。不 import ``foundation``、
不 import 任何 L2/L4 实现、不与其它 harness 叶子模块互相 import（契约 §3.2 的 H1/H2）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from agent_sec_perf.contracts.model import ChatMessage, Role

__all__ = ["ContextBudget", "assemble"]

#: 每条消息的固定结构开销（角色包装与分隔符）。与下面按字符估算的正文相加，
#: 构成该消息的 token **上界**估计（口径见模块 docstring）。
_PER_MESSAGE_OVERHEAD_TOKENS = 4


@dataclass(frozen=True)
class ContextBudget:
    """提示侧的 token 预算（契约 §3.1）。

    ``reserve_tokens`` 是给**完成**（模型生成）预留的量：可用提示预算 =
    ``max_tokens - reserve_tokens``。

    **校验失败即拒绝**（``__post_init__``）：非法预算只可能来自调用点的缺陷，
    静默回退默认值等于把"预算算错了"伪装成"预算是对的"（fail-secure）。
    """

    max_tokens: int
    reserve_tokens: int = 1024

    def __post_init__(self) -> None:
        max_tokens = _require_int(self.max_tokens, name="max_tokens")
        reserve_tokens = _require_int(self.reserve_tokens, name="reserve_tokens")
        if max_tokens <= 0:
            msg = f"max_tokens 必须为正数，收到 {max_tokens}"
            raise ValueError(msg)
        if reserve_tokens < 0:
            msg = f"reserve_tokens 不得为负，收到 {reserve_tokens}"
            raise ValueError(msg)
        if reserve_tokens >= max_tokens:
            msg = (
                f"reserve_tokens({reserve_tokens}) 必须小于 max_tokens({max_tokens})："
                "否则可用提示预算非正，等于没有预算"
            )
            raise ValueError(msg)


def assemble(
    *,
    system: str,
    task: str,
    history: Sequence[ChatMessage],
    budget: ContextBudget,
    data_context: Sequence[ChatMessage] = (),
) -> tuple[ChatMessage, ...]:
    """装配消息序列（契约 §3.1）：``[SYSTEM] + data_context + [USER(task)] + history``。

    Args:
        system: 系统提示原文。它来自 ``prompts`` 的常量模板，是**唯一可信的指令位**。
            本形参是字符串而非消息对象：指令位的消息由本函数构造，调用方无法把
            一条外来角色的消息塞进指令位。
        task: 用户任务原文（**数据**，进入 ``USER`` 位）。
        history: 既往对话（通常包含 ``ASSISTANT(tool_calls)`` 与 ``TOOL`` 回执）。
            超出预算时从**最旧**一端按原子组整体丢弃（见模块 docstring）。
        budget: 提示预算；可用量 = ``max_tokens - reserve_tokens``。
        data_context: "以**数据**身份进入上下文的补充消息"（本轮只有领域包片段一条）。
            **每一条都必须是 ``role is USER``**，否则 :class:`ValueError` —— 这是
            "包内容永不进 SYSTEM 位置"的结构性守卫（契约 §4.4 第 2 条）。

    Returns:
        消息元组，顺序固定为 ``SYSTEM → data_context → USER(task) → 历史后缀``。

    Raises:
        ValueError: ``data_context`` 里出现非 ``USER`` 角色的消息；或
            ``SYSTEM`` / 数据段 / 任务消息本身就超出可用预算（此时**不**截断它们，
            也**不**静默超预算 —— 两者都会把"配置过小"伪装成"装配成功"）。

    本函数**不修改任何入参**（不就地改写 ``history``，不改写消息对象）。
    """
    for message in data_context:
        if message.role is not Role.USER:
            msg = (
                "data_context 只接受 role=USER 的数据消息：领域包片段永不进入 SYSTEM 位置"
                f"（收到 role={message.role.value}）。"
            )
            raise ValueError(msg)

    system_message = ChatMessage(role=Role.SYSTEM, content=system)
    task_message = ChatMessage(role=Role.USER, content=task)
    essential: tuple[ChatMessage, ...] = (system_message, *data_context, task_message)

    usable_tokens = budget.max_tokens - budget.reserve_tokens
    essential_tokens = _estimate_messages_tokens(essential)
    if essential_tokens > usable_tokens:
        msg = (
            "提示预算不足：SYSTEM / 数据段 / 任务消息本身已超出可用预算"
            f"（需要 {essential_tokens}，可用 {usable_tokens}）。"
            "不得截断 SYSTEM 与任务消息，也不得静默超出预算。"
        )
        raise ValueError(msg)

    kept_history = _fit_history(history, remaining_tokens=usable_tokens - essential_tokens)
    return (*essential, *kept_history)


def _fit_history(
    history: Sequence[ChatMessage], *, remaining_tokens: int
) -> tuple[ChatMessage, ...]:
    """从最旧一端按原子组整体丢弃历史，直到装入 ``remaining_tokens``。

    保留的是历史的**连续后缀**（丢弃的一定是最旧的一段）：出现"中间挖空"会让模型
    看到断裂的对话，比单纯少给历史更糟。若一次组都装不下，则返回空元组
    （``SYSTEM`` / 数据段 / 任务消息仍由调用方保留）。
    """
    groups = _group_history(history)
    total_tokens = sum(_estimate_messages_tokens(group) for group in groups)
    first_kept = 0
    while first_kept < len(groups) and total_tokens > remaining_tokens:
        total_tokens -= _estimate_messages_tokens(groups[first_kept])
        first_kept += 1
    return tuple(message for group in groups[first_kept:] for message in group)


def _group_history(history: Sequence[ChatMessage]) -> list[list[ChatMessage]]:
    """把历史切成**原子组**：一条 ``ASSISTANT`` 及其 ``tool_calls`` 的全部 ``TOOL`` 回执。

    非 ``TOOL`` 消息各自起一组；紧随其后的 ``TOOL`` 消息若其 ``tool_call_id`` 落在
    该 ``ASSISTANT`` 的 ``tool_calls``     里，则并入同组。孤立 ``TOOL`` 消息（找不到父）
    自成一组 —— 它对配对不变式没有贡献，丢弃它不会切断任何配对。
    """
    groups: list[list[ChatMessage]] = []
    index = 0
    while index < len(history):
        message = history[index]
        group = [message]
        index += 1
        if message.role is Role.ASSISTANT and message.tool_calls:
            expected = {call.call_id for call in message.tool_calls}
            while (
                index < len(history)
                and history[index].role is Role.TOOL
                and history[index].tool_call_id in expected
            ):
                group.append(history[index])
                index += 1
        groups.append(group)
    return groups


def _estimate_messages_tokens(messages: Sequence[ChatMessage]) -> int:
    """一组消息的 token 上界估计。"""
    return sum(_estimate_message_tokens(message) for message in messages)


def _estimate_message_tokens(message: ChatMessage) -> int:
    """单条消息的 token **上界**估计（字符数，见模块 docstring 第 4 段）。

    计入正文、角色名、``tool_call_id`` 与每个 ``tool_call`` 的
    ``call_id`` / ``name`` / ``arguments_json`` —— 后三者是回指与配对键，
    漏计会让估计变成下界，而**下界会导致超预算放行**。
    """
    total = _PER_MESSAGE_OVERHEAD_TOKENS + len(message.role.value)
    if message.content is not None:
        total += len(message.content)
    if message.tool_call_id is not None:
        total += len(message.tool_call_id)
    for call in message.tool_calls:
        total += len(call.call_id) + len(call.name) + len(call.arguments_json)
    return total


def _require_int(value: object, *, name: str) -> int:
    """要求 ``value`` 是**真正的**整数（``bool`` 不算，它是 ``int`` 的子类）。

    用 ``object`` 形参而不是直接在 ``__post_init__`` 里判定：这样类型检查器与运行时
    守卫说的是同一件事，而不是靠"字段注记是 ``int``"来免除运行时检查
    （来自配置文件 / 反序列化的值不受注记约束）。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{name} 必须是整数，收到 {type(value).__name__}"
        raise ValueError(msg)
    return value
