"""内置文件工具（``G8``，L2 能力层：信任边界的**执行侧**）。

契约见 ``docs/design/interfaces/tools.md``；本模块只实现 ``Tool``，不改契约。

三条硬约束（每条都对应一个明确的失效模式）：

1. **每次文件访问都经 ``foundation.paths.resolve_within``**（``ADR-0015`` §7.1 R4）。
   路径是**不可信输入**（模型给的、或从工具输出里回灌的）⇒ 先规范化再判定是否落在
   ``ctx.allowed_roots`` 内。``..`` 上跳与符号链接逃逸由 ``resolve()`` 展开后判定，
   **天然被拒**；越界即 ``ok=False``，**不回退**为放行（``REQ-SEC-05``）。
2. **参数按不可信输入处理**：``invoke`` 收到的 ``args`` 虽已由 HARNESS 校验，
   仍逐项做类型 / 取值校验，失败即 ``ToolResult(ok=False, ...)``。
3. **输出体积有上限**（``truncate_output``），超出即 ``truncated=True``。

每次调用都经 :func:`~agent_sec_perf.tools.registry.audit_tool_call` 落一条
``TOOL_CALL`` 审计事件，其 ``event_id`` 写进 ``ToolResult.audit_id``——``REQ-SEC-06``
的"可回放"要能回答"这一次调用做了什么、结果如何"。**审计写入失败必须冒泡**。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from agent_sec_perf.contracts.audit import AuditSink
from agent_sec_perf.contracts.policy import Capability
from agent_sec_perf.contracts.tools import ExecutionContext, Tool, ToolResult, ToolSpec
from agent_sec_perf.foundation.errors import PathNotAllowedError
from agent_sec_perf.foundation.logging import sanitize_for_display
from agent_sec_perf.tools.registry import (
    MAX_TOOL_OUTPUT_BYTES,
    ToolArgumentError,
    audit_tool_call,
    optional_positive_int_arg,
    reject_unknown_args,
    require_str_arg,
    resolve_tool_path,
    truncate_output,
)

__all__ = [
    "ListDirTool",
    "ReadFileTool",
    "ReviseReportTool",
    "WriteFileTool",
    "WriteIncidentReportTool",
    "WriteReportTool",
]

#: 受限写工具的唯一目标文件名（固定在工作区根目录；不是模型可选的路径）。
REPORT_FILENAME = "report.md"

#: 面向模型的错误信息上限（不可信文本，先净化再截断，防一条错误信息被塞进海量文本）。
_ERROR_LIMIT = 200

#: ``list_dir`` 未显式给出 ``max_entries`` 时的条目上限（防一次列目录返回海量条目）。
_DEFAULT_MAX_ENTRIES = 256


class ReadFileTool(Tool):
    """读取白名单根内**普通文件**的内容（能力：``READ_FILE``）。

    Args:
        sink: 审计落点。刻意**无默认值**：不存在"悄悄不审计"的工具实例。
    """

    def __init__(self, sink: AuditSink) -> None:
        self._sink = sink
        self._spec = ToolSpec(
            name="read_file",
            description="读取允许目录内的一个文本文件，返回其内容（超出上限会被截断）。",
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径（允许根内）"},
                    "max_bytes": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "本次读取的字节上限，省略则用默认上限",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            capabilities=frozenset({Capability.READ_FILE}),
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def invoke(self, args: Mapping[str, object], *, ctx: ExecutionContext) -> ToolResult:
        """读取文件；一切异常路径都收敛为 ``ok=False``（审计仍落一条 ``ERROR``）。"""
        try:
            reject_unknown_args(args, frozenset({"path", "max_bytes"}))
            path = require_str_arg(args, "path")
            requested_bytes = optional_positive_int_arg(args, "max_bytes")
        except ToolArgumentError as exc:
            return self._fail(ctx, str(exc), reason="invalid_arguments")

        limit = MAX_TOOL_OUTPUT_BYTES if requested_bytes is None else requested_bytes
        try:
            resolved = resolve_tool_path(path, ctx=ctx, what="path")
        except PathNotAllowedError as exc:
            return self._fail(
                ctx, sanitize_for_display(str(exc), limit=_ERROR_LIMIT), reason="path_not_allowed"
            )
        if not resolved.is_file():
            return self._fail(ctx, "目标是目录或非普通文件，无法按文件读取", reason="not_a_file")

        try:
            data = resolved.read_bytes()
        except OSError as exc:
            return self._fail(ctx, f"文件读取失败（{type(exc).__name__}）", reason="io_error")

        truncated = len(data) > limit
        content = data[:limit].decode("utf-8", errors="replace")
        if truncated:
            content += "\n…（输出已按上限截断）"
        return self._ok(ctx, content, truncated=truncated)

    def _ok(self, ctx: ExecutionContext, content: str, *, truncated: bool) -> ToolResult:
        audit_id = audit_tool_call(
            self._sink,
            ctx=ctx,
            tool_name=self._spec.name,
            ok=True,
            detail={"truncated": truncated},
        )
        return ToolResult(ok=True, content=content, truncated=truncated, audit_id=audit_id)

    def _fail(self, ctx: ExecutionContext, message: str, *, reason: str) -> ToolResult:
        audit_id = audit_tool_call(
            self._sink,
            ctx=ctx,
            tool_name=self._spec.name,
            ok=False,
            detail={"reason": reason},
        )
        return ToolResult(ok=False, content="", error=message, audit_id=audit_id)


class WriteFileTool(Tool):
    """把文本写入白名单根内的文件（能力：``WRITE_FILE``）。

    写入是**特权操作**：目标路径同样经 ``resolve_within``，父目录只在**已判定的根内**创建。
    """

    def __init__(self, sink: AuditSink) -> None:
        self._sink = sink
        self._spec = ToolSpec(
            name="write_file",
            description="把文本写入允许目录内的一个文件（覆盖既有内容，必要时创建父目录）。",
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "文件路径（允许根内）"},
                    "content": {"type": "string", "description": "要写入的文本"},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            capabilities=frozenset({Capability.WRITE_FILE}),
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def invoke(self, args: Mapping[str, object], *, ctx: ExecutionContext) -> ToolResult:
        try:
            reject_unknown_args(args, frozenset({"path", "content"}))
            path = require_str_arg(args, "path")
            content = require_str_arg(args, "content", allow_empty=True)
        except ToolArgumentError as exc:
            return self._fail(ctx, str(exc), reason="invalid_arguments")

        data = content.encode("utf-8")
        if len(data) > MAX_TOOL_OUTPUT_BYTES:
            return self._fail(ctx, "写入内容超过单次上限，已拒绝", reason="payload_too_large")

        try:
            resolved = resolve_tool_path(path, ctx=ctx, what="path")
        except PathNotAllowedError as exc:
            return self._fail(
                ctx, sanitize_for_display(str(exc), limit=_ERROR_LIMIT), reason="path_not_allowed"
            )
        if resolved.exists() and not resolved.is_file():
            return self._fail(ctx, "目标已存在且不是普通文件，拒绝覆盖", reason="not_a_file")

        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_bytes(data)
        except OSError as exc:
            return self._fail(ctx, f"文件写入失败（{type(exc).__name__}）", reason="io_error")

        return self._ok(ctx, f"已写入 {len(data)} 字节", written_bytes=len(data))

    def _ok(self, ctx: ExecutionContext, content: str, *, written_bytes: int) -> ToolResult:
        audit_id = audit_tool_call(
            self._sink,
            ctx=ctx,
            tool_name=self._spec.name,
            ok=True,
            detail={"written_bytes": written_bytes},
        )
        return ToolResult(ok=True, content=content, audit_id=audit_id)

    def _fail(self, ctx: ExecutionContext, message: str, *, reason: str) -> ToolResult:
        audit_id = audit_tool_call(
            self._sink,
            ctx=ctx,
            tool_name=self._spec.name,
            ok=False,
            detail={"reason": reason},
        )
        return ToolResult(ok=False, content="", error=message, audit_id=audit_id)


class ListDirTool(Tool):
    """列出白名单根内某目录的条目名（能力：``READ_FILE``）。"""

    def __init__(self, sink: AuditSink) -> None:
        self._sink = sink
        self._spec = ToolSpec(
            name="list_dir",
            description="列出允许目录内某个目录的条目名（按名称排序，超出上限会被截断）。",
            parameters_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "目录路径（允许根内）"},
                    "max_entries": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "本次列出的条目数上限，省略则用默认上限",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            capabilities=frozenset({Capability.READ_FILE}),
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def invoke(self, args: Mapping[str, object], *, ctx: ExecutionContext) -> ToolResult:
        try:
            reject_unknown_args(args, frozenset({"path", "max_entries"}))
            path = require_str_arg(args, "path")
            requested_entries = optional_positive_int_arg(args, "max_entries")
        except ToolArgumentError as exc:
            return self._fail(ctx, str(exc), reason="invalid_arguments")

        try:
            resolved = resolve_tool_path(path, ctx=ctx, what="path")
        except PathNotAllowedError as exc:
            return self._fail(
                ctx, sanitize_for_display(str(exc), limit=_ERROR_LIMIT), reason="path_not_allowed"
            )
        if not resolved.is_dir():
            return self._fail(ctx, "目标不是目录", reason="not_a_directory")

        try:
            names = sorted(entry.name for entry in resolved.iterdir())
        except OSError as exc:
            return self._fail(ctx, f"目录读取失败（{type(exc).__name__}）", reason="io_error")

        limit = _DEFAULT_MAX_ENTRIES if requested_entries is None else requested_entries
        truncated = len(names) > limit
        content, byte_truncated = truncate_output("\n".join(names[:limit]))
        return self._ok(ctx, content, truncated=truncated or byte_truncated)

    def _ok(self, ctx: ExecutionContext, content: str, *, truncated: bool) -> ToolResult:
        audit_id = audit_tool_call(
            self._sink,
            ctx=ctx,
            tool_name=self._spec.name,
            ok=True,
            detail={"truncated": truncated},
        )
        return ToolResult(ok=True, content=content, truncated=truncated, audit_id=audit_id)

    def _fail(self, ctx: ExecutionContext, message: str, *, reason: str) -> ToolResult:
        audit_id = audit_tool_call(
            self._sink,
            ctx=ctx,
            tool_name=self._spec.name,
            ok=False,
            detail={"reason": reason},
        )
        return ToolResult(ok=False, content="", error=message, audit_id=audit_id)


def _report_format_problem(content: str) -> str | None:
    """报告格式闸门：每行必须是 ``KEY=VALUE``，值非空且不含未替换的尖括号。

    为什么校验放在**工具**而不是只写在提示里：2026-10-26 实测两类失真都出现过——
    ① 照抄提示中的模板占位符（把 ``CODE=<...>`` 原样写进文件）；
    ② 改写时丢值（把 ``CODE=E42`` 写成只有 ``CODE``）。
    两者在**写入时**就能判定，让它在写入时被拒，比"写成功后由机检拦下"少一轮推理，
    也让"错在哪一行"直接出现在工具结果里。

    只校验**格式**，不校验业务取值（那属于机检）：工具不该知道 ``E42`` 是不是正确答案。
    """
    lines = [line for line in content.splitlines() if line.strip()]
    if not lines:
        return "报告内容为空：至少需要一行 KEY=VALUE"
    for index, line in enumerate(lines, start=1):
        key, separator, value = line.partition("=")
        if not separator or not key.strip():
            return f"第 {index} 行不是 KEY=VALUE 形式：{_shown(line)}"
        stripped = value.strip()
        if not stripped:
            return f"第 {index} 行的值为空：{_shown(key)}"
        if "<" in stripped or ">" in stripped:
            return f"第 {index} 行的值仍是占位符（含尖括号），请替换成文件里的真实值：{_shown(key)}"
    return None


def _shown(text: str) -> str:
    """把不可信文本净化并截断后回显（错误信息会进入模型上下文与审计）。"""
    return sanitize_for_display(text.strip(), limit=_ERROR_LIMIT)


class WriteReportTool(Tool):
    """把报告写入工作区根目录的 ``report.md``（能力：``WRITE_FILE``）。

    **为什么不是把通用 ``write_file`` 声明成低风险**：通用写工具的目标路径由模型给出，
    而"路径由不可信输入决定"是路径穿越与覆盖攻击的入口；把它标成 ``low`` 等于把
    "需人工确认"改成"自动放行"（项目规则明令禁止，须先走 ADR）。
    本工具把**目标固定**为工作区内的 ``report.md``、**参数只有一个 content**，
    影响面被工具本身收窄 ⇒ 领域包把它声明为 ``low`` 是"能力更窄"，不是"默认更松"。

    覆盖语义：重复调用会覆盖同名文件。这正是"按复核意见修订报告"的载体，
    但每次写入都会留一条 ``TOOL_CALL`` 审计，修订不是"隐形的第二次写"。
    """

    def __init__(self, sink: AuditSink) -> None:
        self._sink = sink
        self._spec = ToolSpec(
            name="write_report",
            description=(
                "把报告写入工作区根目录的 report.md（覆盖既有内容）。"
                "参数只有报告全文，不能指定其它路径。"
                "正文必须是每行 KEY=VALUE 的形式：值不得为空，不得含未替换的尖括号占位符。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "报告全文（纯文本或 Markdown）",
                    }
                },
                "required": ["content"],
                "additionalProperties": False,
            },
            capabilities=frozenset({Capability.WRITE_FILE}),
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def invoke(self, args: Mapping[str, object], *, ctx: ExecutionContext) -> ToolResult:
        try:
            reject_unknown_args(args, frozenset({"content"}))
            content = require_str_arg(args, "content")
        except ToolArgumentError as exc:
            return self._fail(ctx, str(exc), reason="invalid_arguments")

        problem = _report_format_problem(content)
        if problem is not None:
            return self._fail(ctx, problem, reason="invalid_report_format")

        data = content.encode("utf-8")
        if len(data) > MAX_TOOL_OUTPUT_BYTES:
            return self._fail(ctx, "报告内容超过单次上限，已拒绝", reason="payload_too_large")

        try:
            resolved = resolve_tool_path(REPORT_FILENAME, ctx=ctx, what="报告文件")
        except PathNotAllowedError as exc:
            return self._fail(
                ctx, sanitize_for_display(str(exc), limit=_ERROR_LIMIT), reason="path_not_allowed"
            )
        if resolved.exists() and not resolved.is_file():
            return self._fail(ctx, "同名目标不是普通文件，拒绝写入", reason="not_a_file")

        try:
            resolved.write_bytes(data)
        except OSError as exc:
            return self._fail(ctx, f"报告写入失败（{type(exc).__name__}）", reason="io_error")

        return self._ok(
            ctx, f"已写入 {len(data)} 字节到 {REPORT_FILENAME}", written_bytes=len(data)
        )

    def _ok(self, ctx: ExecutionContext, content: str, *, written_bytes: int) -> ToolResult:
        audit_id = audit_tool_call(
            self._sink,
            ctx=ctx,
            tool_name=self._spec.name,
            ok=True,
            detail={"written_bytes": written_bytes, "artifact": REPORT_FILENAME},
        )
        return ToolResult(ok=True, content=content, audit_id=audit_id)

    def _fail(self, ctx: ExecutionContext, message: str, *, reason: str) -> ToolResult:
        audit_id = audit_tool_call(
            self._sink,
            ctx=ctx,
            tool_name=self._spec.name,
            ok=False,
            # message 含模型写入的内容 ⇒ 先净化再截断；它只进审计，不进事件流。
            detail={"reason": reason, "message": _shown(message)},
        )
        return ToolResult(ok=False, content="", error=message, audit_id=audit_id)


def _field_problem(key: str, value: str) -> str | None:
    """结构化报告的单个字段校验（值非空、单行、不含未替换的尖括号）。"""
    text = value.strip()
    if not text:
        return f"字段 {key} 的值为空"
    if "\n" in text or "\r" in text:
        return f"字段 {key} 的值不能包含换行"
    if "<" in text or ">" in text:
        return f"字段 {key} 的值仍是占位符（含尖括号）"
    return None


#: ``write_incident_report`` 的**固定四字段**（参数名 → 报告行名）。
#:
#: ⚠️ 为什么是显式四字段而不是"任意键值对象"：项目的参数校验器
#: （``harness/arguments.py``，ADR-0020）要求 ``additionalProperties`` **显式为 ``false``**，
#: 任意键对象无法表达 ⇒ 用它定义的工具**每次调用都会被判参数非法**（2026-10-26 实测：
#: `deny / invalid_arguments`，工具根本没被调用）。
_INCIDENT_REPORT_FIELDS: Final[tuple[tuple[str, str], ...]] = (
    ("code", "CODE"),
    ("log_timeout_ms", "LOG_TIMEOUT_MS"),
    ("config_timeout_ms", "CONFIG_TIMEOUT_MS"),
    ("verdict", "VERDICT"),
)


class WriteIncidentReportTool(Tool):
    """按**固定四字段**写故障报告（能力：``WRITE_FILE``）。

    这是**领域工具**（形状由 `incident-triage` 案例约定），不是通用写能力：
    参数只有四个字段值，``KEY=VALUE`` 的行由工具生成。

    为什么需要它（2026-10-26 第六次实测）：即使用 ``write_report`` + 格式闸门，
    4B 模型第一次仍会把 ``VERDICT=待复核`` 写成只有行名；被拒后**直接放弃**（不再重试），
    整段 ``FAILED``。小模型不擅长"按说明拼文本"，但它**能**填字段。
    ⇒ 把格式责任移到工具后，"格式写错"这个失效模式**不存在**。
    目标仍是固定 ``report.md``，参数里没有任何路径。

    **证据校验（第七次实测后新增）**：格式对了，模型仍会**编造取值**
    （实测：`CODE=PAY001` / `LOG_TIMEOUT_MS=1200`，两份现场文件里都没有）。
    ⇒ 三个取证字段的值必须**逐字出现在**现场文件里，否则拒绝写入并指出哪个字段找不到。
    这不是"工具替模型做题"：工具只判断"这个值有没有在文件里出现过"，
    不做选择、不做推断；模型仍要自己读、自己定位、自己挑对那一行。
    ``verdict`` 不参与校验（它是人给的复核结论，不是从文件里抄的）。
    """

    #: 证据文件（相对工作区根；都是案例现场文件）。
    EVIDENCE_FILES: Final[tuple[str, ...]] = ("app.log", "service.toml")
    #: 参与"值必须能在证据文件里找到"校验的字段（``verdict`` 除外）。
    EVIDENCE_FIELDS: Final[tuple[str, ...]] = ("code", "log_timeout_ms", "config_timeout_ms")

    def __init__(self, sink: AuditSink) -> None:
        self._sink = sink
        self._spec = ToolSpec(
            name="write_incident_report",
            description=(
                "把故障报告写入工作区根目录的 report.md（覆盖既有内容）。"
                "只填四个字段：code / log_timeout_ms / config_timeout_ms / verdict；"
                "行格式由工具生成，值只写内容本身。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    name: {"type": "string", "description": f"{name} 的值"}
                    for name, _ in _INCIDENT_REPORT_FIELDS
                },
                "required": [name for name, _ in _INCIDENT_REPORT_FIELDS],
                "additionalProperties": False,
            },
            capabilities=frozenset({Capability.WRITE_FILE}),
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def _evidence_text(self, ctx: ExecutionContext) -> str | None:
        """把证据文件拼成一段文本；任一文件读不出来就返回 ``None``（fail-secure）。"""
        chunks: list[str] = []
        for name in self.EVIDENCE_FILES:
            try:
                resolved = resolve_tool_path(name, ctx=ctx, what="现场文件")
            except PathNotAllowedError:
                return None
            try:
                chunks.append(resolved.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                return None
        return "\n".join(chunks)

    def invoke(self, args: Mapping[str, object], *, ctx: ExecutionContext) -> ToolResult:
        values: list[tuple[str, str]] = []
        try:
            reject_unknown_args(args, frozenset(name for name, _ in _INCIDENT_REPORT_FIELDS))
            for name, _ in _INCIDENT_REPORT_FIELDS:
                values.append((name, require_str_arg(args, name)))
        except ToolArgumentError as exc:
            return self._fail(ctx, str(exc), reason="invalid_arguments")

        supplied = dict(values)
        lines: list[str] = []
        for name, label in _INCIDENT_REPORT_FIELDS:
            value = supplied[name]
            problem = _field_problem(label, value)
            if problem is not None:
                return self._fail(ctx, problem, reason="invalid_report_format")
            lines.append(f"{label}={value.strip()}")

        evidence = self._evidence_text(ctx)
        if evidence is None:
            return self._fail(
                ctx,
                "无法读取现场文件，不能校验收取值的来源，已拒绝写入",
                reason="evidence_unavailable",
            )
        for name in self.EVIDENCE_FIELDS:
            value = supplied[name].strip()
            if value not in evidence:
                return self._fail(
                    ctx,
                    f"字段 {name} 的值 {value!r} 不在这两份现场文件里；"
                    "请逐字抄写文件里的原文，不要推测",
                    reason="value_not_in_evidence",
                )
        content = "\n".join(lines) + "\n"

        data = content.encode("utf-8")
        if len(data) > MAX_TOOL_OUTPUT_BYTES:
            return self._fail(ctx, "报告内容超过单次上限，已拒绝", reason="payload_too_large")

        try:
            resolved = resolve_tool_path(REPORT_FILENAME, ctx=ctx, what="报告文件")
        except PathNotAllowedError as exc:
            return self._fail(
                ctx, sanitize_for_display(str(exc), limit=_ERROR_LIMIT), reason="path_not_allowed"
            )
        if resolved.exists() and not resolved.is_file():
            return self._fail(ctx, "同名目标不是普通文件，拒绝写入", reason="not_a_file")
        try:
            resolved.write_bytes(data)
        except OSError as exc:
            return self._fail(ctx, f"报告写入失败（{type(exc).__name__}）", reason="io_error")

        return self._ok(
            ctx,
            # 明确"已完成、不要重复写"：实测模型会在一次响应里并列多个调用，重复写 20+ 次
            # （内容幂等，但白烧推理、刷屏日志）。这是引导，不是约束。
            f"已写入 {len(lines)} 行到 {REPORT_FILENAME}；报告已完成，不要重复调用本工具。",
            rows=len(lines),
        )

    def _ok(self, ctx: ExecutionContext, content: str, *, rows: int) -> ToolResult:
        audit_id = audit_tool_call(
            self._sink,
            ctx=ctx,
            tool_name=self._spec.name,
            ok=True,
            detail={"artifact": REPORT_FILENAME, "rows": rows},
        )
        return ToolResult(ok=True, content=content, audit_id=audit_id)

    def _fail(self, ctx: ExecutionContext, message: str, *, reason: str) -> ToolResult:
        audit_id = audit_tool_call(
            self._sink,
            ctx=ctx,
            tool_name=self._spec.name,
            ok=False,
            detail={"reason": reason, "message": _shown(message)},
        )
        return ToolResult(ok=False, content="", error=message, audit_id=audit_id)


def _revision_value_problem(key: str, value: str) -> str | None:
    """修订参数校验：``key`` 是行名、``value`` 是单行值（不含换行与占位符）。"""
    if not key or "=" in key or any(char in key for char in "\r\n"):
        return f"key 必须是行名（不含等号或换行）：{_shown(key)}"
    if not value:
        return "value 不能为空"
    if any(char in value for char in "\r\n"):
        return "value 不能包含换行：一次只改一行"
    if "<" in value or ">" in value:
        return f"value 仍是占位符（含尖括号），请替换成真实值：{_shown(value)}"
    return None


class ReviseReportTool(Tool):
    """只改报告里的**一行**（能力：``WRITE_FILE``）。

    为什么需要它（2026-10-26 实测，两次）：把"只改结论行"写成**提示约束**时，4B 模型会
    重写整份报告——丢行、并引入幻觉行（实测写出过 ``STATUS=ACTIVE``）。
    小模型不擅长"逐字复制其余内容"，而这件事**工具可以保证**：
    读现有报告 → 只替换指定 ``KEY`` 那一行 → 写回，其余行逐字不动。

    影响面比 ``write_report`` 更窄：目标仍是固定 ``report.md``，参数是 ``key`` + ``value``，
    且 ``key`` **必须已存在且只出现一次**（否则拒绝）⇒ 模型无法用它新增行或重排结构。
    """

    def __init__(self, sink: AuditSink) -> None:
        self._sink = sink
        self._spec = ToolSpec(
            name="revise_report",
            description=(
                "按复核意见修订工作区根目录的 report.md：只把指定 KEY 那一行的值替换掉，"
                "其余行逐字保留。KEY 必须已存在且只出现一次；不能新增行、不能重排。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "要修改的行名，例如 VERDICT"},
                    "value": {"type": "string", "description": "该行的新值（只写值本身）"},
                },
                "required": ["key", "value"],
                "additionalProperties": False,
            },
            capabilities=frozenset({Capability.WRITE_FILE}),
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def invoke(self, args: Mapping[str, object], *, ctx: ExecutionContext) -> ToolResult:
        try:
            reject_unknown_args(args, frozenset({"key", "value"}))
            key = require_str_arg(args, "key").strip()
            value = require_str_arg(args, "value").strip()
        except ToolArgumentError as exc:
            return self._fail(ctx, str(exc), reason="invalid_arguments")

        problem = _revision_value_problem(key, value)
        if problem is not None:
            return self._fail(ctx, problem, reason="invalid_report_format")

        try:
            resolved = resolve_tool_path(REPORT_FILENAME, ctx=ctx, what="报告文件")
        except PathNotAllowedError as exc:
            return self._fail(
                ctx, sanitize_for_display(str(exc), limit=_ERROR_LIMIT), reason="path_not_allowed"
            )
        if not resolved.is_file():
            return self._fail(
                ctx, "报告还不存在：请先用 write_report 写初稿", reason="report_missing"
            )

        try:
            original = resolved.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return self._fail(ctx, f"报告读取失败（{type(exc).__name__}）", reason="io_error")

        # keepends：其余行必须**逐字**保留（含原有换行符），不重新格式化整份报告。
        lines = original.splitlines(keepends=True)
        target = key.upper()
        hits = [
            index
            for index, line in enumerate(lines)
            if line.partition("=")[0].strip().upper() == target
        ]
        if len(hits) != 1:
            return self._fail(
                ctx,
                f"报告中 {target} 出现 {len(hits)} 次（要求恰好 1 次），拒绝修订",
                reason="key_not_unique",
            )
        index = hits[0]
        ending = "\n" if lines[index].endswith(("\n", "\r")) else ""
        lines[index] = f"{target}={value}{ending}"
        try:
            resolved.write_text("".join(lines), encoding="utf-8")
        except OSError as exc:
            return self._fail(ctx, f"报告写入失败（{type(exc).__name__}）", reason="io_error")

        return self._ok(ctx, f"已把 {target} 更新（{len(value)} 个字符）", key=target)

    def _ok(self, ctx: ExecutionContext, content: str, *, key: str) -> ToolResult:
        audit_id = audit_tool_call(
            self._sink,
            ctx=ctx,
            tool_name=self._spec.name,
            ok=True,
            detail={"artifact": REPORT_FILENAME, "key": key},
        )
        return ToolResult(ok=True, content=content, audit_id=audit_id)

    def _fail(self, ctx: ExecutionContext, message: str, *, reason: str) -> ToolResult:
        audit_id = audit_tool_call(
            self._sink,
            ctx=ctx,
            tool_name=self._spec.name,
            ok=False,
            detail={"reason": reason, "message": _shown(message)},
        )
        return ToolResult(ok=False, content="", error=message, audit_id=audit_id)
