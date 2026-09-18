"""沙箱契约（横切 SEC 层）。

ADR-0015 §5.4.1 只给出类型名 ``SandboxRequest`` / ``SandboxResult`` / ``IsolationMatrix``，
字段均未规定 ⇒ 全部以占位形式落定，实现者不得据此假设任何字段。
逐维度隔离探针的机制类别见 ADR-0006 / ADR-0007。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SandboxRequest:
    """一次沙箱执行的请求。**字段待澄清**（ADR-0015 未规定）。"""


@dataclass(frozen=True)
class SandboxResult:
    """一次沙箱执行的结果。**字段待澄清**（ADR-0015 未规定）。"""


@dataclass(frozen=True)
class IsolationMatrix:
    """逐维度隔离探针的结果矩阵（依据 ADR-0007）。**字段待澄清**。"""
