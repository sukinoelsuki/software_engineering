"""能力包内核：把"给端侧模型构造能力集"的过程做成可运行、可校验的一步。

对外只暴露四类东西，**不暴露实现细节**：

* :mod:`~agent_sec_perf.pack.case` —— 案例契约（``case.toml``）的解析与校验；
* :mod:`~agent_sec_perf.pack.criteria` —— 内容判据与**判据自证**；
* :mod:`~agent_sec_perf.pack.bundle` —— bundle 的构建、摘要与完整性校验；
* :mod:`~agent_sec_perf.pack.state` / :mod:`~agent_sec_perf.pack.plan` —— 状态机与修复动作目录。

层级定位见 :doc:`ADR-0034`：本包是**产品层**（与 ``cli/`` 同层、位于 ``harness/`` 之上），
只依赖 ``contracts`` / ``foundation``；它不 import ``cli``，执行通过调用方注入的 runner 完成。
"""

from __future__ import annotations
