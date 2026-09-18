"""L2 能力层：工具实现（TOOL）。

内置工具（文件 / 命令 / 检索）与工具注册表；工具是信任边界的**执行侧**。
已实现：``registry``（``ToolRegistry`` 与工具层共用辅助）、``files``
（``ReadFileTool`` / ``WriteFileTool`` / ``ListDirTool``）、``shell``（``ShellCommandTool``）。
规划模块（ADR-0015 §5.4.1，**尚未实现**）：``search``。

依赖约束（ADR-0015 §7.1 R1/R4）：只向下依赖 ``foundation``；命令执行**唯一**经
``foundation.proc``，路径校验**唯一**经 ``foundation.paths``，不得另开第二条路子。
"""

from __future__ import annotations

from agent_sec_perf.tools.files import ListDirTool, ReadFileTool, WriteFileTool
from agent_sec_perf.tools.registry import ToolRegistry
from agent_sec_perf.tools.shell import ShellCommandTool

__all__ = [
    "ListDirTool",
    "ReadFileTool",
    "ShellCommandTool",
    "ToolRegistry",
    "WriteFileTool",
]
