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

__all__ = ["ListDirTool", "ReadFileTool", "WriteFileTool"]

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
