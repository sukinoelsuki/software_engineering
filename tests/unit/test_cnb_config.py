"""CI 配置的架构约束（把两条"用一次故障换来"的规则钉成机器检查）。

2026-09-16 的真实故障：`bench/nightly` 的 `push` 流水线第一行就退出码 2——
`sh: 1: set: Illegal option -o pipefail`。原因是两个：

1. **阶段脚本由镜像的 `/bin/sh` 执行**，而 Debian 12 的 dash 不支持 `set -o pipefail`；
   `endStages` 里更因为它是非 `-e` 的 `set`，整段脚本被中止，连数据发布都没执行；
2. **门禁镜像用了浮动标签 `python:3.12`**，该标签已从 Debian 12 漂到 Debian 13
   （dash 0.5.12-12 支持 pipefail），于是同一份 `.cnb.yml` 在门禁里能跑、
   在基准流水线（bookworm 的镜像）里挂——**配置没变，环境变了**。

2026-09-17 的首夜故障（测量跑完 6.59 核时，发布阶段 `Error 141`）暴露了发布脚本的
两个缺陷，同样固化成断言：

3. **`… | head` 在 `set -o pipefail` 下会变成退出码 141（SIGPIPE）**：head 读完若干行
   即退出，生产者后续写入收到 SIGPIPE，整条管道被判为非零 ⇒ 发布中止。
   它只在"输出超过截断行数"时出现——09-16 只有 6 个文件故未暴露，09-17 有 135 个；
4. **发布必须"合并本轮"，不能整体替换数据子目录**：CI 的数据根目录只有本轮，
   整体替换会删掉历史轮次的日志/产物/报告，数据分支永远只剩最新一轮。

这里把结论固化成断言：禁止 bash 专有语法、镜像必须钉到发行版、
阶段脚本不得把 git 输出接入 `head`、发布脚本必须走合并路径。
"""

from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CNB_YML = REPO_ROOT / ".cnb.yml"
PUBLISH_SH = REPO_ROOT / "scripts" / "bench" / "publish.sh"

#: python 镜像必须带发行版后缀（浮动标签会随时间漂移）
PINNED_PYTHON_IMAGE = re.compile(r"^python:3\.\d+(-slim)?-(bookworm|trixie|bullseye)$")


def _lines() -> list[str]:
    return CNB_YML.read_text(encoding="utf-8").splitlines()


@pytest.mark.unit
def test_stage_scripts_avoid_bash_only_shell_options() -> None:
    """阶段脚本不得使用 `pipefail`（dash 不支持，会直接中止整段脚本）。"""
    offenders = [
        line.strip() for line in _lines() if line.strip().startswith("set -") and "pipefail" in line
    ]

    assert offenders == [], (
        "阶段脚本由 /bin/sh 执行，dash 不支持 pipefail；"
        f"请改用 `set -eu`（需要 pipefail 时显式切 bash）。违规行：{offenders}"
    )


@pytest.mark.unit
def test_python_images_are_pinned_to_a_distribution() -> None:
    """流水线用到的 python 镜像必须钉到具体发行版，避免"今天能跑明天不能"。"""
    images = [
        line.split("image:", 1)[1].strip()
        for line in _lines()
        if "image:" in line and "python" in line
    ]

    assert images, "至少应声明一条 python 镜像（门禁用）"
    for image in images:
        assert PINNED_PYTHON_IMAGE.match(image), (
            f"镜像未钉到发行版：{image}（如 python:3.12 会随上游漂移，与开发镜像的 bookworm 分叉）"
        )


@pytest.mark.unit
def test_bench_crontab_keys_are_declared() -> None:
    """定时任务的键名与 cron 表达式是"数据节奏"本身，改动必须被看见。"""
    text = "\n".join(_lines())

    assert '"crontab: 0 4 * * 2-6,0"' in text, "夜轮（周二~周日 04:00）缺失"
    assert '"crontab: 0 4 * * 1"' in text, "深跑（周一 04:00）缺失"
    assert "bench/nightly:" in text, "基准流水线必须挂在单一明确分支上"


@pytest.mark.unit
def test_publish_script_never_pipes_git_output_into_head() -> None:
    """禁止 `… | head`：`set -o pipefail` 下 head 提前退出会让生产者收到 SIGPIPE。

    2026-09-17 的首夜发布就是栽在这里：`git show --stat … | head -30` 在提交含 135 个
    文件时必现（09-16 只有 6 个文件，输出不足 30 行，所以第一次没暴露）。
    正确写法是先落盘、再截断。
    """
    offenders = [
        line.strip()
        for line in PUBLISH_SH.read_text(encoding="utf-8").splitlines()
        if not line.strip().startswith("#") and re.search(r"\|\s*head\b", line)
    ]

    assert offenders == [], f"不要用管道把 git 输出接到 head（SIGPIPE 141）：{offenders}"


@pytest.mark.unit
def test_publish_script_merges_instead_of_replacing_published_history() -> None:
    """发布必须"合并本轮"，而不是整体替换数据子目录——否则数据分支永远只剩最新一轮。"""
    lines = PUBLISH_SH.read_text(encoding="utf-8").splitlines()
    text = "\n".join(lines)

    assert "--merge-into" in text, "发布阶段应调用 --merge-into 合并索引与 latest 报告"
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        assert not re.search(r'rm -rf\s+"\$\{WORKTREE:\?\}/\$\{DATA_SUBDIR:\?\}"\s*$', stripped), (
            "不得整体删除已发布的数据子目录（会丢掉历史轮次的日志、产物与报告）"
        )
