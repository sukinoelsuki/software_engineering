"""harness 对抗性测试的构造注入替身（``Protocol`` 实现）。

本模块**不承担任何断言**：所有判定都在 ``tests/security/test_harness_*`` 里针对
``agent_sec_perf.harness.Session`` 的**真实行为**做出。这里的 fake 只是把依赖以契约
要求的构造注入方式提供进去，并且在被调用时**只记录调用事实**（记录 != 断言）。

注入合法性：``Session`` / ``TaskLoop`` 的依赖全部是构造注入的 ``Protocol``
（ADR-0015 §7.3 / 契约 §3.1），因此写 fake 驱动用例是被允许的；但被测行为**不得**
是 fake 自己的语义。例如在 ``S-new-1`` 里，``FakeApprovalGate`` 只是按 ``outcome``
返回 ``ApprovalResult``，真正的断言是「事件流里出现 ``APPROVAL_RESULT`` 且
``outcome is DENY``、审计里出现 ``kind=APPROVAL``」——那是对 harness 的断言。
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from agent_sec_perf.contracts.audit import (
    AuditEvent,
    AuditEventKind,
    AuditOutcome,
    AuditSink,
)
from agent_sec_perf.contracts.harness import (
    ApprovalGate,
    ApprovalOutcome,
    ApprovalRequest,
    ApprovalResult,
    ArgumentValidator,
    CapabilityTier,
    Session,
    SessionConfig,
)
from agent_sec_perf.contracts.model import (
    FinishReason,
    ModelClient,
    ModelResponse,
    TokenUsage,
    ToolCallRequest,
)
from agent_sec_perf.contracts.policy import (
    Capability,
    PolicyDecision,
    PolicyEngine,
    PolicyRequest,
)
from agent_sec_perf.contracts.tools import (
    ExecutionContext,
    Tool,
    ToolRegistry,
    ToolResult,
    ToolSpec,
)


def _now() -> str:
    """带时区的 ISO-8601 时间戳（与 ``AuditEvent`` 同口径）。"""
    return datetime.now(tz=UTC).isoformat()


class RecordingSink(AuditSink):
    """只捕获 ``emit`` 事件的审计桩（不含任何判定逻辑）。"""

    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def emit(self, event: AuditEvent) -> None:
        self.events.append(event)

    def flush(self) -> None:
        pass


class RaisingSink(AuditSink):
    """``emit`` 一律抛异常的审计桩（用于 ``S-new-3``：「审计写入失败必须冒泡」）。"""

    def emit(self, event: AuditEvent) -> None:
        msg = "injected audit failure"
        raise RuntimeError(msg)

    def flush(self) -> None:
        pass


def make_tool_spec(*, name: str, capabilities: frozenset[Capability]) -> ToolSpec:
    """构造一个最小 ``ToolSpec``（只读声明，不含行为）。"""
    return ToolSpec(
        name=name,
        description=f"fake tool {name}",
        parameters_schema={"type": "object", "properties": {}},
        capabilities=capabilities,
        source="builtin",
        description_digest=None,
    )


def make_tool_call_response(*, call_id: str, name: str, arguments_json: str) -> ModelResponse:
    """构造「含一次工具调用」的模型响应（用于驱动决策序列）。"""
    return ModelResponse(
        content=None,
        tool_calls=(ToolCallRequest(call_id=call_id, name=name, arguments_json=arguments_json),),
        finish_reason=FinishReason.TOOL_CALLS,
        usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        model_id="fake-model",
    )


def make_final_response(*, content: str = "done") -> ModelResponse:
    """构造「无工具调用」的最终响应（让会话以 ``COMPLETED`` 收尾）。"""
    return ModelResponse(
        content=content,
        tool_calls=(),
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        model_id="fake-model",
    )


class FakeModelClient(ModelClient):
    """构造注入的模型客户端替身：按预设序列返回响应（不承担断言）。"""

    def __init__(self, first: ModelResponse, then_final: ModelResponse | None = None) -> None:
        self._first = first
        self._final = then_final or make_final_response()
        self.chat_calls = 0

    def chat(self, messages, *, tools=None, temperature=0.0, max_tokens=None, timeout_s=60.0):
        self.chat_calls += 1
        if self.chat_calls == 1:
            return self._first
        return self._final

    def close(self) -> None:
        pass


class FakeTool(Tool):
    """构造注入的工具替身：记录调用（含 ``ctx``），返回预设结果（不承担断言）。"""

    def __init__(self, spec: ToolSpec, result: ToolResult | None = None) -> None:
        self._spec = spec
        self._result = result or ToolResult(ok=True, content="ok", audit_id="tool-ok-1")
        self.invoke_calls: list[tuple[Mapping[str, object], ExecutionContext]] = []

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def invoke(self, args: Mapping[str, object], *, ctx: ExecutionContext) -> ToolResult:
        self.invoke_calls.append((args, ctx))
        return self._result


class FakeRegistry(ToolRegistry):
    """构造注入的注册表替身：以全量 spec 集合 + 名字→工具映射提供解析（不承担断言）。"""

    def __init__(self, specs: Sequence[ToolSpec], tools_by_name: Mapping[str, Tool]) -> None:
        self._specs = tuple(specs)
        self._tools = dict(tools_by_name)

    def specs(self) -> Sequence[ToolSpec]:
        return self._specs

    def resolve(self, name: str) -> Tool | None:
        return self._tools.get(name)


class FakePolicyEngine(PolicyEngine):
    """构造注入的策略引擎替身：返回预设决策，并忠实复刻「decide 内 emit POLICY_DECISION 审计」。

    真实 ``PolicyEngine`` 会在 ``decide()`` 内 emit 一条 ``POLICY_DECISION`` 审计
    （``audit.md``）；本替身复刻该行为（用 ``decision.audit_id`` 作事件 id），使
    「审计可回放」断言在 fake 下仍成立。它**只记录 decide 调用**，不判定任何安全语义。
    """

    def __init__(self, decision: PolicyDecision, sink: AuditSink) -> None:
        self._decision = decision
        self._sink = sink
        self.decide_calls: list[PolicyRequest] = []

    def decide(self, request: PolicyRequest) -> PolicyDecision:
        self.decide_calls.append(request)
        self._sink.emit(
            AuditEvent(
                event_id=self._decision.audit_id,
                kind=AuditEventKind.POLICY_DECISION,
                timestamp=_now(),
                session_id=request.session_id,
                outcome=AuditOutcome.DENY if not self._decision.allow else AuditOutcome.ALLOW,
                call_id=request.call_id,
                tool_name=request.tool_name,
                capability=None,
                risk_level=self._decision.risk_level,
                detail={"requested": sorted(c.value for c in request.requested)},
            )
        )
        return self._decision


class FakeArgumentValidator(ArgumentValidator):
    """构造注入的参数校验器替身：返回预设的结构化参数，或抛预设异常（不承担断言）。"""

    def __init__(
        self,
        result: Mapping[str, object] | None = None,
        exc: Exception | None = None,
    ) -> None:
        self._result = result if result is not None else {}
        self._exc = exc
        self.validate_calls: list[tuple[ToolSpec, str]] = []

    def validate(self, *, spec: ToolSpec, arguments_json: str) -> Mapping[str, object]:
        self.validate_calls.append((spec, arguments_json))
        if self._exc is not None:
            raise self._exc
        return self._result


def _approval_audit_outcome(outcome: ApprovalOutcome) -> AuditOutcome:
    """确认审计结果的口径映射（DENY→DENY，其余→CONFIRM）。"""
    if outcome is ApprovalOutcome.DENY:
        return AuditOutcome.DENY
    return AuditOutcome.CONFIRM


class FakeApprovalGate(ApprovalGate):
    """构造注入的确认通路替身。

    - ``outcome``：返回的 ``ApprovalResult`` 决定；
    - ``raise_with``：非 ``None`` 时 ``request`` 直接抛该异常（R3 故障场景）；
    - ``bad_return``：非 ``None`` 时直接返回该对象（用于 R4 形状非法场景）；
    - 仅当真正返回 ``ApprovalResult`` 时，按契约 ``A4`` 先 emit 一条 ``kind=APPROVAL`` 审计。
    """

    def __init__(
        self,
        sink: AuditSink,
        outcome: ApprovalOutcome = ApprovalOutcome.DENY,
        audit_id: str | None = None,
        raise_with: Exception | None = None,
        bad_return: object | None = None,
    ) -> None:
        self._sink = sink
        self._outcome = outcome
        self._audit_id = audit_id or f"approval-{uuid.uuid4().hex}"
        self._raise_with = raise_with
        self._bad_return = bad_return
        self.request_calls: list[ApprovalRequest] = []

    def request(self, request: ApprovalRequest) -> ApprovalResult:
        self.request_calls.append(request)
        if self._raise_with is not None:
            raise self._raise_with
        if self._bad_return is not None:
            return self._bad_return
        self._sink.emit(
            AuditEvent(
                event_id=self._audit_id,
                kind=AuditEventKind.APPROVAL,
                timestamp=_now(),
                session_id=request.session_id,
                outcome=_approval_audit_outcome(self._outcome),
                call_id=request.call_id,
                tool_name=request.tool_name,
                detail={},
            )
        )
        return ApprovalResult(outcome=self._outcome, audit_id=self._audit_id)


def make_session(
    *,
    session_id: str,
    working_dir: Path,
    allowed_roots: tuple[Path, ...],
    sink: AuditSink,
    model: ModelClient,
    registry: ToolRegistry,
    policy: PolicyEngine,
    validator: ArgumentValidator,
    approval: ApprovalGate | None = None,
    capability_tier: CapabilityTier = CapabilityTier.BASIC,
) -> Session:
    """用构造注入的 fake 装配一个真实 ``Session``（只暴露可信的契约边界）。"""
    config = SessionConfig(
        working_dir=working_dir,
        allowed_roots=allowed_roots,
        capability_tier=capability_tier,
        max_steps=12,
        max_consecutive_failures=3,
        tool_timeout_s=30.0,
        max_prompt_tokens=8192,
        max_completion_tokens=None,
    )
    return Session(
        session_id=session_id,
        config=config,
        model=model,
        registry=registry,
        policy=policy,
        approval=approval,
        sink=sink,
        validator=validator,
        pack=None,
    )


def collect(session: Session, task: str) -> list:
    """把事件流物化为列表（触发整个决策序列；便于断言）。"""
    return list(session.run(task))
