"""工具契约（L2 能力层）。

字段级定义见 ``docs/design/interfaces/tools.md``（本模块是它的唯一实现）；
边界处签名与语义的上游决策见 ADR-0015 §5.1.2。
``ToolRegistry`` 的**实现**仍在 ``tools/registry.py``，本零行为契约层只放 Protocol。
本模块只依赖标准库与 ``contracts`` 内部模块（ADR-0015 §7.1 R3）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from agent_sec_perf.contracts.policy import Capability


@dataclass(frozen=True)
class ToolCallRequest:
    """一次工具调用的请求。

    ``arguments_json`` 是**原始 JSON 文本：不可信、尚未解析**——它把信任边界**钉在
    HARNESS 侧**；工具实现不得自行解析它（否则校验可被绕过，``REQ-SEC-03``）。
    """

    call_id: str
    name: str
    arguments_json: str


@dataclass(frozen=True)
class ToolResult:
    """一次工具调用的结果。

    ``ok=False`` 表达**工具级失败**（可回喂给模型，属正常数据通路，不是异常）。
    """

    ok: bool
    content: str
    error: str | None = None
    truncated: bool = False
    audit_id: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    """工具描述（数据，不是行为）。

    ``description_digest`` 的初始口径（覆盖范围 + 规范化 + sha256）见
    ``docs/design/interfaces/tools.md`` §2.3（未决项 U2）；``builtin`` 来源可为 ``None``，
    外部来源（MCP）必须做摘要比对以防描述被篡改（``REQ-TOOL-03``）。
    """

    name: str
    description: str
    parameters_schema: Mapping[str, object]
    capabilities: frozenset[Capability]
    source: str = "builtin"
    description_digest: str | None = None


@dataclass(frozen=True)
class ExecutionContext:
    """一次工具调用的执行上下文（由 HARNESS 在 ``PolicyDecision`` 允许后构造）。

    不变式：``working_dir`` 必须**落在** ``allowed_roots`` 之内；``network_allowed``
    默认 ``False``（default-deny）。本类型**不含凭据、不暴露** ``os.environ``；命令执行
    唯一经 ``foundation.proc``（后者自建最小环境）。
    """

    session_id: str
    call_id: str
    working_dir: Path
    allowed_roots: tuple[Path, ...]
    timeout_s: float
    network_allowed: bool = False


class Tool(Protocol):
    """工具的统一抽象。

    ``invoke`` 收到的 ``args`` 必须是**已校验的结构化参数**；``invoke`` 不得再解析原始
    JSON 字符串（ADR-0015 §5.1.2 的关键约定）。
    """

    @property
    def spec(self) -> ToolSpec: ...

    def invoke(self, args: Mapping[str, object], *, ctx: ExecutionContext) -> ToolResult: ...


class ToolRegistry(Protocol):
    """工具注册表（被 ``harness/`` 消费；实现见 ``tools/registry.py``）。

    ``specs()`` 返回当前**裁剪后**、可暴露给模型的工具描述（``REQ-HARNESS-03``）；
    ``resolve()`` 对**未知工具返回 ``None``**——模型幻觉出不存在的工具是预期的不可信
    输入，由调用方**默认拒绝 + 审计**，**不抛异常**（``REQ-SEC-01``）。
    并发：只读视图，注册发生在启动阶段，会话期间不变。
    """

    def specs(self) -> Sequence[ToolSpec]: ...

    def resolve(self, name: str) -> Tool | None: ...
