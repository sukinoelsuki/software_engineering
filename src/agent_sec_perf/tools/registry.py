"""工具注册表与工具层共用辅助（``G8``，L2 能力层）。

契约（``ToolRegistry`` / ``ToolSpec`` 字段与语义）见 ``docs/design/interfaces/tools.md``；
本模块只实现它，不改契约。

三条约定：

1. **``resolve()`` 对未知工具返回 ``None``，不抛异常**（``tools.md`` §2.6）。
   模型幻觉出一个不存在的工具是**预期的不可信输入**（弱模型尤甚）；用返回值表达，
   才能让"未知工具 ⇒ 拒绝 + 审计"成为一条**显式、可测**的路径，而不是被某个
   ``except`` 笼统吞掉。注册在**启动阶段**完成、会话期只读（构造后不再变更）。
2. **摘要校验防投毒 / rug-pull**（``REQ-TOOL-03``，``tools.md`` §2.3 初始口径）：
   ``description_digest`` 覆盖 ``name`` + ``description`` + ``parameters_schema``，
   经 ``json.dumps(sort_keys=True, ensure_ascii=False, separators=(",", ":"))`` 规范化后取
   sha256。``builtin`` 工具代码即来源，摘要可省略；**外部来源**（``mcp:<server_id>``）
   必须给出摘要且与重算值一致，否则**拒绝使用**——fail-secure 的落法是**启动即失败**，
   **不是**"静默把它从列表里剔掉"（后者会让"用了被投毒的描述"与"少了一个工具"同形）。
3. **工具层共用辅助集中在本模块**（审计写入、输出上限、参数校验）：审计与输出上限是
   失效代价不对称的约束（丢证据、内存耗尽）。``files`` 与 ``shell`` 各写一份，
   就等于存在两条**可能漂移**的安全口径。

工具侧的信任模型（``tools.md`` §1）：``invoke(args)`` 收到的 ``args`` 虽已由 HARNESS
按 schema 校验，**仍按不可信输入处理**——逐项做类型 / 取值校验，失败即
``ToolResult(ok=False, ...)``（fail-secure，**不得**回退放行）。
"""

from __future__ import annotations

import hashlib
import json
import math
import pathlib
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from agent_sec_perf.contracts.audit import AuditEvent, AuditEventKind, AuditOutcome, AuditSink
from agent_sec_perf.contracts.tools import (
    ExecutionContext,
    Tool,
    ToolSpec,
)
from agent_sec_perf.contracts.tools import ToolRegistry as ToolRegistryContract
from agent_sec_perf.foundation.errors import BenchError
from agent_sec_perf.foundation.logging import sanitize_for_display
from agent_sec_perf.foundation.paths import resolve_within

__all__ = [
    "MAX_TOOL_OUTPUT_BYTES",
    "REGISTRY_BUILTIN_SOURCE",
    "ToolArgumentError",
    "ToolRegistrationError",
    "ToolRegistry",
    "audit_tool_call",
    "description_digest",
    "optional_positive_int_arg",
    "optional_positive_number_arg",
    "optional_str_arg",
    "reject_unknown_args",
    "require_str_arg",
    "resolve_tool_path",
    "truncate_output",
]

#: 内置来源标识：代码即来源（``tools.md`` §2.3）。
REGISTRY_BUILTIN_SOURCE = "builtin"

#: 单个工具的单次输出字节上限（安全基线：限制输出体积，避免一次读取耗尽内存 / 上下文）。
MAX_TOOL_OUTPUT_BYTES = 64 * 1024

#: 参数名 / 工具名回显进错误信息前的截断长度（不可信文本，防日志被塞满）。
_LABEL_LIMIT = 32


class ToolRegistrationError(BenchError):
    """工具注册被拒绝（重名 / 外部来源摘要缺失或不一致）。

    fail-secure：**拒绝启动**，而不是静默剔除——"少了一个工具"与"工具描述被篡改"
    必须在启动期就区分开（``REQ-TOOL-03``）。
    """


class ToolArgumentError(ValueError):
    """工具参数不符合 schema 或载荷形状（fail-secure：拒绝执行，**不回退**为放行）。

    刻意独立于 :class:`ValueError` 的其它用法：``invoke`` 只需捕获这一种，
    既不会误吞无关异常，也不会漏掉参数缺陷。
    """


class ToolRegistry(ToolRegistryContract):
    """只读工具注册表（``tools.md`` §2.6 的 ``specs()`` / ``resolve()``）。

    Args:
        tools: 启动阶段登记的工具序列。构造即完成注册与摘要校验，
            此后**只读**（会话期间不变，无锁假设）。

    Raises:
        ToolRegistrationError: 工具名重复，或摘要校验未通过（见模块 docstring 第 2 条）。
    """

    def __init__(self, tools: Sequence[Tool]) -> None:
        registered: dict[str, Tool] = {}
        specs: list[ToolSpec] = []
        for tool in tools:
            spec = tool.spec
            _verify_digest(spec)
            if spec.name in registered:
                shown = sanitize_for_display(spec.name, limit=_LABEL_LIMIT)
                msg = f"工具名重复：{shown!r}（注册表的键必须唯一）"
                raise ToolRegistrationError(msg)
            registered[spec.name] = tool
            specs.append(spec)
        self._tools = registered
        self._specs: tuple[ToolSpec, ...] = tuple(specs)

    def specs(self) -> Sequence[ToolSpec]:
        """返回**全量注册集（未裁剪）**的工具描述（``ToolSpec``，不是可执行句柄）。

        裁剪**不**在本层发生：它由 ``harness/trimming.py::select_tools`` 按
        ``capability_tier`` 与本会话白名单承担（``REQ-HARNESS-03``）；注册表知道的是
        "启动了哪些工具"，不知道"本会话暴露哪些"——两件事分别属于两个模块，避免形成
        两份必然漂移的判定。``T6`` 已于 2026-09-19 裁决为**全量**（依据与联动清单见
        ``docs/design/interfaces/tools.md`` §2.6、``docs/design/interfaces/harness.md`` §8）。
        """
        return self._specs

    def resolve(self, name: str) -> Tool | None:
        """按名取工具；**未知工具返回 ``None``**（调用方默认拒绝 + 审计，不抛异常）。"""
        # 运行期可能收到非 ``str``（鸭子类型的装配点）；按 ``object`` 判定，避免类型标注掩盖形状问题。
        requested: object = name
        if not isinstance(requested, str):
            return None
        return self._tools.get(requested)


def description_digest(name: str, description: str, parameters_schema: Mapping[str, object]) -> str:
    """按 ``tools.md`` §2.3 的初始口径计算描述摘要（sha256 十六进制）。

    Raises:
        ToolRegistrationError: 描述里含不可 JSON 规范化的结构（无法给出稳定摘要）。
    """
    payload = {
        "name": name,
        "description": description,
        "parameters_schema": parameters_schema,
    }
    try:
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        shown = sanitize_for_display(name, limit=_LABEL_LIMIT)
        msg = f"工具描述无法规范化（含不可序列化的结构）：{shown!r}"
        raise ToolRegistrationError(msg) from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _verify_digest(spec: ToolSpec) -> None:
    """校验描述摘要：外部来源必须提供且一致；``builtin`` 只校验"提供了就必须一致"。"""
    expected = description_digest(spec.name, spec.description, spec.parameters_schema)
    shown = sanitize_for_display(spec.name, limit=_LABEL_LIMIT)
    if spec.source == REGISTRY_BUILTIN_SOURCE:
        if spec.description_digest is not None and spec.description_digest != expected:
            msg = f"内置工具的 description_digest 与描述不一致：{shown!r}"
            raise ToolRegistrationError(msg)
        return
    if spec.description_digest is None:
        msg = f"外部来源（{spec.source}）工具必须提供 description_digest：{shown!r}"
        raise ToolRegistrationError(msg)
    if spec.description_digest != expected:
        msg = f"工具描述摘要不一致（疑似被篡改，拒绝使用）：{shown!r}"
        raise ToolRegistrationError(msg)


# ----------------------------------------------------------------------
# 共用辅助：审计
# ----------------------------------------------------------------------


def audit_tool_call(
    sink: AuditSink,
    *,
    ctx: ExecutionContext,
    tool_name: str,
    ok: bool,
    detail: Mapping[str, object] | None = None,
) -> str:
    """记录一条 ``TOOL_CALL`` 审计事件并返回其 ``event_id``。

    返回的 id 由调用方放进 ``ToolResult.audit_id``（``audit.md`` §2.3：两者同源），
    从而让"这一次工具调用"可被 :meth:`JsonlAuditSink.query_by_id` 回放。

    ``detail`` **只放结构化、我方产生的字段**（布尔 / 计数 / 短码）：调用参数与工具输出
    都是不可信数据，原样写进审计等于把不可信内容固化成长期证据。

    Raises:
        Exception: ``sink.emit`` 的异常**原样冒泡**（不捕获）。静默丢事件等于
            ``REQ-SEC-06`` 的"可回放"验收失败（``audit.md`` §2.4）。
    """
    event_id = uuid.uuid4().hex
    event = AuditEvent(
        event_id=event_id,
        kind=AuditEventKind.TOOL_CALL,
        timestamp=datetime.now(tz=UTC).isoformat(),
        session_id=ctx.session_id,
        outcome=AuditOutcome.OK if ok else AuditOutcome.ERROR,
        call_id=ctx.call_id,
        tool_name=tool_name,
        detail={} if detail is None else dict(detail),
    )
    # 此处**不得**包 try：审计基础设施故障必须冒泡，不得被伪装成"工具失败"。
    sink.emit(event)
    return event_id


# ----------------------------------------------------------------------
# 共用辅助：输出上限
# ----------------------------------------------------------------------


def truncate_output(text: str, *, limit: int = MAX_TOOL_OUTPUT_BYTES) -> tuple[str, bool]:
    """按**字节**上限截断输出，返回 ``(文本, 是否被截断)``。

    以字节计而不是字符：上限的对手是内存与上下文体积，而一个字符在 UTF-8 下最多 4 字节。
    截断点可能落在多字节字符中间，故用 ``errors="ignore"`` 丢掉不完整的尾字节
    （否则会返回坏编码，回喂给模型时又要在别处再兜一次）。
    """
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text, False
    return raw[:limit].decode("utf-8", errors="ignore"), True


# ----------------------------------------------------------------------
# 共用辅助：路径解析（相对路径一律相对会话工作目录）
# ----------------------------------------------------------------------


def resolve_tool_path(raw: str, *, ctx: ExecutionContext, what: str) -> pathlib.Path:
    """把工具参数里的路径解析为绝对路径，并做白名单校验。

    **相对路径一律相对 ``ctx.working_dir``，而不是进程 CWD**。两者看起来等价、
    实际不是：进程 CWD 是**实现细节**（取决于 HARNESS 从哪里启动、cli 如何被调用），
    而工具参数里的相对路径在语义上就是"相对本次会话的工作目录"。用 CWD 解析会让：

    1. **同一份参数在不同启动方式下解析到不同位置**——不可复现，也解释了"测试里能过、
       换一处跑就报路径不在允许根内"这类现象；
    2. **解析结果与"沙箱工作目录"这一安全语义脱钩**：允许根判定仍在
       :func:`~agent_sec_perf.foundation.paths.resolve_within` 完成，但候选路径的**基准**
       变成了一个与上下文无关的全局量。

    规范化与白名单判定仍交给 ``resolve_within``：``..`` 上跳与符号链接逃逸
    在 ``resolve()`` 展开后判定，**天然被拒**（``REQ-SEC-05``）。

    Raises:
        PathNotAllowedError: 规范化后的路径不在 ``ctx.allowed_roots`` 内。
    """
    candidate = pathlib.Path(raw)
    base = candidate if candidate.is_absolute() else ctx.working_dir / candidate
    return resolve_within(base, ctx.allowed_roots, what=what)


# ----------------------------------------------------------------------
# 共用辅助：参数校验（args 一律按不可信输入处理）
# ----------------------------------------------------------------------


def reject_unknown_args(args: Mapping[str, object], allowed: frozenset[str]) -> None:
    """拒绝 schema 未声明的参数（fail-secure：不把多出来的键当"顺带接受"）。"""
    unknown = sorted(
        sanitize_for_display(str(key), limit=_LABEL_LIMIT) for key in args if key not in allowed
    )
    if unknown:
        msg = f"出现不支持的参数：{'、'.join(unknown)}"
        raise ToolArgumentError(msg)


def require_str_arg(args: Mapping[str, object], key: str, *, allow_empty: bool = False) -> str:
    """取必填字符串参数；缺失 / 类型不符 / （未允许时）空串一律拒绝。"""
    value = args.get(key)
    if not isinstance(value, str):
        raise ToolArgumentError(f"参数 {key} 必须是字符串")
    if not value and not allow_empty:
        raise ToolArgumentError(f"参数 {key} 必须是非空字符串")
    return value


def optional_str_arg(args: Mapping[str, object], key: str) -> str | None:
    """取可选字符串参数；**存在**时必须是非空字符串（``None`` 视为未提供）。"""
    value = args.get(key)
    if value is None:
        return None
    return require_str_arg(args, key)


def optional_positive_int_arg(args: Mapping[str, object], key: str) -> int | None:
    """取可选正整数参数；``bool`` 显式排除（``True`` 是 ``int`` 的实例，不挡会被当成 1）。"""
    value = args.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ToolArgumentError(f"参数 {key} 必须是正整数")
    return value


def optional_positive_number_arg(args: Mapping[str, object], key: str) -> float | None:
    """取可选正数参数（接受 int / float；``bool`` 与非正数拒绝）。"""
    value = args.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ToolArgumentError(f"参数 {key} 必须是正数")
    number = float(value)
    # ``NaN`` / ``inf`` / 非正数一律拒绝：一个"无限等待"的超时等于没有超时。
    if not math.isfinite(number) or number <= 0:
        raise ToolArgumentError(f"参数 {key} 必须是有限正数")
    return number
