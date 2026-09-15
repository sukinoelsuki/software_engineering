# 基准任务集选型：为档位对比与 Harness 增益评估引入离线可跑的任务集

- 日期：2026-09-15
- 关联：[ADR-0012](../adr/0012-benchmark-task-set-selection.md)（决策）、
  [ADR-0010](../adr/0010-dynamic-hardware-adaptation.md)（档位机制）、
  [ADR-0011](../adr/0011-tier-composition-revision.md)（档位构成）、
  [三档对比](2026-09-15-tiers-s-m-l-comparison.md)（提出"任务集过简"问题）、
  [`.ide/assets/benchmarks.txt`](../../.ide/assets/benchmarks.txt)、
  [devlog 0010](../devlog/0010-2026-09-15-动态硬件适配与分层Harness.md)
- 时间盒：2 小时（实际约 1.5 小时）
- 状态：**已完成**（选型与首批引入落地；自建阶梯任务集待实现）

---

## 1. 问题

三档实测（2B/4B/8B）暴露了一个硬事实：

> **现有三项任务对 2B 而言都偏易，三档几乎打平**——降级曲线在**能力维度是一条平线**。

因此需要引入一个**能在受限环境内跑、判定客观、且真正能区分档位（并探测能力上限）的任务集**，
作为后续开发与实验的基准。项目所有者同时提出两点约束：

1. 任务集应作为**环境 assets** 引入（随镜像固化，可随时调用）；
2. 想要**能体现模型能力上限**的集合。

---

## 2. 选型判据

| # | 判据 | 权重 | 理由 |
| --- | --- | --- | --- |
| J1 | **离线可跑**（不依赖运行时网络/外部 API） | 5 | 产品定位是离线低资源环境；在线评测测出的能力与真实场景不符 |
| J2 | **判定客观且自动化**（单元测试优先，避免 LLM 裁判） | 5 | 弱模型既当被测又当裁判不可信；且要可复现 |
| J3 | **可区分性**（既不全过也不全挂，落在能力边界附近） | 5 | 当前最大痛点：任务过易导致平线结论 |
| J4 | **不引入新依赖** | 4 | 项目对依赖极度克制；为一个评测集引入重依赖不划算 |
| J5 | **许可证明确** | 5 | 供应链条款：来源与许可证必须明确（无许可证即排除） |
| J6 | **体积可控**（可随镜像固化） | 3 | 影响构建时间与镜像体积 |
| J7 | **覆盖工具调用/多步执行** | 4 | 这是本项目的核心能力，而非纯函数补全 |
| J8 | **能够探测上限**（存在足够难的层级） | 4 | 项目所有者明确要求"能力上限" |

---

## 3. 候选调研结果（2026-09-15 实测核对）

| 候选 | 许可证 | 数据形式 | 体积 | 判定方式 | J1 离线 | 结论 |
| --- | --- | --- | --- | --- | --- | --- |
| **HumanEval+**（EvalPlus） | Apache-2.0 | **JSONL** | 11.45 MB | 单测 | ✅ | ✅ **引入** |
| **MBPP+**（EvalPlus） | Apache-2.0 | 仅 parquet（378 行） | 4.89 MB | 单测 | ✅（经 rows API） | ✅ **引入** |
| **BigCodeBench** v0.1.4 | Apache-2.0 | 仅 parquet（1140 行） | 6.96 MB | 单测 | ✅（经 rows API） | ✅ **引入** |
| RepoBench | CC-BY-4.0 | 仅 parquet | **约 470 MB** | EM/单测 | ✅ | ❌ 体积过大 |
| SWE-bench Lite | **未标注** | 仅 parquet | 1.24 MB | 补丁通过测试 | ⚠️ 每例需容器+仓库 | ❌ 许可证未标注（J5）+ 无法离线轻量跑 |
| SWE-bench（完整） | MIT | parquet | 78.7 MB | 同上 | ⚠️ | ❌ 运行成本 |
| Terminal-Bench-1 | Apache-2.0 | 仓库内任务定义 | 141.6 MB | 容器内任务 | ❌ 需容器 | ⚠️ 仅作**上限探测**备选 |
| BFCL（工具调用） | Apache-2.0 | 在 gorilla 仓库内 | 371 MB | AST 匹配 + 实际执行 | ⚠️ 部分类目需真实 API | ⚠️ 第二阶段 |
| τ-bench | MIT | parquet | 6 MB | 数据库终态 | ❌ **需 LLM 扮演用户** | ❌ 与"离线 + 弱模型"前提冲突 |
| LiveCodeBench | MIT | 数据集 id 待确认 | 3.3 MB（仓库） | 单测 | ✅ | ⚠️ 待补（防污染，可后续加入） |
| AgentBench | Apache-2.0 | 需运行环境 | 29.7 MB | 多环境交互 | ❌ | ❌ 环境成本过高 |

---

## 4. 关键发现一：HF 数据集**几乎只提供 parquet**，而读 parquet 需要新依赖

实测发现一个系统性障碍：**Hugging Face 上的数据集普遍以 parquet 为唯一分发格式**，
而读取 parquet 需要 `pyarrow`/`pandas`（评测量级的新依赖，与 J4 冲突）。

**解法（本轮的实现突破）**：HF 的 **datasets-server rows API** 直接返回 JSON：

```text
https://datasets-server.huggingface.co/rows?dataset=<id>&config=<cfg>&split=<split>&offset=<n>&length=100
```

按 100 行分页取完，即可用**标准库**落成 JSONL。已实测：
MBPP+ 378 行、BigCodeBench 1140 行全部取回，且**两次独立生成得到同一 sha256**
（行序按 offset 递增、字段按 key 排序）——因此**可以固定摘要做 fail-secure 校验**。

> 这一条把"大量仅 parquet 的公开数据集"从"不可用"变成"可用"，且**零新依赖**。

---

## 5. 关键发现二：多数基准**自带 agent harness**，直接采用会答错问题

项目所有者提出过一个准确的直觉："这样的集在 AI 里，是不是本身就是工具"——**是的**。
SWE-bench、Terminal-Bench、τ-bench、aider benchmark 都自带 agent 执行框架。

| 采用方式 | 测出的东西 | 能否回答本项目核心问题 |
| --- | --- | --- |
| 直接跑**它们的** harness | 别人的 Harness + 我们的模型 | ❌ 不能 |
| 只取**任务定义 + 客观判定器**，用自己的链路驱动 | 我们的 Harness + 我们的模型 | ✅ 能 |

本项目要回答的是"**我们自己的 Harness 相对裸模型提升多少**"（SRS 核心论点），
因此**必须只取任务与判定字段**（`test` / `entry_point` / `libs`），
由自研执行与判定链路驱动。官方 harness 仅作为**可选的外部参照**另行评估。

---

## 6. 关键发现三：**单题成本**决定了不能跑全集（这是最硬的现实约束）

以 M 档（Qwen3-4B，prefill 67 tok/s、生成 16.6 tok/s）估算：

| 数据集 | 题量 | 平均提示 | 单题估算耗时 | **全集耗时** |
| --- | --- | --- | --- | --- |
| HumanEval+ | 164 | ≈150 tok | ≈17 s | **≈46 分钟** |
| MBPP+ | 378 | ≈35 tok | ≈12 s | **≈75 分钟** |
| BigCodeBench | 1140 | ≈300 tok | ≈23 s | **≈7.3 小时** |

**合计约 9.5 小时/模型**；三档（2B/4B/8B）约 **28 小时**；若再叠加 Harness 的多轮工具调用，
成本还要数倍。⇒ **全集评测在本项目不可行**。

**应对**：采用**固定子集**（固定随机种子 + 分层抽样），例如
HumanEval+/MBPP+ 全集（成本可接受）+ BigCodeBench 抽 150 题，
使单模型单轮降至约 3 小时，且**子集索引必须归档**以保证跨轮可比。

---

## 7. 决策（详见 [ADR-0012](../adr/0012-benchmark-task-set-selection.md)）

采用**三层结构**：

| 层 | 内容 | 作用 | 本轮状态 |
| --- | --- | --- | --- |
| **L0 外部锚点** | HumanEval+（164）、MBPP+（378）、BigCodeBench（1140，抽子集） | 与公开分数对照；确认评测链路本身没坏；提供难度梯度 | ✅ **已引入镜像** |
| **L1 自建阶梯任务集** | 贴合本项目分布的 10~20 条任务，按难度分阶（单函数 → 多文件改动 → 需检索 → 多步工具调用） | **真正探测能力上限**；且可验证 Harness 增益 | ⏳ 待实现（见 ADR-0012） |
| **L2 上限探测** | Terminal-Bench-1 / SWE-bench（按需） | 预期接近地板分，仅用于展示"离真正的 agent 任务还有多远" | ⚠️ 可选，不纳入默认循环 |

**为什么上限主要靠 L1 而不是 L0**：L0 的任务分布与我们的实际用途不同，
且对 2B~8B 而言难度梯度有限；而 L1 可以用**我们自己真实遇到的需求**构造，
难度可控、判定客观、且能直接暴露"哪一阶开始崩"——这正是"能力边界"的定义。

---

## 8. 已实施

- **`.ide/assets/benchmarks.txt`**：3 个数据集，逐项记录 sha256、体积、许可证与用途；
  并写明"已评估但未引入的候选及原因"，避免以后重复调研。
- **`.ide/fetch-assets.sh`**：新增 `benchmarks` 子命令，支持两类来源：
  - `file`：直接下载（HumanEval+）；
  - `hf-rows`：经 rows API 取数落为 JSONL（MBPP+、BigCodeBench）；
  两类共用同一套"跳过已存在且校验通过 → 失败即删并中止构建"的逻辑；
  并把 `verify` 扩展为**同时校验模型与基准**（构建自检因此覆盖全部资产）。
- **`.ide/Dockerfile`**：新增第 12 阶段（预置基准集），自检扩为第 13 阶段。
- **`.cnb.yml`**：`build.by` 加入 `benchmarks.txt`。
- **参考资料**：新增 `harbor`（Terminal-Bench 团队的 agent 评估与优化框架，
  Apache-2.0，130 MB）——与本项目"评估 Harness 增益"的主线直接对应。

**验证记录（当前环境实测，无需重建镜像）**

| 项 | 结果 |
| --- | --- |
| 脚本语法 `bash -n` | ✅ |
| `plan` 解析三个清单 | ✅ 4 模型 + 3 基准 + 9 参考资料（含 harbor）逐条正确 |
| 真实获取 3 个基准集 | ✅ 全部完成，共 **18.7 秒** |
| sha256 校验 | ✅ 3/3 通过；HumanEval+ 与 HF 官方摘要**逐字符一致** |
| 生成结果可复现 | ✅ 两次独立生成 MBPP+ 得到**同一摘要** |
| `verify` 覆盖模型 + 基准 | ✅ 4 模型 + 3 基准全部通过 |
| 判定字段完整性 | ✅ 三者**每题都含 `test`**；HumanEval+/BigCodeBench 另含 `entry_point` |

---

## 9. 待验证

| # | 待验证 | 方式 |
| --- | --- | --- |
| B-1 | 基准集实际区分度（2B/4B/8B 在 HumanEval+/BigCodeBench 上的分数跨度） | 跑固定子集，观察是否形成梯度而非平线 |
| B-2 | BigCodeBench 抽样方案的稳定性（不同种子下的分数波动） | 固定 3 个种子对比 |
| B-3 | 自研判定链路与官方判据的一致性 | 抽取若干题与 EvalPlus/BigCodeBench 官方结果比对 |
| B-4 | `hf-rows` 摘要的长期稳定性（上游若更新 split 内容会中断构建） | 重建时观察；若失败则重新生成并记录 |
| B-5 | 上限探测层是否值得纳入（Terminal-Bench-1 预期接近地板） | 抽 10 题试跑，成本收益比不划算则不纳入 |

---

## 10. 参考

- EvalPlus / HumanEval+ / MBPP+：<https://github.com/evalplus/evalplus>、HF `evalplus/humanevalplus`、`evalplus/mbppplus`
- BigCodeBench：<https://github.com/bigcode-project/bigcodebench>、HF `bigcode/bigcodebench`
- Harbor（agent 评估与优化框架）：<https://github.com/harbor-framework/harbor>
- Terminal-Bench：<https://github.com/harbor-framework/terminal-bench-1>
