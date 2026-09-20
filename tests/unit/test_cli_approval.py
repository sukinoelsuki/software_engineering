"""``cli/approval.py`` 的行为断言（契约 ``harness.md`` §2.5.5 的 ``R2``/``R5``/``R6`` + ``A4``）。

实现侧的功能断言；对抗性验收（``S-new-1`` 等）属 ``tests/security/``，由验证角色独立完成。
本文件钉住的道口：

* ``R2`` 无 TTY ⇒ ``DENY`` 且**不阻塞读**；
* ``R5`` 没有答复（EOF / 超时）⇒ ``DENY``；
* 非预期输入 ⇒ ``DENY``（fail-secure：不猜）；
* ``R6`` ``allow_always`` 被接受且**如实记录**为 ``allow_always``；
* ``A4`` 每次确认都在返回前落**恰好一条** ``kind=APPROVAL`` 审计，``audit_id`` 即其 ``event_id``；
* 提示里的 ``tool_name``（不可信）已经净化。
"""

from __future__ import annotations

import io
from datetime import datetime
from typing import Final

import pytest

from agent_sec_perf.cli.approval import InteractiveApprovalGate, StdioApprovalInput
from agent_sec_perf.contracts.audit import AuditEvent, AuditEventKind, AuditOutcome
from agent_sec_perf.contracts.harness import ApprovalOutcome, ApprovalRequest
from agent_sec_perf.contracts.policy import RiskLevel

ESC: Final = "\x1b[2J"
TIMEOUT_S: Final = 7.5


class _FakeInput:
    """``ApprovalInput`` 的替身：交互性可控、答复固定、记录读超时。"""

    def __init__(self, *, interactive: bool, line: str | None = None) -> None:
        self._interactive = interactive
        self._line = line
        self.timeouts: list[float] = []

    def is_interactive(self) -> bool:
        return self._interactive

    def read_line(self, *, timeout_s: float) -> str | None:
        self.timeouts.append(timeout_s)
        return self._line


class _RecordingSink:
    """``AuditSink`` 的替身（只追加）。"""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []
        self.flushes = 0

    def emit(self, event: AuditEvent) -> None:
        self.events.append(event)

    def flush(self) -> None:
        self.flushes += 1


def _datetime_is_aware(value: str) -> bool:
    """时间戳必须带时区（与 ``AuditEvent`` 同口径）。"""
    return datetime.fromisoformat(value).tzinfo is not None


def _request(**overrides: object) -> ApprovalRequest:
    fields: dict[str, object] = {
        "session_id": "s-cli",
        "call_id": "c1",
        "tool_name": "write_file",
        "risk_level": RiskLevel.HIGH,
        "reason": "我方生成的中文理由",
        "arguments_summary": "path=<str>",
    }
    fields.update(overrides)
    return ApprovalRequest(**fields)  # type: ignore[arg-type]


def _gate(
    *, interactive: bool, line: str | None
) -> tuple[InteractiveApprovalGate, _RecordingSink, io.StringIO]:
    sink = _RecordingSink()
    prompt = io.StringIO()
    gate = InteractiveApprovalGate(
        sink=sink,
        source=_FakeInput(interactive=interactive, line=line),
        prompt_stream=prompt,
        timeout_s=TIMEOUT_S,
    )
    return gate, sink, prompt


# ---------------------------------------------------------------------------
# R2：无 TTY ⇒ 拒绝，且不阻塞读
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_no_tty_is_denied_without_reading() -> None:
    """``R2``：无 TTY ⇒ ``DENY``，**不得**阻塞等待 stdin，且仍然留下审计。"""
    gate, sink, prompt = _gate(interactive=False, line="1")

    result = gate.request(_request())

    assert result.outcome is ApprovalOutcome.DENY
    source = gate.source
    assert isinstance(source, _FakeInput)
    assert source.timeouts == [], "无 TTY 时不得尝试读取（否则会挂死）"
    assert "未检测到交互式终端" in prompt.getvalue()
    assert len(sink.events) == 1


# ---------------------------------------------------------------------------
# R5：没有答复 ⇒ 拒绝
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_no_answer_is_denied_and_timeout_is_honoured() -> None:
    """``R5``：EOF / 超时 ⇒ ``DENY``（不是异常），且超时值原样透传给输入源。"""
    gate, sink, prompt = _gate(interactive=True, line=None)

    result = gate.request(_request())

    assert result.outcome is ApprovalOutcome.DENY
    source = gate.source
    assert isinstance(source, _FakeInput)
    assert source.timeouts == [TIMEOUT_S]
    assert "未在限时内收到答复" in prompt.getvalue()
    assert len(sink.events) == 1


@pytest.mark.unit
def test_stdio_input_reports_no_tty_and_no_answer_for_a_non_terminal_stream() -> None:
    """真实实现：``StringIO`` 既不是 TTY，也不是可 ``select`` 等待的输入 ⇒ 两条都返回"否"。"""
    source = StdioApprovalInput(io.StringIO())

    assert source.is_interactive() is False
    assert source.read_line(timeout_s=0.01) is None


# ---------------------------------------------------------------------------
# 三种选择 + 不可识别输入
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        pytest.param("1", ApprovalOutcome.ALLOW_ONCE, id="allow_once"),
        pytest.param("y", ApprovalOutcome.ALLOW_ONCE, id="allow_once_short"),
        pytest.param("2", ApprovalOutcome.ALLOW_ALWAYS, id="allow_always"),
        pytest.param("3", ApprovalOutcome.DENY, id="deny"),
        pytest.param("n", ApprovalOutcome.DENY, id="deny_short"),
    ],
)
def test_recognised_answers_map_to_the_three_outcomes(
    answer: str, expected: ApprovalOutcome
) -> None:
    """三种选择均可用（``REQ-UX-02``），且 ``allow_always`` 被**接受**（``R6``）。"""
    gate, _, _ = _gate(interactive=True, line=answer)

    assert gate.request(_request()).outcome is expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "answer",
    [
        pytest.param("", id="empty"),
        pytest.param("   ", id="blank"),
        pytest.param("maybe", id="gibberish"),
        pytest.param("yes please", id="not_exact"),
        pytest.param("1; rm -rf /", id="injected_compound"),
    ],
)
def test_unrecognised_answers_are_denied(answer: str) -> None:
    """非预期输入 ⇒ ``DENY``（fail-secure：绝不把"看不懂"当成"允许"）。"""
    gate, sink, _ = _gate(interactive=True, line=answer)

    result = gate.request(_request())

    assert result.outcome is ApprovalOutcome.DENY
    assert sink.events[0].detail["choice"] == "deny"


# ---------------------------------------------------------------------------
# A4：审计
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("answer", "expected_outcome", "expected_choice"),
    [
        pytest.param("1", AuditOutcome.ALLOW, "allow_once", id="allow_once"),
        pytest.param("2", AuditOutcome.ALLOW, "allow_always", id="allow_always"),
        pytest.param("3", AuditOutcome.DENY, "deny", id="deny"),
    ],
)
def test_every_choice_is_audited_with_the_exact_choice(
    answer: str, expected_outcome: AuditOutcome, expected_choice: str
) -> None:
    """``A4`` + ``REQ-UX-02``：三种选择均落一条 ``APPROVAL`` 审计，``choice`` 如实记录。"""
    request = _request()
    gate, sink, _ = _gate(interactive=True, line=answer)

    result = gate.request(request)

    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.kind is AuditEventKind.APPROVAL
    assert event.outcome is expected_outcome
    assert event.detail["choice"] == expected_choice
    assert event.event_id == result.audit_id
    assert event.session_id == "s-cli"
    assert event.call_id == "c1"
    assert event.tool_name == "write_file"
    assert event.risk_level is RiskLevel.HIGH
    assert _datetime_is_aware(event.timestamp)


@pytest.mark.unit
def test_allow_always_is_recorded_not_silently_downgraded() -> None:
    """``R6``：``allow_always`` 本轮等价于 ``allow_once``，但**必须如实记录**。"""
    gate, sink, _ = _gate(interactive=True, line="2")

    result = gate.request(_request())

    assert result.outcome is ApprovalOutcome.ALLOW_ALWAYS
    assert sink.events[0].detail["choice"] == "allow_always"
    assert sink.events[0].outcome is AuditOutcome.ALLOW


# ---------------------------------------------------------------------------
# 提示面
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_prompt_sanitizes_the_untrusted_tool_name() -> None:
    """``tool_name`` 不可信 ⇒ 写进提示前经 ``sanitize_for_display``（ESC 被替换为 ``?``）。"""
    gate, _, prompt = _gate(interactive=True, line="3")

    gate.request(_request(tool_name=f"write_file{ESC}"))

    text = prompt.getvalue()
    assert "\x1b" not in text
    assert "?" in text
    assert "需人工确认" in text
    assert "我方生成的中文理由" in text
    assert "path=<str>" in text


@pytest.mark.unit
def test_prompt_marks_a_missing_arguments_summary_explicitly() -> None:
    """``arguments_summary is None`` ⇒ 明说"无摘要"，不静默留白。"""
    gate, _, prompt = _gate(interactive=True, line="3")

    gate.request(_request(arguments_summary=None))

    assert "（无可展示的参数摘要）" in prompt.getvalue()
