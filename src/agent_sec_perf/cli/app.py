"""最小 CLI 入口：参数解析、装配、事件循环与退出码（``harness.md`` §5）。

本模块是 L4 表现层的入口，目标只有一个：**让产品真的能跑起来**——把 §5.1 的装配点按
**固定顺序**接起来，把 :class:`~agent_sec_perf.harness.session.Session` 的事件流按 §5.2
渲染或序列化，并按 §5.2 的退出码表返回。**不追求功能完备**（性能度量机制明确不在本轮范围）。

装配顺序（契约 §5.1「装配顺序」，⚠️ **能力收窄必须早于 ``policy``**——``PolicyEngine``
构造后 ``granted`` 是只读视图，再收窄也不会生效）::

    config → sink → pack（若启用）→ 能力收窄 → policy → registry → model → Session

**一处与契约字面不同的实现说明（如实登记，非静默偏离）**：``load_pack`` 需要 ``known_tools``
（契约 §5.1 第 9 行的 ``frozenset(spec.name for spec in registry.specs())``），而工具名只能从
**工具实例**取得 ⇒ 本模块在 ``sink`` 之后先构造 **4 个内置工具实例**（**不是** ``ToolRegistry``），
把它们的 ``spec.name`` 交给 ``load_pack``；``ToolRegistry`` 本身仍按契约排在 ``policy``
**之后**。"registry 在 policy 之后"这一条**未被调换**。

退出码（契约 §5.2 的表；**必须**让"任务失败"与"环境 / 配置故障"可区分）::

    0  ``TASK_FINISHED.status is COMPLETED``
    1  ``TASK_FINISHED.status is FAILED``
    2  ``TASK_FINISHED.status is LIMIT_REACHED``
    3  装配期 ``BenchError``（``ConfigError`` / ``PathNotAllowedError`` / ``DomainPackError`` /
       ``ModelUnavailableError`` / ``ToolRegistrationError`` / ``UnknownCapabilityError`` …）
    4  审计写入失败（``emit`` / ``flush`` 的异常）
    5  其它未预期异常

⚠️ **退出码 ``4`` 的归因方式**：审计写入发生在**下游多个模块内部**（``policy.decide`` /
工具层 / ``loop`` / ``session.close``），到 ``cli`` 时只剩一个异常对象，**凭类型无法归因**
（审计实现抛出的是 ``OSError`` 等通用类型）。因此本模块用 :class:`_AuditRecorder` 记住
"是否发生过审计写入失败"，退出码在末尾据它判定；**异常类型不被改写**——``audit.md`` §2.4
要求审计失败必须**原样冒泡**，把它包装成新类型会改变下游的 ``except`` 语义。
⚠️ 该处置在**审批路径**上有一处**已知缺口**（gate 内部 ``emit`` 失败会被 ``loop`` 的 ``R3``
收敛）：边界与下轮修法见 :class:`_AuditRecorder` 的 docstring（领导 2026-09-19 裁决，本轮登记）。

⚠️ **CLI 用法错误**（未知选项 / 缺必填参数）由 Typer/Click 以退出码 ``2`` 结束，与
``LIMIT_REACHED`` 数值相同。那是框架的既定行为，**不在**本契约的退出码表内。

输出通道（契约 §5.2 的输出通道硬规定）：``--output-format json`` 时 **stdout 只出 JSONL**，
诊断一律走 **stderr**（``configure_logging(stream=stderr, ...)`` + 交互提示写 stderr）。
"""

from __future__ import annotations

import math
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Annotated, Final

import typer

from agent_sec_perf.cli.approval import InteractiveApprovalGate, StdioApprovalInput
from agent_sec_perf.cli.render import render_event, serialize_event
from agent_sec_perf.contracts.audit import AuditEvent, AuditSink
from agent_sec_perf.contracts.harness import (
    SessionConfig,
    SessionEvent,
    SessionEventKind,
    TaskStatus,
)
from agent_sec_perf.contracts.model import CapabilityTier, ChatMessage, ModelClient, ModelResponse
from agent_sec_perf.contracts.policy import RiskLevel
from agent_sec_perf.contracts.tools import Tool, ToolSpec
from agent_sec_perf.foundation.config import AppConfig, load_config
from agent_sec_perf.foundation.errors import BenchError, ConfigError
from agent_sec_perf.foundation.logging import configure_logging, get_logger, sanitize_for_display
from agent_sec_perf.harness.arguments import SubsetArgumentValidator
from agent_sec_perf.harness.domain_pack import load_pack
from agent_sec_perf.harness.session import Session
from agent_sec_perf.model.client import LocalLlamaClient
from agent_sec_perf.observability.audit import JsonlAuditSink
from agent_sec_perf.security.capabilities import narrow_granted, parse_capabilities
from agent_sec_perf.security.policy import PolicyEngine
from agent_sec_perf.tools.files import ListDirTool, ReadFileTool, WriteFileTool
from agent_sec_perf.tools.registry import ToolRegistry
from agent_sec_perf.tools.shell import ShellCommandTool

__all__ = [
    "EXIT_ASSEMBLY",
    "EXIT_AUDIT",
    "EXIT_LIMIT_REACHED",
    "EXIT_OK",
    "EXIT_TASK_FAILED",
    "EXIT_UNEXPECTED",
    "OUTPUT_JSON",
    "OUTPUT_TEXT",
    "RunRequest",
    "app",
    "execute",
    "main",
]

# ---------------------------------------------------------------------------
# 常量（全部是**我方**的固定取值，可直接显示）
# ---------------------------------------------------------------------------

EXIT_OK: Final = 0
EXIT_TASK_FAILED: Final = 1
EXIT_LIMIT_REACHED: Final = 2
EXIT_ASSEMBLY: Final = 3
EXIT_AUDIT: Final = 4
EXIT_UNEXPECTED: Final = 5

OUTPUT_TEXT: Final = "text"
OUTPUT_JSON: Final = "json"

#: 未显式给出模型日志落点时的默认文件名（放在会话工作目录内；其父目录即服务进程的 cwd）。
DEFAULT_MODEL_LOG_NAME: Final = "llama-server.log"

#: 展示模型路径 / 输出格式一类**命令行输入**时的截断长度。
_LABEL_LIMIT: Final = 64

_EXIT_BY_STATUS: Final[Mapping[TaskStatus, int]] = {
    TaskStatus.COMPLETED: EXIT_OK,
    TaskStatus.FAILED: EXIT_TASK_FAILED,
    TaskStatus.LIMIT_REACHED: EXIT_LIMIT_REACHED,
}


# ---------------------------------------------------------------------------
# 请求（CLI 解析后的、不可变的参数集合）
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RunRequest:
    """一次会话运行的全部入参（由 Typer 命令构造，或由测试直接构造）。

    ``allowed_roots`` 省略（空元组）时由装配点取 ``(working_dir,)``——这是**最保守的
    非空集合**（只允许工作目录自己）；契约 §2.8 要求"空允许根集合 ⇒ 拒绝启动"，
    而 CLI 侧不给出一个可用的默认就等于"这个命令永远跑不起来"，两者的分界在此。

    ``max_completion_tokens`` / ``model_request_timeout_s`` / ``model_ready_timeout_s``
    是**可用性参数**（不是权限参数）：弱硬件上生成速度可能只有 3.5 tok/s，一次无上限的
    补全会远超客户端默认的 60s 请求超时。三者默认值都与下层既有默认值**逐字相同**
    ⇒ 不给这些选项时行为不变；给了则按给定值生效（不做"猜一个更合理值"的修正）。
    """

    task: str
    working_dir: Path
    allowed_roots: tuple[Path, ...] = ()
    output_format: str = OUTPUT_TEXT
    interactive: bool = False
    pack_directory: Path | None = None
    capability_tier: CapabilityTier = CapabilityTier.BASIC
    max_steps: int = 12
    max_consecutive_failures: int = 3
    tool_timeout_s: float = 30.0
    max_prompt_tokens: int = 8192
    max_completion_tokens: int | None = None
    model_binary: str = "llama-server"
    model_path: Path | None = None
    model_log: Path | None = None
    host: str = "127.0.0.1"
    port: int = 8080
    approval_timeout_s: float = 120.0
    #: 单次模型请求的超时（秒）。默认 ``60.0`` = ``ModelClient`` 协议签名上的默认值。
    model_request_timeout_s: float = 60.0
    #: 模型服务就绪等待上限（秒）。默认 ``60.0`` = ``LocalLlamaClient`` 的构造默认值。
    model_ready_timeout_s: float = 60.0


# ---------------------------------------------------------------------------
# 审计落点的"失败可判"包装
# ---------------------------------------------------------------------------


class _AuditRecorder(AuditSink):
    """给审计落点包一层"失败可判"的记录器（**不改写异常类型**）。

    只做一件事：记住是否发生过审计写入失败，并把异常**原样**重新抛出。退出码表把
    "审计写入失败"单列（``4``），但该失败发生在下游模块内部，``cli`` 无法凭异常类型归因
    （理由见模块 docstring）。**不吞、不改类型**是硬要求：审计失败必须冒泡
    （``audit.md`` §2.4），把它转成一条业务事件等于把"基础设施故障"伪装成"任务失败"。

    ⚠️ **已知缺口（2026-09-19 领导裁决；本类只如实登记，不改写异常类型）**：
    当 gate（``cli/approval.py``）**内部**的 ``sink.emit`` 失败时，``harness/loop.py`` 的
    ``R3`` 会把它当作"审批通路故障"收敛为 ``ERROR(INTERNAL)`` + ``TASK_FINISHED(FAILED)``，
    与 ``audit.md`` §2.4"审计失败必须冒泡"在这**一条路径**上不完全一致。**边界（不放大也不缩小）**：

    * **退出码正确**：本类的旁路标记让 ``execute`` 最终仍返回 ``4``；
    * **事件流响亮**：流里确实有一条 ``ERROR(INTERNAL)`` + ``FAILED``；
    * **缺的是可判定性**：审计失败与 gate 自身故障在事件 ``text`` / ``error_kind`` 上**不可分**，
      且 ``R3`` 的固定文案（"人工确认通路故障"）在**审计失败**时会**表述失真** ⇒
      **直接调 ``Session.run()`` 而不经 CLI 的消费者**会把"证据面坏了"读成"任务失败"。

    **下轮的修法（属契约变更，需架构师 + 验证者参与，不在本轮）**：给审计写入失败一个
    **可识别类型**（建议 ``AuditWriteError``；``observability/audit.py`` 写入失败时**带
    ``__cause__``** 抛出），``loop`` 在 ``R3`` 捕获**之前** ``except AuditWriteError: raise``。
    **不得**为了让归因好看而包装异常类型——那会改掉下游 ``except`` 的语义（它比归因更重要）。
    """

    def __init__(self, sink: AuditSink) -> None:
        self._sink = sink
        self.failed = False

    def emit(self, event: AuditEvent) -> None:
        try:
            self._sink.emit(event)
        except Exception:
            self.failed = True
            raise

    def flush(self) -> None:
        try:
            self._sink.flush()
        except Exception:
            self.failed = True
            raise


@dataclass(frozen=True)
class _Assembly:
    """装配结果：会话本体、审计记录器、以及本次是否走 JSONL 输出。"""

    session: Session
    recorder: _AuditRecorder
    json_mode: bool


# ---------------------------------------------------------------------------
# 入口编排
# ---------------------------------------------------------------------------


def execute(
    request: RunRequest,
    *,
    stdout: IO[str],
    stderr: IO[str],
    config: AppConfig | None = None,
    sink: AuditSink | None = None,
    model: ModelClient | None = None,
) -> int:
    """装配并运行一次会话，返回**退出码**（契约 §5.2 的表）。

    Args:
        request: 已解析的运行参数。
        stdout: 产品输出流（文本模式是渲染行；JSON 模式是 JSONL）。
        stderr: 诊断与交互提示流（**永不**写产品输出）。
        config: 注入的配置；``None`` ⇒ ``load_config()``。
        sink: 注入的审计落点；``None`` ⇒ ``JsonlAuditSink(config.audit.directory)``。
            注入点存在不是为了绕过白名单：默认落点的路径校验在 ``JsonlAuditSink`` 构造里，
            注入方想换落点也逃不过它自己的校验。
        model: 注入的模型客户端；``None`` ⇒ 构造 ``LocalLlamaClient``（会真的起进程）。

    Returns:
        ``0`` / ``1`` / ``2`` / ``3`` / ``4`` / ``5``（含义见模块 docstring）。
        **不抛异常**：所有失败都收敛为退出码，诊断写 ``stderr``。
    """
    # 先用默认级别把日志管线接到 stderr：配置加载本身可能失败，而失败信息也得有去处。
    configure_logging(stream=stderr, level="INFO")
    logger = get_logger(__name__)

    try:
        app_config = load_config() if config is None else config
    except BenchError as exc:
        logger.error("配置加载失败", error_type=type(exc).__name__)
        return EXIT_ASSEMBLY

    # 拿到真实级别后再配一次（configure_logging 幂等且立即生效）。
    configure_logging(stream=stderr, level=app_config.logging.level)

    try:
        assembly = _assemble(request, config=app_config, sink=sink, model=model, stderr=stderr)
    except BenchError as exc:
        logger.error("装配失败", error_type=type(exc).__name__)
        return EXIT_ASSEMBLY
    except Exception as exc:
        logger.error("装配期出现未预期异常", error_type=type(exc).__name__)
        return EXIT_UNEXPECTED

    status: TaskStatus | None = None
    try:
        with assembly.session as session:
            for event in session.run(request.task):
                _write_event(event, stdout=stdout, json_mode=assembly.json_mode)
                if event.kind is SessionEventKind.TASK_FINISHED:
                    status = event.status
    except Exception as exc:
        if assembly.recorder.failed:
            logger.error("审计写入失败", error_type=type(exc).__name__)
            return EXIT_AUDIT
        logger.error("会话执行出现未预期异常", error_type=type(exc).__name__)
        return EXIT_UNEXPECTED

    stdout.flush()
    if assembly.recorder.failed:
        # 审计写入失败过但没冒泡到这个层级（例如被下游某个收敛点接住）⇒ 仍按 4 返回：
        # "证据面坏了"不能被任务自身的成败状态掩盖。
        logger.error("审计写入失败（已被下游收敛，按 4 返回）")
        return EXIT_AUDIT
    if status is None:
        # 契约 ``I6`` 要求恰好一条 ``TASK_FINISHED``；没有它就无法判定结果。
        logger.error("事件流缺少 TASK_FINISHED（I6 被破）")
        return EXIT_UNEXPECTED
    return _EXIT_BY_STATUS[status]


def main() -> None:
    """控制台入口（``python -m agent_sec_perf.cli.app`` 或打包后的脚本）。"""
    app()


# ---------------------------------------------------------------------------
# 装配（契约 §5.1）
# ---------------------------------------------------------------------------


def _assemble(
    request: RunRequest,
    *,
    config: AppConfig,
    sink: AuditSink | None,
    model: ModelClient | None,
    stderr: IO[str],
) -> _Assembly:
    """按 §5.1 的顺序装配一个 ``Session``；**任何一步失败都拒绝启动**。

    能力收窄（``narrow_granted``）刻意排在 ``PolicyEngine`` **之前**：引擎构造后
    ``granted`` 是只读视图，之后再收窄不会生效（契约 §5.1 的装配顺序注）。
    """
    json_mode = _validated_output_format(request.output_format)

    # 可用性参数在**装配期**校验：非法取值属"配置错了"（退出码 3），必须在这里拒绝启动。
    # 若拖到第一次模型调用，它会以 ``ValueError`` 形式被 ``harness/loop.py`` 归为任务失败（1）
    # ⇒ 把"配置故障"伪装成"模型/任务故障"，与本模块退出码表的分工相悖。
    _require_finite_positive(request.model_request_timeout_s, name="model_request_timeout_s")
    _require_finite_non_negative(request.model_ready_timeout_s, name="model_ready_timeout_s")
    _require_positive_int_or_none(request.max_completion_tokens, name="max_completion_tokens")

    working_dir = Path(request.working_dir)
    roots: tuple[Path, ...] = tuple(request.allowed_roots)
    if not roots:
        roots = (working_dir,)

    # 1) sink：默认落点的白名单校验在 JsonlAuditSink 构造里（越界即拒绝启动，不回退）。
    raw_sink: AuditSink = sink if sink is not None else JsonlAuditSink(config.audit.directory)
    audited = _AuditRecorder(raw_sink)

    # 2) 工具实例：先建是因为 load_pack 需要 known_tools（见模块 docstring 的说明）。
    tools: tuple[Tool, ...] = (
        ReadFileTool(audited),
        WriteFileTool(audited),
        ListDirTool(audited),
        ShellCommandTool(audited),
    )
    known_tools = frozenset(tool.spec.name for tool in tools)

    # 3) 能力收窄：生效授予 = 配置授予 ∩ pack 声明（只能收窄，绝不并集）。
    granted = parse_capabilities(config.policy.granted_capabilities)
    pack = None
    if request.pack_directory is not None:
        pack = load_pack(Path(request.pack_directory), roots=roots, known_tools=known_tools)
        granted = narrow_granted(granted, pack.capabilities_allowlist)

    # 4) policy：带包前缀的风险声明优先（REQ-HARNESS-08）。
    tool_risk: Mapping[str, RiskLevel] = (
        {}
        if pack is None
        else {f"{pack.name}:{name}": level for name, level in pack.risk_overrides.items()}
    )
    policy = PolicyEngine(granted=granted, sink=audited, tool_risk=tool_risk)

    # 5) registry（全量注册集；裁剪由 Session 内的 trimming.select_tools 承担）。
    registry = ToolRegistry(tools)

    # 6) model：云端客户端未开工，本轮只有本地 llama-server。
    client: ModelClient = (
        model if model is not None else _build_local_model(request, working_dir=working_dir)
    )

    # 7) approval：非交互模式**显式传 None**（需确认的调用一律拒绝，§2.5.5 的 R1）。
    approval = _build_approval(request, sink=audited, stderr=stderr)

    session = Session(
        session_id=uuid.uuid4().hex,
        config=SessionConfig(
            working_dir=working_dir,
            allowed_roots=roots,
            capability_tier=request.capability_tier,
            max_steps=request.max_steps,
            max_consecutive_failures=request.max_consecutive_failures,
            tool_timeout_s=request.tool_timeout_s,
            max_prompt_tokens=request.max_prompt_tokens,
            max_completion_tokens=request.max_completion_tokens,
        ),
        model=client,
        registry=registry,
        policy=policy,
        approval=approval,
        sink=audited,
        # ADR-0020 的唯一实现（不校验的"临时绕过"是明令禁止的）。
        validator=SubsetArgumentValidator(),
        pack=pack,
    )
    return _Assembly(session=session, recorder=audited, json_mode=json_mode)


class _RequestTimeoutModel(ModelClient):
    """把 CLI 配置的**单次请求超时**应用到本地模型客户端的每一次 ``chat``。

    为什么需要这一层（2026-09-19 实测根因）：``harness/loop.py`` 调 ``chat`` 时**不传**
    ``timeout_s``，于是每次补全都落到 :class:`~agent_sec_perf.contracts.model.ModelClient`
    协议签名上的默认值 ``60.0``。而本地弱硬件上实测生成速度约 ``3.5 tok/s``（一次 256 token
    的有界补全耗时 81 s）⇒ 60 s 必然以 ``ModelUnavailableError`` 结束，``loop`` 按
    ``REQ-HARNESS-06`` 重试到预算耗尽 ⇒ ``TASK_FINISHED(FAILED)``。

    为什么做在 CLI 侧而不是改下层：超时是**可用性参数**（不是权限），而"本次部署有多慢"
    只有 CLI 知道。给 ``contracts.SessionConfig`` 加字段或改 ``model/client.py`` 的默认值
    都属**接口变更**（不归本层）；本包装只承载"把 CLI 的取值应用到每一次 ``chat``"。

    ``timeout_s`` 形参**必须保留**（``ModelClient`` 的结构化子类型检查要求签名一致），
    但其取值由本层决定：CLI 已给出本次部署统一的可用性参数，这里不再叠加第二层默认值
    （两处默认值必然漂移）。
    """

    def __init__(self, inner: ModelClient, *, request_timeout_s: float) -> None:
        self._inner = inner
        self._request_timeout_s = request_timeout_s

    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        timeout_s: float = 60.0,
    ) -> ModelResponse:
        """转发一次补全；请求超时取本层配置值（见类 docstring）。"""
        del timeout_s
        return self._inner.chat(
            messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_s=self._request_timeout_s,
        )

    def close(self) -> None:
        """透传关闭（进程型后端由内层回收；本层不持有任何资源）。"""
        self._inner.close()


def _build_local_model(request: RunRequest, *, working_dir: Path) -> ModelClient:
    """构造本地模型客户端（并入 CLI 的可用性参数）；未给模型路径 ⇒ **拒绝启动**（不猜默认模型）。"""
    if request.model_path is None:
        raise ConfigError("未指定模型路径：请用 --model-path 指定 GGUF 模型（不猜默认值）")
    log_path = (
        request.model_log
        if request.model_log is not None
        else working_dir / (DEFAULT_MODEL_LOG_NAME)
    )
    client = LocalLlamaClient(
        binary=request.model_binary,
        model_path=Path(request.model_path),
        log_path=Path(log_path),
        host=request.host,
        port=request.port,
        ready_timeout_s=request.model_ready_timeout_s,
    )
    return _RequestTimeoutModel(client, request_timeout_s=request.model_request_timeout_s)


def _build_approval(
    request: RunRequest, *, sink: AuditSink, stderr: IO[str]
) -> InteractiveApprovalGate | None:
    """交互模式 ⇒ 交互式 gate；否则 **显式 ``None``**（``R1``：需确认即拒绝）。

    **不得**为图方便注入一个恒放行的 gate。提示写 ``stderr``：JSON 模式要求 stdout 只出 JSONL。
    """
    if not request.interactive:
        return None
    return InteractiveApprovalGate(
        sink=sink,
        source=StdioApprovalInput(sys.stdin),
        prompt_stream=stderr,
        timeout_s=request.approval_timeout_s,
    )


def _validated_output_format(value: str) -> bool:
    """校验输出格式，返回"是否 JSON 模式"。

    非法取值 ⇒ ``ConfigError``（装配期语义 ⇒ 退出码 ``3``）：它属"配置错了"，
    与"任务失败"是两类信号，不得混为一谈。
    """
    if value == OUTPUT_JSON:
        return True
    if value == OUTPUT_TEXT:
        return False
    shown = sanitize_for_display(value, limit=_LABEL_LIMIT)
    raise ConfigError(f"未知的输出格式：{shown!r}（只接受 {OUTPUT_TEXT} | {OUTPUT_JSON}）")


def _require_finite_positive(value: object, *, name: str) -> float:
    """要求**有限正数**（``None`` / ``nan`` / ``inf`` / 非正数一律拒绝）。

    口径与 ``model/client.py`` 的 ``_validated_positive_float`` 一致：``inf`` 不是"很大的
    超时"，而是"**没有**超时"（一次挂起的调用会永久占住会话）；``None`` 也不得被当作无限等待。
    这里**重复**这条判定的理由只是归因：同样的非法值在装配期是 ``ConfigError``（退出码 3），
    在第一次调用时只会是 ``ValueError``（被 loop 归为任务失败 1）——两者的用户信号不同。
    """
    number = _finite_number(value, name=name)
    if number <= 0:
        raise ConfigError(f"{name} 必须为正数，收到 {value!r}")
    return number


def _require_finite_non_negative(value: object, *, name: str) -> float:
    """要求**有限非负数**（允许 ``0``）。

    ``ready_timeout_s = 0`` 的语义是"不等待，假定已就绪"（``model/client.py`` 的既有约定），
    这里与它保持一致，不额外收紧。
    """
    number = _finite_number(value, name=name)
    if number < 0:
        raise ConfigError(f"{name} 不得为负，收到 {value!r}")
    return number


def _finite_number(value: object, *, name: str) -> float:
    """把取值校验成有限数值（``bool`` 不算数值：它是开关，不是数量）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"{name} 必须是有限数值，收到 {type(value).__name__}")
    number = float(value)
    if not math.isfinite(number):
        raise ConfigError(f"{name} 必须有限（nan / inf 一律拒绝），收到 {value!r}")
    return number


def _require_positive_int_or_none(value: object, *, name: str) -> int | None:
    """``None`` 合法（= 不设完成上限，用服务端默认）；否则必须是正整数。**不静默修正。**"""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"{name} 必须是正整数，或省略以使用服务端默认，收到 {value!r}")
    return value


def _write_event(event: SessionEvent, *, stdout: IO[str], json_mode: bool) -> None:
    """把一条事件写进产品输出流（JSON 模式只写 JSONL，文本模式写渲染行）。"""
    if json_mode:
        stdout.write(f"{serialize_event(event)}\n")
        return
    line = render_event(event)
    if line is not None:
        stdout.write(f"{line}\n")


# ---------------------------------------------------------------------------
# Typer 命令（参数解析）
# ---------------------------------------------------------------------------

app = typer.Typer(
    add_completion=False,
    help="端侧 agent 的安全与性能基线 CLI（Phase 1 最小入口：能跑一次完整会话）。",
)


@app.callback()
def _root() -> None:
    """顶层回调。

    存在的唯一理由是**结构**：没有它时，Typer 会把"唯一的一个命令"折叠成顶层命令，
    ``cli run <task>`` 里的 ``run`` 会被当成**任务文本**（任务恰好叫 ``run`` 时更荒诞）。
    加了回调，``run`` 就是一个真正的子命令（后续还能长出 ``bench`` 等），
    用法稳定为 ``<prog> run <task> [OPTIONS]``。
    """


@app.command()
def run(
    task: Annotated[str, typer.Argument(help="交给会话执行的任务")],
    output_format: Annotated[
        str, typer.Option("--output-format", help="text | json（json 时 stdout 只出 JSONL）")
    ] = OUTPUT_TEXT,
    interactive: Annotated[
        bool,
        typer.Option("--interactive", help="启用交互式人工确认（需要 TTY；省略即不提供确认通路）"),
    ] = False,
    working_dir: Annotated[
        Path | None, typer.Option("--working-dir", help="会话工作目录；省略取 cwd")
    ] = None,
    allowed_root: Annotated[
        list[Path] | None,
        typer.Option("--allowed-root", help="允许访问的根目录（可重复）；省略取工作目录"),
    ] = None,
    pack_directory: Annotated[
        Path | None, typer.Option("--pack", help="领域包目录；省略即不启用领域包")
    ] = None,
    capability_tier: Annotated[
        str, typer.Option("--capability-tier", help="basic | standard | advanced")
    ] = CapabilityTier.BASIC.value,
    max_steps: Annotated[
        int, typer.Option("--max-steps", min=1, help="单次任务的模型往返上限")
    ] = 12,
    max_consecutive_failures: Annotated[
        int, typer.Option("--max-consecutive-failures", min=1, help="连续未执行/工具失败的上限")
    ] = 3,
    tool_timeout_s: Annotated[
        float, typer.Option("--tool-timeout-s", min=0.001, help="单次工具超时秒")
    ] = 30.0,
    max_prompt_tokens: Annotated[
        int, typer.Option("--max-prompt-tokens", min=1, help="提示 token 预算")
    ] = 8192,
    max_completion_tokens: Annotated[
        int | None,
        typer.Option(
            "--max-completion-tokens",
            min=1,
            help="单次补全的 token 上限（省略 = 用服务端默认；弱硬件建议显式给上限）",
        ),
    ] = None,
    model_request_timeout_s: Annotated[
        float,
        typer.Option(
            "--model-request-timeout-s",
            min=0.001,
            help="本地模型单次请求超时秒（慢硬件需调大；默认 60 与协议默认值一致）",
        ),
    ] = 60.0,
    model_ready_timeout_s: Annotated[
        float,
        typer.Option(
            "--model-ready-timeout-s",
            min=0.0,
            help="本地模型服务就绪等待上限秒（0 = 不等待，假定已就绪）",
        ),
    ] = 60.0,
    model_binary: Annotated[
        str, typer.Option("--model-binary", help="llama-server 可执行文件")
    ] = "llama-server",
    model_path: Annotated[
        Path | None, typer.Option("--model-path", help="GGUF 模型路径（必填）")
    ] = None,
    model_log: Annotated[Path | None, typer.Option("--model-log", help="服务日志落点")] = None,
    host: Annotated[str, typer.Option("--host", help="后端主机（只允许回环地址）")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", min=1, max=65535, help="后端端口")] = 8080,
    approval_timeout_s: Annotated[
        float,
        typer.Option("--approval-timeout-s", min=0.001, help="人工确认的等待上限秒（超时即拒绝）"),
    ] = 120.0,
) -> None:
    """运行一次会话（装配 → 事件循环 → 退出码）。"""
    request = RunRequest(
        task=task,
        working_dir=Path.cwd() if working_dir is None else working_dir,
        allowed_roots=tuple(allowed_root or ()),
        output_format=output_format,
        interactive=interactive,
        pack_directory=pack_directory,
        capability_tier=_parse_capability_tier(capability_tier),
        max_steps=max_steps,
        max_consecutive_failures=max_consecutive_failures,
        tool_timeout_s=tool_timeout_s,
        max_prompt_tokens=max_prompt_tokens,
        max_completion_tokens=max_completion_tokens,
        model_binary=model_binary,
        model_path=model_path,
        model_log=model_log,
        host=host,
        port=port,
        approval_timeout_s=approval_timeout_s,
        model_request_timeout_s=model_request_timeout_s,
        model_ready_timeout_s=model_ready_timeout_s,
    )
    raise typer.Exit(code=execute(request, stdout=sys.stdout, stderr=sys.stderr))


def _parse_capability_tier(value: str) -> CapabilityTier:
    """把 ``--capability-tier`` 解析成枚举；未知取值 ⇒ 用法错误（不猜一个档位）。

    ``select_tools`` 对未知档位**不回落**（``harness/trimming.py``）；档位不确定时应显式取
    最保守的 ``BASIC``，因此这里也**不**默默改成默认值。
    """
    try:
        return CapabilityTier(value)
    except ValueError:
        allowed = " | ".join(member.value for member in CapabilityTier)
        raise typer.BadParameter(f"未知的能力档位（只接受 {allowed}）") from None


if __name__ == "__main__":  # pragma: no cover - 进程入口
    main()
