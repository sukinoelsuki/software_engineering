"""``harness/session.py`` 的行为断言（契约 §2.8 的装配期校验、§3.1 的装配四步、§2.9 的生命周期）。

覆盖 ``H-8``（装配期校验失败即拒绝启动）与 ``H-9``（``close()`` 幂等、``with`` 退出按序 teardown）。

这是**实现侧**的功能断言；对抗性验收属 ``tests/security/``（安全断言不得由实现者自证）。

变异探针（逐条能被一个具体改动杀死）：

* 把 ``_check_config`` 的任一条改成"回退默认值继续" ⇒
  :func:`test_invalid_numeric_parameters_are_rejected` 失败；
* 把 ``resolve_within`` 的调用删掉 ⇒ :func:`test_working_dir_outside_the_roots_is_rejected` 失败；
* 让领域包片段走 SYSTEM 位置 ⇒
  :func:`test_pack_fragments_enter_as_a_user_data_message` 失败（包内容会出现在 SYSTEM 文本里）；
* 把 ``allowlist`` 改成"并集"或删掉 ⇒ :func:`test_pack_allowlist_can_only_narrow` 失败；
* 给 ``loop`` 传 ``pack`` 对象而不是 ``pack_name`` ⇒ H2 的机器检查（``test_harness_internals``）变红；
* 把 ``close()`` 的幂等守卫删掉 ⇒ :func:`test_close_is_idempotent_and_ordered` 失败；
* 把 ``sink.flush()`` 从 ``finally`` 里挪出来 ⇒
  :func:`test_audit_is_flushed_even_when_the_model_close_fails` 失败。
"""

from __future__ import annotations

import pathlib
from collections.abc import Mapping, Sequence
from typing import Final

import pytest

from agent_sec_perf.contracts.audit import AuditEvent
from agent_sec_perf.contracts.harness import SessionConfig, SessionEventKind, TaskStatus
from agent_sec_perf.contracts.model import (
    CapabilityTier,
    ChatMessage,
    FinishReason,
    ModelResponse,
    Role,
    TokenUsage,
)
from agent_sec_perf.contracts.policy import Capability, PolicyDecision, PolicyRequest, RiskLevel
from agent_sec_perf.contracts.tools import ExecutionContext, ToolCallRequest, ToolResult, ToolSpec
from agent_sec_perf.foundation.errors import PathNotAllowedError
from agent_sec_perf.harness import prompts
from agent_sec_perf.harness.domain_pack import DomainPack
from agent_sec_perf.harness.session import Session

FRAGMENT: Final = "评审时先列出改动点（领域包数据段）"

READ_FILE = ToolSpec(
    name="read_file",
    description="读文件（测试用）",
    parameters_schema={"type": "object", "properties": {}, "additionalProperties": False},
    capabilities=frozenset({Capability.READ_FILE}),
)
WRITE_FILE = ToolSpec(
    name="write_file",
    description="写文件（测试用）",
    parameters_schema={"type": "object", "properties": {}, "additionalProperties": False},
    capabilities=frozenset({Capability.WRITE_FILE}),
)


# ---------------------------------------------------------------------------
# 替身
# ---------------------------------------------------------------------------


class _Recorder:
    """记录 teardown 的调用顺序（``H-9`` 的取证点）。"""

    def __init__(self) -> None:
        self.calls: list[str] = []


class _FakeTool:
    def __init__(self, spec: ToolSpec) -> None:
        self._spec = spec

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def invoke(self, args: Mapping[str, object], *, ctx: ExecutionContext) -> ToolResult:
        del args, ctx
        return ToolResult(ok=True, content="ok", audit_id="audit-tool")


class _FakeRegistry:
    def __init__(self, specs: Sequence[ToolSpec]) -> None:
        self._tools = {spec.name: _FakeTool(spec) for spec in specs}
        self._specs = tuple(specs)

    def specs(self) -> Sequence[ToolSpec]:
        return self._specs

    def resolve(self, name: str) -> _FakeTool | None:
        return self._tools.get(name)


class _RecordingModel:
    """记录 ``close()`` 顺序的模型客户端替身（可注入关闭失败）。"""

    def __init__(
        self,
        *,
        recorder: _Recorder,
        script: Sequence[ModelResponse] = (),
        close_error: BaseException | None = None,
    ) -> None:
        self._recorder = recorder
        self._script = list(script)
        self._close_error = close_error
        self.requests: list[tuple[tuple[ChatMessage, ...], tuple[ToolSpec, ...]]] = []

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        timeout_s: float = 60.0,
    ) -> ModelResponse:
        del temperature, max_tokens, timeout_s
        self.requests.append((tuple(messages), tuple(tools or ())))
        return self._script.pop(0)

    def close(self) -> None:
        self._recorder.calls.append("model.close")
        if self._close_error is not None:
            raise self._close_error


class _RecordingSink:
    """记录 ``flush()`` 顺序的审计落点替身。"""

    def __init__(self, *, recorder: _Recorder) -> None:
        self._recorder = recorder
        self.events: list[AuditEvent] = []

    def emit(self, event: AuditEvent) -> None:
        self.events.append(event)

    def flush(self) -> None:
        self._recorder.calls.append("sink.flush")


class _FakeValidator:
    def validate(self, *, spec: ToolSpec, arguments_json: str) -> Mapping[str, object]:
        del spec, arguments_json
        return {}


class _FakePolicy:
    """记录 ``PolicyRequest`` 的策略替身（用于断言 ``domain_pack`` 的传入口径）。"""

    def __init__(self, decision: PolicyDecision | None = None) -> None:
        self._decision = decision
        self.requests: list[PolicyRequest] = []

    def decide(self, request: PolicyRequest) -> PolicyDecision:
        self.requests.append(request)
        if self._decision is None:
            raise AssertionError("本用例不应触发策略求值")
        return self._decision


def _response(*calls: ToolCallRequest, content: str | None = None) -> ModelResponse:
    return ModelResponse(
        content=content,
        tool_calls=tuple(calls),
        finish_reason=FinishReason.TOOL_CALLS if calls else FinishReason.STOP,
        usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        model_id="fake-model",
    )


def _config(
    working_dir: pathlib.Path, roots: tuple[pathlib.Path, ...], **overrides: object
) -> SessionConfig:
    values: dict[str, object] = {
        "working_dir": working_dir,
        "allowed_roots": roots,
    }
    values.update(overrides)
    return SessionConfig(**values)  # type: ignore[arg-type]


def _workspace(tmp_path: pathlib.Path) -> tuple[pathlib.Path, tuple[pathlib.Path, ...]]:
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    return workspace, (tmp_path,)


def _pack(
    *,
    allowlist: frozenset[str] = frozenset({"read_file"}),
    fragments: tuple[str, ...] = (FRAGMENT,),
) -> DomainPack:
    return DomainPack(
        name="code-review",
        version="1.0.0",
        prompt_fragments=fragments,
        tool_allowlist=allowlist,
        capabilities_allowlist=frozenset({Capability.READ_FILE}),
        risk_overrides={},
        output_format="markdown",
        source=pathlib.Path("/tmp/pack/pack.toml"),
    )


def _build(
    *,
    tmp_path: pathlib.Path,
    config: SessionConfig | None = None,
    specs: Sequence[ToolSpec] = (READ_FILE, WRITE_FILE),
    pack: DomainPack | None = None,
    recorder: _Recorder | None = None,
    script: Sequence[ModelResponse] = (),
    close_error: BaseException | None = None,
    policy_decision: PolicyDecision | None = None,
) -> tuple[Session, _RecordingModel, _RecordingSink, _FakePolicy]:
    actual_recorder = recorder if recorder is not None else _Recorder()
    workspace, roots = _workspace(tmp_path)
    model = _RecordingModel(recorder=actual_recorder, script=script, close_error=close_error)
    sink = _RecordingSink(recorder=actual_recorder)
    policy = _FakePolicy(policy_decision)
    session = Session(
        session_id="s-session",
        config=config if config is not None else _config(workspace, roots),
        model=model,
        registry=_FakeRegistry(specs),
        policy=policy,
        approval=None,
        sink=sink,
        validator=_FakeValidator(),
        pack=pack,
    )
    return session, model, sink, policy


# ---------------------------------------------------------------------------
# H-8：装配期校验（失败即拒绝启动）
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_working_dir_outside_the_roots_is_rejected(tmp_path: pathlib.Path) -> None:
    """``working_dir`` 不在 ``allowed_roots`` 内 ⇒ ``PathNotAllowedError``（§2.8 第 1 条）。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    config = _config(workspace, (outside,))
    recorder = _Recorder()

    with pytest.raises(PathNotAllowedError):
        _build(tmp_path=tmp_path, config=config, recorder=recorder)

    # 拒绝发生在装配期：模型客户端**没有**被启动过，更没有跑过任何一步。
    assert recorder.calls == []


@pytest.mark.unit
def test_empty_allowed_roots_is_rejected(tmp_path: pathlib.Path) -> None:
    """空 ``allowed_roots`` ⇒ 拒绝启动（"什么都不允许"是拒绝，不是"不限制"）。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()

    with pytest.raises(ValueError, match="allowed_roots 不得为空"):
        _build(tmp_path=tmp_path, config=_config(workspace, ()))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        pytest.param({"max_steps": 0}, "max_steps", id="max_steps_zero"),
        pytest.param({"max_steps": -1}, "max_steps", id="max_steps_negative"),
        pytest.param({"max_consecutive_failures": 0}, "max_consecutive_failures", id="mcf_zero"),
        pytest.param({"tool_timeout_s": 0.0}, "tool_timeout_s", id="timeout_zero"),
        pytest.param({"tool_timeout_s": float("inf")}, "tool_timeout_s", id="timeout_inf"),
        pytest.param({"tool_timeout_s": float("nan")}, "tool_timeout_s", id="timeout_nan"),
        pytest.param({"max_prompt_tokens": 0}, "max_prompt_tokens", id="prompt_tokens_zero"),
    ],
)
def test_invalid_numeric_parameters_are_rejected(
    tmp_path: pathlib.Path, overrides: dict[str, object], expected: str
) -> None:
    """非法数值参数 ⇒ ``ValueError``，**不回退默认值继续**（§2.8 第 2/3 条）。"""
    workspace = tmp_path / "ws"
    workspace.mkdir()

    with pytest.raises(ValueError, match=expected):
        _build(tmp_path=tmp_path, config=_config(workspace, (tmp_path,), **overrides))


# ---------------------------------------------------------------------------
# 装配四步：暴露集合、SYSTEM 与数据段、pack_name 的传入口径
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_tier_and_pack_allowlist_can_only_narrow_the_exposure(tmp_path: pathlib.Path) -> None:
    """暴露集合 = 档位预算 ∩ 领域包白名单（两层都只收窄）。"""
    session, model, _, _ = _build(
        tmp_path=tmp_path,
        pack=_pack(allowlist=frozenset({"read_file"})),
        script=[_response(content="完成")],
    )

    list(session.run("任务"))

    assert [spec.name for spec in _tools_of(model)] == ["read_file"]


@pytest.mark.unit
def test_pack_allowlist_cannot_widen_beyond_the_tier_budget(tmp_path: pathlib.Path) -> None:
    """白名单里写了预算外的工具也**不会**被暴露（绝不并集）。"""
    session, model, _, _ = _build(
        tmp_path=tmp_path,
        config=_config_allowed(tmp_path, capability_tier=CapabilityTier.BASIC),
        pack=_pack(allowlist=frozenset({"read_file", "write_file"})),
        script=[_response(content="完成")],
    )

    list(session.run("任务"))

    assert [spec.name for spec in _tools_of(model)] == ["read_file"]


@pytest.mark.unit
def test_system_position_uses_the_frozen_prompt_template(tmp_path: pathlib.Path) -> None:
    """SYSTEM 位置的内容 = ``prompts.build_system_prompt(tier)``（§3.1 第 5 条，第七版）。

    钉住"取 ``str`` 形态"这个来源：SYSTEM 文本只由 ``prompts`` 的常量模板产生，
    不接受任何外部内容（§4.4 第 2 条的结构性保证）。
    """
    session, model, _, _ = _build(tmp_path=tmp_path, script=[_response(content="完成")])

    list(session.run("任务"))

    system_message = model.requests[0][0][0]
    assert system_message.role is Role.SYSTEM
    assert system_message.content == prompts.build_system_prompt(CapabilityTier.BASIC)


@pytest.mark.unit
def test_pack_fragments_enter_as_a_user_data_message(tmp_path: pathlib.Path) -> None:
    """领域包片段是**一条 ``role=USER`` 的数据消息**，且**不出现**在 SYSTEM 位置（§4.4 第 2 条）。"""
    session, model, _, _ = _build(
        tmp_path=tmp_path, pack=_pack(), script=[_response(content="完成")]
    )

    list(session.run("任务"))

    messages = model.requests[0][0]
    assert messages[0].role is Role.SYSTEM
    assert FRAGMENT not in (messages[0].content or "")
    assert messages[1].role is Role.USER
    assert FRAGMENT in (messages[1].content or "")
    assert messages[2].role is Role.USER
    assert messages[2].content == "任务"


@pytest.mark.unit
def test_pack_without_fragments_adds_no_data_message(tmp_path: pathlib.Path) -> None:
    """空片段 ⇒ **不产**数据消息（不编造空占位）；任务消息紧随 SYSTEM。"""
    session, model, _, _ = _build(
        tmp_path=tmp_path, pack=_pack(fragments=()), script=[_response(content="完成")]
    )

    list(session.run("任务"))

    messages = model.requests[0][0]
    assert messages[0].role is Role.SYSTEM
    assert messages[1].role is Role.USER
    assert messages[1].content == "任务"


@pytest.mark.unit
def test_pack_name_is_handed_to_the_policy_request(tmp_path: pathlib.Path) -> None:
    """传给 ``loop`` 的是 ``pack_name``（字符串），最终出现在 ``PolicyRequest.domain_pack``（H2）。"""
    call = ToolCallRequest(call_id="c1", name="read_file", arguments_json="{}")
    session, _, _, policy = _build(
        tmp_path=tmp_path,
        pack=_pack(),
        script=[_response(call), _response(content="完成")],
        policy_decision=PolicyDecision(
            allow=True,
            requires_confirmation=False,
            risk_level=RiskLevel.LOW,
            reason="测试决策",
            audit_id="policy-audit",
        ),
    )

    list(session.run("任务"))

    assert [request.domain_pack for request in policy.requests] == ["code-review"]


@pytest.mark.unit
def test_session_without_a_pack_uses_every_registered_name(tmp_path: pathlib.Path) -> None:
    """无 pack ⇒ 白名单取``注册表全量``（仍受档位预算约束），且 ``domain_pack`` 为 ``None``。"""
    session, model, _, _ = _build(
        tmp_path=tmp_path,
        config=_config_allowed(tmp_path, capability_tier=CapabilityTier.ADVANCED),
        script=[_response(content="完成")],
    )

    events = list(session.run("任务"))

    assert [spec.name for spec in _tools_of(model)] == ["read_file", "write_file"]
    assert events[-1].status is TaskStatus.COMPLETED


# ---------------------------------------------------------------------------
# H-9：生命周期
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_close_is_idempotent_and_ordered(tmp_path: pathlib.Path) -> None:
    """``close()`` 幂等；teardown 顺序为模型客户端 → 审计 flush（§2.9）。"""
    recorder = _Recorder()
    session, _, _, _ = _build(tmp_path=tmp_path, recorder=recorder)

    session.close()
    session.close()

    assert recorder.calls == ["model.close", "sink.flush"]


@pytest.mark.unit
def test_context_manager_tears_down_on_exit(tmp_path: pathlib.Path) -> None:
    """``with`` 退出即按序 teardown（``__enter__`` 返回自身）。"""
    recorder = _Recorder()
    session, _, _, _ = _build(
        tmp_path=tmp_path, recorder=recorder, script=[_response(content="完成")]
    )

    with session as entered:
        assert entered is session
        list(session.run("任务"))
        assert recorder.calls == []

    assert recorder.calls == ["model.close", "sink.flush"]


@pytest.mark.unit
def test_context_manager_tears_down_even_when_the_body_raises(tmp_path: pathlib.Path) -> None:
    """块内抛异常也**照样** teardown，且异常不被抑制。"""
    recorder = _Recorder()
    session, _, _, _ = _build(tmp_path=tmp_path, recorder=recorder)

    with pytest.raises(RuntimeError, match="块内失败"), session:
        raise RuntimeError("块内失败")

    assert recorder.calls == ["model.close", "sink.flush"]


@pytest.mark.unit
def test_audit_is_flushed_even_when_the_model_close_fails(tmp_path: pathlib.Path) -> None:
    """模型客户端关闭失败时**仍然** flush 审计（证据面优先），且异常原样冒泡。"""
    recorder = _Recorder()
    session, _, _, _ = _build(
        tmp_path=tmp_path, recorder=recorder, close_error=RuntimeError("关闭失败")
    )

    with pytest.raises(RuntimeError, match="关闭失败"):
        session.close()

    assert recorder.calls == ["model.close", "sink.flush"]


@pytest.mark.unit
def test_run_produces_a_terminated_event_stream(tmp_path: pathlib.Path) -> None:
    """``run`` 是透传：事件流以唯一一条 ``TASK_FINISHED`` 收尾（``I6``）。"""
    session, _, _, _ = _build(
        tmp_path=tmp_path, script=[_response(content="完成"), _response(content="第二次")]
    )

    first = list(session.run("任务一"))
    second = list(session.run("任务二"))

    assert [event.kind for event in first] == [
        SessionEventKind.MODEL_RESPONSE,
        SessionEventKind.TASK_FINISHED,
    ]
    # seq 跨 run 不重置（I9；会话状态由 loop 持有）。
    assert second[0].seq == len(first)


def _config_allowed(tmp_path: pathlib.Path, **overrides: object) -> SessionConfig:
    workspace = tmp_path / "ws"
    workspace.mkdir(exist_ok=True)
    return _config(workspace, (tmp_path,), **overrides)


def _tools_of(model: _RecordingModel) -> tuple[ToolSpec, ...]:
    """取该次会话实际暴露给模型的工具（``chat`` 的 ``tools`` 形参，最后一次调用为准）。"""
    assert model.requests, "模型替身未记录任何请求"
    return model.requests[-1][1]
