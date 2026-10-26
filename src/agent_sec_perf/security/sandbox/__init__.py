"""沙箱后端与逐维度隔离探针（横切 SEC 层）。

契约：``docs/design/interfaces/sandbox.md``；类型：``contracts/sandbox.py``。

本包当前只实现**能力探测**（:mod:`agent_sec_perf.security.sandbox.probe`）。
真正的隔离执行后端（``bwrap`` / ``firejail`` / 容器）尚未接入工具执行路径——
``tools/shell.py`` 仍走 ``foundation.proc.run(isolation="user")`` 的 L1 路径。

为什么先做探针：``enforced=True`` **只能**来自"负向探针确实被阻止"，
而"某个沙箱工具装上了"不等于"它拦住了什么"。本包的存在就是为了让
"到底隔离了什么"成为一个可复现的结论，而不是一句形容词。
"""

from __future__ import annotations

from agent_sec_perf.security.sandbox.probe import (
    IsolationReport,
    emit_degradation_audit,
    probe_isolation,
)

__all__ = ["IsolationReport", "emit_degradation_audit", "probe_isolation"]
