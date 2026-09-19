"""会话装配与生命周期（L3 编排层；``harness.md`` §3.1 的 ``session.py`` 段 / §2.8 / §2.9）。

本类做两件事，**都不产事件**：

1. **构造期装配**（顺序固定，见 :meth:`Session.__init__` 的四步）：先做 §2.8 的装配期校验
   （失败即**拒绝启动**，不回退默认值），再用 ``trimming`` 算出会话内**固定**的暴露集合、
   用 ``prompts`` 取出 SYSTEM 常量与领域包数据消息，最后把参数交给 :class:`TaskLoop`；
2. **生命周期**：``run(task)`` 只做**透传**；``close()`` 按序 teardown 且**幂等**。

**为什么装配期校验放在这里**：``SessionConfig`` 在契约层是零行为的
（``contracts/harness.py`` 不写 ``__post_init__``），因此"非法配置 ⇒ 拒绝启动"这条
**生产者义务**落在本模块。三条校验的取向都是 fail-secure：
空允许根集合等于"什么都不允许"、``max_steps=0`` 等于"一步都不许走"、
``inf`` 超时等于"没有超时"——把它们静默修正成默认值，就是把"配置错了"伪装成"配置是对的"。

**teardown 的诚实标注（缺口登记）**：契约 §2.9 写的顺序是
「工具 → 模型客户端 → ``llama-server`` 进程 → ``flush`` 审计」。其中
``Tool`` / ``ToolRegistry`` 两个 Protocol **都没有 ``close()``** ⇒ 「工具」一步**没有载体**；
``llama-server`` 进程由 ``ModelClient.close()`` 内部承担（其 docstring：进程型后端由
``ExitStack`` 托管）。因此本实现按**可观察的两步** teardown（``model.close()`` →
``sink.flush()``），并把这条缺口记在这里而不是假装四步齐备。
**审计 flush 用 ``finally`` 兜住**：即便模型客户端关闭失败，证据面也必须落盘。

依赖：``contracts`` + ``foundation``（``paths`` / ``errors``）+ ``harness`` 的
``errors`` / ``prompts`` / ``trimming`` / ``domain_pack`` / ``loop``（契约 §3.2 的 H1：
**不** import ``model`` / ``tools`` / ``security`` / ``observability`` / ``cli`` 的实现模块）。
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from pathlib import Path

from agent_sec_perf.contracts.audit import AuditSink
from agent_sec_perf.contracts.harness import (
    ApprovalGate,
    ArgumentValidator,
    SessionConfig,
    SessionEvent,
)
from agent_sec_perf.contracts.harness import Session as SessionContract
from agent_sec_perf.contracts.model import ChatMessage, ModelClient
from agent_sec_perf.contracts.policy import PolicyEngine
from agent_sec_perf.contracts.tools import ToolRegistry, ToolSpec
from agent_sec_perf.foundation.paths import resolve_within
from agent_sec_perf.harness import errors, prompts, trimming
from agent_sec_perf.harness.domain_pack import DomainPack
from agent_sec_perf.harness.loop import TaskLoop

__all__ = ["Session"]


class Session(SessionContract):
    """一次会话：装配、透传事件流、按序 teardown（契约 §3.1 / §2.9）。

    同一实例**允许多次 ``run``**（每次一个任务）；会话状态与 ``seq`` 跨 ``run`` 保留（``I9``，
    由 :class:`TaskLoop` 持有）。**单会话单线程**：事件流串行产出，不引入 ``asyncio``。

    错误面：``run()`` 内的业务失败一律经 ``ERROR`` / ``TASK_FINISHED`` 表达、**不抛异常**；
    逃逸的只有三类——装配期异常（本类的构造期）、**审计写入失败**、以及编程缺陷。
    """

    def __init__(
        self,
        *,
        session_id: str,
        config: SessionConfig,
        model: ModelClient,
        registry: ToolRegistry,
        policy: PolicyEngine,
        approval: ApprovalGate | None = None,
        sink: AuditSink,
        validator: ArgumentValidator,
        pack: DomainPack | None = None,
    ) -> None:
        """装配一个会话；**任何一步失败都拒绝启动**（不降级、不回退默认值）。

        构造期固定顺序（契约 §3.1「构造期的三件事 + 第 4 条」）：

        1. **装配期校验**（§2.8 的三条）：``working_dir`` 必须经
           :func:`foundation.paths.resolve_within` 落在 ``allowed_roots`` 内；数值参数必须
           合法。失败 ⇒ 抛异常，**不**继续。
        2. ``exposed = trimming.select_tools(...)``：档位能力预算 **∩** 领域包名字白名单
           （``allowlist`` **只能收窄**；无 pack 时取全部已注册名字）。会话内**固定**
           （``REQ-PERF-06``"运行中不调整"）。
        3. SYSTEM 常量与领域包数据消息：``pack`` 片段经 ``prompts.pack_context_message``
           装配成**一条 ``role=USER`` 的数据消息**，**永不进 SYSTEM 位置**；
           ``context.assemble`` 会以"``role is USER``"的结构性守卫兜住它（``S-new-6``）。
        4. 传进 :class:`TaskLoop` 的是 **``pack_name``（字符串）**，不是 pack 对象：
           ``H2`` 明令 ``loop`` 不得依赖 ``domain_pack``，数据以参数传入。

        Args:
            approval: 人工确认通路；``None`` ⇒ 需确认的调用**一律拒绝**（§2.5.5 的 ``R1``）。
                **不得**为图方便传一个恒放行的 gate。
            pack: 已由 ``load_pack`` 加载并校验的领域包；``None`` ⇒ 不启用领域包。

        Raises:
            ValueError: ``allowed_roots`` 为空 / 含非 ``Path``；``working_dir`` 非 ``Path``；
                ``max_steps`` / ``max_consecutive_failures`` 非正整数；
                ``tool_timeout_s`` / ``max_prompt_tokens`` 非有限正数。
            PathNotAllowedError: ``working_dir`` 不在 ``allowed_roots`` 内（含符号链接逃逸）。
            HarnessInternalError: ``prompts`` 的 SYSTEM 常量未写入 ``content``（不可达；
                出现即说明模板被改坏，按 fail-secure 拒绝启动而不是继续发空提示）。
        """
        _check_config(config)

        all_names = frozenset(spec.name for spec in registry.specs())
        allowlist = all_names if pack is None else pack.tool_allowlist
        exposed: tuple[ToolSpec, ...] = trimming.select_tools(
            registry.specs(), tier=config.capability_tier, allowlist=allowlist
        )

        system_message = prompts.build_system_message(config.capability_tier)
        system = system_message.content
        if system is None:
            msg = "内部错误：prompts.build_system_message 未写入 content"
            raise errors.HarnessInternalError(msg)

        pack_message = None
        if pack is not None:
            pack_message = prompts.pack_context_message(
                pack_name=pack.name, fragments=pack.prompt_fragments
            )
        data_context: tuple[ChatMessage, ...] = () if pack_message is None else (pack_message,)

        self._model = model
        self._sink = sink
        self._closed = False
        self._loop = TaskLoop(
            session_id=session_id,
            config=config,
            model=model,
            registry=registry,
            exposed=exposed,
            policy=policy,
            approval=approval,
            sink=sink,
            validator=validator,
            system=system,
            data_context=data_context,
            pack_name=None if pack is None else pack.name,
        )

    def run(self, task: str) -> Iterator[SessionEvent]:
        """把任务交给循环并返回事件流（**本类不产事件，只做透传**）。"""
        return self._loop.run(task)

    def close(self) -> None:
        """按序 teardown：``model.close()`` → ``sink.flush()``；**幂等**（可重复调用）。

        审计 ``flush`` 放在 ``finally`` 里：即便模型客户端关闭失败，证据面也**必须**落盘
        （``audit.md`` §2.4 的取向：审计是不可替代的证据面；静默丢事件即验收失败）。

        Raises:
            Exception: ``model.close()`` / ``sink.flush()`` 的异常**原样冒泡**——teardown
                失败不得被静默吞掉（否则资源泄漏与审计丢失都会变成无声事件）。
        """
        if self._closed:
            return
        # 先置位再执行：一次失败的 close 也**不**在后续调用里重试，
        # "幂等"说的是"重复调用不产生额外副作用"，不是"重试到成功"。
        self._closed = True
        try:
            self._model.close()
        finally:
            self._sink.flush()

    def __enter__(self) -> Session:
        return self

    def __exit__(self, *exc_info: object) -> None:
        """退出 ``with`` 块：按序 teardown（本类**不**抑制块内异常）。"""
        del exc_info
        self.close()


def _check_config(config: SessionConfig) -> None:
    """§2.8 的装配期校验（三条）；**失败即拒绝启动**，不回退默认值。

    只做形状与白名单校验，不做"猜一个合理值"的修正：把非法配置静默改成默认值，等于把
    "配置错了"伪装成"配置是对的"，调用方将永远看不到自己写错了什么。
    """
    if not all(isinstance(root, Path) for root in config.allowed_roots):
        msg = "allowed_roots 的每一项都必须是 Path（不得为 None 或字符串）"
        raise ValueError(msg)
    if not config.allowed_roots:
        msg = "allowed_roots 不得为空：空的允许根集合等于什么都不允许（拒绝启动）"
        raise ValueError(msg)
    # 取出后再判定：字段注记说的是"应该是什么"，挡不住鸭子类型调用方传进来的
    # `None` / 字符串（`SessionConfig` 在契约层是零行为的，没有 `__post_init__` 兜底）。
    working_dir: object = config.working_dir
    if not isinstance(working_dir, Path):
        msg = "working_dir 必须是 Path（不得为 None 或字符串）"
        raise ValueError(msg)
    # 生产者义务：`ExecutionContext` 的不变式"working_dir 落在 allowed_roots 之内"由此保证。
    resolve_within(working_dir, config.allowed_roots, what="会话工作目录")

    _require_positive_int(config.max_steps, name="max_steps")
    _require_positive_int(config.max_consecutive_failures, name="max_consecutive_failures")
    _require_finite_positive(config.tool_timeout_s, name="tool_timeout_s")
    _require_finite_positive(config.max_prompt_tokens, name="max_prompt_tokens")


def _require_positive_int(value: object, *, name: str) -> None:
    """要求正整数（``bool`` 不算整数：它是 ``int`` 的子类，但语义上是开关）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        msg = f"{name} 必须是整数，收到 {type(value).__name__}"
        raise ValueError(msg)
    if value <= 0:
        msg = f"{name} 必须为正整数，收到 {value}"
        raise ValueError(msg)


def _require_finite_positive(value: object, *, name: str) -> None:
    """要求**有限正数**：``None`` / ``nan`` / ``inf`` / 非正数一律拒绝。

    ``inf`` 尤其要拦：它不是"很大的超时"，而是"**没有**超时"，会让一次挂起的调用永久
    占住会话（``model/client.py`` 对 ``timeout_s`` 的同一取向）。
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        msg = f"{name} 必须是数值，收到 {type(value).__name__}"
        raise ValueError(msg)
    if not math.isfinite(value) or value <= 0:
        msg = f"{name} 必须是有限正数（None / nan / inf / 非正数一律拒绝），收到 {value!r}"
        raise ValueError(msg)
