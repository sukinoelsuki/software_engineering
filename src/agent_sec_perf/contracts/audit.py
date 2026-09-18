"""审计契约（横切 OBS 层）。

``AuditSink.emit(AuditEvent) -> None`` 取自 ADR-0015 §5.1.2 的接口形态表；
``flush()`` 对应同表的资源生命周期要求（进程退出前必须 flush）。

错误语义：审计写入失败**必须**冒泡——"可回放"是 ``REQ-SEC-06`` 的验收标准，
静默丢事件等于验收失败。``AuditEvent`` 字段未规定 ⇒ 占位。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AuditEvent:
    """一条可回放的审计事件。**字段待澄清**（ADR-0015 未规定）。"""


class AuditSink(Protocol):
    """审计落点。

    并发假设：无状态；实现内部串行化写入。
    资源生命周期：进程退出前必须 :meth:`flush`。
    """

    def emit(self, event: AuditEvent) -> None: ...

    def flush(self) -> None: ...
