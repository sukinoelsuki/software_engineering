"""``cli/exam.py`` 的行为断言（交互式子命令 ``agent-sec-perf exam``）。

实现侧的功能断言；对抗性验收属 ``tests/security/``（安全断言不得由实现者自证）。

本文件钉住的四条口径（每条都能被一个具体改动杀死）：

* **一次输入 = 一轮 ``Session.run``**：事件被渲染成人能读的形式（``→ 调用`` / ``✓ … 完成`` /
  ``模型> …``）——把 ``_Presenter.show`` 改成空实现即翻红；
* **``/new`` 真的换了会话**：新一轮的首次请求里**不含**上一轮的任何消息（历史归零）——
  把 ``/new`` 改成"重置 presenter"即翻红；
* **未知命令不退出**：手误不该结束一场笔试；
* **退出码只反映"进程级"失败**：装配失败 ``3``、审计写入失败 ``4``；而"模型某轮失败"
  **不**终止进程（人在场，可以继续纠正）。

替身全部**手写**（与 ``tests/unit/test_cli_app.py`` 同一取向）：用生产代码构造替身，
等于让被测对象自己出题。
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from pathlib import Path
from typing import Final

import pytest

from agent_sec_perf.cli.app import EXIT_ASSEMBLY, EXIT_AUDIT, EXIT_OK
from agent_sec_perf.cli.exam import ExamRequest, run_exam
from agent_sec_perf.contracts.audit import AuditEvent
from agent_sec_perf.contracts.model import (
    CapabilityTier,
    ChatMessage,
    FinishReason,
    ModelResponse,
    TokenUsage,
)
from agent_sec_perf.contracts.tools import ToolCallRequest, ToolSpec
from agent_sec_perf.foundation.config import AppConfig, PolicyConfig

pytestmark = pytest.mark.unit

REPO_ROOT: Final = Path(__file__).resolve().parents[2]

#: 用**仓库里真实存在的**那份笔试领域包：包的生效与否直接影响"工具能不能被执行"，
#: 在测试里另造一份合成就测不到"真包与子命令是否配套"。
EXAM_PACK: Final = REPO_ROOT / "exam" / "pack"


# ---------------------------------------------------------------------------
# 替身
# ---------------------------------------------------------------------------


class _ScriptedModel:
    """``ModelClient`` 的替身：按脚本逐次返回响应，并记录每次请求的消息序列。

    ``close()`` 只置位、不阻止后续调用：``/new`` 会关掉旧会话（连带关掉注入的客户端），
    而生产路径里新会话会另起一个客户端——替身必须容忍这一点，否则测的是替身的脾气。
    """

    def __init__(self, script: Sequence[ModelResponse]) -> None:
        self._script = list(script)
        self.requests: list[tuple[ChatMessage, ...]] = []
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
        del tools, temperature, max_tokens, timeout_s
        self.requests.append(tuple(messages))
        if not self._script:
            msg = "模型脚本已用尽：用例给出的响应数少于循环实际请求数"
            raise AssertionError(msg)
        return self._script.pop(0)

    def close(self) -> None:
        self.closed = True


class _RecordingSink:
    """``AuditSink`` 的替身：可注入 ``emit`` 失败（用于退出码 ``4``）。"""

    def __init__(self, *, fail_emit: bool = False) -> None:
        self.events: list[AuditEvent] = []
        self._fail_emit = fail_emit

    def emit(self, event: AuditEvent) -> None:
        if self._fail_emit:
            msg = "审计写入失败（测试注入）"
            raise OSError(msg)
        self.events.append(event)

    def flush(self) -> None:
        return


# ---------------------------------------------------------------------------
# 构造器
# ---------------------------------------------------------------------------


def _call(name: str, arguments_json: str = "{}") -> ToolCallRequest:
    return ToolCallRequest(call_id="c1", name=name, arguments_json=arguments_json)


def _response(
    content: str | None = None, *, calls: tuple[ToolCallRequest, ...] = ()
) -> ModelResponse:
    return ModelResponse(
        content=content,
        tool_calls=calls,
        finish_reason=FinishReason.TOOL_CALLS if calls else FinishReason.STOP,
        usage=TokenUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        model_id="fake",
    )


def _request(working_dir: Path) -> ExamRequest:
    """面向人的请求：工作目录为临时目录，领域包指向仓库里的真包。"""
    return ExamRequest(
        working_dir=working_dir,
        allowed_roots=(working_dir, EXAM_PACK),
        pack_directory=EXAM_PACK,
        capability_tier=CapabilityTier.ADVANCED,
        model_path=Path("/nonexistent/model.gguf"),  # 模型被注入，这里只是占位
    )


def _config() -> AppConfig:
    """授予三项能力（与 ``exam/config/lowspec.toml`` 的口径一致）。"""
    return AppConfig(
        policy=PolicyConfig(granted_capabilities=("read_file", "write_file", "execute_command"))
    )


class _Run:
    """一次 ``run_exam`` 的产物（便于逐项断言）。"""

    def __init__(self, code: int, stdout: str, stderr: str) -> None:
        self.code = code
        self.stdout = stdout
        self.stderr = stderr


def _run(
    tmp_path: Path,
    *,
    stdin_text: str,
    script: Sequence[ModelResponse],
    sink: _RecordingSink | None = None,
) -> _Run:
    out, err = io.StringIO(), io.StringIO()
    model = _ScriptedModel(script)
    code = run_exam(
        _request(tmp_path),
        stdin=io.StringIO(stdin_text),
        stdout=out,
        stderr=err,
        config=_config(),
        sink=sink if sink is not None else _RecordingSink(),
        model=model,
    )
    return _Run(code, out.getvalue(), err.getvalue())


# ---------------------------------------------------------------------------
# 基本流程
# ---------------------------------------------------------------------------


def test_quit_on_first_line_exits_zero(tmp_path: Path) -> None:
    """``/quit`` 立刻退出，退出码 ``0``，且开场横幅确实告诉了使用者有哪些工具可用。"""
    result = _run(tmp_path, stdin_text="/quit\n", script=[])

    assert result.code == EXIT_OK
    assert "AI coding 笔试" in result.stdout
    # 工具清单来自**会话的生效暴露集合**：本包四个工具都该出现。
    for name in ("read_file", "list_dir", "write_file", "run_command"):
        assert name in result.stdout


def test_eof_exits_zero(tmp_path: Path) -> None:
    """输入流结束（EOF）⇒ 正常退出，而不是异常。"""
    result = _run(tmp_path, stdin_text="", script=[])

    assert result.code == EXIT_OK
    assert "输入结束" in result.stdout


def test_turn_renders_tool_call_and_model_text(tmp_path: Path) -> None:
    """一轮里：模型调工具 ⇒ 显示"调用了什么（带参数）"与"结果如何"；然后显示模型正文。"""
    result = _run(
        tmp_path,
        stdin_text="看一下工作目录\n/quit\n",
        script=[
            _response(calls=(_call("list_dir", '{"path": "."}'),)),
            _response(content="目录已看完"),
        ],
    )

    assert result.code == EXIT_OK
    assert "→ 调用 list_dir" in result.stdout
    assert 'path="."' in result.stdout  # 参数摘要必须可见（笔试的"看得见才纠得动"）
    assert "✓ list_dir 完成" in result.stdout
    assert "模型> 目录已看完" in result.stdout


def test_unknown_command_does_not_end_the_exam(tmp_path: Path) -> None:
    """未知 ``/`` 命令只提示，不退出（手误不该结束一场笔试）。"""
    result = _run(tmp_path, stdin_text="/nonsense\n/quit\n", script=[])

    assert result.code == EXIT_OK
    assert "未知命令" in result.stdout


@pytest.mark.parametrize("command", ["/help", "/h", "/?"])
def test_help_is_available_under_several_spellings(tmp_path: Path, command: str) -> None:
    """``/help``（及两种简写）给出命令清单，且提到 ``/new`` 的语义是"清空上下文"。"""
    result = _run(tmp_path, stdin_text=f"{command}\n/quit\n", script=[])

    assert result.code == EXIT_OK
    assert "/new" in result.stdout
    assert "清空" in result.stdout


# ---------------------------------------------------------------------------
# 上下文隔离（本套件"可重复使用"的核心断言）
# ---------------------------------------------------------------------------


def test_new_spawns_a_session_without_previous_history(tmp_path: Path) -> None:
    """``/new`` 之后，新一轮的**首次请求**里不得出现上一轮的任何消息。

    这是"重开一次流程不要上下文污染"的可机器验证形态：装配顺序是
    ``[SYSTEM] + 数据段 + [USER(任务)] + 历史``，历史为空时消息数必须是 **3**；
    上一轮的助手消息与工具回执若被带过来，这个数字会变大。
    """
    model = _ScriptedModel(
        [
            _response(calls=(_call("list_dir", '{"path": "."}'),)),
            _response(content="第一轮结束"),
            _response(content="第二轮结束"),
        ]
    )
    out, err = io.StringIO(), io.StringIO()
    code = run_exam(
        _request(tmp_path),
        stdin=io.StringIO("先看看目录\n/new\n重新开始\n/quit\n"),
        stdout=out,
        stderr=err,
        config=_config(),
        sink=_RecordingSink(),
        model=model,
    )

    assert code == EXIT_OK
    assert "新会话已就绪" in out.getvalue()
    # 第二轮的首个请求应当只有 [SYSTEM] + 数据段 + [USER(任务)] 三条；带上一轮的助手消息
    # 或工具回执会让条数变多——那正是"上下文污染"。
    last = model.requests[-1]
    assert len(last) == 3, f"新会话的首个请求应只有三条，实际 {len(last)}"
    assert {message.role.value for message in last} == {"system", "user"}
    assert last[-1].content == "重新开始"


# ---------------------------------------------------------------------------
# 退出码：只反映进程级失败
# ---------------------------------------------------------------------------


def test_audit_failure_exits_four(tmp_path: Path) -> None:
    """审计写入失败 ⇒ ``4``（证据面坏了不能被当成"任务失败"）。"""
    result = _run(
        tmp_path,
        stdin_text="看一下工作目录\n",
        script=[_response(calls=(_call("list_dir", '{"path": "."}'),))],
        sink=_RecordingSink(fail_emit=True),
    )

    assert result.code == EXIT_AUDIT
    assert "审计写入失败" in result.stderr


def test_pack_without_manifest_exits_assembly_error(tmp_path: Path) -> None:
    """领域包目录存在但缺 ``pack.toml`` ⇒ 装配失败（``3``），而不是"跑起来再说"。

    刻意不用"目录不存在"来构造这一场景：``load_pack`` 的既有契约把"目录不可读 / 不存在"
    判为**环境故障**并让 ``OSError`` 原样冒泡（``exec`` 的退出码表里属 ``5``），
    而"包里少了清单"才是**配置错误**（``DomainPackError`` ⇒ ``3``）。两者不是一回事，
    用例也分别对待——把环境故障说成配置错误，会让人去改配置而查不出真正的原因。
    """
    empty_pack = tmp_path / "empty-pack"
    empty_pack.mkdir()
    out, err = io.StringIO(), io.StringIO()
    request = ExamRequest(
        working_dir=tmp_path,
        allowed_roots=(tmp_path,),
        pack_directory=empty_pack,
        model_path=Path("/nonexistent/model.gguf"),
    )
    code = run_exam(
        request,
        stdin=io.StringIO(""),
        stdout=out,
        stderr=err,
        config=_config(),
        sink=_RecordingSink(),
        model=_ScriptedModel([]),
    )

    assert code == EXIT_ASSEMBLY
    assert "装配失败" in err.getvalue()
