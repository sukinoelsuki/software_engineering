"""原生智能系统的安全与加速工程实践。

本包当前仅提供工程基线 Phase 0。模块划分、子系统边界与对外接口
将在 base project 选型冻结后确定。参见 ``docs/proposals/0001-base-project-selection.md``。
"""

from __future__ import annotations

__all__ = ["__version__"]

#: 版本号**不由提交历史推导**，只能取 pyproject.toml 的 ``[tool.lowspec.releases]``
#: 台账里列出的里程碑值；本值是该台账的 4 处副本之一，一致性由
#: ``tests/unit/test_release_policy.py`` 机器检查。当前 ``0.0.0`` = 尚未到达第一个里程碑。
__version__ = "0.0.1"
