#!/usr/bin/env python3
"""把 markdown 里的 Mermaid 图块抽成**一个单体 HTML**，用于在不支持 Mermaid 的预览里看图。

为什么需要它
------------
编辑器的 markdown 预览能不能渲染 Mermaid，取决于**客户端**（IDE 发行版、浏览器、
webview 的 CSP 与资源路径），而**不是**取决于仓库或镜像。当预览不渲染时，
`mermaid_check.py` 只能告诉你"图结构没问题"，不能让你把图**看见**。

本脚本提供一条**不依赖 IDE** 的看图路径：
把仓库里所有 Mermaid 块抽出来，生成一个 HTML；在浏览器里打开即可。
mermaid 库由页面从公开 CDN 加载（因此**需要浏览器能访问外网**；
这正是"本机能正常看图"的那台机器通常具备的条件）。

边界（重要）
------------
* 它是**查看工具**，不是构建或验收环节的一部分；**产物不要入库**
  （默认写到系统临时目录）。
* 它**不替代**渲染平台、**不改**任何仓库文件。
* 它从 CDN 取库 ⇒ **离线环境不可用**；本脚本不解决离线预览。
* 它不是"第二份真源"：只读仓库，不生成任何被仓库引用的产物。

用法
----
    python3 mermaid_view.py 需求分析文档                 # 抽取该目录下所有图
    python3 mermaid_view.py docs/ --out /tmp/figs.html   # 指定输出位置
    python3 mermaid_view.py --list docs/                 # 只看清单，不生成 HTML
"""

from __future__ import annotations

import argparse
import html
import sys
import tempfile
from pathlib import Path

#: 与 mermaid_check.py 保持一致：跳过第三方与缓存目录
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

#: 页面模板（占位符用 py 侧替换，避免与 CSS 的花括号冲突）
PAGE_START = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Mermaid 图集预览</title>
<style>
  body { background: #1e1e1e; color: #d4d4d4;
         font-family: "Sarasa Mono SC", "Noto Sans Mono CJK SC", monospace;
         margin: 0 auto; max-width: 1100px; padding: 24px; line-height: 1.6; }
  h1 { font-size: 20px; border-bottom: 1px solid #444; padding-bottom: 8px; }
  h2 { font-size: 15px; color: #9cdcfe; margin-top: 32px; }
  .meta { color: #808080; font-size: 12px; }
  .diagram { background: #252526; border: 1px solid #333; border-radius: 6px;
             padding: 12px; margin: 8px 0 24px; overflow-x: auto; }
  .empty { color: #f48771; }
</style>
</head>
<body>
<h1>Mermaid 图集预览</h1>
<p class="meta" id="summary"></p>
"""

PAGE_END = """
<script type="module">
  import mermaid from 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs';
  mermaid.initialize({ startOnLoad: true, theme: 'dark', securityLevel: 'loose' });
</script>
</body>
</html>
"""


def _write(text: str = "") -> None:
    """输出一行（不用 print：仓库 lint 禁止遗留调试打印）。"""
    sys.stdout.write(text + "\n")


def iter_markdown(root: Path) -> list[Path]:
    """收集待抽取的 markdown 文件。"""
    if root.is_file():
        return [root]
    found: list[Path] = []
    for path in sorted(root.rglob("*.md")):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        found.append(path)
    return found


def extract_blocks(path: Path) -> list[tuple[int, str]]:
    """抽取一个文件里的 mermaid 块：返回 (起始行号, 图正文)。"""
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    blocks: list[tuple[int, str]] = []
    body: list[str] = []
    start = 0
    inside = False
    for index, line in enumerate(raw.splitlines(), start=1):
        stripped = line.strip()
        if not inside:
            if stripped.lower().startswith("```mermaid"):
                inside = True
                start = index
                body = []
            continue
        if stripped.startswith("```"):
            inside = False
            blocks.append((start, "\n".join(body)))
            continue
        body.append(line)
    if inside:
        blocks.append((start, "\n".join(body)))
    return blocks


def build_html(root: Path, files: list[Path]) -> tuple[str, int]:
    """生成 HTML 文本与图数量。"""
    parts: list[str] = [PAGE_START]
    total = 0
    for path in files:
        blocks = extract_blocks(path)
        if not blocks:
            continue
        try:
            label = str(path.relative_to(root))
        except ValueError:
            label = str(path)
        parts.append(f"<h2>{html.escape(label)}</h2>")
        parts.append(
            '<p class="meta">本页图来自该文件的 mermaid 代码块；'
            "若显示为空白，说明浏览器未能加载 mermaid 库（需可访问外网）。</p>"
        )
        for line_no, body in blocks:
            total += 1
            escaped = html.escape(body.strip())
            parts.append(f'<p class="meta">{label}:{line_no}</p>')
            parts.append(f'<div class="diagram mermaid">{escaped}</div>')
    parts.append(PAGE_END)
    return "".join(parts), total


def parse_args(argv: list[str]) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="把 markdown 里的 Mermaid 图抽成单体 HTML")
    parser.add_argument("root", nargs="?", default=".", help="要抽取的目录或文件")
    parser.add_argument("--out", default="", help="输出 HTML 路径（默认写到系统临时目录）")
    parser.add_argument("--list", action="store_true", help="只列出图清单，不生成 HTML")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """入口。返回 0 表示成功。"""
    args = parse_args(sys.argv[1:] if argv is None else argv)
    root = Path(args.root)
    if not root.exists():
        _write(f"路径不存在：{root}")
        return 2

    files = iter_markdown(root)
    if args.list:
        count = 0
        for path in files:
            for line_no, _ in extract_blocks(path):
                count += 1
                _write(f"{path}:{line_no}")
        _write(f"共 {count} 张图")
        return 0

    text, total = build_html(root, files)
    out = Path(args.out) if args.out else Path(tempfile.gettempdir()) / "mermaid-preview.html"
    out.write_text(text, encoding="utf-8")
    _write(f"已生成：{out}")
    _write(f"共 {total} 张图；用浏览器打开该文件即可（需能访问外网以加载 mermaid 库）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
