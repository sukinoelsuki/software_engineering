"""``harness/loop.py`` 的行为断言（契约 §3.3 的六步决策序列 + §2.2 的 ``I1``~``I10``）。

这是**实现侧**的功能断言：它证明"决策序列按契约走"，**不**证明安全属性。
对抗性验收（``S1`` / ``S1-b`` / ``S1-c`` / ``S-new-1``~``S-new-6``，见契约 §6.2）
属 ``tests/security/``，由验证角色独立完成——安全断言不得由实现者自证
（``CODEBUDDY.md`` §10.2 规则 4）。

**本文件的组织方式**：先把 ``I1``~``I10`` 写成一个**统一的断言函数**
（:func:`_assert_invariants`，即 ``H-1`` 要求的那个），再让**每个场景**复用它；
``H-2``（``seq`` 连续、``TASK_FINISHED`` 唯一且最后）由同一函数覆盖。

**``I1`` 的例外由断言判定，不由调用方的开关判定**：``R3``/``R4`` 等"响应中途终止"的路径上
``TOOL_CALL`` 已产出、``TOOL_RESULT`` 不再产出。契约 §2.2（**第七版**）只允许这种**悬空**出现在
"以 ``FAILED`` 终止**且**流中至少一条 ``ERROR(error_kind=INTERNAL)``"的流里 ⇒
``_assert_invariants`` **从流本身**判定（**没有** ``expect_pairing`` 这类开关——否则"例外"会退化成
"每个用例自己说自己可以不合规"），并由
:func:`test_the_dangling_call_rule_can_actually_fire` 的元测试证明该判据**真的会触发**。
理由与联动见 ``harness/loop.py`` 的模块 docstring。

变异探针（逐条能被一个具体改动杀死）：

* 把步 1 的两个短码合并成一个 ⇒ :func:`test_unknown_tool_and_trimmed_tool_get_different_codes` 失败；
* 把步 0 的 ``TOOL_CALL`` 挪到步 1 之后 ⇒ :func:`_assert_invariants` 的配对断言失败；
* 把 ``approval is None`` 分支改成"放行" ⇒ :func:`test_confirmation_without_gate_is_denied` 失败；
* 把 ``R3``（gate 抛异常）吞成一次普通拒绝 ⇒ :func:`test_gate_failure_terminates_the_task` 失败；
* 把 ``ERROR.text`` 改成 ``str(exc)`` ⇒ :func:`test_error_text_never_echoes_the_exception_message` 失败；
* 去掉 ``_deny`` 里那条 ``TOOL_CALL/DENY`` 审计 ⇒ ``I4`` 的回放断言失败；
* 把 ``_event`` 的 ``seq`` 改成每次 ``run`` 从 0 开始 ⇒ :func:`test_seq_is_not_reset_across_runs` 失败；
* 把 ``I1`` 例外的判定删掉、或退回成"调用方传一个 ``expect_pairing`` 开关" ⇒
  :func:`test_the_dangling_call_rule_can_actually_fire` 失败（判据会退化成"被默认"）。
"""

from __future__ import annotations

import pathlib
from collections.abc import Mapping, Sequence
from dataclasses import fields
from datetime import datetime
from typing import Final

import pytest

from agent_sec_perf.contracts.audit import AuditEvent, AuditEventKind, AuditOutcome
from agent_sec_perf.contracts.harness import (
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalResult,
    SessionConfig,
    SessionErrorKind,
    SessionEvent,
    SessionEventKind,
    TaskStatus,
)
from agent_sec_perf.contracts.model import (
    ChatMessage,
    FinishReason,
    ModelResponse,
    Role,
    TokenUsage,
)
from agent_sec_perf.contracts.policy import Capability, PolicyDecision, PolicyRequest, RiskLevel
from agent_sec_perf.contracts.tools import (
    ExecutionContext,
    ToolCallRequest,
    ToolResult,
    ToolSpec,
)
from agent_sec_perf.foundation.errors import (
    AuditWriteError,
    ModelProtocolError,
    ModelUnavailableError,
    ToolArgumentsInvalidError,
)
from agent_sec_perf.harness import errors
from agent_sec_perf.harness.loop import TaskLoop, summarize_arguments

WORKING_DIR: Final = pathlib.Path("/tmp/lowspec-test-ws")
ALLOWED_ROOTS: Final = (pathlib.Path("/tmp"),)

#: 唯一的 sentinel：用于证明"参数值不进事件 / 审计 / text"（``I8`` / ``V4``）。
SENTINEL: Final = "SENTINEL-7f3a-不可回显"

#: ``SessionEvent`` 的字段全集（``I10``：不得出现凭据类字段）。
EXPECTED_EVENT_FIELDS: Final = {
    "kind",
    "session_id",
    "seq",
    "timestamp",
    "call_id",
    "tool_name",
    "response",
    "decision",
    "approval",
    "result",
    "text",
    "error_kind",
    "status",
    "audit_id",
}


# ---------------------------------------------------------------------------
# 替身（测试自带构造器，不借用生产代码的实现）
# ---------------------------------------------------------------------------


class _FakeModel:
    """按脚本逐次返回响应 / 抛异常的模型客户端替身，并记录每次收到的消息与工具。"""

    def __init__(self, script: Sequence[ModelResponse | BaseException]) -> None:
        self._script = list(script)
        self.requests: list[tuple[tuple[ChatMessage, ...], tuple[ToolSpec, ...]]] = []
        self.closed = False

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        timeout_s: float = 60.0,
    ) -> ModelResponse:
        self.requests.append((tuple(messages), tuple(tools or ())))
        if not self._script:
            raise IndexError("测试脚本已用尽：用例给出的响应数少于循环实际请求数")
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self) -> None:
        self.closed = True

    def assert_exhausted(self) -> None:
        """断言脚本已被恰好用完（防止"多请求了一次"被静默吞掉）。"""
        assert self._script == []


class _FakeRegistry:
    """``ToolRegistry`` 的替身：全名集与可执行句柄都来自同一批工具。"""

    def __init__(self, tools: Sequence[_FakeTool]) -> None:
        self._tools = {tool.spec.name: tool for tool in tools}

    def specs(self) -> Sequence[ToolSpec]:
        return tuple(tool.spec for tool in self._tools.values())

    def resolve(self, name: str) -> _FakeTool | None:
        return self._tools.get(name)


class _FakeTool:
    """``Tool`` 的替身：记录每次 ``invoke``，并**像工具层一样**先记一条 ``TOOL_CALL`` 审计。

    已执行路径的审计生产者是**工具层**（``tools/registry.py::audit_tool_call``），不是
    ``loop``。替身复现这一点，``I4``（可回放）才能在已执行路径上被真正断言到，
    而不是靠"反正没人检查"通过。
    """

    def __init__(
        self,
        spec: ToolSpec,
        *,
        ok: bool = True,
        content: str = "ok",
        error: str | None = None,
        raises: BaseException | None = None,
    ) -> None:
        self._spec = spec
        self._ok = ok
        self._content = content
        self._error = error
        self._raises = raises
        self.calls: list[tuple[Mapping[str, object], ExecutionContext]] = []
        self.sink: _FakeSink | None = None

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def invoke(self, args: Mapping[str, object], *, ctx: ExecutionContext) -> ToolResult:
        self.calls.append((dict(args), ctx))
        if self._raises is not None:
            raise self._raises
        audit_id = f"audit-{self._spec.name}-{len(self.calls)}"
        if self.sink is not None:
            self.sink.emit(
                AuditEvent(
                    event_id=audit_id,
                    kind=AuditEventKind.TOOL_CALL,
                    timestamp="2026-09-19T00:00:00+00:00",
                    session_id=ctx.session_id,
                    outcome=AuditOutcome.OK if self._ok else AuditOutcome.ERROR,
                    call_id=ctx.call_id,
                    tool_name=self._spec.name,
                )
            )
        return ToolResult(
            ok=self._ok,
            content=self._content,
            error=self._error,
            truncated=False,
            audit_id=audit_id,
        )


class _FakeSink:
    """``AuditSink`` 的替身：只追加、可注入写入失败。"""

    def __init__(self, *, fail_on_call: int | None = None) -> None:
        self.events: list[AuditEvent] = []
        self.flushes = 0
        self._fail_on_call = fail_on_call

    def emit(self, event: AuditEvent) -> None:
        if self._fail_on_call is not None and len(self.events) == self._fail_on_call:
            msg = "审计写入失败（测试注入）"
            raise OSError(msg)
        self.events.append(event)

    def flush(self) -> None:
        self.flushes += 1


class _FakeValidator:
    """``ArgumentValidator`` 的替身：返回固定参数或抛指定异常。"""

    def __init__(
        self,
        *,
        arguments: Mapping[str, object] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self._arguments: Mapping[str, object] = {} if arguments is None else arguments
        self._error = error
        self.seen: list[tuple[str, str]] = []

    def validate(self, *, spec: ToolSpec, arguments_json: str) -> Mapping[str, object]:
        self.seen.append((spec.name, arguments_json))
        if self._error is not None:
            raise self._error
        return self._arguments


class _FakePolicy:
    """``PolicyEngine`` 的替身：按脚本返回决策（记录收到的请求）。"""

    def __init__(self, decisions: Sequence[PolicyDecision]) -> None:
        self._decisions = list(decisions)
        self.requests: list[PolicyRequest] = []

    def decide(self, request: PolicyRequest) -> PolicyDecision:
        self.requests.append(request)
        if not self._decisions:
            raise AssertionError("策略脚本已用尽：用例给出的决策数少于实际求值次数")
        return self._decisions.pop(0)


class _FakeGate:
    """``ApprovalGate`` 的替身：记录请求，返回应答或抛异常。"""

    def __init__(
        self,
        *,
        result: object | None = None,
        error: BaseException | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self.requests: list[ApprovalRequest] = []

    def request(self, request: ApprovalRequest) -> ApprovalResult:
        self.requests.append(request)
        if self._error is not None:
            raise self._error
        return self._result  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# 构造器
# ---------------------------------------------------------------------------


def _spec(name: str, *capabilities: Capability) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"{name}（测试用）",
        parameters_schema={"type": "object", "properties": {}, "additionalProperties": False},
        capabilities=frozenset(capabilities),
    )


READ_FILE = _spec("read_file", Capability.READ_FILE)


def _call(name: str, *, call_id: str = "c1", arguments_json: str = "{}") -> ToolCallRequest:
    return ToolCallRequest(call_id=call_id, name=name, arguments_json=arguments_json)


def _response(*calls: ToolCallRequest, content: str | None = None) -> ModelResponse:
    return ModelResponse(
        content=content,
        tool_calls=tuple(calls),
        finish_reason=FinishReason.TOOL_CALLS if calls else FinishReason.STOP,
        usage=TokenUsage(prompt_tokens=7, completion_tokens=3, total_tokens=10),
        model_id="fake-model",
    )


def _decision(
    *,
    allow: bool,
    requires_confirmation: bool,
    risk: RiskLevel = RiskLevel.MEDIUM,
    reason: str = "测试决策",
    audit_id: str = "policy-audit",
) -> PolicyDecision:
    return PolicyDecision(
        allow=allow,
        requires_confirmation=requires_confirmation,
        risk_level=risk,
        reason=reason,
        audit_id=audit_id,
    )


def _config(**overrides: object) -> SessionConfig:
    fields_map: dict[str, object] = {
        "working_dir": WORKING_DIR,
        "allowed_roots": ALLOWED_ROOTS,
        "max_steps": 12,
        "max_consecutive_failures": 3,
        "tool_timeout_s": 30.0,
        "max_prompt_tokens": 8192,
    }
    fields_map.update(overrides)
    return SessionConfig(**fields_map)  # type: ignore[arg-type]


def _build(
    *,
    responses: Sequence[ModelResponse | BaseException],
    tools: Sequence[_FakeTool] = (),
    exposed: tuple[ToolSpec, ...] | None = None,
    validator: _FakeValidator | None = None,
    decisions: Sequence[PolicyDecision] = (),
    gate: _FakeGate | None = None,
    sink: _FakeSink | None = None,
    config: SessionConfig | None = None,
    pack_name: str | None = None,
) -> tuple[TaskLoop, _FakeModel, _FakeSink]:
    model = _FakeModel(responses)
    actual_sink = sink if sink is not None else _FakeSink()
    registry = _FakeRegistry(tools)
    for tool in tools:
        # 工具层与 harness 共用同一个审计落点（装配点的真实形态）。
        tool.sink = actual_sink
    loop = TaskLoop(
        session_id="s-loop",
        config=config if config is not None else _config(),
        model=model,
        registry=registry,
        exposed=tuple(tool.spec for tool in tools) if exposed is None else exposed,
        policy=_FakePolicy(decisions),
        approval=gate,
        sink=actual_sink,
        validator=validator if validator is not None else _FakeValidator(),
        system="SYSTEM（测试常量）",
        pack_name=pack_name,
    )
    return loop, model, actual_sink


# ---------------------------------------------------------------------------
# ``H-1``：``I1``~``I10`` 的统一断言函数（每个场景复用）
# ---------------------------------------------------------------------------


def _dangling_call_ids(events: Sequence[SessionEvent]) -> set[str]:
    """模型请求过、但流中**没有** ``TOOL_RESULT`` 的 ``call_id``（§2.2 的 ``I1`` 例外面）。"""
    requested: set[str] = set()
    for event in events:
        if event.kind is SessionEventKind.MODEL_RESPONSE and event.response is not None:
            requested.update(call.call_id for call in event.response.tool_calls)
    answered = {event.call_id for event in events if event.kind is SessionEventKind.TOOL_RESULT}
    return {call_id for call_id in requested if call_id not in answered}


def _assert_invariants(
    events: Sequence[SessionEvent],
    *,
    sink: _FakeSink | None = None,
    first_seq: int = 0,
) -> None:
    """把契约 §2.2 的 ``I1``~``I10`` 一次断言完（``H-1`` / ``H-2``）。

    ``I1`` 的**例外**（第七版）由本函数**从流本身**判定，而**不是**由调用方开关：
    悬空 ``TOOL_CALL`` 只允许出现在"以 ``FAILED`` 终止**且**流中至少一条
    ``ERROR(error_kind=INTERNAL)``"的流里；任何 ``COMPLETED`` / ``LIMIT_REACHED`` 的流
    必须逐条配对。⇒ "没有 ``TOOL_RESULT``"本身不能是静默的，它必须伴随**响亮的终止**。
    （该判据本身由 :func:`test_the_dangling_call_rule_can_actually_fire` 证明会触发。）
    """
    assert events, "事件流不得为空"
    session_ids = {event.session_id for event in events}
    assert len(session_ids) == 1

    # I9 / H-2：seq 连续无空洞。
    seqs = [event.seq for event in events]
    assert seqs == list(range(first_seq, first_seq + len(seqs))), seqs

    # 时间戳带时区（与 AuditEvent 同口径；naive 时间被拒绝）。
    for event in events:
        assert datetime.fromisoformat(event.timestamp).tzinfo is not None

    # I10：事件不含凭据类字段。
    assert {field.name for field in fields(SessionEvent)} == EXPECTED_EVENT_FIELDS

    # I6 / H-2：TASK_FINISHED 恰好一条且是最后一条；status 与 text 均非空。
    finished = [event for event in events if event.kind is SessionEventKind.TASK_FINISHED]
    assert len(finished) == 1
    assert events[-1] is finished[0]
    assert finished[0].status is not None
    assert finished[0].text

    # I7（单向）：FAILED ⇒ 至少一条 ERROR；反向不成立。
    if finished[0].status is TaskStatus.FAILED:
        assert any(event.kind is SessionEventKind.ERROR for event in events)

    # I5：决策与审批的关联键。
    for event in events:
        if event.kind is SessionEventKind.POLICY_DECISION:
            assert event.decision is not None
            assert event.audit_id == event.decision.audit_id
            assert event.call_id is not None and event.tool_name is not None
        if event.kind is SessionEventKind.APPROVAL_RESULT:
            assert event.approval is not None
            assert event.audit_id == event.approval.audit_id
            assert event.call_id is not None and event.tool_name is not None

    tool_calls = [event for event in events if event.kind is SessionEventKind.TOOL_CALL]
    tool_results = [event for event in events if event.kind is SessionEventKind.TOOL_RESULT]
    position = {id(event): index for index, event in enumerate(events)}
    has_internal_error = any(
        event.kind is SessionEventKind.ERROR and event.error_kind is SessionErrorKind.INTERNAL
        for event in events
    )

    # I1 / I2 / I4：逐条模型请求的配对与相对位置。
    for event in events:
        if event.kind is not SessionEventKind.MODEL_RESPONSE:
            continue
        assert event.response is not None
        for call in event.response.tool_calls:
            calls = [item for item in tool_calls if item.call_id == call.call_id]
            results = [item for item in tool_results if item.call_id == call.call_id]
            assert len(calls) == 1, f"{call.call_id} 的 TOOL_CALL 不是恰好一条"
            assert calls[0].tool_name == call.name
            assert len(calls[0].__dict__) == len(EXPECTED_EVENT_FIELDS)
            if not results:
                # §2.2 的 I1 例外：**悬空 TOOL_CALL 必须伴随响亮的终止**。
                assert finished[0].status is TaskStatus.FAILED, (
                    f"{call.call_id} 悬空，但流以 {finished[0].status} 终止："
                    "悬空只允许出现在 FAILED 的流里"
                )
                assert has_internal_error, (
                    f"{call.call_id} 悬空，但流中没有 ERROR(error_kind=INTERNAL)："
                    "悬空必须伴随响亮的终止"
                )
                continue
            assert len(results) == 1, f"{call.call_id} 的 TOOL_RESULT 不是恰好一条"
            assert position[id(calls[0])] < position[id(results[0])]
            between = events[position[id(calls[0])] + 1 : position[id(results[0])]]
            policies = [item for item in between if item.kind is SessionEventKind.POLICY_DECISION]
            approvals = [item for item in between if item.kind is SessionEventKind.APPROVAL_RESULT]
            assert len(policies) <= 1
            assert len(approvals) <= 1
            if policies and approvals:
                assert position[id(policies[0])] < position[id(approvals[0])]

    # I3 / I4：结果的语义与可回放性。
    for result_event in tool_results:
        assert result_event.tool_name is not None
        assert result_event.audit_id
        if result_event.result is None:
            # 未执行 ⇒ text 必填（"拒绝"必须能说明理由）。
            assert result_event.text
        else:
            # 已执行 ⇒ 成败只能看 result.ok；且审计 id 与工具结果同源。
            assert result_event.audit_id == result_event.result.audit_id
        if sink is not None:
            replayable = [
                audit
                for audit in sink.events
                if audit.kind is AuditEventKind.TOOL_CALL
                and audit.call_id == result_event.call_id
                and audit.event_id == result_event.audit_id
            ]
            assert replayable, f"{result_event.call_id} 的审计事件不可回放"

    # I8：事件里不含原始 arguments_json（结构上：事件没有这个字段），
    # 且 sentinel **不出现**在任何 text / 工具名里。
    for event in events:
        for value in (event.text, event.tool_name):
            assert SENTINEL not in (value or "")
    if sink is not None:
        assert all(SENTINEL not in repr(dict(audit.detail)) for audit in sink.events)


def _raw_event(kind: SessionEventKind, seq: int, **fields: object) -> SessionEvent:
    """手工构造一条事件（**只用于元测试**：证明 H-1 的判据真的会触发）。"""
    return SessionEvent(
        kind=kind,
        session_id="s-synthetic",
        seq=seq,
        timestamp="2026-09-19T00:00:00+00:00",
        **fields,
    )


@pytest.mark.unit
def test_the_dangling_call_rule_can_actually_fire() -> None:
    """元测试：``H-1`` 的 ``I1`` 例外判据**真的会触发**（否则"被断言"退化成"被默认"）。

    没有这一条，``_assert_invariants`` 里的悬空检查在"判定写错了"时会**静默通过**——
    与 ``test_harness_internals.py::test_the_axis_guard_can_actually_fire`` 同一取向
    （``Makefile`` 对 ``LOCAL_HOOKS`` 的同一条教训：声称的缓解必须能被实测）。
    """
    response = _response(_call("read_file", call_id="c1"))

    # ① 悬空 + COMPLETED ⇒ 必须失败（悬空不得出现在"正常结束"的流里）。
    completed = [
        _raw_event(SessionEventKind.MODEL_RESPONSE, 0, response=response),
        _raw_event(SessionEventKind.TOOL_CALL, 1, call_id="c1", tool_name="read_file"),
        _raw_event(
            SessionEventKind.ERROR, 2, error_kind=SessionErrorKind.INTERNAL, text="伪造的内部错误"
        ),
        _raw_event(SessionEventKind.TASK_FINISHED, 3, status=TaskStatus.COMPLETED, text="完成"),
    ]
    with pytest.raises(AssertionError, match="悬空只允许出现在 FAILED 的流里"):
        _assert_invariants(completed)

    # ② 悬空 + FAILED，但**没有** ERROR(INTERNAL) ⇒ 也必须失败（"静默地没有 TOOL_RESULT"不行）。
    silent = [
        _raw_event(SessionEventKind.MODEL_RESPONSE, 0, response=response),
        _raw_event(SessionEventKind.TOOL_CALL, 1, call_id="c1", tool_name="read_file"),
        _raw_event(
            SessionEventKind.ERROR, 2, error_kind=SessionErrorKind.TRANSIENT, text="不可信断言"
        ),
        _raw_event(SessionEventKind.TASK_FINISHED, 3, status=TaskStatus.FAILED, text="失败"),
    ]
    with pytest.raises(AssertionError, match="必须伴随响亮的终止"):
        _assert_invariants(silent)

    # ③ 真终止路径的形状（悬空 + FAILED + INTERNAL）⇒ 合法，必须通过。
    legitimate = [
        _raw_event(SessionEventKind.MODEL_RESPONSE, 0, response=response),
        _raw_event(SessionEventKind.TOOL_CALL, 1, call_id="c1", tool_name="read_file"),
        _raw_event(
            SessionEventKind.ERROR, 2, error_kind=SessionErrorKind.INTERNAL, text="通路故障"
        ),
        _raw_event(SessionEventKind.TASK_FINISHED, 3, status=TaskStatus.FAILED, text="终止"),
    ]
    _assert_invariants(legitimate)


# ---------------------------------------------------------------------------
# 正常路径
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_completed_without_tool_calls_emits_response_then_finished() -> None:
    """模型直接给出最终回复 ⇒ ``MODEL_RESPONSE`` + ``TASK_FINISHED(COMPLETED)``。"""
    loop, model, sink = _build(responses=[_response(content="最终答复")])

    events = list(loop.run("任务"))

    assert [event.kind for event in events] == [
        SessionEventKind.MODEL_RESPONSE,
        SessionEventKind.TASK_FINISHED,
    ]
    assert events[1].status is TaskStatus.COMPLETED
    assert events[0].response is not None and events[0].response.content == "最终答复"
    model.assert_exhausted()
    _assert_invariants(events, sink=sink)


@pytest.mark.unit
def test_successful_tool_call_is_executed_and_fed_back_as_tool_message() -> None:
    """已执行的调用：``TOOL_RESULT`` 带 ``result``，并把观察内容作为 ``TOOL`` 消息回喂。"""
    tool = _FakeTool(READ_FILE, content="文件内容")
    validator = _FakeValidator(arguments={"path": "a.txt"})
    loop, model, sink = _build(
        responses=[
            _response(_call("read_file")),
            _response(content="读完的结论"),
        ],
        tools=[tool],
        validator=validator,
        decisions=[_decision(allow=True, requires_confirmation=False, risk=RiskLevel.LOW)],
    )

    events = list(loop.run("读文件"))

    assert tool.calls and tool.calls[0][0] == {"path": "a.txt"}
    results = [event for event in events if event.kind is SessionEventKind.TOOL_RESULT]
    assert results[0].result is not None and results[0].result.ok
    # 回喂：第二条请求的消息序列里必须有一条 TOOL 消息，且回指键正确。
    second_messages = model.requests[1][0]
    tool_messages = [message for message in second_messages if message.role is Role.TOOL]
    assert [message.content for message in tool_messages] == ["文件内容"]
    assert tool_messages[0].tool_call_id == "c1"
    # 且上一条 ASSISTANT 消息携带了同一个 tool_call（配对键的唯一来源）。
    assistant = [message for message in second_messages if message.role is Role.ASSISTANT]
    assert [call.call_id for call in assistant[-1].tool_calls] == ["c1"]
    model.assert_exhausted()
    _assert_invariants(events, sink=sink)


@pytest.mark.unit
def test_exposed_specs_are_handed_to_the_model() -> None:
    """暴露集合原样交给模型（``REQ-HARNESS-03`` 的落点；裁剪在 ``session`` 完成）。"""
    tool = _FakeTool(READ_FILE)
    loop, model, _ = _build(responses=[_response(content="ok")], tools=[tool])

    list(loop.run("任务"))

    assert [spec.name for spec in model.requests[0][1]] == ["read_file"]


# ---------------------------------------------------------------------------
# 拒绝路径：四个短码 + 审批 fail-secure
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_unknown_tool_and_trimmed_tool_get_different_codes() -> None:
    """``unknown_tool``（模型幻觉）与 ``not_exposed``（我们裁掉了）**必须可分**（``R-4``）。"""
    tool = _FakeTool(READ_FILE)
    # 注册表里有 read_file，但本次会话只暴露了空集合 ⇒ read_file 属"被裁剪"。
    loop, _, sink = _build(
        responses=[
            _response(_call("ghost_tool", call_id="c-ghost")),
            _response(_call("read_file", call_id="c-trimmed")),
            _response(content="收尾"),
        ],
        tools=[tool],
        exposed=(),
    )

    events = list(loop.run("任务"))

    assert tool.calls == []
    details = {
        (audit.call_id, audit.detail["denied_reason"])
        for audit in sink.events
        if audit.kind is AuditEventKind.TOOL_CALL
    }
    assert details == {("c-ghost", "unknown_tool"), ("c-trimmed", "not_exposed")}
    assert all(
        audit.outcome is AuditOutcome.DENY for audit in sink.events if audit.call_id is not None
    )
    results = [event for event in events if event.kind is SessionEventKind.TOOL_RESULT]
    assert [event.result for event in results] == [None, None]
    assert all(event.text for event in results)
    _assert_invariants(events, sink=sink)


@pytest.mark.unit
def test_invalid_arguments_are_denied_and_the_value_is_never_echoed() -> None:
    """参数校验失败 ⇒ ``invalid_arguments``；回喂的是校验器的说明，**不含参数值**（``V4``）。"""
    tool = _FakeTool(READ_FILE)
    validator = _FakeValidator(
        error=ToolArgumentsInvalidError(
            "键 path 类型不符：应为 string；键 ghost 未在 schema 中声明"
        )
    )
    loop, _, sink = _build(
        responses=[
            _response(_call("read_file", arguments_json=f'{{"path": "{SENTINEL}"}}')),
            _response(content="收尾"),
        ],
        tools=[tool],
        validator=validator,
    )

    events = list(loop.run("任务"))

    assert tool.calls == []
    assert sink.events[0].detail["denied_reason"] == "invalid_arguments"
    result = next(event for event in events if event.kind is SessionEventKind.TOOL_RESULT)
    assert result.result is None
    # 校验器的说明（键名与期望类型）是**有用**的回喂内容；参数值则永不出现。
    assert "path" in (result.text or "")
    assert SENTINEL not in (result.text or "")
    _assert_invariants(events, sink=sink)


@pytest.mark.unit
def test_hard_policy_denial_is_not_executed() -> None:
    """策略硬拒绝（``allow=False`` / ``requires_confirmation=False``）⇒ ``policy_denied``。"""
    tool = _FakeTool(READ_FILE)
    loop, _, sink = _build(
        responses=[_response(_call("read_file")), _response(content="收尾")],
        tools=[tool],
        decisions=[_decision(allow=False, requires_confirmation=False, risk=RiskLevel.CRITICAL)],
    )

    events = list(loop.run("任务"))

    assert tool.calls == []
    assert sink.events[0].detail["denied_reason"] == "policy_denied"
    decisions = [event for event in events if event.kind is SessionEventKind.POLICY_DECISION]
    assert len(decisions) == 1
    _assert_invariants(events, sink=sink)


@pytest.mark.unit
def test_confirmation_without_gate_is_denied_and_produces_no_approval_event() -> None:
    """``R1``：未提供确认通路 ⇒ 一律不执行，且**不产** ``APPROVAL_RESULT``（没有人被问过）。"""
    tool = _FakeTool(READ_FILE)
    loop, _, sink = _build(
        responses=[_response(_call("read_file")), _response(content="收尾")],
        tools=[tool],
        decisions=[_decision(allow=True, requires_confirmation=True)],
    )

    events = list(loop.run("任务"))

    assert tool.calls == []
    assert not [event for event in events if event.kind is SessionEventKind.APPROVAL_RESULT]
    assert sink.events[0].detail["denied_reason"] == "approval_denied"
    _assert_invariants(events, sink=sink)


@pytest.mark.unit
def test_confirmation_denied_by_gate_is_not_executed() -> None:
    """``gate`` 返回 ``DENY`` ⇒ ``approval_denied``，且确认结果被如实记录（``REQ-UX-02``）。"""
    tool = _FakeTool(READ_FILE)
    gate = _FakeGate(result=ApprovalResult(outcome=ApprovalOutcome.DENY, audit_id="approval-audit"))
    loop, _, sink = _build(
        responses=[_response(_call("read_file")), _response(content="收尾")],
        tools=[tool],
        decisions=[_decision(allow=True, requires_confirmation=True)],
        gate=gate,
    )

    events = list(loop.run("任务"))

    assert tool.calls == []
    approvals = [event for event in events if event.kind is SessionEventKind.APPROVAL_RESULT]
    assert [event.approval.outcome for event in approvals if event.approval] == [
        ApprovalOutcome.DENY
    ]
    assert sink.events[0].detail["denied_reason"] == "approval_denied"
    _assert_invariants(events, sink=sink)


@pytest.mark.unit
def test_audit_write_failure_escapes_run_instead_of_being_a_task_failure() -> None:
    """``P-3``：审计写入失败（**证据面损坏**）**不得**被收敛成"任务失败"。

    两者性质不同（威胁模型 §8.2 的 ``P-3``）：

    * 审计写入失败 = 证据面坏了——任务可能其实成功了，只是**我们没留下痕迹**；
    * 任务失败 = 业务路径真的没走通。

    同形会让消费者去查任务逻辑而不是磁盘，且**落盘的事件流会永久把这次事故记成"任务失败"**。
    """
    tool = _FakeTool(READ_FILE)
    gate = _FakeGate(error=AuditWriteError("审计写入失败：/tmp/x.jsonl"))
    loop, _, _ = _build(
        responses=[_response(_call("read_file"))],
        tools=[tool],
        decisions=[_decision(allow=True, requires_confirmation=True)],
        gate=gate,
    )

    with pytest.raises(AuditWriteError):
        list(loop.run("任务"))

    assert tool.calls == []  # 失败方向仍是"拒绝"，没有放行


@pytest.mark.unit
def test_generic_gate_failure_still_terminates_as_task_failure() -> None:
    """对照组（证明上一条**不是**恒过，且新增的 ``except`` **没有**过度捕获）。

    gate 抛**非审计**异常 ⇒ 仍按 ``R3`` 收敛为 ``ERROR(INTERNAL)`` + ``TASK_FINISHED(FAILED)``，
    且**不**逃逸出 ``run()``。若新增的 ``except AuditWriteError`` 写成了 ``except Exception``，
    本用例必红。
    """
    tool = _FakeTool(READ_FILE)
    gate = _FakeGate(error=RuntimeError("人工确认通路自身故障"))
    loop, _, _ = _build(
        responses=[_response(_call("read_file"))],
        tools=[tool],
        decisions=[_decision(allow=True, requires_confirmation=True)],
        gate=gate,
    )

    events = list(loop.run("任务"))  # 不得抛出

    assert tool.calls == []
    assert any(event.kind is SessionEventKind.ERROR for event in events)
    assert any(event.kind is SessionEventKind.TASK_FINISHED for event in events)


@pytest.mark.unit
def test_confirmation_allowed_by_gate_executes_the_tool() -> None:
    """``ALLOW_ONCE`` ⇒ 执行；且 ``APPROVAL_RESULT`` 的审计关联键与应答一致。"""
    tool = _FakeTool(READ_FILE, content="内容")
    gate = _FakeGate(
        result=ApprovalResult(outcome=ApprovalOutcome.ALLOW_ONCE, audit_id="approval-audit")
    )
    loop, _, sink = _build(
        responses=[_response(_call("read_file")), _response(content="收尾")],
        tools=[tool],
        decisions=[_decision(allow=True, requires_confirmation=True)],
        gate=gate,
    )

    events = list(loop.run("任务"))

    assert len(tool.calls) == 1
    approvals = [event for event in events if event.kind is SessionEventKind.APPROVAL_RESULT]
    assert approvals[0].audit_id == "approval-audit"
    assert gate.requests[0].call_id == "c1"
    assert gate.requests[0].tool_name == "read_file"
    _assert_invariants(events, sink=sink)


@pytest.mark.unit
def test_confirmation_request_carries_a_summary_not_the_raw_arguments() -> None:
    """确认请求带的是 ``harness`` 生成的**摘要**，不是原始 JSON（``S1``/``S3``）。"""
    tool = _FakeTool(READ_FILE, content="内容")
    gate = _FakeGate(
        result=ApprovalResult(outcome=ApprovalOutcome.ALLOW_ONCE, audit_id="approval-audit")
    )
    loop, _, _ = _build(
        responses=[_response(_call("read_file")), _response(content="收尾")],
        tools=[tool],
        validator=_FakeValidator(arguments={"path": f"a-{SENTINEL}.txt"}),
        decisions=[_decision(allow=True, requires_confirmation=True)],
        gate=gate,
    )

    list(loop.run("任务"))

    summary = gate.requests[0].arguments_summary
    assert summary is not None
    # 摘要**可以**含 str 值（它是操作对象，已净化 + 截断）；但绝不出现"原始 JSON 文本"。
    assert not summary.strip().startswith("{")
    assert "arguments_json" not in summary


@pytest.mark.unit
def test_gate_failure_terminates_the_task() -> None:
    """``R3``：审批通路故障 ⇒ ``ERROR(INTERNAL)`` + ``FAILED``，**不**吞成一次普通拒绝。"""
    tool = _FakeTool(READ_FILE)
    gate = _FakeGate(error=RuntimeError("TTY 不见了（不可信消息）"))
    loop, _, sink = _build(
        responses=[_response(_call("read_file"))],
        tools=[tool],
        decisions=[_decision(allow=True, requires_confirmation=True)],
        gate=gate,
    )

    events = list(loop.run("任务"))

    assert tool.calls == []
    assert sink.events == []  # 没有 TOOL_CALL/DENY —— 它不是"策略拒绝"
    error = next(event for event in events if event.kind is SessionEventKind.ERROR)
    assert error.error_kind is SessionErrorKind.INTERNAL
    assert events[-1].status is TaskStatus.FAILED
    # 本场景正是 §2.2 的 I1 例外：悬空 TOOL_CALL + FAILED + ERROR(INTERNAL)。
    assert _dangling_call_ids(events) == {"c1"}
    _assert_invariants(events, sink=sink)


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_answer",
    [
        pytest.param("not-an-approval-result", id="not_approval_result"),
        pytest.param(
            ApprovalResult(outcome="deny", audit_id="a"),  # type: ignore[arg-type]
            id="unknown_outcome",
        ),
        pytest.param(
            ApprovalResult(outcome=ApprovalOutcome.DENY, audit_id=""), id="empty_audit_id"
        ),
    ],
)
def test_malformed_approval_answer_terminates_the_task(bad_answer: object) -> None:
    """``R4``：应答形状非法 ⇒ 不执行 + ``ERROR(INTERNAL)`` + ``FAILED``。"""
    tool = _FakeTool(READ_FILE)
    loop, _, _ = _build(
        responses=[_response(_call("read_file"))],
        tools=[tool],
        decisions=[_decision(allow=True, requires_confirmation=True)],
        gate=_FakeGate(result=bad_answer),
    )

    events = list(loop.run("任务"))

    assert tool.calls == []
    assert any(
        event.kind is SessionEventKind.ERROR and event.error_kind is SessionErrorKind.INTERNAL
        for event in events
    )
    assert events[-1].status is TaskStatus.FAILED
    # 同样是 §2.2 的 I1 例外：悬空 TOOL_CALL，但终止是响亮的（FAILED + INTERNAL）。
    assert _dangling_call_ids(events) == {"c1"}
    _assert_invariants(events)


# ---------------------------------------------------------------------------
# 工具级失败 / 连续失败 / 后端不可达 / 步数用尽
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tool_level_failure_is_fed_back_without_an_error_event() -> None:
    """``result.ok=False`` 是**正常数据通路**：回喂 + 重置连续失败计数，**不产** ``ERROR``。"""
    tool = _FakeTool(READ_FILE, ok=False, content="", error="读不到")
    loop, model, sink = _build(
        responses=[_response(_call("read_file")), _response(content="收尾")],
        tools=[tool],
        decisions=[_decision(allow=True, requires_confirmation=False, risk=RiskLevel.LOW)],
    )

    events = list(loop.run("任务"))

    assert not [event for event in events if event.kind is SessionEventKind.ERROR]
    assert events[-1].status is TaskStatus.COMPLETED
    tool_messages = [message for message in model.requests[1][0] if message.role is Role.TOOL]
    assert [message.content for message in tool_messages] == ["读不到"]
    _assert_invariants(events, sink=sink)


@pytest.mark.unit
def test_tool_exception_becomes_a_tool_level_failure_with_an_audit_trail() -> None:
    """工具抛异常 ⇒ 合成 ``ToolResult(ok=False)`` + ``TOOL_CALL/ERROR`` 审计；**不回显异常消息**。"""
    tool = _FakeTool(READ_FILE, raises=RuntimeError(f"内部路径 /etc/{SENTINEL}"))
    loop, model, sink = _build(
        responses=[_response(_call("read_file")), _response(content="收尾")],
        tools=[tool],
        decisions=[_decision(allow=True, requires_confirmation=False, risk=RiskLevel.LOW)],
    )

    events = list(loop.run("任务"))

    result = next(event for event in events if event.kind is SessionEventKind.TOOL_RESULT)
    assert result.result is not None
    assert result.result.ok is False
    assert result.result.error == "工具内部错误：RuntimeError"
    assert sink.events[0].outcome is AuditOutcome.ERROR
    assert sink.events[0].detail["failed_reason"] == "tool_exception"
    tool_messages = [message for message in model.requests[1][0] if message.role is Role.TOOL]
    assert SENTINEL not in (tool_messages[0].content or "")
    _assert_invariants(events, sink=sink)


@pytest.mark.unit
def test_consecutive_failures_reach_the_limit_and_stall_the_task() -> None:
    """连续"未执行或工具级失败"达上限 ⇒ ``ERROR(STALLED)`` + ``FAILED``（§2.8）。"""
    tool = _FakeTool(READ_FILE)
    loop, _, sink = _build(
        responses=[
            _response(_call("ghost_tool", call_id="c1")),
            _response(_call("ghost_tool", call_id="c2")),
        ],
        tools=[tool],
        config=_config(max_consecutive_failures=2),
    )

    events = list(loop.run("任务"))

    errors_seen = [event for event in events if event.kind is SessionEventKind.ERROR]
    assert [event.error_kind for event in errors_seen] == [SessionErrorKind.STALLED]
    assert events[-1].status is TaskStatus.FAILED
    _assert_invariants(events, sink=sink)


@pytest.mark.unit
def test_unreachable_backend_is_retried_then_aborts() -> None:
    """``ModelUnavailableError``：预算内报 ``TRANSIENT`` 并重试，耗尽后报 ``UNREACHABLE``。"""
    loop, model, _ = _build(
        responses=[
            ModelUnavailableError("后端不可达"),
            ModelUnavailableError("后端不可达"),
            ModelUnavailableError("后端不可达"),
        ],
    )

    events = list(loop.run("任务"))

    kinds = [event.error_kind for event in events if event.kind is SessionEventKind.ERROR]
    assert kinds == [
        SessionErrorKind.TRANSIENT,
        SessionErrorKind.TRANSIENT,
        SessionErrorKind.UNREACHABLE,
    ]
    # 重试**不**新开一步：总尝试次数 = 1 + MAX_TRANSIENT_RETRIES。
    assert len(model.requests) == errors.MAX_TRANSIENT_RETRIES + 1
    assert events[-1].status is TaskStatus.FAILED
    _assert_invariants(events)


@pytest.mark.unit
def test_error_text_never_echoes_the_exception_message() -> None:
    """``ERROR.text`` = 我方常量 + **类型名**；异常消息里的不可信串不得进事件流（§2.4）。"""
    loop, _, _ = _build(
        responses=[
            ModelProtocolError(f"响应体里出现了 {SENTINEL}"),
            ModelProtocolError(f"响应体里出现了 {SENTINEL}"),
        ],
    )

    events = list(loop.run("任务"))

    for event in events:
        assert SENTINEL not in (event.text or "")
    error = next(event for event in events if event.kind is SessionEventKind.ERROR)
    assert error.error_kind is SessionErrorKind.PROTOCOL
    assert "ModelProtocolError" in (error.text or "")


@pytest.mark.unit
def test_step_budget_exhaustion_yields_limit_reached() -> None:
    """步数预算用尽 ⇒ ``TASK_FINISHED(LIMIT_REACHED)``（**不是** ``FAILED``；§2.3 的理由）。"""
    tool = _FakeTool(READ_FILE)
    loop, model, sink = _build(
        responses=[_response(_call("read_file"))],
        tools=[tool],
        decisions=[_decision(allow=True, requires_confirmation=False, risk=RiskLevel.LOW)],
        config=_config(max_steps=1),
    )

    events = list(loop.run("任务"))

    assert events[-1].status is TaskStatus.LIMIT_REACHED
    assert len(model.requests) == 1
    model.assert_exhausted()
    _assert_invariants(events, sink=sink)


# ---------------------------------------------------------------------------
# 审计面与序号
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_audit_failure_propagates_out_of_run() -> None:
    """审计写入失败**必须冒泡**（不得被转成 ``ERROR`` 事件后继续；``S-new-3`` 的实现侧对应）。"""
    tool = _FakeTool(READ_FILE)
    sink = _FakeSink(fail_on_call=0)
    loop, _, _ = _build(
        responses=[_response(_call("ghost_tool")), _response(content="收尾")],
        tools=[tool],
        sink=sink,
    )

    with pytest.raises(OSError, match="审计写入失败"):
        list(loop.run("任务"))


@pytest.mark.unit
def test_seq_is_not_reset_across_runs() -> None:
    """``seq`` 在同一实例的生命周期内连续无空洞，**多次 run 不重置**（``I9``）。"""
    loop, _, _ = _build(responses=[_response(content="一"), _response(content="二")])

    first = list(loop.run("任务一"))
    second = list(loop.run("任务二"))

    _assert_invariants(first, first_seq=0)
    _assert_invariants(second, first_seq=len(first))


# ---------------------------------------------------------------------------
# ``summarize_arguments``（契约 §2.5.3 的 ``S2`` / ``S5``）
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_arguments_summary_returns_none_for_an_empty_mapping() -> None:
    """空参数映射 ⇒ ``None``（``S5``：不编造 ``{}`` 一类占位）。"""
    assert summarize_arguments({}) is None


@pytest.mark.unit
def test_arguments_summary_is_sorted_and_marks_non_scalar_shapes() -> None:
    """键名升序；非标量只给形态与元素个数（不静默略去，也不回显标量取值）。"""
    summary = summarize_arguments(
        {"z": 1, "a": "v", "m": [1, 2, 3], "n": {"x": 1, "y": 2}, "f": 1.5, "b": True, "k": None}
    )

    assert summary is not None
    assert summary.split("; ")[0] == "a=v"
    assert "b=<bool>" in summary
    assert "f=<float>" in summary
    assert "k=<null>" in summary
    assert "m=<list: 3>" in summary
    assert "n=<object: 2>" in summary
    assert "z=<int>" in summary


@pytest.mark.unit
def test_arguments_summary_is_bounded_and_marks_truncation() -> None:
    """超过 8 个键时标注"还有 N 项"；总长不超过 400 字符（``S2``）。"""
    many = {f"key{index:03d}": "x" * 200 for index in range(12)}

    summary = summarize_arguments(many)

    assert summary is not None
    assert len(summary) <= 400
    assert summary.endswith("（已截断）")
    assert "key000" in summary


@pytest.mark.unit
def test_arguments_summary_marks_extra_keys_when_under_the_length_cap() -> None:
    """键数超上限但正文没超长 ⇒ 仍要标出"还有 N 项"（两个上限互相独立）。"""
    summary = summarize_arguments({f"k{index}": index for index in range(11)})

    assert summary is not None
    assert summary.endswith("; …（还有 3 项）")


@pytest.mark.unit
def test_arguments_summary_sanitizes_control_characters() -> None:
    """控制字符（含 ESC）先被替换为 ``?``：摘要会进确认界面，不能允许篡改显示（§2.6 第 5 条）。"""
    summary = summarize_arguments({"a": "\x1b[31mred\x07"})

    assert summary is not None
    assert "\x1b" not in summary
    assert "?" in summary


@pytest.mark.unit
def test_denied_reason_constants_match_the_closed_set() -> None:
    """审计短码 = 契约 §2.7 ``D2`` 的闭集（多一个少一个都是对审计语义的改动）。"""
    from agent_sec_perf.harness import loop as loop_module

    assert {
        loop_module.DENIED_UNKNOWN_TOOL,
        loop_module.DENIED_NOT_EXPOSED,
        loop_module.DENIED_INVALID_ARGUMENTS,
        loop_module.DENIED_POLICY_DENIED,
        loop_module.DENIED_APPROVAL_DENIED,
    } == {"unknown_tool", "not_exposed", "invalid_arguments", "policy_denied", "approval_denied"}
