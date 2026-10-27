"""CLI 退出码（唯一真源）。

从 ``cli/app.py`` 拆出：``app`` 需要注册 ``pack`` 子命令，而子命令模块又需要退出码，
放在 ``app`` 里会形成导入环。语义表仍在 ``cli/app.py`` 的模块 docstring（契约 §5.2），
本模块只承载常量，**不承载判定逻辑**。
"""

from __future__ import annotations

from typing import Final

__all__ = [
    "EXIT_ASSEMBLY",
    "EXIT_AUDIT",
    "EXIT_LIMIT_REACHED",
    "EXIT_OK",
    "EXIT_TASK_FAILED",
    "EXIT_UNEXPECTED",
    "OUTPUT_JSON",
    "OUTPUT_TEXT",
]

EXIT_OK: Final = 0
EXIT_TASK_FAILED: Final = 1
EXIT_LIMIT_REACHED: Final = 2
EXIT_ASSEMBLY: Final = 3
EXIT_AUDIT: Final = 4
EXIT_UNEXPECTED: Final = 5

OUTPUT_TEXT: Final = "text"
OUTPUT_JSON: Final = "json"
