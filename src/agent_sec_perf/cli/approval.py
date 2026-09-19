"""交互式人工确认通路（``harness.md`` §2.5；``cli/`` 侧实现，装配时注入给 ``Session``）。

**为什么实现在 ``cli/``**：``R1`` 禁止 ``harness`` 依赖终端；提示文案与 TTY 判定属**表现层**，
``harness`` 只依赖 ``contracts.ApprovalGate``（依赖倒置，§2.5.4 采纳的候选 (a)）。
单测因此可以注入替身，不必真的开一个 TTY。

fail-secure 三条（契约 §2.5.5；**没有答复 ⇒ 拒绝，不得降级为放行**）：

* ``R2`` **无 TTY ⇒ 返回 ``DENY``**，不得阻塞等待 stdin；
* ``R5`` **超时 ⇒ 返回 ``DENY``**（而**不是**抛异常）。超时由**本实现**负责——``harness``
  不设超时：单线程模型里给一个阻塞调用加超时必然引入线程或信号，那是契约 §2.9 已否决的复杂度；
* **非预期输入**（空行 / 无法识别的选择 / EOF）⇒ ``DENY``。

``A4``：每次确认**必须先 ``emit(AuditEvent(kind=APPROVAL))`` 再返回**，返回的 ``audit_id``
即该事件 ``event_id``——``REQ-UX-02`` 要求三种选择（本次 / 总是 / 拒绝）**均被审计**，
``allow_always`` 亦**如实记录**（``R6``：本轮等价于 ``allow_once``，持久授权是未决项 ``U3``，
**不得**表述为"已支持持久授权"）。

**提示一律写调用方给的流（装配点是 stderr）**：``--output-format json`` 要求 stdout 只出 JSONL
（契约 §5.2 的输出通道硬规定 1），把交互提示写进 stdout 会直接破坏该协议。

**不可信文本先净化**：``ApprovalRequest.tool_name`` 原样取自模型请求，展示前必须经
``foundation.logging.sanitize_for_display``（``risk_level`` / ``reason`` / ``arguments_summary``
分别是枚举值与我方生成的中文，按契约可直接显示）。
"""

from __future__ import annotations

import select
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import IO, Final, Protocol

from agent_sec_perf.contracts.audit import AuditEvent, AuditEventKind, AuditOutcome, AuditSink
from agent_sec_perf.contracts.harness import (
    ApprovalGate,
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalResult,
)
from agent_sec_perf.foundation.logging import sanitize_for_display

__all__ = ["ApprovalInput", "InteractiveApprovalGate", "StdioApprovalInput"]

#: 工具名回显进提示前的截断长度（不可信文本）。
_NAME_LIMIT: Final = 64

_NO_TTY_TEXT: Final = "未检测到交互式终端：按默认拒绝处置（fail-secure，契约 §2.5.5 的 R2）"
_NO_ANSWER_TEXT: Final = (
    "未在限时内收到答复（或输入已结束）：按默认拒绝处置（fail-secure，契约 §2.5.5 的 R5）"
)
_NO_SUMMARY_TEXT: Final = "（无可展示的参数摘要）"
_CHOICES_TEXT: Final = "请选择：[1] 允许一次  [2] 始终允许（本轮等同允许一次）  [3] 拒绝"

#: 可接受的答复集合。取值**故意收得很紧**：无法识别的一律落到拒绝（fail-secure），
#: 不做"猜用户想说什么"的模糊匹配（"yolo" 不该被当成允许）。
_ALLOW_ALWAYS_ANSWERS: Final[frozenset[str]] = frozenset(
    {"2", "a", "always", "allow_always", "始终允许"}
)
_ALLOW_ONCE_ANSWERS: Final[frozenset[str]] = frozenset(
    {"1", "y", "yes", "once", "allow_once", "允许", "允许一次"}
)
_DENY_ANSWERS: Final[frozenset[str]] = frozenset({"3", "n", "no", "deny", "拒绝"})


class ApprovalInput(Protocol):
    """确认输入的抽象（把"是不是 TTY"与"读一行是否超时"留在可替换的边界内）。

    抽象出来的理由不是"为了测试好写"，而是这两件事**只有在表现层才知道**：``harness``
    既不能检测 TTY，也不能给阻塞调用加超时（契约 §2.5.5 的 ``R2``/``R5``）。
    """

    def is_interactive(self) -> bool:
        """当前输入是否是可交互的终端。**否 ⇒ 调用方必须直接拒绝**，不得阻塞。"""
        ...

    def read_line(self, *, timeout_s: float) -> str | None:
        """读一行用户输入；``None`` 表示"没有答复"（EOF 或超时），**不是**异常。"""
        ...


class StdioApprovalInput:
    """真实标准输入：``isatty`` 判定 + ``select`` 上的读超时（**不引入线程 / 信号**）。

    ``select`` 是本项目里"给单线程阻塞读加超时"的标准做法：它只**探测**可读性，
    不改变"单会话单线程、串行产出"的执行模型（契约 §2.9）。
    """

    def __init__(self, stdin: IO[str]) -> None:
        self._stdin = stdin

    def is_interactive(self) -> bool:
        """``isatty``；调用失败（流已被关闭等）一律按**非交互**处置。"""
        try:
            return bool(self._stdin.isatty())
        except (OSError, ValueError):
            return False

    def read_line(self, *, timeout_s: float) -> str | None:
        """在 ``timeout_s`` 内读一行；不可等待 / 超时 / EOF 都返回 ``None``。

        三种"没有答复"合并成同一个返回值是**故意的**：``R2`` 要求无 TTY 直接拒绝、
        ``R5`` 要求超时返回 ``DENY``，两者的处置一致（都拒绝），区分它们只会多出一条
        无从处置的分支。**但绝不把任何一条当成"允许"**。
        """
        try:
            ready, _, _ = select.select([self._stdin], [], [], timeout_s)
        except (OSError, ValueError):
            # 不是可等待的交互输入（例如被替换成 StringIO，或流已被关闭）。
            return None
        if not ready:
            return None
        try:
            line = self._stdin.readline()
        except (OSError, ValueError):
            return None
        if not line:
            return None
        return line.rstrip("\n")


@dataclass(frozen=True)
class InteractiveApprovalGate(ApprovalGate):
    """阻塞式交互确认（契约 §2.5.4 候选 (a)、§2.5.5）。

    Args:
        sink: 审计落点。刻意**无默认值**：确认是权限判定的一环，不存在"悄悄不审计"的通路。
        source: 输入来源（``StdioApprovalInput`` 或单测替身）。
        prompt_stream: 提示写到哪里。装配点传 **stderr**（JSON 模式下 stdout 只许出 JSONL）。
        timeout_s: 等待答复的上限；超时⇒拒绝（``R5``）。
    """

    sink: AuditSink
    source: ApprovalInput
    prompt_stream: IO[str]
    timeout_s: float = 120.0

    def request(self, request: ApprovalRequest) -> ApprovalResult:
        """走一次确认并返回结果；**任何**路径（含拒绝）都先 emit 审计（``A4``）。

        Raises:
            Exception: ``sink.emit`` 的异常**原样冒泡**（审计是证据面，失败不得被吞掉）。
        """
        outcome = self._ask(request)
        return ApprovalResult(outcome=outcome, audit_id=self._record(request, outcome))

    def _ask(self, request: ApprovalRequest) -> ApprovalOutcome:
        """取用户选择；一切"没有可用答复"的情形都收敛为 ``DENY``。"""
        if not self.source.is_interactive():
            self._say(_NO_TTY_TEXT)
            return ApprovalOutcome.DENY
        self._prompt(request)
        answer = self.source.read_line(timeout_s=self.timeout_s)
        if answer is None:
            self._say(_NO_ANSWER_TEXT)
            return ApprovalOutcome.DENY
        return _parse_answer(answer)

    def _prompt(self, request: ApprovalRequest) -> None:
        """打印确认提示（``REQ-SEC-02`` 要求展示风险说明；``tool_name`` 先净化）。"""
        tool = sanitize_for_display(request.tool_name, limit=_NAME_LIMIT)
        summary = request.arguments_summary or _NO_SUMMARY_TEXT
        self._say(
            "\n".join(
                (
                    f"需人工确认：{tool}（风险 {request.risk_level.value}）",
                    f"理由：{request.reason}",
                    f"操作对象：{summary}",
                    _CHOICES_TEXT,
                )
            )
        )

    def _say(self, text: str) -> None:
        """把一行提示写到提示流；**提示的失败必须可见**（不吞 ``OSError`` / ``ValueError``）。"""
        self.prompt_stream.write(f"{text}\n")
        self.prompt_stream.flush()

    def _record(self, request: ApprovalRequest, outcome: ApprovalOutcome) -> str:
        """落一条 ``kind=APPROVAL`` 审计事件，返回其 ``event_id``（``audit.md`` §2.2 的生产者表）。

        ``detail["choice"]`` 记录**具体是哪种选择**（``REQ-UX-02``：本次 / 总是 / 拒绝均可回放）；
        它是**我方枚举值**，不含任何不可信内容。
        """
        audit_id = uuid.uuid4().hex
        self.sink.emit(
            AuditEvent(
                event_id=audit_id,
                kind=AuditEventKind.APPROVAL,
                timestamp=datetime.now(tz=UTC).isoformat(),
                session_id=request.session_id,
                outcome=_audit_outcome(outcome),
                call_id=request.call_id,
                tool_name=request.tool_name,
                risk_level=request.risk_level,
                detail={"choice": outcome.value},
            )
        )
        return audit_id


def _audit_outcome(outcome: ApprovalOutcome) -> AuditOutcome:
    """确认结果 → 审计结果（``audit.md`` §2.2：``APPROVAL`` 只允许 ``ALLOW`` / ``DENY``）。

    ``ALLOW_ALWAYS`` 映射为 ``ALLOW``：它在**本轮**与 ``ALLOW_ONCE`` 等效（``R6``），
    具体是哪种已由 ``detail["choice"]`` 如实记录，不靠 ``outcome`` 一栏区分。
    """
    if outcome is ApprovalOutcome.DENY:
        return AuditOutcome.DENY
    return AuditOutcome.ALLOW


def _parse_answer(raw: str) -> ApprovalOutcome:
    """把一行答复解析成选择；**无法识别 ⇒ ``DENY``**（fail-secure，绝不猜）。"""
    answer = raw.strip().lower()
    if answer in _ALLOW_ALWAYS_ANSWERS:
        return ApprovalOutcome.ALLOW_ALWAYS
    if answer in _ALLOW_ONCE_ANSWERS:
        return ApprovalOutcome.ALLOW_ONCE
    if answer in _DENY_ANSWERS:
        return ApprovalOutcome.DENY
    return ApprovalOutcome.DENY
