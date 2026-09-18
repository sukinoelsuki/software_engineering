"""审计契约（横切 OBS 层）。

字段级定义见 ``docs/design/interfaces/audit.md``（本模块是它的唯一实现）；
``AuditSink`` 的实现见 ``observability/audit.py``。
错误语义：``emit`` / ``flush`` 的异常**必须冒泡**——静默丢事件等于 ``REQ-SEC-06`` 验收失败。
本模块只依赖标准库与 ``contracts`` 内部模块（ADR-0015 §7.1 R3）。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from agent_sec_perf.contracts.policy import Capability, RiskLevel


class AuditEventKind(StrEnum):
    """事件类别；每一个成员都对应一条明确需求（``REQ-SEC-06/08``、``REQ-UX-02``、``REQ-OBS-01``）。"""

    POLICY_DECISION = "policy_decision"
    APPROVAL = "approval"
    TOOL_CALL = "tool_call"
    EXECUTION_DEGRADATION = "execution_degradation"
    REFUSAL = "refusal"


class AuditOutcome(StrEnum):
    """事件结果；``kind`` 与允许的 ``outcome`` 的约束见 ``docs/design/interfaces/audit.md`` §2.2。

    用枚举报结果而不是裸 ``str``：``REQ-OBS-01`` 要"按结果检索"，裸字符串会因拼写差异静默漏项。
    """

    ALLOW = "allow"
    DENY = "deny"
    CONFIRM = "confirm"
    OK = "ok"
    ERROR = "error"
    DEGRADED = "degraded"


@dataclass(frozen=True)
class AuditEvent:
    """一条可回放的审计事件（只追加、不可变——``frozen=True`` 是契约级保证）。

    ``timestamp`` 为**带时区的 ISO-8601 UTC 文本**（存储形态，避免序列化时区不一致）；
    ``detail`` **必须已脱敏**（``REQ-SEC-07``，具体规则属观测层设计）。

    不变式（逐条定义与裁决见 ``docs/design/interfaces/audit.md`` §2.3）：
    **I1**（关联键）``kind == POLICY_DECISION`` ⇒ ``call_id`` / ``risk_level`` 均非 ``None``；
    **I2**（能力集合可回放）``kind == POLICY_DECISION`` ⇒ ``detail["requested"]`` **存在**，
    为 ``Capability`` 值组成的**升序** ``list[str]``（请求不可解析时记 ``[]``，此时 ``detail["error"]``
    存在）；**I3**（单值字段与集合字段一致）``capability is None`` **⇔** ``detail["requested"] == []``，
    且 ``capability`` 非 ``None`` 时其值必在该列表内；
    ``kind == TOOL_CALL`` ⇒ ``call_id`` / ``tool_name`` 非 ``None``。
    """

    event_id: str
    kind: AuditEventKind
    timestamp: str
    session_id: str
    outcome: AuditOutcome
    call_id: str | None = None
    tool_name: str | None = None
    capability: Capability | None = None
    risk_level: RiskLevel | None = None
    detail: Mapping[str, object] = field(default_factory=dict)


class AuditSink(Protocol):
    """审计落点。

    并发：无状态；实现内部串行化写入（可被多线程调用）。
    资源生命周期：进程退出前**必须** :meth:`flush`；``flush`` 应可重复调用（退出路径可能多次触发）。
    """

    def emit(self, event: AuditEvent) -> None: ...

    def flush(self) -> None: ...
