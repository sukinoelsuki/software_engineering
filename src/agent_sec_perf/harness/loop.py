"""单任务 ReAct 循环与工具调用决策序列的**唯一持有者**（L3 编排层）。

契约：``docs/design/interfaces/harness.md``

* §3.3 六步决策序列（每步入参 / 出参写死；``S1`` 的验收对象）；
* §2.2 的 ``I1``~``I10``（``SessionEvent`` 不变式，由本模块**生产**——含 ``seq`` / ``timestamp``）；
* §2.3 / §2.4（终态与错误分级）、§2.5.5（审批通路的 fail-secure 六条）、§2.5.3（``arguments_summary``）。

**本模块是本轮最易实现错的地方**，故把四条判定写在这里，而不是留给读者推断：

1. **"拒绝 ≠ 失败"**：未执行 ⇒ ``TOOL_RESULT(result=None)`` + 中文 ``text`` + 一条
   ``TOOL_CALL/DENY`` 审计；已执行但失败 ⇒ ``result.ok is False``。两者在事件与审计里
   **不同形**（``architecture.md`` §5.3 硬规定 2）。
2. **``denied_reason`` 必须可分**：``not_exposed``（**我们把它裁掉了**）与 ``unknown_tool``
   （**模型幻觉了一个工具**）分开，判定点见 :meth:`TaskLoop._handle_tool_call` 的步 1。
3. **``ERROR.text`` 不取 ``str(exc)``**：异常消息可能内嵌路径 / 不可信串。文案 = 我方常量 +
   **异常类型名**（``harness.md`` §2.4）。
4. **回喂内容按来源分型**：工具成功 / 失败回喂 ``result.content`` / ``result.error``；
   被拒路径回喂**我方生成的中文说明**——拒绝路径上**不构造** ``ToolResult``。

**两处刻意的不对称（写清理由，避免后人改回）**：

* **异常终止路径不补 ``TOOL_RESULT``**：步 4 的 ``R3``/``R4``（审批通路故障 / 应答形状非法）、
  步 1 的"名字在 ``exposed`` 内却 ``resolve`` 不到"与"工具未给审计关联键"会直接以
  ``ERROR(INTERNAL)`` + ``TASK_FINISHED(FAILED)`` 收尾，此时该 ``call_id`` 上 ``I1`` 的配对
  **不成立**。之所以不补：步 6b 的 ``denied_reason`` 被契约 §2.7 的 ``D2`` 限定为**闭集**，
  其中**没有**"审批通路故障"这一档；补一条 ``TOOL_RESULT`` 就必然要么违反闭集、要么借用
  ``approval_denied`` 把"基础设施故障"与"人拒绝了"在审计里弄成同形——而 ``R3`` 明令
  **不得**做后者；"审计关联键缺失"那条更是**根本无从**构造 ``I3`` 要求的非空 ``audit_id``。
  ⇒ 契约 §2.2 的 ``I1``（第七版）已就此开出**可判定的例外**：悬空 ``TOOL_CALL``
  **只允许**出现在"以 ``FAILED`` 终止**且**流中至少一条 ``ERROR(error_kind=INTERNAL)``"的流里；
  任何 ``status is COMPLETED`` / ``LIMIT_REACHED`` 的流**必须**逐条配对。
  **该判据由 ``H-1`` 断言**：``tests/unit/test_harness_loop.py`` 的 ``_assert_invariants``
  **从流本身**判定（**不接受**调用方传入"本场景可以悬空"的开关），并有一条元测试证明它真的会触发。
* **``ErrorDisposition.FEEDBACK`` 只在工具路径落地**：契约 §2.4 的表把 ``PROTOCOL`` 定为
  **终止**，而"模型响应不合契约"没有回喂载体（``SessionEvent`` 只有 7 种 kind，
  ``ChatMessage`` 也没有"协议错误"位）。故模型调用路径上：``RETRY`` ⇒ 原地重试，
  其余 ⇒ 终止。工具路径的"回喂"由 ``ChatMessage(role=TOOL, tool_call_id=...)`` 承担。

**``T6`` 已裁决（2026-09-19）：``ToolRegistry.specs()`` 是**全量注册集（未裁剪）****。
本模块据此实现步 1 的"两短码"判定——它就是 ``specs()`` 与 ``exposed`` 的**差集**：
在 ``specs()`` 里但不在 ``exposed`` 里 ⇒ ``not_exposed``（**我们把它裁掉了**），
两边都没有 ⇒ ``unknown_tool``（**模型幻觉**）。
裁决依据与被否决的替代方案见 ``docs/design/interfaces/harness.md`` §8 的 ``T6`` 行；
三处口径（``tools.md`` §2.6、``contracts/tools.py`` 的 docstring、本模块）**已一致**。
反之若 ``specs()`` 返回"已裁剪"集合，``not_exposed`` 这一档就永远不可达、契约 §6.2 的
``S1-b`` 第②问会退化成**空断言** ⇒ 这条口径**不得**被静默改动。

**重试与步数**：``max_steps`` 计的是**模型往返**（ReAct 的"步"）。一次瞬时故障的重试
**不**新开一步——它仍在同一步内，重试次数由 ``errors.MAX_TRANSIENT_RETRIES`` 独立兜住，
故模型调用总数不超过 ``max_steps`` 的 ``(1 + MAX_TRANSIENT_RETRIES)`` 倍。

**异常面的边界**：本模块的每个 ``try`` 都**不**包住 ``sink.emit``——审计是证据面，
其失败必须原样冒泡（``audit.md`` §2.4）；把它转成一条业务事件等于把"审计基础设施故障"
伪装成"任务失败"（``AUDIT_FAILED`` 是契约 §2.1 **被否决**的 kind）。

依赖：只依赖 ``contracts``、``foundation``（异常 / 展示净化）与 ``harness`` 的
``context`` / ``errors`` 两个兄弟模块（契约 §3.2 的 H1/H2）。**不** import ``session`` /
``domain_pack``——领域包只以 ``pack_name`` 参数到达。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from agent_sec_perf.contracts.audit import AuditEvent, AuditEventKind, AuditOutcome, AuditSink
from agent_sec_perf.contracts.harness import (
    ApprovalGate,
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalResult,
    ArgumentValidator,
    SessionConfig,
    SessionErrorKind,
    SessionEvent,
    SessionEventKind,
    TaskStatus,
)
from agent_sec_perf.contracts.model import ChatMessage, ModelClient, ModelResponse, Role
from agent_sec_perf.contracts.policy import PolicyDecision, PolicyEngine, PolicyRequest
from agent_sec_perf.contracts.tools import (
    ExecutionContext,
    Tool,
    ToolCallRequest,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)
from agent_sec_perf.foundation.errors import AuditWriteError, ToolArgumentsInvalidError
from agent_sec_perf.foundation.logging import sanitize_for_display
from agent_sec_perf.harness import errors
from agent_sec_perf.harness.context import ContextBudget, assemble

__all__ = ["TaskLoop", "summarize_arguments"]

# ---------------------------------------------------------------------------
# 审计短码（契约 §2.7 的 ``D2``：闭集，均为我方生成的定长短码，不含不可信内容）
# ---------------------------------------------------------------------------

DENIED_UNKNOWN_TOOL: Final = "unknown_tool"
DENIED_NOT_EXPOSED: Final = "not_exposed"
DENIED_INVALID_ARGUMENTS: Final = "invalid_arguments"
DENIED_POLICY_DENIED: Final = "policy_denied"
DENIED_APPROVAL_DENIED: Final = "approval_denied"

#: 未执行路径的**我方中文说明**（既是事件的 ``text``，也是回喂模型的观察内容）。
#: 刻意不含任何工具 / 模型 / 参数原文（``I8``）。
_DENIED_TEXTS: Final[Mapping[str, str]] = {
    DENIED_UNKNOWN_TOOL: "工具不存在：模型请求了一个未注册的工具名，本次调用未执行",
    DENIED_NOT_EXPOSED: "工具未暴露给本次会话（被档位裁剪或不在领域包白名单内），本次调用未执行",
    DENIED_INVALID_ARGUMENTS: "参数校验未通过，本次调用未执行",
    DENIED_POLICY_DENIED: "策略拒绝，本次调用未执行",
    DENIED_APPROVAL_DENIED: "人工确认未通过（或未提供确认通路，按默认拒绝处置），本次调用未执行",
}

#: 校验器给出的中文说明在回喂前的展示上限（它可能含被截断的键名，属**不可信输入**的回显面）。
_VALIDATOR_DETAIL_LIMIT: Final = 200

#: ``arguments_summary`` 的口径常量（契约 §2.5.3 的 ``S2``）。
_SUMMARY_MAX_KEYS: Final = 8
_SUMMARY_KEY_LIMIT: Final = 64
_SUMMARY_VALUE_LIMIT: Final = 80
_SUMMARY_MAX_LENGTH: Final = 400
_SUMMARY_TRUNCATED: Final = "…（已截断）"
_SUMMARY_MORE: Final = "; …（还有 {remaining} 项）"

_TEXT_COMPLETED: Final = "模型给出最终回复（未请求工具调用），任务结束"
_TEXT_LIMIT_REACHED: Final = "已达单次任务的模型往返上限（max_steps={max_steps}），任务停止"
_TEXT_STALLED: Final = (
    "连续失败次数达上限（max_consecutive_failures={max_failures}）：工具既未执行也未成功，任务终止"
)
_TEXT_BROKEN_INVARIANT: Final = "内部不变量被破：工具名在暴露集合内却无法从注册表解析，任务终止"
_TEXT_VALIDATOR_FAILED: Final = "参数校验器抛出了非契约异常，无法判定参数可信性，任务终止"
_TEXT_APPROVAL_PATH_FAILED: Final = "人工确认通路故障：权限判定的输入面不可用，任务终止"
_TEXT_APPROVAL_SHAPE_INVALID: Final = "人工确认通路返回的应答形状非法：允许与拒绝都不可信，任务终止"
_TEXT_MODEL_FAILED: Final = "模型调用失败（{error_type}），任务终止"
_TEXT_AUDIT_ID_MISSING: Final = "工具结果缺少审计关联键（audit_id），该次调用无法回放，任务终止"


def summarize_arguments(args: Mapping[str, object]) -> str | None:
    """生成确认界面用的**参数摘要**（契约 §2.5.3 的 ``S2``；**只供展示**）。

    Args:
        args: **已通过校验**的结构化参数（不是原始 ``arguments_json``）。

    Returns:
        按**键名升序**遍历顶层键生成的摘要；参数映射为空 ⇒ ``None``
        （``S5``：不编造 ``{}`` 一类占位）。

    口径（确定性、可测）：``str`` 值 ⇒ ``key=<经 sanitize_for_display 的值>``；
    ``list``/``tuple`` ⇒ ``key=<list: N>``；``Mapping`` ⇒ ``key=<object: N>``；
    ``None``/``bool``/``int``/``float`` ⇒ ``<null>``/``<bool>``/``<int>``/``<float>``
    （**不回显标量取值**）。最多 ``8`` 个键，超出时末尾追加 ``; …（还有 N 项）``；
    **总长上限 400 字符**（超出即截断并加 ``…（已截断）``，连同标记一起计入上限）。

    ``S3``/``S4`` 的硬规定：**只供展示**——任何控制流、权限判定、工具选择都不得读它；
    也不得把它当成"已看到全部参数"的凭据（非标量值的形态由 ``<list: 3>`` 一类标注**显式**给出）。
    """
    items = sorted(args.items(), key=lambda item: str(item[0]))
    if not items:
        return None

    parts = [
        f"{sanitize_for_display(str(key), limit=_SUMMARY_KEY_LIMIT)}={_value_label(value)}"
        for key, value in items[:_SUMMARY_MAX_KEYS]
    ]
    remaining = len(items) - _SUMMARY_MAX_KEYS
    body = "; ".join(parts)
    if remaining > 0:
        body = f"{body}{_SUMMARY_MORE.format(remaining=remaining)}"

    if len(body) > _SUMMARY_MAX_LENGTH:
        return f"{body[: _SUMMARY_MAX_LENGTH - len(_SUMMARY_TRUNCATED)]}{_SUMMARY_TRUNCATED}"
    return body


def _value_label(value: object) -> str:
    """单个参数值的**展示标签**（``S2``；非标量只给形态与元素个数，不静默略去）。"""
    if value is None:
        return "<null>"
    if isinstance(value, bool):
        return "<bool>"
    if isinstance(value, str):
        return sanitize_for_display(value, limit=_SUMMARY_VALUE_LIMIT)
    if isinstance(value, list | tuple):
        return f"<list: {len(value)}>"
    if isinstance(value, Mapping):
        return f"<object: {len(value)}>"
    if isinstance(value, int):
        return "<int>"
    if isinstance(value, float):
        return "<float>"
    # 校验器只放行 JSON 标量 / 数组 / 对象；走到这里说明上游破约 ⇒ 只报类型名，不回显内容。
    return f"<{type(value).__name__}>"


def _approval_shape_problem(result: object) -> str | None:
    """确认应答的形状校验（契约 §2.5.5 的 ``R4``）。

    Returns:
        合法返回 ``None``；否则返回一个**定长短码**（我方生成，不含不可信内容），
        供事件文案里区分具体是哪种形状问题。
    """
    if not isinstance(result, ApprovalResult):
        return "not_approval_result"
    # 逐字段取出后再判定：`ApprovalResult` 的字段注记说的是"应该是什么"，挡不住
    # 鸭子类型的调用方传进来的裸 `str`（`StrEnum` 成员与其 `str` 值相等，故必须**类型检查**）。
    outcome: object = result.outcome
    if not isinstance(outcome, ApprovalOutcome):
        return "unknown_outcome"
    audit_id: object = result.audit_id
    if not isinstance(audit_id, str) or not audit_id:
        return "empty_audit_id"
    return None


def _now_iso() -> str:
    """带时区的 ISO-8601 时间戳（与 ``AuditEvent`` / ``PolicyDecision`` 同口径）。"""
    return datetime.now(tz=UTC).isoformat()


@dataclass(frozen=True)
class _ModelStep:
    """一次"模型往返"的结果。

    ``response is None`` ⇔ 本步失败并且**应当终止**；此时 ``events`` 末尾已含一条
    ``TASK_FINISHED``（以及在此之前的一条或多条 ``ERROR``）。
    """

    response: ModelResponse | None
    events: tuple[SessionEvent, ...]


@dataclass(frozen=True)
class _ApprovalVerdict:
    """确认通路一次尝试的结果（契约 §3.3 的步 4）。

    ``terminal`` 非空 ⇔ ``R3``/``R4`` 命中，流程应当终止（其中已含 ``ERROR`` +
    ``TASK_FINISHED``）；否则 ``approval_event`` 是待产出的 ``APPROVAL_RESULT``。
    """

    approval_event: SessionEvent | None
    outcome: ApprovalOutcome | None
    terminal: tuple[SessionEvent, ...]


@dataclass(frozen=True)
class _ToolCallOutcome:
    """一次工具调用（步 0~6b）的事件与观察内容。

    ``observation is None`` ⇔ 本次调用**未执行**且流程应当终止；此时 ``events`` 末尾含一条
    ``TASK_FINISHED``（见模块 docstring 的"异常终止路径不补 ``TOOL_RESULT``"）。
    """

    events: tuple[SessionEvent, ...]
    observation: str | None
    failed: bool


class TaskLoop:
    """单任务 ReAct 循环；**事件流的唯一生产者**（含 ``seq`` / ``timestamp``）。

    生命周期：同一实例可多次 :meth:`run`；``seq`` 与消息历史跨 ``run`` 保留（``I9``）。
    并发：**单会话单线程**、事件流串行产出，``seq`` 的分配无需锁（契约 §2.9）。

    错误面：``run()`` 内的业务失败一律经 ``ERROR`` / ``TASK_FINISHED`` 表达；
    **审计写入失败**（``sink.emit`` 的异常）原样冒泡。
    """

    def __init__(
        self,
        *,
        session_id: str,
        config: SessionConfig,
        model: ModelClient,
        registry: ToolRegistry,
        exposed: tuple[ToolSpec, ...],
        policy: PolicyEngine,
        approval: ApprovalGate | None = None,
        sink: AuditSink,
        validator: ArgumentValidator,
        system: str,
        data_context: tuple[ChatMessage, ...] = (),
        pack_name: str | None = None,
    ) -> None:
        """注入本循环的全部外部依赖（契约 §3.1）。

        Args:
            registry: 工具注册表。步 1 用它区分"**被裁剪**"（``not_exposed``）与
                "**模型幻觉**"（``unknown_tool``），步 5 用它解析可执行句柄。以**构造注入的
                Protocol** 到达（``H1``）：本模块不 import ``tools/`` 的实现模块。
                ``specs()`` **已裁决**为返回**全量注册集（``T6``，2026-09-19）**——否则
                ``not_exposed`` 这一档永远不可达；依据与联动见模块 docstring 的 ``T6`` 段。
            exposed: 由 ``session`` 用 ``trimming.select_tools`` 算好的暴露集合
                （会话内固定，契约 §3.1 第 3 条）。
            system: 系统提示原文（**唯一可信的指令位**），由 ``session`` 从 ``prompts`` 的常量
                模板取出；本模块把它交给 ``context.assemble``，**不改写**它。
            data_context: 以**数据**身份进入上下文的补充消息（本轮只有领域包片段一条）。
                ``context.assemble`` 会断言它们全部是 ``role=USER``。
            pack_name: 领域包标识，只用于 ``PolicyRequest.domain_pack``（**只传名字**：
                ``H2`` 禁止 ``loop`` 依赖 ``domain_pack``，数据以参数传入）。
        """
        self._session_id = session_id
        self._config = config
        self._model = model
        self._registry = registry
        self._exposed: tuple[ToolSpec, ...] = tuple(exposed)
        self._policy = policy
        self._approval = approval
        self._sink = sink
        self._validator = validator
        self._system = system
        self._data_context: tuple[ChatMessage, ...] = tuple(data_context)
        self._pack_name = pack_name

        # 暴露集合与注册表全名集在构造期定形：会话期间两者都不变（契约 §3.1 / `tools.md` §2.6）。
        self._exposed_by_name: Mapping[str, ToolSpec] = {spec.name: spec for spec in self._exposed}
        self._registered_names: frozenset[str] = frozenset(
            spec.name for spec in self._registry.specs()
        )

        self._seq = 0
        self._history: tuple[ChatMessage, ...] = ()

    def run(self, task: str) -> Iterator[SessionEvent]:
        """执行一个任务并产出事件流（同步迭代器；契约 §2.9）。

        Yields:
            ``SessionEvent``：以**恰好一条** ``TASK_FINISHED`` 结尾（``I6``）；``seq`` 自会话
            起点连续无空洞（``I9``）。

        Raises:
            Exception: ``AuditSink.emit`` 的异常**原样冒泡**——审计是证据面，失败不得降级为
                一条业务事件（``AUDIT_FAILED`` 是契约 §2.1 **被否决**的 kind）。
        """
        config = self._config
        history: list[ChatMessage] = list(self._history)
        steps = 0
        consecutive_failures = 0

        while True:
            if steps >= config.max_steps:
                yield self._event(
                    SessionEventKind.TASK_FINISHED,
                    status=TaskStatus.LIMIT_REACHED,
                    text=_TEXT_LIMIT_REACHED.format(max_steps=config.max_steps),
                )
                return

            step = self._model_step(task, tuple(history))
            yield from step.events
            response = step.response
            if response is None:
                return
            steps += 1

            yield self._event(SessionEventKind.MODEL_RESPONSE, response=response)
            history.append(
                ChatMessage(
                    role=Role.ASSISTANT,
                    content=response.content,
                    tool_calls=response.tool_calls,
                )
            )

            # 无工具调用 ⇒ 模型给出最终回复（契约 §2.3 的 ``COMPLETED``）。
            if not response.tool_calls:
                self._history = tuple(history)
                yield self._event(
                    SessionEventKind.TASK_FINISHED,
                    status=TaskStatus.COMPLETED,
                    text=_TEXT_COMPLETED,
                )
                return

            # 同一 ``MODEL_RESPONSE`` 的多个 ``tool_calls`` **按 tuple 顺序串行处理完**（``I1``）。
            for call in response.tool_calls:
                spec = self._exposed_by_name.get(call.name)
                outcome = self._handle_tool_call(
                    call,
                    spec=spec,
                    tool=self._registry.resolve(call.name) if spec is not None else None,
                )
                yield from outcome.events
                if outcome.observation is None:
                    return
                history.append(
                    ChatMessage(
                        role=Role.TOOL,
                        content=outcome.observation,
                        tool_call_id=call.call_id,
                    )
                )
                consecutive_failures = consecutive_failures + 1 if outcome.failed else 0

            if consecutive_failures >= config.max_consecutive_failures:
                text = _TEXT_STALLED.format(max_failures=config.max_consecutive_failures)
                yield self._event(
                    SessionEventKind.ERROR,
                    error_kind=SessionErrorKind.STALLED,
                    text=text,
                )
                yield self._event(
                    SessionEventKind.TASK_FINISHED,
                    status=TaskStatus.FAILED,
                    text=text,
                )
                return

            # 只有在"整条响应处理完"之后才更新跨 run 的历史快照：中途终止时让历史停在
            # 上一个自洽的状态，避免后续消息序列里出现悬空的 ``tool_calls``。
            self._history = tuple(history)

    # ------------------------------------------------------------------
    # 模型往返（含瞬时重试）
    # ------------------------------------------------------------------

    def _model_step(self, task: str, history: Sequence[ChatMessage]) -> _ModelStep:
        """调用一次模型；瞬时故障在预算内重试（``REQ-HARNESS-06``）。

        处置只由 ``errors.resolve_disposition`` 决定（**只看异常类型**，不看消息）：
        ``RETRY`` ⇒ 产出 ``ERROR(TRANSIENT)`` 并原地重试；其余 ⇒ ``ERROR`` +
        ``TASK_FINISHED(FAILED)``。``error_kind`` 由 ``errors.error_kind`` 按"类型 + 处置"给出。

        消息装配失败（预算过小 ⇒ ``ValueError``）也在此收敛为事件：它同样是"本步失败"，
        不该以异常形态逃逸（fail-secure：不静默、也不继续）。
        """
        retries_used = 0
        events: list[SessionEvent] = []
        while True:
            try:
                response = self._model.chat(
                    assemble(
                        system=self._system,
                        task=task,
                        history=history,
                        budget=ContextBudget(max_tokens=self._config.max_prompt_tokens),
                        data_context=self._data_context,
                    ),
                    tools=self._exposed,
                    max_tokens=self._config.max_completion_tokens,
                )
            except Exception as exc:
                disposition = errors.resolve_disposition(exc, retries_used=retries_used)
                events.append(
                    self._event(
                        SessionEventKind.ERROR,
                        error_kind=errors.error_kind(exc, disposition=disposition),
                        text=self._failure_text(exc),
                    )
                )
                if disposition is errors.ErrorDisposition.RETRY:
                    retries_used += 1
                    continue
                events.append(
                    self._event(
                        SessionEventKind.TASK_FINISHED,
                        status=TaskStatus.FAILED,
                        text=_TEXT_MODEL_FAILED.format(error_type=type(exc).__name__),
                    )
                )
                return _ModelStep(response=None, events=tuple(events))
            return _ModelStep(response=response, events=tuple(events))

    def _failure_text(self, error: BaseException) -> str:
        """``ERROR.text`` 的文案：**我方常量 + 异常类型名**（契约 §2.4）。

        **不得**取 ``str(error)``：异常消息可能内嵌路径或不可信串，而事件流是界面载体。
        """
        return f"模型调用失败：{type(error).__name__}"

    # ------------------------------------------------------------------
    # 工具调用的决策序列（契约 §3.3）
    # ------------------------------------------------------------------

    def _handle_tool_call(
        self,
        call: ToolCallRequest,
        *,
        spec: ToolSpec | None,
        tool: Tool | None,
    ) -> _ToolCallOutcome:
        """处理一次工具调用：步 0 → 1 → 2 → 3 → 4 → 5 → 6a/6b。

        Args:
            call: 模型的原始请求（``arguments_json`` 是**不可信文本**）。
            spec: 该名字在**暴露集合**里的描述；``None`` ⇒ 未暴露 / 未知工具。
            tool: 注册表解析出的句柄；仅当 ``spec is not None`` 时才尝试解析。
        """
        events: list[SessionEvent] = [
            # 步 0：**无条件**产出——"模型请求过"本身是事实（``REQ-SEC-01`` 要求它可见）。
            # ``tool_name`` 原样取自模型请求 ⇒ 不可信数据，只按数据形态承载（渲染前净化）。
            self._event(
                SessionEventKind.TOOL_CALL,
                call_id=call.call_id,
                tool_name=call.name,
            )
        ]

        # ---- 步 1：解析域判定 -------------------------------------------------
        if spec is None:
            reason = (
                DENIED_NOT_EXPOSED if call.name in self._registered_names else DENIED_UNKNOWN_TOOL
            )
            return self._deny(call, tool_name=call.name, reason=reason, events=events)

        if tool is None:
            # 名字在 exposed 内却解析不到 ⇒ 我方不变量被破（契约 §3.3 步 1）：
            # **不得**静默跳过，直接终止。
            return self._terminate(events, text=_TEXT_BROKEN_INVARIANT)

        # ---- 步 2：严格校验（信任边界；原始 JSON 只在此处被解析） ----------------
        try:
            arguments = self._validator.validate(spec=spec, arguments_json=call.arguments_json)
        except ToolArgumentsInvalidError as exc:
            # 错误信息**不得**回显原始不可信内容（§3.4 的 ``V4``）：校验器的说明只描述
            # "哪个键、期望什么类型"；此处再净化 + 截断一次（纵深防御）。
            return self._deny(
                call,
                tool_name=spec.name,
                reason=DENIED_INVALID_ARGUMENTS,
                events=events,
                detail=sanitize_for_display(str(exc), limit=_VALIDATOR_DETAIL_LIMIT),
            )
        except Exception as exc:
            # 校验器抛非契约异常 ⇒ 无法判定参数可信性。fail-secure：终止，**不放行**。
            return self._terminate(
                events,
                text=f"{_TEXT_VALIDATOR_FAILED}（{type(exc).__name__}）",
            )

        # ---- 步 3：策略求值 ---------------------------------------------------
        if not spec.capabilities:
            # 工具未声明任何能力属**声明缺陷**：不得构造空集 ``PolicyRequest``
            # （``policy.md`` §2.3 的前置条件）⇒ 按参数缺陷拒绝。
            return self._deny(
                call,
                tool_name=spec.name,
                reason=DENIED_INVALID_ARGUMENTS,
                events=events,
            )

        decision = self._policy.decide(
            PolicyRequest(
                session_id=self._session_id,
                call_id=call.call_id,
                # 工具名取 ``spec.name``（**不**取 ``call.name``）：两者在步 1 已确认一致，
                # 用注册表一侧的名字让审计的可信来源明确（契约 §3.3 步 3）。
                tool_name=spec.name,
                arguments=arguments,
                requested=spec.capabilities,
                domain_pack=self._pack_name,
            )
        )
        events.append(
            self._event(
                SessionEventKind.POLICY_DECISION,
                call_id=call.call_id,
                tool_name=spec.name,
                decision=decision,
                audit_id=decision.audit_id,
            )
        )

        # ---- 步 4：审批通路（fail-secure：没有答复 ⇒ 拒绝，不得降级为放行） --------
        if decision.requires_confirmation:
            if self._approval is None:
                # ``R1``：未提供交互通路（典型是非交互运行）⇒ 一律**不执行**，且**不产**
                # ``APPROVAL_RESULT``——没有人被问过，伪造一条"用户拒绝"会让审计撒谎（``I2``）。
                # **禁止**放行；也**不得**为图方便注入一个恒放行的 gate（契约 §5.1 第 7 行）。
                return self._deny(
                    call,
                    tool_name=spec.name,
                    reason=DENIED_APPROVAL_DENIED,
                    events=events,
                )
            verdict = self._request_approval(
                call, spec=spec, decision=decision, arguments=arguments
            )
            events.extend(verdict.terminal)
            if verdict.terminal:
                return _ToolCallOutcome(events=tuple(events), observation=None, failed=False)
            if verdict.approval_event is not None:
                events.append(verdict.approval_event)
            if verdict.outcome is ApprovalOutcome.DENY:
                return self._deny(
                    call,
                    tool_name=spec.name,
                    reason=DENIED_APPROVAL_DENIED,
                    events=events,
                )
        elif not decision.allow:
            return self._deny(
                call,
                tool_name=spec.name,
                reason=DENIED_POLICY_DENIED,
                events=events,
            )

        # ---- 步 5：执行（唯一构造 ``ExecutionContext`` 的地方） ------------------
        context = ExecutionContext(
            session_id=self._session_id,
            call_id=call.call_id,
            working_dir=self._config.working_dir,
            allowed_roots=self._config.allowed_roots,
            timeout_s=self._config.tool_timeout_s,
            # ⚠️ 本轮**恒为** False：出站白名单与云端客户端均未实现。
            # ``NETWORK_OUTBOUND`` 已授予**不等于**可以出站（``T-10`` 保持未缓解）。
            network_allowed=False,
        )
        try:
            result = tool.invoke(arguments, ctx=context)
        except Exception as exc:
            # 工具抛未预期异常 ⇒ 不当成 ``ERROR`` 事件了事：记一条 ``TOOL_CALL/ERROR`` 审计，
            # 并合成 ``ToolResult(ok=False)`` 走**正常**回喂通路（契约 §3.3 步 5）。
            # **不回显异常消息内容**（可能携带路径 / 不可信串），只记类型名。
            audit_id = self._emit_tool_call_audit(
                call_id=call.call_id,
                tool_name=spec.name,
                outcome=AuditOutcome.ERROR,
                detail={"failed_reason": "tool_exception"},
            )
            result = ToolResult(
                ok=False,
                content="",
                error=f"工具内部错误：{type(exc).__name__}",
                truncated=False,
                audit_id=audit_id,
            )

        # ---- 步 6a：已执行 -----------------------------------------------------
        if result.audit_id is None:
            # ``I4`` 要求 ``TOOL_RESULT.audit_id`` 非空且可在审计中回放；缺了它就没有
            # "这一次调用"的证据链。属不变量被破 ⇒ 终止（而不是发一条无法回放的事件）。
            return self._terminate(events, text=_TEXT_AUDIT_ID_MISSING)

        events.append(
            self._event(
                SessionEventKind.TOOL_RESULT,
                call_id=call.call_id,
                tool_name=call.name,
                result=result,
                audit_id=result.audit_id,
            )
        )
        observation = result.error if (not result.ok and result.error) else result.content
        return _ToolCallOutcome(events=tuple(events), observation=observation, failed=not result.ok)

    def _request_approval(
        self,
        call: ToolCallRequest,
        *,
        spec: ToolSpec,
        decision: PolicyDecision,
        arguments: Mapping[str, object],
    ) -> _ApprovalVerdict:
        """走一次确认通路并校验应答形状（契约 §2.5.5 的 ``R3`` / ``R4``）。

        ``R3``（gate 抛异常）与 ``R4``（应答形状非法）**都不得**被吞成"一次普通拒绝"——
        那会把"审批基础设施故障"伪装成"用户拒绝"（``policy.md`` §2.5 的相反处置同源）。
        gate 实现**必须**先 ``emit(AuditEvent(kind=APPROVAL))`` 再返回（``A4``），
        这里的 ``audit_id`` 即该事件 id。
        """
        gate = self._approval
        if gate is None:  # pragma: no cover - 调用点已先行判定
            msg = "内部错误：未提供确认通路却已进入审批步骤"
            raise errors.HarnessInternalError(msg)

        try:
            result = gate.request(
                ApprovalRequest(
                    session_id=self._session_id,
                    call_id=call.call_id,
                    tool_name=spec.name,
                    risk_level=decision.risk_level,
                    reason=decision.reason,
                    arguments_summary=summarize_arguments(arguments),
                )
            )
        except AuditWriteError:
            # 证据面损坏**不是**"审批通路故障"：前者是"我们没留下痕迹"，后者是
            # "权限判定的输入面不可用"。同形会让消费者把"证据面坏了"读成"任务失败"
            # （威胁模型 §8.2 的 P-3）⇒ 按契约 audit.md §2.4「必须冒泡」，原样抛出。
            raise
        except Exception as exc:
            return _ApprovalVerdict(
                approval_event=None,
                outcome=None,
                terminal=self._terminal_events(
                    text=f"{_TEXT_APPROVAL_PATH_FAILED}（{type(exc).__name__}）",
                    finished_text=_TEXT_APPROVAL_PATH_FAILED,
                ),
            )

        problem = _approval_shape_problem(result)
        if problem is not None:
            return _ApprovalVerdict(
                approval_event=None,
                outcome=None,
                terminal=self._terminal_events(
                    text=f"{_TEXT_APPROVAL_SHAPE_INVALID}（{problem}）",
                    finished_text=_TEXT_APPROVAL_SHAPE_INVALID,
                ),
            )

        return _ApprovalVerdict(
            approval_event=self._event(
                SessionEventKind.APPROVAL_RESULT,
                call_id=call.call_id,
                tool_name=spec.name,
                approval=result,
                audit_id=result.audit_id,
            ),
            outcome=result.outcome,
            terminal=(),
        )

    def _deny(
        self,
        call: ToolCallRequest,
        *,
        tool_name: str,
        reason: str,
        events: list[SessionEvent],
        detail: str | None = None,
    ) -> _ToolCallOutcome:
        """步 6b：**未执行**路径（拒绝 ≠ 失败；**不**构造 ``ToolResult``）。

        先记一条 ``TOOL_CALL/DENY`` 审计（``D1``/``D3``），其 ``event_id`` 回填进
        ``SessionEvent.audit_id``；再把**我方生成的中文说明**作为观察内容回喂给模型。
        """
        audit_id = self._emit_tool_call_audit(
            call_id=call.call_id,
            tool_name=tool_name,
            outcome=AuditOutcome.DENY,
            detail={"denied_reason": reason},
        )
        text = _DENIED_TEXTS[reason]
        if detail:
            text = f"{text}：{detail}"
        events.append(
            self._event(
                SessionEventKind.TOOL_RESULT,
                call_id=call.call_id,
                tool_name=tool_name,
                result=None,
                audit_id=audit_id,
                text=text,
            )
        )
        return _ToolCallOutcome(events=tuple(events), observation=text, failed=True)

    def _terminal_events(self, *, text: str, finished_text: str) -> tuple[SessionEvent, ...]:
        """``ERROR(INTERNAL)`` + ``TASK_FINISHED(FAILED)`` 两件套（`I7` 的必备方向）。"""
        return (
            self._event(
                SessionEventKind.ERROR,
                error_kind=SessionErrorKind.INTERNAL,
                text=text,
            ),
            self._event(
                SessionEventKind.TASK_FINISHED,
                status=TaskStatus.FAILED,
                text=finished_text,
            ),
        )

    def _terminate(self, events: list[SessionEvent], *, text: str) -> _ToolCallOutcome:
        """不变量被破时终止任务；刻意**不**补 ``TOOL_RESULT``（理由见模块 docstring）。"""
        events.extend(self._terminal_events(text=text, finished_text=text))
        return _ToolCallOutcome(events=tuple(events), observation=None, failed=False)

    # ------------------------------------------------------------------
    # 审计与事件装配
    # ------------------------------------------------------------------

    def _emit_tool_call_audit(
        self,
        *,
        call_id: str,
        tool_name: str,
        outcome: AuditOutcome,
        detail: Mapping[str, object],
    ) -> str:
        """记一条 ``TOOL_CALL`` 审计事件并返回其 ``event_id``（``D3``）。

        ``detail`` 只放**结构化、我方产生**的定长短码（``D2``）：调用参数与工具输出都是
        不可信数据，原样写进审计等于把不可信内容固化成长期证据。

        Raises:
            Exception: ``sink.emit`` 的异常**原样冒泡**（本函数不做任何捕获）。
        """
        event_id = uuid.uuid4().hex
        self._sink.emit(
            AuditEvent(
                event_id=event_id,
                kind=AuditEventKind.TOOL_CALL,
                timestamp=_now_iso(),
                session_id=self._session_id,
                outcome=outcome,
                call_id=call_id,
                tool_name=tool_name,
                detail=dict(detail),
            )
        )
        return event_id

    def _event(
        self,
        kind: SessionEventKind,
        *,
        call_id: str | None = None,
        tool_name: str | None = None,
        response: ModelResponse | None = None,
        decision: PolicyDecision | None = None,
        approval: ApprovalResult | None = None,
        result: ToolResult | None = None,
        text: str | None = None,
        error_kind: SessionErrorKind | None = None,
        status: TaskStatus | None = None,
        audit_id: str | None = None,
    ) -> SessionEvent:
        """装配一条事件并**推进** ``seq``（``I9``：从 0 起、单调、无空洞、跨 ``run`` 不重置）。

        本方法是事件时间戳与序号的唯一来源；调用点一律写成 ``yield self._event(...)``，
        使"装配"与"产出"之间不存在可抛异常的间隙（否则会在流里留下序号空洞）。
        """
        event = SessionEvent(
            kind=kind,
            session_id=self._session_id,
            seq=self._seq,
            timestamp=_now_iso(),
            call_id=call_id,
            tool_name=tool_name,
            response=response,
            decision=decision,
            approval=approval,
            result=result,
            text=text,
            error_kind=error_kind,
            status=status,
            audit_id=audit_id,
        )
        self._seq += 1
        return event
