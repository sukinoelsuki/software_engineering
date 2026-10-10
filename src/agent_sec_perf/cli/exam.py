"""交互式 AI coding 笔试 REPL（L4 表现层；子命令 ``agent-sec-perf exam``）。

**为什么需要它**：``run`` 子命令是**一次性**的——任务在启动时给定，模型跑到收敛就结束，
人只能在最后看结果。而"AI coding 笔试"要的恰恰相反：**人和模型多轮对话**，人看着模型
每一步做了什么、发现跑偏就出言纠正。本模块把这条通路补上。

**它不新增任何编排逻辑**：装配仍走 ``cli/app.py`` 的 :data:`assemble_session`
（= ``_assemble`` 的公开别名），会话仍是 ``harness.session.Session``，策略、审计、
工具裁剪一行未改。本模块只做三件事：把一行人话变成一次 ``Session.run(task)``、
把事件流渲染成人能读的样子、以及在 ``/new`` 时换一个**全新**会话。

**上下文隔离（``/new``，笔试可重复使用的关键）**：对话历史由 ``TaskLoop`` 持有、跨 ``run``
保留（契约 ``I9``）。重开一轮必须**没有上一轮残留**，因此 ``/new`` 关掉当前会话并**重新装配**
一个（新 ``session_id``、历史归零）。代价是模型进程随之重启——本机实测加载 8B 权重约 1~2 s，
一轮笔试只发生一次；换来的是"会话生命周期只有一处所有者"（``Session.close()``），
不存在"模型客户端该由谁关"的模糊地带。

**为什么参数要在终端展示出来**（与 ``cli/render.py`` 的取向看似不同，实则不冲突）：
``render_event`` 是**产品事件流**的渲染，它按契约 ``I8`` 不显示 ``arguments_json``；
本模块是**笔试教练视角**的渲染——人必须看见模型把什么路径、什么 ``argv``、多长的内容交给工具，
否则"规范模型、纠正模型"这件事无从下手。展示前一律经净化与截断（见 :func:`_one_line`），
而**权限判定与控制流一个字都不读它**（``harness.md`` §2.5.3 的 ``S3``/``S4`` 同源取向）。
原始 ``arguments_json`` 取自 ``MODEL_RESPONSE`` 的 ``response.tool_calls``（按 ``call_id`` 回指），
**不新增任何字段、不触碰 ``SessionEvent.text``、不进审计**。

**多行输出为什么要单独一个净化函数**：模型的答复与编译错误里，**换行是内容的一部分**
（``sanitize_for_display`` 会把换行换成 ``?``，那是为"一行一条日志"设计的）。本模块因此用
:func:`_multiline` 保留换行与制表符、其余控制字符照旧替换——ESC 仍然进不来，
终端不会被不可信内容改写。

**退出码**沿用 ``cli/app.py`` 的表，但有一处**语义澄清**：交互式 REPL 里"某轮任务失败"（``1``）
与"步数用尽"（``2``）**不是进程级失败**——人还在，可以继续纠正，因此它们只渲染成一行提示，
进程继续；只有装配失败（``3``）、审计写入失败（``4``）与未预期异常（``5``）才终止进程。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Final, cast

from agent_sec_perf.cli.app import (
    EXIT_ASSEMBLY,
    EXIT_AUDIT,
    EXIT_OK,
    OUTPUT_TEXT,
    Assembly,
    RunRequest,
    assemble_session,
)
from agent_sec_perf.contracts.audit import AuditSink
from agent_sec_perf.contracts.harness import SessionEvent, SessionEventKind, TaskStatus
from agent_sec_perf.contracts.model import CapabilityTier, ModelClient
from agent_sec_perf.foundation.config import AppConfig, load_config
from agent_sec_perf.foundation.errors import AuditWriteError, BenchError
from agent_sec_perf.foundation.logging import configure_logging
from agent_sec_perf.harness.session import Session

__all__ = ["ExamRequest", "run_exam"]

#: 模型正文的展示上限（字符）。比 ``cli/render.py`` 的 4096 大：笔试里模型会贴出整段代码，
#: 截得太早会让人看不到关键行。仍然设上限——终端的可读性不该由不可信内容决定。
_MODEL_TEXT_LIMIT: Final = 20000

#: 工具输出（编译错误等）的展示上限：gcc 报错可能很长，但要留得下主要几条。
_TOOL_OUTPUT_LIMIT: Final = 8000

#: 单次工具调用参数摘要的展示上限。
_ARGUMENTS_LIMIT: Final = 240

_PROMPT_TEXT: Final = "你> "
_MODEL_PREFIX: Final = "模型> "


@dataclass(frozen=True)
class ExamRequest:
    """一次笔试进程的全部入参（默认值按"端侧 8B + 编译执行"这一场景选取）。

    与 ``RunRequest`` 的关系：本类是**面向人的一层**（少几个对笔试无意义的开关、有一套
    适合笔试的默认值），装配时逐字段映射成 :class:`~agent_sec_perf.cli.app.RunRequest`，
    **不复制装配逻辑**（映射见 :func:`_to_run_request`）。

    各默认值的理由（都不是随便挑的）：

    * ``capability_tier = ADVANCED``：``BASIC`` 只暴露只读工具、``STANDARD`` 不含执行——
      笔试必须能"写文件 + 编译运行"，三档里只有 ``ADVANCED`` 覆盖 ``EXECUTE_COMMAND``；
    * ``max_steps = 16``：实测一道小题要 6 轮往返（写→编译→运行→改→再编译→收尾），
      默认的 12 会在更复杂的题上以 ``LIMIT_REACHED`` 收尾；
    * ``max_prompt_tokens = 32768`` / ``max_completion_tokens = 4096``：
      上下文估算按**字符数**上界（``harness/context``），整段代码回灌后 8192 会过早触发裁剪；
    * ``model_request_timeout_s = 1800``：实测弱硬件上单次补全可达数百秒，
      协议默认的 60 s 必然超时；
    * ``tool_timeout_s = 120``：给 gcc 留出余量（其资源上限由 ``foundation.proc`` 另设）。
    """

    working_dir: Path
    allowed_roots: tuple[Path, ...] = ()
    pack_directory: Path | None = None
    capability_tier: CapabilityTier = CapabilityTier.ADVANCED
    max_steps: int = 16
    max_consecutive_failures: int = 5
    tool_timeout_s: float = 120.0
    max_prompt_tokens: int = 32768
    max_completion_tokens: int | None = 4096
    model_binary: str = "llama-server"
    model_path: Path | None = None
    model_log: Path | None = None
    host: str = "127.0.0.1"
    port: int = 8080
    approval_timeout_s: float = 120.0
    model_request_timeout_s: float = 1800.0
    model_ready_timeout_s: float = 300.0


def run_exam(
    request: ExamRequest,
    *,
    stdin: IO[str],
    stdout: IO[str],
    stderr: IO[str],
    config: AppConfig | None = None,
    sink: AuditSink | None = None,
    model: ModelClient | None = None,
) -> int:
    """跑一次交互式笔试，返回**退出码**（含义见模块 docstring）。

    Args:
        stdin / stdout: 人的输入与给人看的输出。诊断走 ``stderr``
            （与 ``run`` 的通道纪律一致，便于把 stdout 重定向成一份"答题记录"）。
        stderr: 诊断与交互提示。
        config / sink / model: 注入点，语义与 ``cli/app.py::execute`` 逐一相同
            （``None`` ⇒ 用各自的默认真实实现）。存在它们只是为了**可测**：
            单测注入替身后不必起 ``llama-server``，也不会真的写审计。

    Returns:
        ``0`` 正常退出（含"模型某轮失败但人继续纠正"）；``3`` 装配失败；
        ``4`` 审计写入失败；``5`` 其它未预期异常。**不抛异常**。
    """
    configure_logging(stream=stderr, level="INFO")
    try:
        app_config = load_config() if config is None else config
    except BenchError as exc:
        stderr.write(f"配置加载失败：{type(exc).__name__}\n")
        return EXIT_ASSEMBLY
    configure_logging(stream=stderr, level=app_config.logging.level)

    assembly = _open_session(request, app_config=app_config, sink=sink, model=model, stderr=stderr)
    if assembly is None:
        return EXIT_ASSEMBLY

    presenter = _Presenter(out=stdout)
    _show_banner(request, assembly=assembly, stdout=stdout)
    code = EXIT_OK
    try:
        while True:
            stdout.write(_PROMPT_TEXT)
            stdout.flush()
            raw = _readline(stdin)
            if raw is None:
                _say(stdout, "\n输入结束（EOF），退出。")
                break
            line = raw.strip()
            if not line:
                continue
            if line.startswith("/"):
                result = _handle_command(
                    line,
                    request=request,
                    app_config=app_config,
                    assembly=assembly,
                    sink=sink,
                    model=model,
                    stderr=stderr,
                    stdout=stdout,
                )
                if result.stop:
                    code = result.exit_code
                    break
                assembly = result.assembly
                continue

            presenter.reset()
            turn_code = _run_turn(assembly, line, presenter=presenter, stdout=stdout, stderr=stderr)
            if turn_code is not None:
                code = turn_code
                break
    except KeyboardInterrupt:
        _say(stdout, "\n收到中断，退出。")
    finally:
        _close_session(assembly.session, stderr=stderr)
    return code


def _run_turn(
    assembly: Assembly,
    line: str,
    *,
    presenter: _Presenter,
    stdout: IO[str],
    stderr: IO[str],
) -> int | None:
    """跑一轮（一行人话）；返回 ``None`` ⇒ 继续用同一会话，返回退出码 ⇒ 终止进程。"""
    try:
        for event in assembly.session.run(line):
            presenter.show(event)
    except AuditWriteError:
        stderr.write("审计写入失败：证据面损坏，按退出码 4 结束。\n")
        return EXIT_AUDIT
    except Exception as exc:
        if assembly.recorder.failed:
            stderr.write("审计写入失败（已被下游收敛），按退出码 4 结束。\n")
            return EXIT_AUDIT
        _say(stdout, f"本轮出现未预期异常（{type(exc).__name__}），可继续输入或 /quit。")
    return None


# ---------------------------------------------------------------------------
# 会话生命周期
# ---------------------------------------------------------------------------


def _open_session(
    request: ExamRequest,
    *,
    app_config: AppConfig,
    sink: AuditSink | None,
    model: ModelClient | None,
    stderr: IO[str],
) -> Assembly | None:
    """按生产的装配路径起一个会话；失败返回 ``None`` 并写诊断（**不抛异常**）。

    每轮 ``/new`` 都走这里 ⇒ 模型客户端与审计落点随会话一起新建（见模块 docstring
    关于"模型进程随 /new 重启"的取舍）。
    """
    try:
        return assemble_session(
            _to_run_request(request), config=app_config, sink=sink, model=model, stderr=stderr
        )
    except BenchError as exc:
        stderr.write(f"装配失败：{type(exc).__name__}（退出码 3）\n")
        return None
    except Exception as exc:
        stderr.write(f"装配期出现未预期异常：{type(exc).__name__}（退出码 5）\n")
        return None


def _to_run_request(request: ExamRequest) -> RunRequest:
    """把面向人的请求映射成生产的 ``RunRequest``（**逐字段，不做任何"猜一个更合理值"**）。"""
    return RunRequest(
        task="",  # 每轮由人输入决定；装配期不使用它
        working_dir=request.working_dir,
        allowed_roots=request.allowed_roots,
        output_format=OUTPUT_TEXT,
        # 交互通路**打开**：写/执行在笔试包里已声明为 low（自动放行），
        # 但若日后有人把某个工具改成 medium/high，这里仍有一条"问人"的路，
        # 而不是静默变成不可用的拒绝。
        interactive=True,
        pack_directory=request.pack_directory,
        capability_tier=request.capability_tier,
        max_steps=request.max_steps,
        max_consecutive_failures=request.max_consecutive_failures,
        tool_timeout_s=request.tool_timeout_s,
        max_prompt_tokens=request.max_prompt_tokens,
        max_completion_tokens=request.max_completion_tokens,
        model_binary=request.model_binary,
        model_path=request.model_path,
        model_log=request.model_log,
        host=request.host,
        port=request.port,
        approval_timeout_s=request.approval_timeout_s,
        model_request_timeout_s=request.model_request_timeout_s,
        model_ready_timeout_s=request.model_ready_timeout_s,
    )


def _close_session(session: Session, *, stderr: IO[str]) -> None:
    """关闭会话，并**让 teardown 故障可见**（打到 ``stderr``，不静默吞掉）。

    ``Session.close()`` 会一并关闭模型客户端与 flush 审计。这里不重新抛出：调用点已经走到
    退出路径，抛出去只会把"关闭失败"变成一段 traceback 并丢掉退出码语义；写到 ``stderr``
    既保留了可见性，也不掩盖——"禁止静默吞异常"要求的是**留痕**，不是"必须崩掉"。
    """
    try:
        session.close()
    except Exception as exc:
        stderr.write(f"会话关闭失败：{type(exc).__name__}（资源可能未完全释放）\n")


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CommandResult:
    """一条 ``/`` 命令的处理结果。"""

    assembly: Assembly
    stop: bool = False
    exit_code: int = EXIT_OK


def _handle_command(
    line: str,
    *,
    request: ExamRequest,
    app_config: AppConfig,
    assembly: Assembly,
    sink: AuditSink | None,
    model: ModelClient | None,
    stderr: IO[str],
    stdout: IO[str],
) -> _CommandResult:
    """处理一条 ``/`` 命令（未知命令只提示，不退出——人的手误不该结束一场笔试）。"""
    name = line.split(maxsplit=1)[0].lower()
    if name in ("/quit", "/exit", "/q"):
        return _CommandResult(assembly=assembly, stop=True)
    if name in ("/help", "/h", "/?"):
        _show_help(stdout)
        return _CommandResult(assembly=assembly)
    if name == "/new":
        _say(stdout, "重开一轮：关闭当前会话并新建（对话历史归零、新 session_id）。")
        _close_session(assembly.session, stderr=stderr)
        opened = _open_session(
            request, app_config=app_config, sink=sink, model=model, stderr=stderr
        )
        if opened is None:
            _say(stdout, "新会话装配失败，本次笔试结束（原会话已关闭，无法继续）。")
            return _CommandResult(assembly=assembly, stop=True, exit_code=EXIT_ASSEMBLY)
        _say(stdout, f"新会话已就绪（session_id={opened.session.session_id}）。")
        return _CommandResult(assembly=opened)
    _say(stdout, f"未知命令：{line}（用 /help 查看可用命令）")
    return _CommandResult(assembly=assembly)


def _show_help(out: IO[str]) -> None:
    """打印可用命令（中文优先，面向使用者而不是开发者）。"""
    _block(
        out,
        "\n".join(
            (
                "可用命令：",
                "  /help   显示本帮助",
                "  /new    重开一轮笔试：清空对话上下文，换一个新的会话"
                "（题目需要重新粘一遍，模型也不记得之前的纠正）",
                "  /quit   退出（等同 /exit、Ctrl-D、Ctrl-C）",
                "",
                "其它任何输入都会作为**一段话**交给模型；模型可自行决定调用工具或直接回答。",
                "想纠正模型就像跟人说话一样直接说，例如：",
                "  「路径要用相对路径 student.c，不要用绝对路径」",
                "  「不要用 scanf 交互，main 里要内置测试数据」",
                "  「先编译再运行，别在回答里直接贴输出」",
                "",
            )
        ),
    )


def _show_banner(request: ExamRequest, *, assembly: Assembly, stdout: IO[str]) -> None:
    """打印开场信息：工作目录、模型、**实际暴露**的工具、可用命令。

    工具名取自会话的只读属性 :attr:`~agent_sec_perf.harness.session.Session.exposed_tools`
    ——即 ``trimming.select_tools`` 的生效结果。不另抄一份清单：抄一份就会与
    ``--capability-tier`` 和领域包白名单的实际交集漂移。
    """
    names = "、".join(spec.name for spec in assembly.session.exposed_tools)
    model_label = "（未指定）" if request.model_path is None else str(request.model_path)
    _block(
        stdout,
        "\n".join(
            (
                "=" * 72,
                " AI coding 笔试（端侧模型）· agent-sec-perf exam",
                f" 工作目录：{request.working_dir}",
                f" 模型：{model_label}",
                f" 会话：{assembly.session.session_id}",
                f" 本轮暴露给模型的工具：{names or '（无）'}",
                " 命令：/help 帮助 · /new 重开一轮（清空上下文）· /quit 退出",
                "=" * 72,
                "把题面整段粘进来即可开始；模型每调用一次工具，这里都会显示它调了什么、结果如何。",
                "",
            )
        ),
    )


# ---------------------------------------------------------------------------
# 事件渲染
# ---------------------------------------------------------------------------


@dataclass
class _Presenter:
    """把事件流渲染成人能读的样子（**只读事件，不参与任何判定**）。

    持有 ``call_id → arguments_json`` 的回指表：``TOOL_CALL`` 事件本身不带参数
    （``I8`` 明令参数不得进事件字段），参数只存在于前一条 ``MODEL_RESPONSE`` 的
    ``response.tool_calls`` 里，因此按 ``call_id`` 在这里做一次配对。
    """

    out: IO[str]
    _arguments: dict[str, str] = field(default_factory=dict)

    def reset(self) -> None:
        """开始新的一轮（清掉上一轮的回指表，避免 ``call_id`` 复用造成的错配）。"""
        self._arguments.clear()

    def show(self, event: SessionEvent) -> None:
        """渲染一条事件（按 ``kind`` 分派；未列出的 kind 不打印，不影响流程）。"""
        kind = event.kind
        if kind is SessionEventKind.MODEL_RESPONSE:
            self._show_model_response(event)
        elif kind is SessionEventKind.TOOL_CALL:
            self._show_tool_call(event)
        elif kind is SessionEventKind.TOOL_RESULT:
            self._show_tool_result(event)
        elif kind is SessionEventKind.POLICY_DECISION:
            self._show_policy_decision(event)
        elif kind is SessionEventKind.APPROVAL_RESULT:
            outcome = getattr(event.approval, "outcome", None)
            _block(self.out, f"  [人工确认] {getattr(outcome, 'value', '?')}")
        elif kind is SessionEventKind.ERROR:
            _block(self.out, f"  ! {getattr(event.error_kind, 'value', '?')}：{event.text or ''}")
        elif kind is SessionEventKind.TASK_FINISHED:
            self._show_task_finished(event)

    def _show_model_response(self, event: SessionEvent) -> None:
        """模型的一次回复：正文（多行）+ 记录本轮各工具调用的参数（供随后回显）。"""
        response = event.response
        if response is None:
            return
        if response.content:
            _block(
                self.out,
                f"{_MODEL_PREFIX}{_multiline(response.content, limit=_MODEL_TEXT_LIMIT)}",
            )
        for call in response.tool_calls:
            self._arguments[call.call_id] = call.arguments_json

    def _show_tool_call(self, event: SessionEvent) -> None:
        name = _one_line(event.tool_name or "?", limit=64)
        raw = "" if event.call_id is None else self._arguments.get(event.call_id, "")
        _block(self.out, f"  → 调用 {name}  {_describe_arguments(raw)}")

    def _show_tool_result(self, event: SessionEvent) -> None:
        name = _one_line(event.tool_name or "?", limit=64)
        result = event.result
        if result is None:
            # 未执行（策略拒绝 / 参数非法 / 需确认而无通路）：text 是我方生成的中文说明。
            _block(self.out, f"  ✗ {name} 未执行：{event.text or ''}")
            return
        if result.ok:
            _block(self.out, f"  ✓ {name} 完成")
        else:
            _block(self.out, f"  ✗ {name} 失败：{_one_line(result.error or '', limit=200)}")
        if result.content:
            _block(self.out, _indent(_multiline(result.content, limit=_TOOL_OUTPUT_LIMIT)))

    def _show_policy_decision(self, event: SessionEvent) -> None:
        """策略结论：**只在"需要人注意"时打印**。

        "已授权 + 低风险 ⇒ 自动放行"每调用一次都要打两行，会把答题过程淹掉；紧跟着的
        ``✓ / ✗`` 已经表达了"执行了没有"。需要人注意的两种（需确认、拒绝）才打印。
        """
        decision = event.decision
        if decision is None:
            return
        if decision.allow and not decision.requires_confirmation:
            return
        verdict = "需确认" if decision.requires_confirmation else "拒绝"
        risk = getattr(decision.risk_level, "value", "?")
        _block(
            self.out,
            f"  [策略] {verdict} {_one_line(event.tool_name or '?', limit=64)}"
            f"（风险 {risk}）— {decision.reason}",
        )

    def _show_task_finished(self, event: SessionEvent) -> None:
        status = event.status
        if status is TaskStatus.LIMIT_REACHED:
            _block(
                self.out,
                "  — 本轮到达步数上限：模型没能在这一轮做完。直接说下一步，"
                "或下次用更大的 --max-steps。",
            )
            return
        if status is TaskStatus.FAILED:
            _block(self.out, f"  — 本轮以失败结束：{event.text or ''}")
            return
        if status is TaskStatus.COMPLETED:
            _block(self.out, "  — 本轮模型给出回复。")


# ---------------------------------------------------------------------------
# 展示层的小工具（全部只读：只做净化与截断，不参与任何判定）
# ---------------------------------------------------------------------------


def _readline(source: IO[str]) -> str | None:
    """读一行；EOF（``''``）返回 ``None``。读失败一律按 EOF 处置（不阻塞、不抛）。"""
    try:
        line = source.readline()
    except (OSError, ValueError):
        return None
    return None if line == "" else line


def _say(out: IO[str], text: str) -> None:
    """写一行并 flush（交互下必须立刻可见）。"""
    out.write(f"{text}\n")
    out.flush()


def _block(out: IO[str], text: str) -> None:
    """写一段（可能多行）文本并 flush。"""
    out.write(f"{text}\n")
    out.flush()


def _indent(text: str) -> str:
    """给多行文本加缩进（工具输出缩进一层，与"→ 调用"行区分开）。"""
    return "\n".join(f"    {line}" for line in text.splitlines())


def _one_line(text: str, *, limit: int = _ARGUMENTS_LIMIT) -> str:
    """把不可信文本压成**一行**（控制字符换 ``?``，超长截断）。

    与 ``foundation.logging.sanitize_for_display`` 同一取向，但**保留制表符**：它在这里
    用于对齐，不构成注入面（换行与 ESC 仍被替换）。
    """
    cleaned = "".join(char if (char.isprintable() or char == "\t") else "?" for char in text)
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[:limit]}…"


def _multiline(text: str, *, limit: int) -> str:
    """保留**换行与制表符**的净化（给"读代码 / 看编译错误"用），其余控制字符换 ``?``。

    与 :func:`_one_line` 的分工：**行的结构本身是内容**时用本函数，一行摘要时用前者。
    截断只在净化之后做一次——先把控制字符清干净再切，避免留下半个转义序列。
    """
    cleaned = "".join(char if (char.isprintable() or char in "\n\t") else "?" for char in text)
    if len(cleaned) <= limit:
        return cleaned
    return f"{cleaned[:limit]}\n…（后续内容已按展示上限截断）"


def _describe_arguments(arguments_json: str) -> str:
    """把一次工具调用的原始参数文本压成**人能读的一行摘要**（只供展示）。

    解析失败或形状不是对象时退回"净化后的原文"——**不猜**，也不因为解析失败就不显示：
    "模型给了什么"在笔试里必须可见。解析结果只用于生成展示文本，**不进入任何判定**。
    """
    if not arguments_json:
        return ""
    try:
        parsed: object = json.loads(arguments_json)
    except (json.JSONDecodeError, TypeError, ValueError):
        return _one_line(arguments_json)
    if not isinstance(parsed, dict):
        return _one_line(arguments_json)
    table = cast("dict[str, object]", parsed)
    parts = [
        f"{_one_line(str(key), limit=32)}={_describe_value(value)}"
        for key, value in sorted(table.items())
    ]
    return _one_line(" ".join(parts))


def _describe_value(value: object) -> str:
    """单个参数值的展示标签：字符串给短摘要、字符串列表逐项、其余只给形态，不回显标量。"""
    if isinstance(value, str):
        if len(value) <= 60:
            return f'"{_one_line(value, limit=60)}"'
        return f'"<{len(value)} 字符> {_one_line(value[:40], limit=40)}…"'
    if isinstance(value, list | tuple):
        items = list(value)
        if all(isinstance(item, str) for item in items):
            return "[" + " ".join(_one_line(str(item), limit=40) for item in items) + "]"
        return f"<list: {len(items)}>"
    if value is None:
        return "<null>"
    if isinstance(value, bool):
        return "<bool>"
    if isinstance(value, int | float):
        return f"{value}"
    return f"<{type(value).__name__}>"
