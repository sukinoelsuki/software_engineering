"""``cli/app.py`` 的行为断言（契约 ``harness.md`` §5.1 的装配顺序 + §5.2 的退出码表与输出通道）。

实现侧的功能断言；对抗性验收属 ``tests/security/``（安全断言不得由实现者自证）。

本文件钉住的道口：

* **退出码表逐行**：``0`` / ``1`` / ``2`` / ``3`` / ``4`` / ``5`` 各一条；
* **输出通道**：``--output-format json`` 时 stdout 只出 JSONL（诊断走 stderr）；
* **``R1`` 经 CLI 成立**：非交互模式**显式传 ``None``** ⇒ 需确认的调用被拒、
  ``TOOL_RESULT(result=None)``、且**不产生** ``APPROVAL_RESULT``；
* **装配失败即拒绝启动**：领域包缺失 ``pack.toml`` ⇒ 退出码 ``3``（不降级为"无 pack 继续跑"）；
* Typer 应用注册可用（``--help``）。

替身全部**手写**：用生产代码构造就等于让被测对象自己出题。
"""

from __future__ import annotations

import io
import json
import pathlib
from collections.abc import Mapping, Sequence
from typing import Final

import pytest
from typer.testing import CliRunner

from agent_sec_perf.cli.app import (
    EXIT_ASSEMBLY,
    EXIT_AUDIT,
    EXIT_LIMIT_REACHED,
    EXIT_OK,
    EXIT_TASK_FAILED,
    EXIT_UNEXPECTED,
    RunRequest,
    app,
    execute,
)
from agent_sec_perf.contracts.audit import AuditEvent, AuditEventKind, AuditOutcome
from agent_sec_perf.contracts.model import (
    ChatMessage,
    FinishReason,
    ModelResponse,
    TokenUsage,
)
from agent_sec_perf.contracts.tools import ToolCallRequest, ToolSpec
from agent_sec_perf.foundation.config import AppConfig, PolicyConfig
from agent_sec_perf.foundation.errors import ModelUnavailableError

TASK: Final = "读一下工作目录"


# ---------------------------------------------------------------------------
# 替身
# ---------------------------------------------------------------------------


class _ScriptedModel:
    """``ModelClient`` 的替身：按脚本逐次返回响应 / 抛异常，并记录请求。"""

    def __init__(
        self,
        script: Sequence[ModelResponse | BaseException],
        *,
        close_error: BaseException | None = None,
    ) -> None:
        self._script = list(script)
        self._close_error = close_error
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
            raise AssertionError("模型脚本已用尽：用例给出的响应数少于循环实际请求数")
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def close(self) -> None:
        self.closed = True
        if self._close_error is not None:
            raise self._close_error


class _RecordingSink:
    """``AuditSink`` 的替身：可注入 ``emit`` / ``flush`` 失败（用于退出码 ``4``）。"""

    def __init__(self, *, fail_emit: bool = False, fail_flush: bool = False) -> None:
        self.events: list[AuditEvent] = []
        self.flushes = 0
        self._fail_emit = fail_emit
        self._fail_flush = fail_flush

    def emit(self, event: AuditEvent) -> None:
        if self._fail_emit:
            msg = "审计写入失败（测试注入）"
            raise OSError(msg)
        self.events.append(event)

    def flush(self) -> None:
        if self._fail_flush:
            msg = "审计写入失败（测试注入）"
            raise OSError(msg)
        self.flushes += 1


# ---------------------------------------------------------------------------
# 构造器
# ---------------------------------------------------------------------------


def _call(name: str, *, arguments_json: str = "{}") -> ToolCallRequest:
    return ToolCallRequest(call_id="c1", name=name, arguments_json=arguments_json)


def _response(
    content: str | None = None, *, calls: tuple[ToolCallRequest, ...] = ()
) -> ModelResponse:
    return ModelResponse(
        content=content,
        tool_calls=calls,
        finish_reason=FinishReason.TOOL_CALLS if calls else FinishReason.STOP,
        usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        model_id="fake-model",
    )


def _request(tmp_path: pathlib.Path, **overrides: object) -> RunRequest:
    fields: dict[str, object] = {
        "task": TASK,
        "working_dir": tmp_path,
        "allowed_roots": (tmp_path,),
    }
    fields.update(overrides)
    return RunRequest(**fields)  # type: ignore[arg-type]


def _run(
    request: RunRequest,
    *,
    model: _ScriptedModel,
    sink: _RecordingSink,
    config: AppConfig | None = None,
) -> tuple[int, str, str]:
    """跑一次 ``execute``，返回 ``(退出码, stdout, stderr)``。"""
    out = io.StringIO()
    err = io.StringIO()
    code = execute(
        request,
        stdout=out,
        stderr=err,
        config=AppConfig() if config is None else config,
        sink=sink,
        model=model,
    )
    return code, out.getvalue(), err.getvalue()


def _json_lines(text: str) -> list[Mapping[str, object]]:
    """把 stdout 解析成 JSONL；任何非 JSON 行都会让用例失败（这正是被测的道口）。"""
    return [json.loads(line) for line in text.splitlines() if line.strip()]


# ---------------------------------------------------------------------------
# 退出码表逐行（§5.2）
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_completed_task_exits_zero(tmp_path: pathlib.Path) -> None:
    """``TASK_FINISHED.status is COMPLETED`` ⇒ ``0``。"""
    code, out, _ = _run(
        _request(tmp_path),
        model=_ScriptedModel([_response(content="done")]),
        sink=_RecordingSink(),
    )

    assert code == EXIT_OK
    assert "done" in out


@pytest.mark.unit
def test_failed_task_exits_one(tmp_path: pathlib.Path) -> None:
    """``TASK_FINISHED.status is FAILED`` ⇒ ``1``（重试预算耗尽后终止）。"""
    code, _, _ = _run(
        _request(tmp_path),
        model=_ScriptedModel([ModelUnavailableError("后端不可达")] * 3),
        sink=_RecordingSink(),
    )

    assert code == EXIT_TASK_FAILED


@pytest.mark.unit
def test_limit_reached_exits_two(tmp_path: pathlib.Path) -> None:
    """``TASK_FINISHED.status is LIMIT_REACHED`` ⇒ ``2``（成功但用尽步数预算）。"""
    code, _, _ = _run(
        _request(tmp_path, max_steps=1),
        model=_ScriptedModel([_response(calls=(_call("ghost_tool"),))]),
        sink=_RecordingSink(),
    )

    assert code == EXIT_LIMIT_REACHED


@pytest.mark.unit
def test_assembly_failure_exits_three(tmp_path: pathlib.Path) -> None:
    """装配期 ``BenchError`` ⇒ ``3``：领域包目录缺 ``pack.toml`` ⇒ ``DomainPackError``。"""
    pack_directory = tmp_path / "empty-pack"
    pack_directory.mkdir()

    code, out, _ = _run(
        _request(tmp_path, pack_directory=pack_directory),
        model=_ScriptedModel([_response(content="不该跑到这里")]),
        sink=_RecordingSink(),
    )

    assert code == EXIT_ASSEMBLY
    assert out == ""


@pytest.mark.unit
def test_missing_model_path_exits_three(tmp_path: pathlib.Path) -> None:
    """未给 ``--model-path`` 且未注入模型 ⇒ 拒绝启动（``3``），**不猜**一个默认模型。

    这条路径在构造 ``LocalLlamaClient`` **之前**就抛 ``ConfigError``，因此不会尝试启动
    任何子进程（用例在有 / 无 ``llama-server`` 的环境下行为一致）。
    """
    out = io.StringIO()
    err = io.StringIO()

    code = execute(
        _request(tmp_path),
        stdout=out,
        stderr=err,
        config=AppConfig(),
        sink=_RecordingSink(),
        model=None,
    )

    assert code == EXIT_ASSEMBLY
    assert out.getvalue() == ""


@pytest.mark.unit
def test_audit_flush_failure_exits_four(tmp_path: pathlib.Path) -> None:
    """``session.close()`` 里的 ``sink.flush()`` 失败 ⇒ ``4``（证据面故障单列一类）。"""
    code, _, err = _run(
        _request(tmp_path),
        model=_ScriptedModel([_response(content="done")]),
        sink=_RecordingSink(fail_flush=True),
    )

    assert code == EXIT_AUDIT
    # 结构化日志经 JSONRenderer 输出，非 ASCII 会被转义 ⇒ 断言打在 ASCII 字段名上。
    assert "error_type" in err


@pytest.mark.unit
def test_audit_emit_failure_exits_four(tmp_path: pathlib.Path) -> None:
    """拒绝路径上的 ``sink.emit`` 失败冒泡出 ``run()`` ⇒ ``4``（与"任务失败"区分开）。"""
    code, _, err = _run(
        _request(tmp_path, max_steps=1),
        model=_ScriptedModel([_response(calls=(_call("ghost_tool"),))]),
        sink=_RecordingSink(fail_emit=True),
    )

    assert code == EXIT_AUDIT
    assert "error_type" in err


@pytest.mark.unit
def test_unexpected_exception_exits_five(tmp_path: pathlib.Path) -> None:
    """其它未预期异常 ⇒ ``5``（这里由 ``model.close()`` 抛出）。"""
    code, _, err = _run(
        _request(tmp_path),
        model=_ScriptedModel([_response(content="done")], close_error=RuntimeError("boom")),
        sink=_RecordingSink(),
    )

    assert code == EXIT_UNEXPECTED
    assert "error_type" in err


# ---------------------------------------------------------------------------
# 输出通道（§5.2 的两条硬规定）
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_json_mode_writes_only_jsonl_to_stdout(tmp_path: pathlib.Path) -> None:
    """``--output-format json`` ⇒ stdout **只出 JSONL**，每行都是合法 JSON 对象。"""
    code, out, _ = _run(
        _request(tmp_path, output_format="json"),
        model=_ScriptedModel([_response(content="done")]),
        sink=_RecordingSink(),
    )

    assert code == EXIT_OK
    payloads = _json_lines(out)
    assert [payload["kind"] for payload in payloads] == ["model_response", "task_finished"]
    assert payloads[0]["response"]["content"] == "done"


@pytest.mark.unit
def test_json_mode_keeps_diagnostics_out_of_stdout(tmp_path: pathlib.Path) -> None:
    """失败诊断走 **stderr**；stdout 仍是纯 JSONL（否则脚本消费方会被非 JSON 行打断）。"""
    code, out, err = _run(
        _request(tmp_path, output_format="json"),
        model=_ScriptedModel([_response(content="done")], close_error=RuntimeError("boom")),
        sink=_RecordingSink(),
    )

    assert code == EXIT_UNEXPECTED
    assert err.strip() != ""
    assert [payload["kind"] for payload in _json_lines(out)] == ["model_response", "task_finished"]


@pytest.mark.unit
def test_text_mode_renders_terminal_lines(tmp_path: pathlib.Path) -> None:
    """文本模式：渲染行写 stdout（含 ``TASK_FINISHED`` 的 ``<status>：<text>``）。"""
    code, out, _ = _run(
        _request(tmp_path),
        model=_ScriptedModel([_response(content="done")]),
        sink=_RecordingSink(),
    )

    assert code == EXIT_OK
    assert "completed：" in out


@pytest.mark.unit
def test_unknown_output_format_exits_three(tmp_path: pathlib.Path) -> None:
    """非法 ``--output-format`` ⇒ 装配期 ``ConfigError`` ⇒ ``3``（不猜、不静默取默认）。"""
    code, _, _ = _run(
        _request(tmp_path, output_format="yaml"),
        model=_ScriptedModel([]),
        sink=_RecordingSink(),
    )

    assert code == EXIT_ASSEMBLY


# ---------------------------------------------------------------------------
# R1：非交互 ⇒ 需确认的调用被拒，且不产 APPROVAL_RESULT
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_non_interactive_confirmation_is_denied_and_leaves_no_approval_event(
    tmp_path: pathlib.Path,
) -> None:
    """``R1``：非交互模式**显式传 ``None``** ⇒ 未执行 + 不产 ``APPROVAL_RESULT``。"""
    sink = _RecordingSink()
    code, out, _ = _run(
        _request(tmp_path, output_format="json"),
        model=_ScriptedModel(
            [
                _response(calls=(_call("read_file", arguments_json='{"path": "a.txt"}'),)),
                _response(content="收尾"),
            ]
        ),
        sink=sink,
        config=AppConfig(policy=PolicyConfig(granted_capabilities=("read_file",))),
    )

    assert code == EXIT_OK
    payloads = _json_lines(out)
    kinds = [payload["kind"] for payload in payloads]
    assert "tool_call" in kinds
    assert "approval_result" not in kinds, "没有人被问过，不得伪造一条用户拒绝"
    result = next(payload for payload in payloads if payload["kind"] == "tool_result")
    assert result["result"] is None
    assert "默认拒绝" in str(result["text"])
    # 审计里必须留下"未执行"的痕迹：TOOL_CALL / DENY / denied_reason=approval_denied。
    denies = [
        event
        for event in sink.events
        if event.kind is AuditEventKind.TOOL_CALL and event.outcome is AuditOutcome.DENY
    ]
    assert len(denies) == 1
    assert denies[0].detail["denied_reason"] == "approval_denied"


# ---------------------------------------------------------------------------
# Typer 应用注册（参数面能被解析）
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_cli_application_registers_a_run_subcommand() -> None:
    """``--help`` 可用：Typer 选项类型（``list[Path]`` / ``Path`` 等）确实注册成功。

    并且 ``run`` 必须是**子命令**而不是被折叠成顶层命令——否则 ``cli run <task>`` 里的
    ``run`` 会被当成任务文本（有顶层回调时才是子命令，见 ``cli/app.py`` 的 ``_root``）。
    """
    runner = CliRunner()

    top = runner.invoke(app, ["--help"])
    sub = runner.invoke(app, ["run", "--help"])

    assert top.exit_code == 0
    assert "run" in top.output
    assert sub.exit_code == 0
    assert "--output-format" in sub.output
    assert "--model-path" in sub.output
    assert "--interactive" in sub.output
