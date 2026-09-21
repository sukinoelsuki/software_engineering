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


def _gate_pipeline_block() -> str:
    """取 `"**":` 门禁流水线组的正文（截止到 `bench/nightly:` 之前）。"""
    return "\n".join(_lines()).split('"**":', 1)[1].split("bench/nightly:", 1)[0]


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
def test_commit_message_stage_exempts_merge_commits() -> None:
    """`commit-message` 阶段必须跳过**合并提交**，否则 `develop`→`main` 每合并一次就红一次。

    2026-09-21 实测：PR #7 / #8 / #9 的 PR 流水线全绿、审查通过，合并后 `main` 的
    `push` 流水线恒红在 `commit-message`（构建 `cnb-5bp-1k31ffrg4`，阶段退出码 2、
    耗时 <1 s，日志只有三行 `commit validation: failed!`）。
    根因：合并提交的信息由平台生成（「合并来自 develop 的合并请求 #9」），**不是人写的**，
    而 Conventional Commits 约束的是人写的提交信息；PR 流水线又没有这个阶段，
    于是症状表现为「PR 绿、合并后必红」——只看 PR 状态永远发现不了。

    判据取"存在第二父提交"（`HEAD^2`）而不是"提交者是谁"：squash 合并是单父，
    那条信息由人填写，仍必须受规范约束 ⇒ 不得一并豁免。
    """
    gate = _gate_pipeline_block()
    stage = gate.split("- name: commit-message", 1)

    assert len(stage) > 1, "门禁流水线里应当有具名的 commit-message 阶段"
    body = stage[1]

    assert "make commit-check" in body, "非合并提交仍必须校验提交信息规范"
    assert re.search(r'rev-parse\s+--verify\s+--quiet\s+"?HEAD\^2"?', body), (
        "合并提交（存在第二父提交）必须被跳过：平台生成的合并提交信息必然违反 "
        "Conventional Commits，不豁免则 develop→main 的每次合并都会红"
    )


@pytest.mark.unit
def test_every_gate_pipeline_has_a_dedicated_secret_scan_stage() -> None:
    """每个门禁流水线都必须有具名密钥扫描阶段（一致性报告 A-15 的处置①）。

    为什么强调"具名"：A-15 的教训是"声称已生效的缓解措施"可能根本没在运行。
    藏在 `make check` 聚合里的扫描无法从流水线上被**直接看到**，
    具名 stage 才让"CI 到底有没有这道扫描"成为肉眼可核的事实。

    判据刻意用**成对计数**，而不是"文本里出现过"：2026-09-18 的变异探针实测，
    后者在"只删掉其中一个流水线的阶段"时**依然通过**——保护没少（`make check`
    仍会跑它），但可观测性少了，而本测试要钉住的正是可观测性。
    成对计数同时让"新增一条门禁流水线"也必须补上这道阶段。
    """
    gate = _gate_pipeline_block()

    gate_runs = gate.count("make check LOCAL_HOOKS=0")
    secret_stages = gate.count("- name: secret-scan")

    assert gate_runs > 0, "门禁流水线组里应当至少有一条执行完整门禁的流水线"
    assert secret_stages == gate_runs, (
        f"具名密钥扫描阶段数（{secret_stages}）与门禁流水线数（{gate_runs}）不一致"
    )
    assert "make security-secrets" in gate, "密钥扫描必须复用 Makefile 的目标（唯一事实来源）"


@pytest.mark.unit
def test_ci_setup_and_check_disable_local_hooks_explicitly() -> None:
    """流水线里的 `make setup` / `make check` 必须**显式**带 `LOCAL_HOOKS=0`。

    两个方向都防：
    ① 忘带 ⇒ `make check` 里的 `hooks-check` 找不到本地钩子（流水线里本就不需要）⇒ 红灯；
    ② 误以为"这个开关可以随便关" ⇒ 本断言把它钉在流水线语境里，
       而本地默认仍是 `LOCAL_HOOKS=1`（安装并断言）。
    """
    offenders = [
        line.strip()
        for line in _lines()
        if line.strip().startswith(("make setup", "make check")) and "LOCAL_HOOKS=0" not in line
    ]

    assert offenders == [], f"流水线中的 make setup/check 必须显式带 LOCAL_HOOKS=0：{offenders}"


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
