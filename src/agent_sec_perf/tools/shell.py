"""内置命令执行工具（``G8``，L2 能力层：执行可执行代码 = 最危险的信任边界）。

契约见 ``docs/design/interfaces/tools.md``；本模块只实现 ``Tool``，不改契约。

四条硬约束：

1. **唯一子进程入口**：命令一律经 ``foundation.proc.run``（``ADR-0015`` §7.1 R4）；
   不经 shell、参数以**列表**传入（``argv``），因此不存在 shell 元字符拼接面
   （``&&`` / ``|`` / ``;`` 只能是普通参数，不会被解释）。
2. **隔离失败即失败**：``isolation="user"`` 是**固定**取值（非特权 uid + 资源上限 + 最小环境）。
   遇到 ``IsolationError`` **不得**回退成非隔离执行——回退等于把"隔离没生效"伪装成
   "命令跑成功了"，那正是资源与权限约束失效的形态（``REQ-SEC-05``）。
3. **``argv[0]`` 解析为绝对路径**：经 ``proc.resolve_binary``，避免 PATH 被污染时
   执行到别的程序（``foundation/proc.py`` 的既定口径）。
4. **输出体积有上限**（``truncate_output``），非零退出码按工具级失败（``ok=False``）回报。

每次调用都落一条 ``TOOL_CALL`` 审计事件，``event_id`` 写进 ``ToolResult.audit_id``；
**审计写入失败必须冒泡**。
"""

from __future__ import annotations

from collections.abc import Mapping

from agent_sec_perf.contracts.audit import AuditSink
from agent_sec_perf.contracts.policy import Capability
from agent_sec_perf.contracts.tools import ExecutionContext, Tool, ToolResult, ToolSpec
from agent_sec_perf.foundation import proc
from agent_sec_perf.foundation.errors import IsolationError, PathNotAllowedError, ProtocolError
from agent_sec_perf.foundation.logging import sanitize_for_display
from agent_sec_perf.tools.registry import (
    ToolArgumentError,
    audit_tool_call,
    optional_positive_number_arg,
    optional_str_arg,
    reject_unknown_args,
    resolve_tool_path,
    truncate_output,
)

__all__ = ["ShellCommandTool"]

#: 面向模型的错误信息上限（不可信文本，先净化再截断）。
_ERROR_LIMIT = 200

#: 隔离档位：**固定**为 ``user``。本工具刻意不提供"非隔离"开关——那会成为一个
#: 由模型参数决定隔离强度的通道，属 fail-open。
_ISOLATION = "user"


class ShellCommandTool(Tool):
    """在受限环境执行一条命令（能力：``EXECUTE_COMMAND``）。

    Args:
        sink: 审计落点。刻意**无默认值**：不存在"悄悄不审计"的工具实例。
    """

    def __init__(self, sink: AuditSink) -> None:
        self._sink = sink
        self._spec = ToolSpec(
            name="run_command",
            description=(
                "在受限（非特权用户 + 资源上限 + 最小环境）的沙箱中执行一条命令，"
                "返回合并后的标准输出与标准错误。参数以列表给出，不经 shell 解释。"
            ),
            parameters_schema={
                "type": "object",
                "properties": {
                    "argv": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "description": '命令与参数列表（如 ["ls", "-la"]），不经 shell',
                    },
                    "cwd": {
                        "type": "string",
                        "description": "工作目录（允许根内）；省略则用会话工作目录",
                    },
                    "timeout_s": {
                        "type": "number",
                        "exclusiveMinimum": 0,
                        "description": "本次命令的超时秒数；省略则用调用上下文的超时",
                    },
                },
                "required": ["argv"],
                "additionalProperties": False,
            },
            capabilities=frozenset({Capability.EXECUTE_COMMAND}),
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    def invoke(self, args: Mapping[str, object], *, ctx: ExecutionContext) -> ToolResult:
        """执行命令；一切失败路径都收敛为 ``ok=False``（审计仍落一条 ``ERROR``）。"""
        try:
            reject_unknown_args(args, frozenset({"argv", "cwd", "timeout_s"}))
            argv = _require_argv(args)
            cwd_arg = optional_str_arg(args, "cwd")
            timeout_arg = optional_positive_number_arg(args, "timeout_s")
        except ToolArgumentError as exc:
            return self._fail(ctx, str(exc), reason="invalid_arguments")

        try:
            cwd = (
                ctx.working_dir
                if cwd_arg is None
                else resolve_tool_path(cwd_arg, ctx=ctx, what="cwd")
            )
        except PathNotAllowedError as exc:
            return self._fail(
                ctx, sanitize_for_display(str(exc), limit=_ERROR_LIMIT), reason="path_not_allowed"
            )

        timeout_s = ctx.timeout_s if timeout_arg is None else timeout_arg
        try:
            binary = proc.resolve_binary(argv[0])
        except ProtocolError as exc:
            return self._fail(
                ctx, sanitize_for_display(str(exc), limit=_ERROR_LIMIT), reason="binary_not_found"
            )

        try:
            # 固定 user 隔离；不经 shell、参数为列表。
            result = proc.run(
                [binary, *argv[1:]],
                cwd=cwd,
                timeout_s=timeout_s,
                isolation=_ISOLATION,
            )
        except IsolationError:
            # 隔离失败**不得**回退为非隔离执行（REQ-SEC-05）；失败即失败。
            return self._fail(
                ctx, "隔离执行失败，已拒绝执行（不回退为非隔离）", reason="isolation_failed"
            )
        except ProtocolError as exc:
            return self._fail(
                ctx, sanitize_for_display(str(exc), limit=_ERROR_LIMIT), reason="execution_error"
            )

        content, truncated = truncate_output(result.output())
        if result.returncode != 0:
            message = f"命令退出码 {result.returncode}"
            return self._fail(
                ctx,
                message,
                reason="nonzero_exit",
                detail={"returncode": result.returncode, "truncated": truncated},
                content=content,
            )
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

    def _fail(
        self,
        ctx: ExecutionContext,
        message: str,
        *,
        reason: str,
        detail: Mapping[str, object] | None = None,
        content: str = "",
    ) -> ToolResult:
        payload: dict[str, object] = {"reason": reason}
        if detail is not None:
            payload.update(detail)
        audit_id = audit_tool_call(
            self._sink, ctx=ctx, tool_name=self._spec.name, ok=False, detail=payload
        )
        return ToolResult(ok=False, content=content, error=message, audit_id=audit_id)


def _require_argv(args: Mapping[str, object]) -> list[str]:
    """校验 ``argv`` 是**非空字符串列表**。

    刻意不接受裸字符串：``["ls -la"]`` 与 ``"ls -la"`` 在"参数以列表传入"的约束下
    是两种不同的东西——前者是一段含空格的程序名，后者才是"把整串当命令"（那正是
    要防的注入形态）。此处直接拒绝字符串，避免调用方误以为它会被拆词。
    """
    raw = args.get("argv")
    if isinstance(raw, (str, bytes)) or not isinstance(raw, (list, tuple)):
        raise ToolArgumentError("参数 argv 必须是字符串列表")
    items = list(raw)
    if not items:
        raise ToolArgumentError("参数 argv 不得为空")
    for index, item in enumerate(items):
        if not isinstance(item, str) or not item:
            raise ToolArgumentError(f"参数 argv[{index}] 必须是非空字符串")
    return items
