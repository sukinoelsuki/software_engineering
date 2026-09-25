---
name: benchmark-protocol
description: 要跑一轮基准、要看历史数据、要发布数据、要改测量口径时按此流程执行。
---

# 基准跑取与发布的可比性流程

> **本 Skill 只写顺序与停下条件，不复述任何规范。** 写法要求见
> [`ADR-0017`](../../../docs/adr/0017-project-level-agent-skills.md) §5.1「三条硬性写作要求」。
> 引用一律写**章节名**；**若指针失效，以权威源为准，并就地修本 Skill**。
> 命令入口一律 `make bench-*`（CI 与本地跑**同一条命令**，见 `benchmark-automation.md` §2）。

## 触发条件

- 要**跑一轮基准**、要看**历史数据**、要**发布数据**时；
- 要**改测量口径**（协议版本 / 任务集 / 启动参数）时。

## 流程要点（只写顺序；每步的停下条件在最右列）

| # | 顺序动作 | 停下条件 |
| --- | --- | --- |
| 1 | 跑之前确认**比较签名一致**：协议版本、`ctx`、`threads`、`max_tokens`、`tiers` **与 CPU 型号** —— 出处 [`benchmark-automation.md`](../../../docs/engineering/benchmark-automation.md) §1「数据在哪、怎么读」与 §7「排障」 | 跨环境或签名不同 ⇒ **不得直接比较**：各成一条序列（CI runner 与开发容器实测差 25% 以上） |
| 2 | 显式 `-np 1`，并核对结果里的槽位数 —— 出处 `benchmark-automation.md` §2、[`devlog 0012`](../../../docs/devlog/0012-2026-09-16-基准自动化数据流水线.md) §2.5 / §4 | 槽位不是 1 ⇒ 停下（默认 4 槽位共享 KV，会让"1 token 的 prefill"混进统计、改变计时口径） |
| 3 | 报告**必须给重复次数与极差** —— 出处 [`post-build-checklist.md`](../../../docs/engineering/post-build-checklist.md) §6.4（同会话对照与极差判据） | **单次采样** ⇒ **不足以判定超阈**，停下补重复 |
| 4 | **加载耗时跨会话不可比**（容器内无法规范化存储状态）⇒ 只作**同会话**指标 —— 出处 `post-build-checklist.md` §6.4 | 拿两次不同会话的加载耗时做对照 ⇒ 停下 |
| 5 | **对照必须在同会话内做** —— 出处 `post-build-checklist.md` §6.4 | 只有跨会话的两个数 ⇒ 停下，不得下"更快 / 更慢"的结论 |
| 6 | 任何会改变测量口径的改动：提 `PROTOCOL_VERSION` 并记 devlog —— 出处 `benchmark-automation.md` §5「改协议 / 改任务集的正确姿势」 | 口径变了而版本未提 ⇒ 停下（"改进"与"口径变了"将无法区分，旧数据不得与新数据放同一条序列） |
| 7 | 发布用 `make bench-publish`：**合并**而非替换，且**不得吞错** —— 出处 `benchmark-automation.md` §8「保留期与体积」、§7「排障」 | 本地演练须显式 `DRY_RUN=1 BENCH_ALLOW_LOCAL=1`；**人手不得直接写数据分支**（只由 CI 写） |
| 8 | 改完测量代码先跑快速自检（S 档小重复），再上全量 —— 出处 `benchmark-automation.md` §2「三条常用命令」 | 快速自检不过 ⇒ 停下，**不要**触发正式轮次（`api_trigger_bench`） |

## 权威源

- [`docs/engineering/benchmark-automation.md`](../../../docs/engineering/benchmark-automation.md) ——
  §1「数据在哪、怎么读」、§2「三条常用命令」、§4「参数与预算」、§5「改协议 / 改任务集的正确姿势」、
  §7「排障」、§8「保留期与体积」。
- [`docs/engineering/post-build-checklist.md`](../../../docs/engineering/post-build-checklist.md) §6.4（同会话对照与极差判据）。
- [`ADR-0014`](../../../docs/adr/0014-benchmark-automation.md)（基准自动化的决策、安全边界与豁免登记）。
- [`docs/notes/evaluation-pitfalls.md`](../../../docs/notes/evaluation-pitfalls.md)（判据为什么这么定：KV 缓存陷阱等情形）。

## 不写什么（硬性）

- **具体基准数字**（属 `bench/data` 分支，不在本仓库的代码线里）；
- 发布脚本的实现与凭据处理（属 `scripts/bench/publish.sh`）；
- CI 触发配置、`lock`、`endStages` 的写法（属 `repo-ci-conventions`）；
- 基准子系统的代码结构（属 `src/agent_sec_perf/bench/`）。
