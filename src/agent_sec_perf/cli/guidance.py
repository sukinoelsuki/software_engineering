"""CLI 的引导与手册文本（``REQ-UX-01/04``；表现层叶子）。

**这里只放"我方生成的固定文案"**：不含任何模型输出、工具输出或配置值，
因此可以安全交给 Rich 渲染。凡是要插入不可信文本的地方，调用方必须自己先净化
（见 ``cli/render.py`` 的净化口径）——本模块不提供"代入变量"的入口。

为什么把文案单独成模块：``app.py`` 已经承载装配与退出码，再塞进三屏引导会让
"改了文案却看不懂装配"与"改了装配却漏改文案"同时发生。文案是产品面，装配是契约面。
"""

from __future__ import annotations

from typing import Final

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

__all__ = ["print_manual", "print_welcome"]

_WELCOME_TITLE: Final = "lowspec · 端侧 agent 安全与性能基线"

_WELCOME_BODY: Final = (
    "一个在受限资源下运行的本地 agent：模型自己决定调用哪些工具，\n"
    "每一次权限判定与工具执行都留下可回放的审计。\n\n"
    "第一次使用，建议按这个顺序走：\n"
    "  1. agent-sec-perf doctor    体检：模型、推理后端、隔离能力、审计落点\n"
    "  2. agent-sec-perf demo      演示：准备一个隔离工作区（不调用模型）\n"
    "  3. agent-sec-perf demo --run  真跑：端侧模型完成一次可机检的任务\n"
    "  4. agent-sec-perf manual    手册：全部命令、退出码与文档索引\n\n"
    '自己写任务：agent-sec-perf run "<任务>" --model-path <GGUF> --pack <领域包>'
)

_MANUAL_SECTIONS: Final[tuple[tuple[str, str], ...]] = (
    ("首次上手", "doctor → demo → demo --run → manual"),
    ("跑一次自己的任务", 'run "<任务>" --model-path <GGUF> --pack <领域包> --allowed-root <目录>'),
    ("仅体检环境", "doctor [--json]"),
    ("演示案例", "demo [--run] [--keep] [--model <GGUF>] [--workspace <目录>]"),
    ("看手册", "manual"),
)

_MANUAL_NOTES: Final[tuple[str, ...]] = (
    "能力默认拒绝：不写 .lowspec.toml 的 granted_capabilities，工具调用一律被拒（这是设计，不是故障）。",
    "领域包只能收窄、不能扩权：包里的 capabilities 与你授予的集合取交集。",
    "退出码：0 完成 / 1 任务失败 / 2 步数用尽 / 3 装配或配置失败 / 4 审计写入失败 / 5 未预期异常。",
    "完整手册：docs/engineering/product-handbook.md；演示脚本：docs/engineering/demo-showcase.md。",
    "案例设计：docs/design/showcase-incident-triage.md。",
)


def print_welcome(console: Console) -> None:
    """打印首屏引导（无子命令时调用；不读盘、不读环境）。"""
    console.print(Panel(Text(_WELCOME_BODY), title=_WELCOME_TITLE, border_style="cyan"))


def print_manual(console: Console) -> None:
    """打印命令速查与关键约定（比 ``--help`` 多一层"为什么"。"""
    table = Table(title="命令速查", show_header=True, header_style="bold cyan")
    table.add_column("场景", style="bold")
    table.add_column("命令")
    for label, command in _MANUAL_SECTIONS:
        table.add_row(Text(label), Text(command))
    console.print(table)

    console.print()
    console.print(Text("必须知道的约定", style="bold cyan"))
    for note in _MANUAL_NOTES:
        console.print(Text(f"· {note}"))
