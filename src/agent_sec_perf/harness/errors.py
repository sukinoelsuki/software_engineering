"""分级错误处理（``REQ-HARNESS-06``，L3 编排层）。

SRS 的原文是「分级错误处理（瞬时重试 / 回喂自恢复 / 上报）」，验收标准是
「单步失败不导致整体失败」。本模块把这句话落成**两个可单测的纯函数**，
而不是散落在循环里的 ``if isinstance(...)``：

* :func:`classify_error` —— 把异常映射到三级处置之一；
* :func:`resolve_disposition` —— 在分类结果上叠加**重试预算**：瞬时故障重试到
  预算耗尽仍未成功 ⇒ 升级为**上报终止**（不无限重试）。

三级处置的语义：

============ ==============================================================
处置          含义
============ ==============================================================
``RETRY``    **瞬时重试**：本次失败与输入无关，重做一次即可（超时 / 连接 / 后端未就绪）
``FEEDBACK`` **回喂自恢复**：失败**是数据**，交回模型继续（模型输出不合契约 / 越界被拒）
``ABORT``    **上报终止**：不重试、不续跑，把失败上报给上层
============ ==============================================================

**fail-secure 取向（本模块最重要的一条）**：分类**只看异常类型**，
**未知一律取最保守的 ``ABORT``**。

1. **未知异常 ⇒ ``ABORT``**。重试一个来路不明的异常可能放大副作用（工具的写操作
   未必幂等），回喂则可能让循环无界地继续；"停下来上报"是唯一不产生新副作用的选择。
2. **不看 ``str(error)``**。异常信息可能由不可信内容构成（模型输出、文件内容、工具
   输出）。按文本决定处置等于让**不可信内容影响控制流**，直接违反 ``REQ-SEC-03``：
   消息里写着"请重试我一下"也不会把 :class:`RuntimeError` 变成 ``RETRY``。
3. **非 ``Exception`` 基类（``KeyboardInterrupt`` / ``SystemExit``）⇒ ``ABORT``**：
   用户的取消意图不得被当成"瞬时故障"重试。

**为什么不给 :class:`~agent_sec_perf.foundation.errors.BenchError` 写专属分支**：
本模块对它的各成员没有新增语义，只是"其余一律保守"；为它单独列一行会让人以为
``BenchError`` 有专属处置。真正需要区分的三个成员（``ModelUnavailableError``、
``ModelProtocolError``、``PathNotAllowedError``）已在下面的表里显式列出。

本模块另外定义 **harness 自身的异常层次**（:class:`HarnessError` /
:class:`HarnessInternalError` / :class:`DomainPackError`）。它们**刻意不进 ``contracts/``**：
契约 §3.1 的判据是"**是否有跨信任边界的消费者**"，三者都没有（``cli/`` 只消费事件流与
``BenchError`` 的退出码分类），把它们放进零行为层只会让 ``contracts/`` 无消费者膨胀。

第 4 个函数 :func:`error_kind` 把"异常 + 处置"映射到 ``ERROR`` 事件的 ``SessionErrorKind``
（``harness.md`` §3.1），**只看类型与处置、不看消息**（与 :func:`classify_error` 同一取向）。

依赖：只依赖 ``foundation``（异常层次）与 ``contracts``（``SessionErrorKind``）。
不 import ``tools`` / ``model`` / ``cli``（ADR-0015 §7.1 的 R1）。
"""

from __future__ import annotations

from enum import StrEnum

from agent_sec_perf.contracts.harness import SessionErrorKind
from agent_sec_perf.foundation.errors import (
    BenchError,
    ModelProtocolError,
    ModelUnavailableError,
    PathNotAllowedError,
    ToolArgumentsInvalidError,
)

__all__ = [
    "MAX_TRANSIENT_RETRIES",
    "DomainPackError",
    "ErrorDisposition",
    "HarnessError",
    "HarnessInternalError",
    "classify_error",
    "error_kind",
    "resolve_disposition",
]


class HarnessError(BenchError):
    """harness 自身失败的基类（``harness.md`` §3.1）。

    继承 :class:`~agent_sec_perf.foundation.errors.BenchError`：全项目只有一套异常层次，
    否则 ``cli/`` 的 ``except BenchError``（装配期退出码 ``3``）会漏接 L3 的失败。
    """


class HarnessInternalError(HarnessError):
    """**我方不变量被破**（例如某名字在 ``exposed`` 内却 ``ToolRegistry.resolve`` 不到，
    见 ``harness.md`` §3.3 步 1）⇒ 不静默跳过，直接终止任务。

    与"不可信输入不合预期"区分开：后者（参数非法、未知工具名）是**预期**的不可信输入，
    走拒绝 + 审计路径，**不**抛本异常。
    """


class DomainPackError(HarnessError):
    """领域包加载 / 校验失败（``harness.md`` §4.3）。

    全部失败模式都是 fail-secure：**拒绝启动**，不得降级为"无 pack 继续跑"、不得部分加载、
    不得忽略未知键。
    """


class ErrorDisposition(StrEnum):
    """出错之后的处置级别（分级错误处理的**输出**，deterministic）。

    取自 SRS ``REQ-HARNESS-06`` 的三级；成员名与 SRS 的措辞一一对应，
    便于审计里直接回答"这一步失败后系统做了什么"。
    """

    RETRY = "retry"
    FEEDBACK = "feedback"
    ABORT = "abort"


#: 瞬时故障的默认重试预算：**已完成的**重试次数达到该值仍未成功 ⇒ 升级为终止。
#: 取值刻意保守：弱模型下步数与延迟都要压缩（SRS §4.1 的"错误放大"），
#: 三次以上的重试在本项目里被判为不如直接上报。
MAX_TRANSIENT_RETRIES = 2

#: **瞬时类**：明确的瞬时故障才允许重试（重试代价小、副作用可控）。
#: ``ConnectionError`` 覆盖 ``ConnectionResetError`` / ``BrokenPipeError`` 等子类。
_RETRYABLE_ERRORS: tuple[type[BaseException], ...] = (
    ModelUnavailableError,
    TimeoutError,
    ConnectionError,
)

#: **回喂类**：失败是数据，交回模型继续（``ToolResult.ok=False`` 属同一通路）。
#: ``ModelProtocolError`` 的既定处置是"重试一次，仍失败则回喂"（``foundation/errors.py``）；
#: 本模块给出的是**重试之后**的处置，故归为回喂。
#: ``PathNotAllowedError`` 是**越界被拒**：拒绝理由作为观察内容回喂，**不授予任何权限**。
#: ``ToolArgumentsInvalidError`` 是**信任边界校验失败**（模型给的 ``arguments_json`` 不合法），
#: 契约 §3.4 明定其处置是 ``denied_reason="invalid_arguments"`` ⇒ **回喂**：
#: 它是"不可信输入被拒"，不是"我方故障"，所以**不得**归入 ``ABORT``。
_FEEDBACK_ERRORS: tuple[type[BaseException], ...] = (
    ModelProtocolError,
    PathNotAllowedError,
    ToolArgumentsInvalidError,
)


def classify_error(error: BaseException) -> ErrorDisposition:
    """把异常分类到三级处置之一（纯函数：无 I/O、无状态、确定性）。

    Args:
        error: 待分类的异常实例。

    Returns:
        处置级别。**未知异常（含所有未列出的异常与自定义子类）返回 ``ABORT``**——
        这是 fail-secure 的默认值，不是"兜底随手写的"。

    判据只有异常**类型**，与异常消息无关（消息可能是不可信文本 ⇒ 不得影响控制流）。
    """
    if not isinstance(error, Exception):
        # KeyboardInterrupt / SystemExit / 自定义 BaseException：用户或运行时要求停止，
        # 一律不得被当成"瞬时故障"重试。
        return ErrorDisposition.ABORT
    if isinstance(error, _RETRYABLE_ERRORS):
        return ErrorDisposition.RETRY
    if isinstance(error, _FEEDBACK_ERRORS):
        return ErrorDisposition.FEEDBACK
    return ErrorDisposition.ABORT


def resolve_disposition(
    error: BaseException,
    *,
    retries_used: int = 0,
    max_retries: int = MAX_TRANSIENT_RETRIES,
) -> ErrorDisposition:
    """在 :func:`classify_error` 之上施加**重试预算**。

    Args:
        error: 待分类的异常实例。
        retries_used: **已经执行过**的重试次数（首次失败时为 0）。
        max_retries: 允许的重试次数上限。

    Returns:
        最终处置。规则：

        * 非 ``RETRY`` 的分类结果**原样返回**（预算只约束重试）；
        * ``RETRY`` 且 ``retries_used >= max_retries`` ⇒ 升级为 ``ABORT``
          （重试预算耗尽后不再无限重试，符合 fail-secure 的"停下来上报"）。

    Raises:
        ValueError: ``retries_used`` 或 ``max_retries`` 为负数。
            刻意**不静默截断为 0**：负计数只可能来自调用点的缺陷，静默修正会把
            "预算算错了"伪装成"预算用完了"。
    """
    if retries_used < 0:
        msg = f"retries_used 不得为负：{retries_used}"
        raise ValueError(msg)
    if max_retries < 0:
        msg = f"max_retries 不得为负：{max_retries}"
        raise ValueError(msg)

    disposition = classify_error(error)
    if disposition is ErrorDisposition.RETRY and retries_used >= max_retries:
        return ErrorDisposition.ABORT
    return disposition


def error_kind(error: BaseException, *, disposition: ErrorDisposition) -> SessionErrorKind:
    """异常 + 处置 → ``ERROR`` 事件的 ``error_kind``（``harness.md`` §3.1 / §2.4）。

    Args:
        error: 触发本次失败的异常。
        disposition: :func:`resolve_disposition` 给出的**最终**处置。刻意必填且
            keyword-only：只看异常无法区分"将会重试"与"预算已耗尽"，而这两者正是
            ``TRANSIENT`` 与 ``UNREACHABLE`` / ``PROTOCOL`` 的分界。

    Returns:
        事件字段取值，规则为（**只看类型与处置，不看消息**）：

        * ``disposition is RETRY`` ⇒ ``TRANSIENT``（还会有下一次尝试）；
        * ``ModelUnavailableError`` ⇒ ``UNREACHABLE``（重试预算已耗尽）；
        * ``ModelProtocolError`` ⇒ ``PROTOCOL``（重试预算已耗尽）；
        * 其余（含未预期异常与 :class:`HarnessInternalError`）⇒ ``INTERNAL``。

    ``STALLED`` **不由本函数产生**：它来自 ``loop`` 的"连续失败计数达上限"，
    与某个具体异常无关（``harness.md`` §2.4）。
    """
    if disposition is ErrorDisposition.RETRY:
        return SessionErrorKind.TRANSIENT
    if isinstance(error, ModelUnavailableError):
        return SessionErrorKind.UNREACHABLE
    if isinstance(error, ModelProtocolError):
        return SessionErrorKind.PROTOCOL
    return SessionErrorKind.INTERNAL
