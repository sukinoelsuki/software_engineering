---
name: repo-ci-conventions
description: 增删改本仓库 .cnb.yml 的 event、stage、endStages、runner、docker 或 build.by 时按此流程执行；流水线出现第一行退出码 2、绿着但没数据、同配置两处行为不一致等诡异失败时同样适用。
---

# 本仓库 CI 流水线约定（改 `.cnb.yml` 或新写 stage 时）

> **本 Skill 只写顺序与停下条件，不复述任何规范。** 写法要求见
> [`ADR-0017`](../../../docs/adr/0017-project-level-agent-skills.md) §5.1「三条硬性写作要求」。
> 引用一律写**章节名**；**若指针失效，以权威源为准，并就地修本 Skill**。
>
> **需要调用 CNB 平台 API**（仓库 / Issue / PR / Git / 流水线触发与日志 / 制品库 / 搜索）时，
> 用 `cnb` 命令 —— 那属于**官方 `cnb-pipeline` Skill** 的职责，不在本 Skill 范围内。
> 分工判据见 `ADR-0017` §5.3.2：**仓库约定应能被本地文件（`.cnb.yml`、`Makefile`、测试）证明，
> 平台/API 用法只能来自上游；有歧义时归官方。**

## 触发条件

- 增删 / 修改 `.cnb.yml` 的 `event`、`stage`、`endStages`、`runner`、`docker`、`build.by` 时；
- 流水线出现诡异失败时（第一行退出码 2、构建"绿着但没数据"、同一份配置在两条流水线上行为不同）。

## 流程要点（只写顺序；每步的停下条件在最右列）

| # | 顺序动作 | 停下条件 |
| --- | --- | --- |
| 1 | 先读 `.cnb.yml` **头部注释**与相关段的内联注释（两条硬规则的来历、`LOCAL_HOOKS` 开关的理由都在那里） | 只看正文不读注释 ⇒ 会漏掉用故障换来的约束 |
| 2 | 阶段脚本的 shell：由镜像 `/bin/sh`（Debian 12 的 dash）执行 ⇒ 用 `set -eu` —— 出处 `.cnb.yml` 头部规则 1、[`benchmark-automation.md`](../../../docs/engineering/benchmark-automation.md) §3「自动触发」提示框与 §7「排障」 | 需要 `pipefail` ⇒ **显式切 bash** 并写清理由；否则不写 `pipefail`（dash 第一行即报错、后续 stage 全不执行） |
| 3 | 镜像：钉到**发行版**（如 `python:3.12-bookworm`）—— 出处 `.cnb.yml` 头部规则 2 | 用浮动标签 ⇒ 停下（同一配置在不同时间行为不同，且与开发镜像分叉） |
| 4 | 流水线里的 `make setup` / `make check`：带 `LOCAL_HOOKS=0` —— 出处 `.cnb.yml` 头部规则 3、[`Makefile`](../../../Makefile) 的 `LOCAL_HOOKS` 段 | 不确定它的语义就**不要顺手关**（它是显式开关，不是"可以随便关"）；本地开发机**不带**该开关 |
| 5 | `build.by`：列全**构建期输入** —— 出处 `.cnb.yml` 的 `vscode` 段与 `bench/*` 各段内联注释 | 新增了构建期文件而未列入 ⇒ 构建**会直接报错**；停下补 `by`，**不要**改 Dockerfile 绕过 |
| 6 | runner 资源：`cpus: 8` ⇒ 内存 `16 GiB`（内存 = 核数 × 2 GiB）—— 出处 `benchmark-automation.md` §4「参数与预算」、`.cnb.yml` 的 `bench/nightly` 段注释 | 想降到 4 核 ⇒ 停下（L 档常驻约 8.70 GiB，4 核只有 8 GiB 会 OOM）；改前先有资源估算依据 |
| 7 | 基准流水线：`lock` **串行**（`key: bench-cpu`）—— 出处 `benchmark-automation.md` §3、[`ADR-0014`](../../../docs/adr/0014-benchmark-automation.md) §2.1 | 为省时间去掉锁 ⇒ 停下（并发测量会让吞吐数字失去可比性） |
| 8 | `endStages` 发布：**不得吞错**（不要 `|| echo`）—— 出处 `.cnb.yml` 的 `bench/*` 段注释、`benchmark-automation.md` §7「排障」首行 | 改完先确认"失败会把构建标红"；"绿着但没数据"是最难发现的失败 |
| 9 | 改完跑 [`tests/unit/test_cnb_config.py`](../../../tests/unit/test_cnb_config.py) | 该测试报红 ⇒ 修配置，**不要改测试**（它钉住的是历史坑） |
| 10 | 改完按 `.cnb.yml` 头部注释自问：这次改动会不会让 `bench/nightly` 与 `develop` 分叉 ⇒ 需要同步时见 `benchmark-automation.md` §2「更新基准分支（重要）」 | 只有基准代码 / `.cnb.yml` 变更才需要同步；纯文档提交不必同步（会白跑一轮约 20 分钟） |

## 权威源

- [`.cnb.yml`](../../../.cnb.yml) —— 头部注释（三条硬规则）与各段内联注释（定位、设计取舍、历史故障）。
- [`docs/engineering/benchmark-automation.md`](../../../docs/engineering/benchmark-automation.md) ——
  §3「自动触发」、§4「参数与预算」、§7「排障：症状 → 原因 → 处置」。
- [`ADR-0014`](../../../docs/adr/0014-benchmark-automation.md) §2.1 / §2.9（基准自动化的决策与豁免登记）。
- [`docs/devlog/0012-2026-09-16-基准自动化数据流水线.md`](../../../docs/devlog/0012-2026-09-16-基准自动化数据流水线.md) §3 / §5（三条坑的原始证据）。
- 机器检查：[`tests/unit/test_cnb_config.py`](../../../tests/unit/test_cnb_config.py)。

## 不写什么（硬性）

- **平台 API 用法**（属官方 `cnb-pipeline` Skill；本项目内 `repo-ci-conventions` 与它**交集必须为空**）；
- 基准**测量口径**与可比性判据（属 `benchmark-protocol`）；
- 发布脚本的实现细节（属 `scripts/bench/publish.sh`）与 `Makefile` 各目标的正文；
- 镜像 Dockerfile 的内部阶段划分（属 `.ide/Dockerfile` 自身的注释与 ADR-0016）。
