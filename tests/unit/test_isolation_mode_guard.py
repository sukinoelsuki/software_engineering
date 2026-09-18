"""隔离模式的静态守卫：默认必须是 `user`，CI 与 Makefile 不得把 `root` 选出来。

所有者 2026-09-18 裁决（devlog 0014 §7 优先级 A 第 2 条，残余项 T-08）：
对 `isolation="root"` 的**凭据继承残余**做**静态机器检查**，
而**不做**"按 `CI` 环境变量在运行时拒绝"——本轮已实测该变量的**存在性不可靠**
（本工作区 `CI` 未设置），拿它当判据会得到"**看起来在拦、实际可能不拦**"的措施，
正是一致性报告 A-15 那一类失效模式（与"非特权子进程逃逸读宿主 `CNB_TOKEN`"同属未缓解项）。

为什么是静态检查、而不是运行时拒绝：
`root` 模式本身是**要保留的能力**——`foundation/proc.py` 写明"`root` 用于本地开发、
**在 CI 中不得使用**"。因此能机器化的部分是"**流水线与 Makefile 的默认值不得把它选出来**"；
真正的运行时拒绝需要有可靠的"我在 CI 里"判据，而当前不存在这样的判据
（详见 devlog 0014 §7 的裁决原文）。
"""

from __future__ import annotations

import pathlib
import re

import pytest

from agent_sec_perf.bench.rounds import _parse_args

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"
CNB_YML = REPO_ROOT / ".cnb.yml"

#: 三种"把 root 选出来"的写法：Makefile 变量赋值、CLI 长选项（含 `=` 与空格两种形态）
ROOT_SELECTION_PATTERNS = (
    re.compile(r"BENCH_ISOLATION\s*[?:]?=\s*root\b"),
    re.compile(r"--isolation[=\s]+root\b"),
)


@pytest.mark.unit
def test_makefile_default_isolation_is_user() -> None:
    """`make bench-round` 的默认隔离模式必须是非特权的 `user`。"""
    text = MAKEFILE.read_text(encoding="utf-8")

    assert "BENCH_ISOLATION ?= user" in text, "默认隔离模式被改成了 user 以外的值"


@pytest.mark.unit
def test_neither_makefile_nor_ci_selects_root_isolation() -> None:
    """Makefile 与 CI 配置中都不得出现"选 root"的写法。

    这条断言防的是**最容易发生的一种回归**：为了让某次基准在容器里跑通，
    顺手在 `Makefile` 默认值或 `.cnb.yml` 的命令里写 `root`——那等于把
    "非特权执行"这道防线在流水线上静默关掉，而测出来的数据也不再可用于安全断言。
    """
    offenders: list[str] = []

    for path in (MAKEFILE, CNB_YML):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.lstrip().startswith("#"):
                continue
            if any(pattern.search(line) for pattern in ROOT_SELECTION_PATTERNS):
                offenders.append(f"{path.name}:{number}: {line.strip()}")

    assert offenders == [], (
        "CI 与 Makefile 不得把隔离模式选成 root（凭据继承、无资源上限）：\n" + "\n".join(offenders)
    )


@pytest.mark.unit
def test_cli_default_isolation_is_user(tmp_path: pathlib.Path) -> None:
    """命令行入口的默认值也必须是 `user`（默认拒绝特权执行的姿态）。"""
    args = _parse_args(["--data-root", str(tmp_path)])

    assert args.isolation == "user"


@pytest.mark.unit
def test_isolation_is_always_passed_explicitly_by_makefile() -> None:
    """`make bench-round` 必须**显式**传 `--isolation`，不能依赖程序默认值。

    显式传参让"这一轮用了什么隔离"出现在命令行里（可被 CI 日志与数据记录佐证），
    而不是隐含在代码的默认参数中。
    """
    text = MAKEFILE.read_text(encoding="utf-8")
    bench_round = text.split("bench-round:", 1)[1].split("\n", 20)

    assert any("--isolation $(BENCH_ISOLATION)" in line for line in bench_round)
