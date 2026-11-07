#!/usr/bin/env python3
"""汇总电子材料并打包（源码部分不含 .git / 虚拟环境 / 缓存）。

用法：uv run python docs/course-report/make_deliverables.py
输出：docs/course-report/软件工程课程报告-电子材料.zip
"""

from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
STAGE = HERE / "电子材料"
ZIP = HERE / "软件工程课程报告-电子材料.zip"
DOCX = HERE / "软件工程课程报告-能力受限的端侧智能体.docx"
PDF = HERE / "软件工程课程报告-能力受限的端侧智能体.pdf"

SRC = ["src/agent_sec_perf/__init__.py", "src/agent_sec_perf/py.typed"]
PACKAGES = {
    "contracts": ["policy", "tools", "audit", "harness", "model", "sandbox"],
    "foundation": ["errors", "paths", "proc", "config", "logging"],
    "security": ["capabilities", "policy", "__init__"],
    "observability": ["audit", "__init__"],
    "model": ["client", "__init__"],
    "tools": ["registry", "files", "shell", "__init__"],
    "harness": [
        "session",
        "loop",
        "arguments",
        "trimming",
        "domain_pack",
        "checkpoint",
        "prompts",
        "__init__",
    ],
    "pack": ["case", "criteria", "bundle", "state", "plan", "review", "example", "__init__"],
    "cli": ["app", "demo", "doctor", "render", "approval", "__init__"],
}


def _say(msg):
    sys.stdout.write(msg + "\n")


def copy_one(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.copy2(src, dst)


def prepare() -> None:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    STAGE.mkdir()
    (STAGE / "01-报告").mkdir()
    (STAGE / "02-关键源码").mkdir()
    (STAGE / "03-执行程序").mkdir()
    (STAGE / "04-说明").mkdir()
    for f in (DOCX, PDF):
        copy_one(f, STAGE / "01-报告" / f.name)
    for rel in SRC:
        copy_one(ROOT / rel, STAGE / "02-关键源码" / rel)
    for pkg, mods in PACKAGES.items():
        for mod in mods:
            rel = f"src/agent_sec_perf/{pkg}/{mod}.py"
            copy_one(ROOT / rel, STAGE / "02-关键源码" / rel)
    for f in sorted((ROOT / "tests/security").glob("*.py")):
        copy_one(f, STAGE / "02-关键源码" / f.relative_to(ROOT))
    for f in sorted((ROOT / "tests/unit").glob("test_architecture_layers.py")):
        copy_one(f, STAGE / "02-关键源码" / f.relative_to(ROOT))
    for f in sorted((ROOT / "dist").glob("*.whl")):
        copy_one(f, STAGE / "03-执行程序" / f.name)
    case = ROOT / "examples/showcase/incident-01"
    if case.exists():
        shutil.copytree(
            case,
            STAGE / "03-执行程序/示例案例/incident-01",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    for name in ("pyproject.toml", "uv.lock", "README.md"):
        copy_one(ROOT / name, STAGE / "04-说明" / name)
    (STAGE / "00-材料清单与说明.md").write_text(
        "# 《软件工程》课程报告 · 电子材料\n\n"
        "本压缩包按课程要求汇总报告、关键源码与执行程序。纸质报告按 `01-报告/` 中的 docx 双面打印。\n\n"
        "| 目录 | 内容 |\n| :--- | :--- |\n"
        "| `01-报告/` | docx（提交件）与 pdf（便于查阅） |\n"
        "| `02-关键源码/` | 报告引用的生产代码、安全对抗性用例、分层机器检查 |\n"
        "| `03-执行程序/` | wheel 安装包与演示案例现场文件 |\n"
        "| `04-说明/` | 依赖清单、项目说明、复现步骤 |\n\n"
        "源码部分**不含** `.git`、虚拟环境、缓存与构建中间产物；"
        "压缩包内所有路径均为相对路径，解压后可直接阅读。\n",
        encoding="utf-8",
    )
    (STAGE / "02-关键源码/README.md").write_text(
        "# 关键源码索引\n\n"
        "目录结构与仓库 `src/agent_sec_perf/` 一致，便于按报告中的路径检索。\n\n"
        "| 分组 | 路径 | 对应报告 |\n| :--- | :--- | :--- |\n"
        "| 契约 | `contracts/` | 3.2、3.3 |\n"
        "| 基础设施 | `foundation/` | 3.5 |\n"
        "| 安全 | `security/` | 3.4、附录 A |\n"
        "| 可观测 | `observability/` | 3.3、4.2 |\n"
        "| 能力 | `model/`、`tools/` | 3.1 |\n"
        "| 编排 | `harness/` | 3.1、3.2 |\n"
        "| 生产 | `pack/` | 3.3、3.4 |\n"
        "| 命令面 | `cli/` | 2.3 |\n"
        "| 测试 | `tests/security/`、`tests/unit/test_architecture_layers.py` | 4.3 |\n",
        encoding="utf-8",
    )
    (STAGE / "03-执行程序/安装说明.md").write_text(
        "# 安装与运行\n\n"
        "```bash\n"
        "python -m venv .venv && . .venv/bin/activate\n"
        "pip install agent_sec_perf-0.2.0-py3-none-any.whl\n"
        "agent-sec-perf doctor          # 环境体检，无需模型\n"
        "agent-sec-perf --help          # 命令入口\n"
        "```\n\n"
        "演示案例需另备 GGUF 模型与 `llama.cpp` 的 `llama-server`（回环 127.0.0.1:8080）。"
        "`示例案例/incident-01/` 是演示现场的输入与判据声明，不含真实主机数据与凭据。\n\n"
        "程序为命令行工具，无图形界面；运行环境要求 Python ≥ 3.12，纯 CPU 路径，无 GPU 亦可。\n",
        encoding="utf-8",
    )
    (STAGE / "04-说明/复现步骤.md").write_text(
        "# 复现步骤\n\n"
        "```text\n"
        "1  uv sync && uv build                         # 依赖与构建\n"
        "2  make check                                  # 六步门禁\n"
        "3  uv run agent-sec-perf doctor                # 环境体检\n"
        '4  uv run pytest -q -m "not slow and not benchmark"\n'
        "5  uv run agent-sec-perf demo --run            # 分钟级，需本地模型\n"
        "6  uv run agent-sec-perf pack example --out /tmp/case\n"
        "7  uv run agent-sec-perf pack build /tmp/case\n"
        "```\n\n"
        "报告第 4.3 节的用例数为 2026-11-07 复跑值；数字随开发推进会变化，"
        "打印前应重跑第 2、4 步。依赖精确版本见 `uv.lock`。\n",
        encoding="utf-8",
    )


def pack() -> None:
    if ZIP.exists():
        ZIP.unlink()
    forbidden = (".git", ".venv", "__pycache__", ".pyc", ".pytest_cache", ".mypy_cache")
    with zipfile.ZipFile(ZIP, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(STAGE.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(STAGE).as_posix()
            if any(part in forbidden for part in f.parts):
                continue
            z.write(f, "软件工程课程报告-电子材料/" + rel)
    with zipfile.ZipFile(ZIP) as z:
        names = z.namelist()
        bad = [n for n in names if any(k in n for k in forbidden)]
        if bad:
            raise SystemExit(f"压缩包含禁止内容：{bad[:3]}")
        _say(f"已生成 {ZIP.name}：{len(names)} 个文件，{ZIP.stat().st_size / 1024:.0f} KiB")
        _say("校验：无 .git / 虚拟环境 / 缓存文件")
    # 只保留 zip：展开目录里的源码副本会被全仓 ruff 按错误路径扫描
    shutil.rmtree(STAGE)


if __name__ == "__main__":
    prepare()
    pack()
