"""审计落点：JSONL 追加写 + 按 ``audit_id`` 查询（``REQ-SEC-06`` / ``REQ-OBS-01``）。

契约（字段与错误语义）见 ``docs/design/interfaces/audit.md``；本模块是 ``AuditSink`` 的实现。

三条设计取舍：

1. **只追加、行即事件**（JSONL）：一行一条事件，断电/崩溃最多影响**正在写的那一行**，
   已写事件仍是完整证据；相比"整份 JSON 每次重写"，它不会因为一次写失败毁掉全部历史。
2. **``emit`` 失败必须冒泡**（契约 §2.4）：审计是"可回放"的载体，静默丢事件等于
   ``REQ-SEC-06`` 验收失败。因此本模块**不**捕获任何写入异常，也不把失败降级为告警。
3. **落点再脱敏一次**（纵深防御）：契约要求生产者入事件前已脱敏，但"生产者漏脱"是现实的
   失效模式；写入前按同一套键名规则再脱一遍 ``detail``，凭据就不会因为一处疏漏而进证据。
   注意审计字段本身（``tool_name`` 等）**保持原样**——证据不得被改写，转义由 JSON 负责。

并发：实现内部用一把锁串行化写入与读取（契约要求"可被多线程调用"）。
资源：不持有长期打开的文件句柄（每次追加即关闭），因此进程异常退出也不会留下半写缓冲；
:meth:`JsonlAuditSink.flush` 通过 ``fsync`` 提供"断电也不丢"的强持久化点。
"""

from __future__ import annotations

import json
import os
import pathlib
import threading
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import cast

from agent_sec_perf.contracts.audit import AuditEvent, AuditEventKind, AuditOutcome, AuditSink
from agent_sec_perf.contracts.policy import Capability, RiskLevel
from agent_sec_perf.foundation import config
from agent_sec_perf.foundation.errors import SchemaError
from agent_sec_perf.foundation.logging import redact_sensitive, sanitize_for_display
from agent_sec_perf.foundation.paths import resolve_within

__all__ = ["DEFAULT_AUDIT_FILENAME", "JsonlAuditSink"]

#: 审计文件名（固定默认值；调用方可覆盖，但覆盖值必须是**单个文件名**）。
DEFAULT_AUDIT_FILENAME = "audit.jsonl"

#: 非法文件名回显时的截断长度（防日志被超长文本塞满）。
_NAME_LIMIT = 64


class JsonlAuditSink(AuditSink):
    """以 JSONL 文件为落点的审计写入器（只追加）。

    落点**不是可信输入**：它可能来自跟着仓库走的 ``.lowspec.toml``（``audit.md`` §2.5）。
    因此构造期**必须**先过路径白名单，越界即拒绝——不放行、不回退默认目录、不静默关闭审计
    （P3/P5）。配置期已在 ``foundation.config`` 校验过一次，这里**再校验一次**覆盖绕过配置的
    调用方（测试、未来的 CLI 参数、其它装配点）。

    Args:
        directory: 审计目录（必须落在 ``roots`` 之内）。
        roots: 允许的根目录集合。``None`` ⇒ 取 ``foundation.config.ALLOWED_AUDIT_ROOTS``
            （**仍在调用时**从模块属性读取，而不是把常量绑进默认形参——后者会让"测试替换根集合"
            静默失效）。``None`` 的语义是"用安全默认"，**不是**"不校验"；空元组同样不是"放行"，
            而是"没有任何根可选" ⇒ 一切都被拒。
        filename: 审计文件名；默认 :data:`DEFAULT_AUDIT_FILENAME`。
            仍做形状校验（不含分隔符、不是 ``.``/``..``）：文件名一旦可配置就是路径输入，
            路径拼接是被规则明令禁止的写法。

    Raises:
        ValueError: 文件名为空 / 含路径分隔符 / 含 NUL / 是 ``.`` 或 ``..``。
        PathNotAllowedError: 目录不在 ``roots`` 内（**装配期**语义：该路径未被允许）。
            **先于** ``mkdir`` 判定，因此越界时不会先按不可信路径创建目录（P4）。
        OSError: 目录无法创建（**构造期**暴露，而不是等到第一次写入才失败）。
    """

    def __init__(
        self,
        directory: pathlib.Path,
        *,
        roots: Sequence[pathlib.Path] | None = None,
        filename: str = DEFAULT_AUDIT_FILENAME,
    ) -> None:
        self._filename = _validated_filename(filename)
        allowed_roots = config.ALLOWED_AUDIT_ROOTS if roots is None else tuple(roots)

        # 顺序不可颠倒：先白名单校验、后 mkdir（P4）。
        # `mkdir(parents=True, exist_ok=True)` 本身就已经按不可信路径**写了一次**（创建目录即越界写），
        # 先建后拒会把"拒绝"退化成"已经动过手再拒绝"。
        resolved = resolve_within(directory, allowed_roots, what="审计目录")
        self._directory = resolved
        self._path = resolved / self._filename
        self._lock = threading.Lock()
        self._directory.mkdir(parents=True, exist_ok=True)

    @property
    def path(self) -> pathlib.Path:
        """审计文件路径（只读；供运维定位与测试断言）。"""
        return self._path

    def emit(self, event: AuditEvent) -> None:
        """追加一条事件并落盘。

        Raises:
            ValueError: 事件的时间戳不是带时区的 ISO-8601 文本（naive 时间跨时区比较会静默出错）。
            OSError: 写入失败（磁盘满、权限、只读挂载……）——**必须冒泡**，不得吞。
            TypeError: ``detail`` 里含不可 JSON 序列化的对象。
                刻意不做 ``default=str`` 兜底：把任意对象"转成字符串"写进证据，
                既可能漏掉结构信息，也可能把不可信文本原样固化进审计。
        """
        payload = _event_to_payload(event)
        line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with self._lock, self._path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(f"{line}\n")

    def flush(self) -> None:
        """把已写入的事件强持久化到磁盘（``fsync``）。

        **幂等**：可重复调用（进程退出路径可能多次触发）；尚无事件文件时为空操作。
        """
        with self._lock:
            if not self._path.is_file():
                return
            with self._path.open("rb+") as handle:
                os.fsync(handle.fileno())

    def read_all(self) -> tuple[AuditEvent, ...]:
        """按写入顺序读出全部事件（"可回放"的读取侧）。

        Raises:
            SchemaError: 某一行不是合法事件（含崩溃时写了一半的截断行）。
                **不跳过坏行**：静默跳过等于让证据链出现无声缺口，
                而"缺口不可见"正是攻击者最想要的效果。
        """
        with self._lock:
            if not self._path.is_file():
                return ()
            with self._path.open("r", encoding="utf-8") as handle:
                text = handle.read()

        events: list[AuditEvent] = []
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            events.append(_parse_line(line, number=number))
        return tuple(events)

    def query_by_id(self, audit_id: str) -> AuditEvent | None:
        """按 ``audit_id`` 查事件（``PolicyDecision.audit_id`` ↔ ``AuditEvent.event_id``）。

        Returns:
            命中的事件；没有则 ``None``（与 ``ToolRegistry.resolve`` 的"未找到返回 None"
            一致，调用方自行决定怎么报告）。
        """
        for event in self.read_all():
            if event.event_id == audit_id:
                return event
        return None


def _validated_filename(filename: str) -> str:
    """校验审计文件名是**单个普通文件名**（不含路径成分）。"""
    if (
        not filename
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or "\x00" in filename
    ):
        shown = sanitize_for_display(filename, limit=_NAME_LIMIT)
        msg = f"审计文件名非法：{shown!r}（必须是单个文件名，不得含路径分隔符）"
        raise ValueError(msg)
    return filename


def _event_to_payload(event: AuditEvent) -> dict[str, object]:
    """把事件转成一行 JSON 的载荷（``detail`` 已脱敏、时间戳已校验）。"""
    if not _is_aware_iso8601(event.timestamp):
        msg = "审计事件的时间戳必须是带时区的 ISO-8601 文本（如 2026-09-19T08:06:08+00:00）"
        raise ValueError(msg)
    return {
        "event_id": event.event_id,
        "kind": event.kind.value,
        "timestamp": event.timestamp,
        "session_id": event.session_id,
        "outcome": event.outcome.value,
        "call_id": event.call_id,
        "tool_name": event.tool_name,
        "capability": None if event.capability is None else event.capability.value,
        "risk_level": None if event.risk_level is None else event.risk_level.value,
        "detail": redact_sensitive(dict(event.detail)),
    }


def _is_aware_iso8601(value: str) -> bool:
    """是否带时区的 ISO-8601 文本（naive 时间必须被拒绝，理由见 ``contracts/audit.py``）。"""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _schema_error(number: int, reason: str) -> SchemaError:
    """构造统一的读取侧校验错误（报行号与原因；不把整行内容回显进错误信息）。"""
    return SchemaError(f"审计文件第 {number} 行不是合法事件：{reason}")


def _parse_line(line: str, *, number: int) -> AuditEvent:
    """把一行 JSONL 还原成 :class:`AuditEvent`，逐字段校验类型与枚举取值。"""
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as exc:
        raise _schema_error(number, "不是合法 JSON") from exc

    if not isinstance(raw, dict):
        raise _schema_error(number, "不是 JSON 对象")
    fields = cast("Mapping[str, object]", raw)

    event_id = _required_str(fields, "event_id", number)
    if not event_id:
        raise _schema_error(number, "event_id 为空（它是回放查询的关联键）")

    try:
        kind = AuditEventKind(_required_str(fields, "kind", number))
        outcome = AuditOutcome(_required_str(fields, "outcome", number))
        capability = _optional_capability(fields, number)
        risk_level = _optional_risk_level(fields, number)
    except ValueError as exc:
        raise _schema_error(number, "枚举字段取值非法") from exc

    timestamp = _required_str(fields, "timestamp", number)
    if not _is_aware_iso8601(timestamp):
        raise _schema_error(number, "timestamp 不是带时区的 ISO-8601 文本")

    return AuditEvent(
        event_id=event_id,
        kind=kind,
        timestamp=timestamp,
        session_id=_required_str(fields, "session_id", number),
        outcome=outcome,
        call_id=_optional_str(fields, "call_id", number),
        tool_name=_optional_str(fields, "tool_name", number),
        capability=capability,
        risk_level=risk_level,
        detail=_detail(fields, number),
    )


def _required_str(fields: Mapping[str, object], key: str, number: int) -> str:
    value = fields.get(key)
    if not isinstance(value, str):
        raise _schema_error(number, f"字段 {key} 缺失或不是字符串")
    return value


def _optional_str(fields: Mapping[str, object], key: str, number: int) -> str | None:
    value = fields.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise _schema_error(number, f"字段 {key} 不是字符串")
    return value


def _optional_capability(fields: Mapping[str, object], number: int) -> Capability | None:
    value = _optional_str(fields, "capability", number)
    return None if value is None else Capability(value)


def _optional_risk_level(fields: Mapping[str, object], number: int) -> RiskLevel | None:
    value = _optional_str(fields, "risk_level", number)
    return None if value is None else RiskLevel(value)


def _detail(fields: Mapping[str, object], number: int) -> Mapping[str, object]:
    value = fields.get("detail")
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise _schema_error(number, "字段 detail 不是 JSON 对象")
    return cast("Mapping[str, object]", value)
