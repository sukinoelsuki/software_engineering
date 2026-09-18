"""工具契约（L2 能力层）。

形状取自 ADR-0015 §5.1.2 的接口形态表与示意代码块：``ToolCallRequest`` /
``ToolResult`` 为**逐字段给定**的数据类型；``Tool`` 为行为 Protocol。

``ToolSpec`` 与 ``ExecutionContext`` 在 ADR 中只被引用、未定义字段，此处以
**无成员 Protocol 占位**——实现者不得据此假设任何成员；完整定义属后续交付物
``docs/design/interfaces/``。本模块只依赖标准库（ADR-0015 §7.1 R3）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ToolCallRequest:
    """一次工具调用的请求。

    ``arguments_json`` 是**原始 JSON 文本**：不可信、尚未解析。校验发生在 HARNESS
    侧的信任边界（按 :class:`ToolSpec` 的参数 schema），工具实现不得自行解析它。
    """

    call_id: str
    name: str
    arguments_json: str


@dataclass(frozen=True)
class ToolResult:
    """一次工具调用的结果。

    ``ok=False`` 表达**工具级失败**（可回喂给模型）；``truncated`` 标记输出被截断；
    ``audit_id`` 关联审计记录，供 :class:`~agent_sec_perf.contracts.audit.AuditSink` 回放。
    """

    ok: bool
    content: str
    error: str | None = None
    truncated: bool = False
    audit_id: str | None = None


class ToolSpec(Protocol):
    """工具描述。

    **形状待澄清**：ADR-0015 只给出类型名（并提及 ``parameters_schema``），
    未规定字段；实现者不得据此假设任何成员。
    """


class ExecutionContext(Protocol):
    """工具调用的执行上下文。

    **形状待澄清**：ADR-0015 只在示意中引用该类型名，未规定字段。
    """


class Tool(Protocol):
    """工具的统一抽象。

    关键约定（ADR-0015 §5.1.2）：``invoke`` 收到的 ``args`` 必须是**已校验的结构化
    参数**；``invoke`` **不得**再解析原始 JSON 字符串，否则校验会被绕过。
    """

    @property
    def spec(self) -> ToolSpec: ...

    def invoke(self, args: Mapping[str, object], *, ctx: ExecutionContext) -> ToolResult: ...
