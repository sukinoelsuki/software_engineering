"""原生智能系统的安全与加速工程实践。

本包的**模块划分、子系统边界与依赖方向已定案**，权威源是
``docs/adr/0015-layering-and-reuse-boundary.md`` §5.1（分层模型）与 §5.4.1（目录结构）；
依赖方向 ``R1``~``R5`` 由 ``tests/unit/test_architecture_layers.py`` 机器检查。

本 docstring **不枚举模块清单**（目录会增删，复制即漂移），也**不给实现状态或安全结论**：
逐模块的"规划 vs 已实现"见 ``docs/design/architecture.md`` §4，
安全结论只以 ``docs/design/threat-model/`` 为准——**已实现不等于已验证**。
"""

from __future__ import annotations

__all__ = ["__version__"]

#: 版本号**不由提交历史推导**，只能取 pyproject.toml 的 ``[tool.lowspec.releases]``
#: 台账里列出的里程碑值；本值是该台账的 4 处副本之一，一致性由
#: ``tests/unit/test_release_policy.py`` 机器检查。本注释**不复制具体版本号**——复制即漂移
#: （本项目已多次因此吃过教训）：当前值见下一行 ``__version__``，可用里程碑与其判据见上述台账。
__version__ = "0.1.0"
