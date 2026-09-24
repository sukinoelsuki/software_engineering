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

2026-09-24 追加一条**方向相反**的约束（`ADR-0023`：CI 从"全自动跑测"降级为
"人工触发 + 环境内自动化"）：**本仓库不得再有任何自动触发的跑测流水线**——
`bench/nightly:` 段与 `web_trigger_bench` 已整体删除，`crontab` 一条不留。
原有那条"两条 crontab 键必须在"的断言由此**换靶**为
`test_no_pipeline_runs_benchmarks_automatically`（门槛未降、靶子换了）。
同时新增 `test_light_gate_is_single_core_path_scoped_and_debounced`，
把"轻门禁必须单核 + 路径过滤 + 只保留最新一条排队"也钉成机器检查——
这三条都直接对应核时消耗，只写在注释里迟早会被改回去。

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
    """取 `"**":` 门禁流水线组的正文（截止到文件末尾的 TODO 注释块之前）。

    2026-09-24 起 `bench/nightly:` 段已被**整体删除**（[ADR-0023](../../docs/adr/0023-ci-downgrade-to-manual-trigger.md)：
    自动跑测下线），因此本函数的终点从"`bench/nightly:` 之前"改为
    "`# TODO（随项目推进补充` 之前"——否则末尾的注释块会被算进门禁组里，
    让下面那两个**成对计数**的断言失去意义。
    """
    tail = "\n".join(_lines()).split('"**":', 1)[1]
    return tail.split("# TODO（随项目推进补充", 1)[0]


#: 一条轻门禁的 `ifModify` 块（**允许块内夹注释**——正是那里的注释解释了为什么省这些核时）
_IFMODIFY_BLOCK = re.compile(r"ifModify:\n((?:(?:[ ]*#[^\n]*\n)|(?:[ ]+- \"[^\"]+\"\n))+)")
#: 从块里只取出路径项，忽略注释 ⇒ 两处副本可以注释不同、**路径必须逐字相同**
_IFMODIFY_ITEM = re.compile(r'^[ ]+- "([^"]+)"$', re.MULTILINE)


def _ifmodify_lists() -> list[list[str]]:
    """取出门禁组里所有 `ifModify` 的路径清单（每条流水线一份）。"""
    return [
        _IFMODIFY_ITEM.findall(block) for block in _IFMODIFY_BLOCK.findall(_gate_pipeline_block())
    ]


@pytest.mark.unit
def test_light_gate_is_single_core_path_scoped_and_debounced() -> None:
    """轻门禁必须是**单核 + 路径过滤 + 只保留最新一条排队**（2026-09-24，ADR-0023）。

    三条都直接对应核时消耗，因此都必须是"机器的判据"而不是注释里的承诺：

    * `cpus: 1` —— **不显式声明就按平台默认 8 核计费**。轻门禁的负载是静态检查与
      单元测试（本地 957 passed ≈ 9 s），8 核纯属浪费；
    * `ifModify` —— 纯文档提交不再触发；这是"路径过滤"这一省法的唯一载体；
    * `lock: {wait: true, cancel-in-wait: true}` —— 排队而不是堆积。1 核的流水线
      若无人清理会排成长队，而**排队期间同样按核时计费**。

    另断言**两处 `ifModify` 清单逐字一致**：它们刻意是重复文本（不用 YAML 锚点，
    理由见 `.cnb.yml` 该段注释），因此"改一处忘另一处"是这里**唯一**的分叉来源，
    没有这条断言就不会有人发现。
    """
    gate = _gate_pipeline_block()
    lists = _ifmodify_lists()

    assert gate.count("cpus: 1") == 2, (
        "两条轻门禁都必须显式声明 `runner.cpus: 1`；"
        f"未声明即按平台默认 8 核计费。实际出现 {gate.count('cpus: 1')} 次"
    )
    assert len(lists) == 2, f"两条轻门禁都必须有 ifModify（实际找到 {len(lists)} 处）"
    assert lists[0] == lists[1], "两处 ifModify 路径清单必须逐字一致（否则会悄悄分叉）"
    for path in ("src/**", "tests/**", "scripts/**", "Makefile", "pyproject.toml", "uv.lock"):
        assert path in lists[0], f"ifModify 缺少关键路径：{path}"
    assert gate.count("lock:") == 2, "两条轻门禁都必须有排队锁"
    assert gate.count("cancel-in-wait: true") == 2, (
        "锁必须配 `cancel-in-wait: true`（只保留最新一条排队），否则会排成一条长队"
    )


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
def test_no_pipeline_runs_benchmarks_on_build_bucket_or_timer() -> None:
    """**不得再有「走构建桶的」或「定时的」跑测流水线**（2026-09-25 换靶，ADR-0025）。

    本用例原名 `test_no_pipeline_runs_benchmarks_automatically`（2026-09-24，ADR-0023），
    当时钉的是"**任何**自动跑测都不许在"。**该前提已被 ADR-0025 推翻**：

    - 旧前提是"自动化 ⇒ 必然烧构建桶（硬顶 160 核时/月）"；
    - 探针 E1 实测证明：判据是"有没有声明 `services: [vscode]`"，不是"怎么被触发"
      ⇒ `api_trigger` + vscode **走开发桶**（`total` = 17600，余 ≈15000）。

    ⇒ **门槛没有降低，靶子换了**：从"不许自动化"换成"**不许走构建桶、不许定时**"。
    （"必须声明 vscode"这一半由
    `test_bench_pipelines_declare_vscode_service_to_stay_in_dev_bucket` 钉住。）

    为什么这两条仍然必须钉死：
    1. **构建桶是硬顶**：`ci_in_sec.total == free == 160` 核时/月，已用 99.33；
    2. **定时不可控**：`crontab` 不等人、不看当天有没有别的事。
       ⚠️ 理由已于 09-25 变更——不是"烧构建桶"，而是**本项目需求是"按需"而非"按点"**；
    3. **跑测编排不得回到流水线**：四道可信闸门已在 `scripts/bench/gates.py`
       （ADR-0023 §2.4），流水线上只许出现 `make bench` 这一个入口调用。

    采用**文本级黑名单**：只要这些入口重新出现在 `.cnb.yml` 里，本用例即失败 ⇒
    "顺手加回一条定时任务"必须**显式改测试**，不可能悄悄发生。

    **只扫非注释行**（与 `test_publish_script_never_pipes_git_output_into_head` 同口径）：
    本文件头部的注释需要**指名**这些被删除的入口来解释"为什么删"，
    若连注释一起扫，"把理由写清楚"反而会踩红——那会逼出一种最糟的写法：
    删掉理由。注释不是配置，判据要看的是**活着的键**。
    """
    text = "\n".join(line for line in _lines() if not line.lstrip().startswith("#"))

    forbidden = {
        "crontab:": "定时触发是最不可控的核时消耗源，已整体移除",
        "bench/nightly:": "基准分支不再挂任何流水线",
        "web_trigger_bench": "页面手动跑测入口已删除（改用 make bench）",
        "bench-round": "跑测编排只允许出现在本地一键入口（Makefile / scripts/bench/）",
        "bench-publish": "发布只允许由 scripts/bench/run.sh 触发",
    }
    offenders = {key: why for key, why in forbidden.items() if key in text}

    assert offenders == {}, f".cnb.yml 不得再出现自动跑测入口：{offenders}"


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


#: 发布脚本里来源分支白名单的声明行（取出其默认值，**在 Python 里实跑**这条正则）
_CODE_BRANCH_PATTERN_RE = re.compile(
    r'readonly CODE_BRANCH_PATTERN="\$\{BENCH_CODE_BRANCH_PATTERN:-([^}]*)\}"'
)


@pytest.mark.unit
def test_publish_script_restricts_source_branches() -> None:
    """发布脚本必须把来源分支限制在 `bench/nightly` 与 `test/*`，**主干不得入选**。

    背景：2026-09-24 起跑测不再由 CI 触发，而是在 `test/<slug>` 借来的云原生开发
    环境里人工触发（[ADR-0023](../../docs/adr/0023-ci-downgrade-to-manual-trigger.md)）。
    白名单因此从"一个分支名"变成"一条正则"，而这正是**最容易被顺手放宽**的地方：
    把它改成 `.*` 或加一条 `develop` 就能让数据从主干上推出去，而后果不是"多跑一次"，
    是"发布的来源无法追溯"。

    判据不止看"文本里有 test/"：本用例把脚本里那条正则**取出来在 Python 里实跑**，
    逐个候选分支验证放行/拒绝——否则 `^test/|develop` 这类的写法会**骗过**文本断言。
    """
    match = _CODE_BRANCH_PATTERN_RE.search(PUBLISH_SH.read_text(encoding="utf-8"))

    assert match, "发布脚本必须声明来源分支白名单 `BENCH_CODE_BRANCH_PATTERN`"
    pattern = match.group(1)

    assert re.search(pattern, "test/ci-quota-downgrade"), "`test/<slug>` 必须被放行"
    assert re.search(pattern, "bench/nightly"), "长驻基准分支仍须被放行（历史数据来源）"
    for branch in ("develop", "main", "master", "refs/heads/develop", "feature/x"):
        assert not re.search(pattern, branch), f"主干/无关分支不得作为发布来源：{branch}"


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


# ---------------------------------------------------------------------------
# 2026-09-25（[ADR-0025](../../docs/adr/0025-benchmark-automation-moves-to-dev-bucket.md)）
#
# 前提被推翻：此前认为"自动化跑测 ⇒ 必然烧构建桶"（ADR-0023 §2.1 据此停掉了
# 全部自动跑测）。探针 E1（`sn=cnb-gnb-1k3ao4u18`）实测证明：
# **判据是"有没有声明 services: [vscode]"，不是"它是怎么被触发的"** ——
# `api_trigger` + vscode 的流水线被平台归类为云原生开发环境（`vscode=远程开发`），
# 用量计入 `dev` 而非 `ci`。
#
# ⇒ 跑测因此迁回自动化并改走开发桶。而"走开发桶"这件事**只靠一行配置**成立，
#   删掉那一行就静默掉回构建桶（硬顶 160 核时/月），且从流水线状态上看不出区别
#   ⇒ 必须由机器钉住。同理，"机器矩阵"里的 GPU tags 实测额度为 0，也不得出现。
# ---------------------------------------------------------------------------

#: 跑测流水线的事件名（ADR-0025 §2.2：以 api_trigger 为主）
_BENCH_EVENT = "api_trigger_bench"

#: 平台提供的**真机**架构（grammar.md / build-node.md，2026-09-25）
#: ⚠️ GPU 两个 tags **不在**其中：`cnb charge get-quota` 实测 `*_gpu_in_sec.total = 0`
_ALLOWED_RUNNER_TAGS = frozenset({"cnb:arch:amd64", "cnb:arch:arm64:v8"})


def _bench_pipeline_blocks() -> list[str]:
    """取出所有跑测流水线的配置块（自 `api_trigger_bench:` 起，到下一个顶层键之前）。"""
    lines = _lines()
    blocks: list[str] = []
    for i, line in enumerate(lines):
        if line.strip().startswith(f"{_BENCH_EVENT}:"):
            j = i + 1
            while j < len(lines) and (not lines[j].strip() or lines[j][0] in " \t"):
                j += 1
            blocks.append("\n".join(lines[i:j]))
    return blocks


def _runner_tags() -> list[str]:
    """取出配置里出现的所有 `runner.tags`（同行写法与列表写法都要覆盖）。"""
    lines = _lines()
    tags: list[str] = []
    for i, line in enumerate(lines):
        inline = re.match(r"^\s*tags:\s*(\S+)\s*$", line)
        if inline:
            tags.append(inline.group(1))
            continue
        if re.match(r"^\s*tags:\s*$", line):
            j = i + 1
            while j < len(lines):
                item = re.match(r"^\s*-\s*(\S+)\s*$", lines[j])
                if not item:
                    break
                tags.append(item.group(1))
                j += 1
    return tags


@pytest.mark.unit
def test_bench_pipelines_declare_vscode_service_to_stay_in_dev_bucket() -> None:
    """跑测流水线**必须**声明 `services: [vscode]` 并给出 `keepAliveTimeout`（ADR-0025）。

    为什么这条必须由机器钉住：

    1. **它决定用哪个桶，而两个桶的余量差两个数量级。** 构建桶 `total == free == 160`
       核时/月（已用 99.33），开发桶 `total == 17600`（余 ≈15000）。去掉 `vscode`
       ⇒ 静默掉回构建桶，而流水线状态、日志、产物**全都看不出区别**；
    2. **它不是"自动 vs 人工"的问题。** ADR-0023 停掉自动跑测的前提是
       "自动化 ⇒ 必烧构建桶"，探针 E1 已推翻该前提（`vscode=远程开发` 标签、
       `ci` 增量仅 +110 s 而 `dev` 冻结量 +37800 s）；
    3. **`keepAliveTimeout` 是无人值守的存活下限**（默认 10 分钟心跳）。
       ⚠️ 它**不能**用来"跑完自动结束"——实测到期不回收（ADR-0025 §2.3），
       但缺了它，无人值守的跑测会在 10 分钟时被回收。
    """
    blocks = _bench_pipeline_blocks()

    assert blocks, (
        f"应当至少有一条跑测流水线（事件名 `{_BENCH_EVENT}`，ADR-0025 §2.2）；"
        "没有则本断言失去意义——请删除本用例而不是留它空过"
    )
    for block in blocks:
        assert "services:" in block, "跑测流水线必须声明 `services:`（否则掉回构建桶）"
        assert re.search(r"^\s*-\s*name:\s*vscode\s*$", block, re.MULTILINE), (
            "跑测流水线必须声明 `services: [vscode]`：这是「分配开发节点、计入云原生开发用量」的"
            "**唯一**判据（docs.cnb.cool/zh/workspaces/workspace-vs-build.md）。"
            "去掉它 ⇒ 走构建桶（硬顶 160 核时/月），且从流水线状态上看不出来"
        )
        assert re.search(r"keepAliveTimeout:\s*\d+\s*(ms|s|m|h)?", block), (
            "跑测流水线必须声明 `keepAliveTimeout`：无人值守环境的默认存活下限是 10 分钟"
        )


@pytest.mark.unit
def test_runner_tags_are_limited_to_machines_that_actually_exist() -> None:
    """`runner.tags` 只能是**有真机、且有额度**的架构（ADR-0025 §2.7）。

    机器矩阵（2026-09-25 实测 + 官方文档）：

    | tags | 架构 | 核数 | 额度 | |
    | --- | --- | --- | --- | --- |
    | `cnb:arch:amd64` | amd64 | 1~64 | 开发桶 17600 | ✅ |
    | `cnb:arch:arm64:v8` | arm64/v8 | 1~**16** | 同上 | ✅ |
    | `cnb:arch:amd64:gpu` / `:gpu:L40` | amd64 | 固定 16 | **`total = 0`** | ❌ |

    ⚠️ GPU 两档虽然写在 build-node.md 里，但 `cnb charge get-quota` 实测
    `ci_gpu_in_sec.total = dev_gpu_in_sec.total = 0` ⇒ **一开就要付费且无额度**
    ⇒ 禁止出现在配置里。

    ⚠️ 另有两条**不在**本断言范围内（它们是"结论口径"而非"能否跑"）：
    ① 第三方转载页把 arm64 写成 1~8 核，官方是 1~16；
    ② riscv64 / loongarch64 **没有真机**，只能 qemu —— 其结果是**结论口径**问题：
    qemu 数字**禁止**进性能表（不可比），只允许进"正确性 / 可移植性"结论。
    """
    tags = _runner_tags()

    for tag in tags:
        assert tag in _ALLOWED_RUNNER_TAGS, (
            f"未知的 runner.tags：{tag}。可选真机只有 {sorted(_ALLOWED_RUNNER_TAGS)}；"
            "GPU tags 实测额度为 0，riscv64 / loongarch64 没有真机节点"
        )
