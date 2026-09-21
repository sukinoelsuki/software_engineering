#!/usr/bin/env python3
"""Mermaid 图表结构自检（仅标准库，不安装任何依赖）。

为什么需要它
------------
本项目用 Mermaid 画图（纯文本、可 diff、可评审），但**图不会被测试运行**——
于是"图写错了却没人发现"是真实风险：括号少一个、`subgraph` 没配 `end`、
引号不成对，渲染时静默失败或画出错误结构。

它做的是**结构自检**，不是渲染：渲染需要 Mermaid CLI（Node 生态），
本项目不为制图新增依赖。因此本工具只回答"这张图**在结构上**像不像合法的 Mermaid"。

检查项
------
error（阻断）：
  * 图类型不在白名单 / 空代码块
  * 括号 `[]` `()` `{}` 不配对（忽略双引号内的内容）
  * `flowchart`/`graph` 的 `subgraph` 与 `end` 数量不符
  * `sequenceDiagram` 的分组关键字（alt/opt/loop/par/critical/rect/box）与 `end` 数量不符
  * `erDiagram` 的关系行写法不合法（常见错误：用了 `-->`）
  * 占位符残留（TODO / TBD / XXX / 待填 / 占位 …）
  * `quadrantChart` 点位坐标不在 0~1 之间
  * `quadrantChart` 的**坐标轴标签含非 ASCII**（2026-09-21 实测：词法器在第一个汉字处报
    `Lexical error ... Unrecognized text`，**整张图渲染失败**且浏览器控制台会刷错误）

warning（可用 `--strict` 升级为阻断）：
  * 单行引号不成对
  * 边标签的竖线 `|` 个数为奇数
  * 流程图节点标签含未加引号的特殊字符（全角括号 / 逗号）
  * 制表符（Mermaid 对缩进敏感，制表符常导致渲染异常）
  * `journey` 任务行不是"任务: 分数: 角色"三段
  * 使用了保留字作为节点 id（end / subgraph / graph / class / style）
  * `quadrantChart` 含**任何**非 ASCII 字符（只有"坐标轴标签"一处有实测证据，
    象限名与数据点的支持情况未经证实 ⇒ 只提醒，不拦）

图型对中文的兼容性（本项目实测口径）
------------------------------------
* `flowchart` / `stateDiagram` / `sequenceDiagram` / `erDiagram` / `journey` / `gantt` / `pie`
  —— 现有文档（含中文标签）渲染正常，**无逆向证据**；
* `quadrantChart` —— **坐标轴标签必须为 ASCII**（有实测的失败复现）。
  ⚠️ "没报错"只是**弱证据**，不等于"已验证支持"；新图型首次使用时应在预览中确认。

用法
----
    python3 mermaid_check.py .                  # 检查目录下所有 markdown
    python3 mermaid_check.py --list .           # 只列出图清单
    python3 mermaid_check.py --strict docs/     # 把警告也算失败
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

#: 支持的图类型（小写比较；`graph` 是 `flowchart` 的旧写法）
DIAGRAM_TYPES: frozenset[str] = frozenset(
    {
        "flowchart",
        "graph",
        "sequencediagram",
        "statediagram",
        "statediagram-v2",
        "erdiagram",
        "journey",
        "gantt",
        "pie",
        "quadrantchart",
        "classdiagram",
        "gitgraph",
        "mindmap",
        "timeline",
        "requirementdiagram",
        "block-beta",
        "sankey-beta",
    }
)

#: 跳过这些目录（第三方、缓存、构建产物）
SKIP_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".venv",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".bench-data",
        "dist",
        "build",
    }
)

#: 出现即视为"图还没写完"
PLACEHOLDERS: tuple[str, ...] = ("TODO", "TBD", "FIXME", "XXX", "???", "待填", "待补", "占位")

#: quadrantChart 对非 ASCII 的实测结论（2026-09-21，来自浏览器控制台的原始报错）：
#: 坐标轴标签处即报 `Lexical error ... Unrecognized text`，且**整张图渲染失败**。
#: 其余位置（象限名、数据点）是否支持中文**未经证实** ⇒ 只提醒，不拦。
QUADRANT_CJK_WARNING = (
    "quadrantChart 含非 ASCII 字符：目前只有（坐标轴标签不接受中文）这一条有实测证据，"
    "其余位置（象限名、数据点）的支持情况未经证实"
    " ⇒ 建议该图型整体使用 ASCII，或改用嵌套子图的 2x2 矩阵"
)

#: 这些 id 在流程图里有特殊含义
RESERVED_NODE_IDS: frozenset[str] = frozenset({"end", "subgraph", "graph", "class", "style"})

#: sequenceDiagram 的分组关键字（每个都需要一个 end）
SEQ_GROUP_KEYWORDS: frozenset[str] = frozenset(
    {"alt", "opt", "loop", "par", "critical", "rect", "box", "break"}
)

ER_RELATION = re.compile(r"^[A-Za-z_]\w*\s+[|}o][|{o]?--[|{o][|{o]?\s+[A-Za-z_]\w*\s*:\s*\S")
QUADRANT_POINT = re.compile(r"^([^:\[\]]+):\s*\[\s*([0-9.]+)\s*,\s*([0-9.]+)\s*\]\s*$")
UNQUOTED_SPECIAL = re.compile(r"\w+\[[^\"]*[（）,，、][^\"]*\]")


@dataclass
class Block:
    """一个 ```mermaid 代码块。"""

    path: Path
    start: int
    kind: str
    lines: list[tuple[int, str]]


@dataclass
class Issue:
    """一条检查结果。"""

    level: str
    path: Path
    line: int
    message: str


def _write(text: str = "") -> None:
    """输出一行（不用 print：仓库 lint 禁止遗留调试打印）。"""
    sys.stdout.write(text + "\n")


def iter_markdown(root: Path) -> list[Path]:
    """收集待检查的 markdown 文件。"""
    if root.is_file():
        return [root]
    found: list[Path] = []
    for path in sorted(root.rglob("*.md")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        found.append(path)
    return found


def strip_quoted(text: str) -> str:
    """去掉双引号内的内容，用于括号配对检查。"""
    out: list[str] = []
    inside = False
    for char in text:
        if char == '"':
            inside = not inside
            continue
        if not inside:
            out.append(char)
    return "".join(out)


def extract_blocks(path: Path) -> list[Block]:
    """从 markdown 中抽出所有 mermaid 代码块。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    blocks: list[Block] = []
    body: list[tuple[int, str]] = []
    start = 0
    in_block = False
    for index, text in enumerate(raw.splitlines(), start=1):
        stripped = text.strip()
        if not in_block:
            if stripped.lower().startswith("```mermaid"):
                in_block = True
                start = index
                body = []
            continue
        if stripped.startswith("```"):
            in_block = False
            blocks.append(Block(path=path, start=start, kind="", lines=body))
            continue
        body.append((index, text))
    if in_block:
        blocks.append(Block(path=path, start=start, kind="", lines=body))
    return blocks


def detect_kind(block: Block) -> str:
    """取第一行有效内容作为图类型。"""
    for _, text in block.lines:
        stripped = text.strip()
        if not stripped or stripped.startswith("%%"):
            continue
        return stripped.split()[0].lower()
    return ""


def block_is_ascii(block: Block) -> bool:
    """判断图块是否全部为 ASCII（用于 quadrantChart 的中文兼容性提醒）。"""
    return all(ord(char) < 128 for _, text in block.lines for char in text)


def check_block(block: Block) -> list[Issue]:
    """检查单个图块，返回问题列表。"""
    issues: list[Issue] = []
    path = block.path

    def report(level: str, line: int, message: str) -> None:
        issues.append(Issue(level=level, path=path, line=line, message=message))

    if not block.lines:
        report("error", block.start, "空的 mermaid 代码块")
        return issues

    kind = detect_kind(block)
    if not kind:
        report("error", block.start, "代码块内没有任何图类型声明")
        return issues
    if kind not in DIAGRAM_TYPES:
        report("error", block.start, f"未知图类型 {kind!r}（请确认拼写，或在白名单中登记）")

    brackets = {"[": "]", "(": ")", "{": "}"}
    closers = {value: key for key, value in brackets.items()}
    depth: dict[str, int] = dict.fromkeys(brackets, 0)
    seq_open = 0
    seq_end = 0
    subgraph_count = 0
    end_count = 0

    for line_no, text in block.lines:
        raw = text.rstrip("\n")
        stripped = raw.strip()
        if stripped.startswith("%%"):
            continue

        if "\t" in raw:
            report("warning", line_no, "含制表符，Mermaid 缩进敏感，建议改为空格")

        upper = stripped.upper()
        for token in PLACEHOLDERS:
            if token.upper() in upper:
                report("error", line_no, f"疑似占位符残留：{token}")

        if raw.count('"') % 2 == 1:
            report("warning", line_no, "双引号不成对")

        # erDiagram 的势标记本身就含 `|` 与 `{`（如 `||--o{`），
        # 不能当作边标签竖线或括号来处理，否则每条关系行都会误报。
        is_er_relation = kind == "erdiagram" and "--" in stripped

        if not is_er_relation and stripped.count("|") % 2 == 1:
            report("warning", line_no, "竖线个数为奇数，可能是边标签未闭合")

        bare = strip_quoted(raw)
        if not is_er_relation:
            for token in brackets:
                depth[token] += bare.count(token)
            for closer, token in closers.items():
                depth[token] -= bare.count(closer)

        head = stripped.split()[0].lower() if stripped.split() else ""
        if (
            head in RESERVED_NODE_IDS
            and not stripped.endswith(":")
            and re.match(rf"^{head}\s*[\[\(]", stripped)
        ):
            report("warning", line_no, f"保留字 {head!r} 被用作节点 id")

        if kind in {"graph", "flowchart"}:
            if head == "subgraph":
                subgraph_count += 1
            elif stripped == "end":
                end_count += 1
            if UNQUOTED_SPECIAL.search(bare):
                report("warning", line_no, "节点标签含特殊字符（全角括号/逗号），建议用引号包起来")

        if kind == "sequencediagram" and head in SEQ_GROUP_KEYWORDS:
            seq_open += 1
        if kind == "sequencediagram" and stripped == "end":
            seq_end += 1

        if kind == "erdiagram" and "--" in stripped and not ER_RELATION.match(stripped):
            report("error", line_no, "erDiagram 关系行写法不合法（应形如 A ||--o{ B : 说明）")

        if kind == "quadrantchart" and "[" in stripped:
            match = QUADRANT_POINT.match(stripped)
            if match:
                for value in (match.group(2), match.group(3)):
                    number = float(value)
                    if not 0.0 <= number <= 1.0:
                        report("error", line_no, f"象限图坐标应落在 0~1，当前为 {value}")

        if kind == "quadrantchart" and head in {"x-axis", "y-axis"}:
            parts = stripped.split(None, 1)
            if len(parts) > 1 and any(ord(char) > 127 for char in parts[1]):
                report(
                    "error",
                    line_no,
                    "quadrantChart 的坐标轴标签不接受非 ASCII 字符"
                    "（词法器在第一个汉字处即报 Lexical error，图会整块渲染失败）"
                    "⇒ 改用英文标签，或改用嵌套子图的 2x2 矩阵",
                )

        if (
            kind == "journey"
            and ":" in stripped
            and head not in {"title", "section"}
            and len(stripped.split(":")) != 3
        ):
            report("warning", line_no, "journey 任务行应为 任务: 分数: 角色 三段")

    for token, closer in brackets.items():
        if depth[token] != 0:
            report(
                "error",
                block.start,
                f"括号不配对：{token} 与 {closer} 相差 {abs(depth[token])} 个",
            )

    if kind == "quadrantchart" and not block_is_ascii(block):
        report("warning", block.start, QUADRANT_CJK_WARNING)

    if kind in {"graph", "flowchart"} and subgraph_count != end_count:
        report(
            "error",
            block.start,
            f"subgraph 与 end 数量不符：{subgraph_count} 个 subgraph / {end_count} 个 end",
        )
    if kind == "sequencediagram" and seq_open != seq_end:
        report(
            "error",
            block.start,
            f"分组关键字与 end 数量不符：{seq_open} 个分组 / {seq_end} 个 end",
        )

    return issues


def check_file(path: Path) -> tuple[list[Issue], int]:
    """检查一个文件，返回（问题列表，图数量）。"""
    issues: list[Issue] = []
    count = 0
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:  # pragma: no cover - 环境相关
        return [Issue("error", path, 0, f"读取失败：{exc}")], 0
    if raw and not raw.endswith("\n"):
        issues.append(Issue("warning", path, len(raw.splitlines()), "文件末尾缺少换行"))
    for block in extract_blocks(path):
        count += 1
        issues.extend(check_block(block))
    return issues, count


def parse_args(argv: list[str]) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="Mermaid 图表结构自检（仅标准库）")
    parser.add_argument("root", nargs="?", default=".", help="要检查的目录或文件")
    parser.add_argument("--list", action="store_true", help="只列出图清单，不做检查")
    parser.add_argument("--strict", action="store_true", help="把警告也视为失败")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """入口。返回 0 表示通过，1 表示存在问题。"""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    root = Path(args.root)
    if not root.exists():
        _write(f"路径不存在：{root}")
        return 2

    files = iter_markdown(root)
    if not files:
        _write(f"未找到 markdown 文件：{root}")
        return 0

    total_blocks = 0
    all_issues: list[Issue] = []
    listed = 0
    for path in files:
        _, count = check_file(path)
        total_blocks += count
        if args.list:
            for block in extract_blocks(path):
                listed += 1
                _write(f"{path}:{block.start}  {detect_kind(block) or '未声明'}")
            continue
        issues, _ = check_file(path)
        all_issues.extend(issues)

    if args.list:
        _write(f"共 {listed} 张图，来自 {len(files)} 个文件")
        return 0

    errors = [item for item in all_issues if item.level == "error"]
    warnings = [item for item in all_issues if item.level == "warning"]
    for item in all_issues:
        marker = "ERROR  " if item.level == "error" else "WARN   "
        _write(f"{item.path}:{item.line}: {marker}{item.message}")

    _write(
        f"检查完成：{len(files)} 个文件 / {total_blocks} 张图 / "
        f"{len(errors)} 项错误 / {len(warnings)} 项警告"
    )
    if errors or (args.strict and warnings):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
