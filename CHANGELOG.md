# 变更日志

本项目所有值得注意的变更都记录在此文件中。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本 2.0.0](https://semver.org/lang/zh-CN/)。

> 本文件由 `make changelog`（基于 commitizen）从符合 Conventional Commits 的提交历史自动生成，
> **请勿手工调整已发布版本的条目**；`[Unreleased]` 段落可由人工补充说明。

---

## [Unreleased]

### Added

- 初始化项目仓库工程骨架：许可证、忽略规则、编辑器配置、属性与 LFS 规则。
- 建立工程流程规范：Git 工作流、软件生命周期、完成定义（DoD）、测试策略。
- 建立安全策略 `SECURITY.md`，明确安全设计原则、开发红线与漏洞披露流程。
- 建立 Agent 协作准则：`CODEBUDDY.md`、`AGENTS.md` 与 `.codebuddy/rules/`。
- 配置 CNB 协作设施：`.cnb.yml` 流水线、Issue 模板、Pull Request 模板。
- 产出 base project 选型提案：`docs/proposals/0001-base-project-selection.md`。
- 新增 [ADR-0006](docs/adr/0006-sandbox-execution-degradation.md)：沙箱执行分层（L1/L2）与在受限容器中的降级。
- 新增 W1 研究笔记（[4B 模型能力与性能实测](docs/research/2026-09-15-w1-4b-model-capability.md)）及其原始数据归档。
- 新增开发日志 [0007](docs/devlog/0007-2026-09-15-开发环境验证与W1风险验证.md)：开发环境验证与 W1 风险验证。
- **预置环境资产**：两个 4B 级端侧模型（Qwen3-4B、AgentCPM-Explore）与 8 个开源 Harness 参考仓库
  随镜像固化（`.ide/assets/` 清单 + `.ide/fetch-assets.sh`，均带 sha256 校验与固定提交）。
- 新增 [ADR-0007](docs/adr/0007-sandbox-capability-matrix.md)：修正沙箱可用性结论，
  按"机制类别"划分隔离能力；新增 [ADR-0008](docs/adr/0008-dev-test-environment-strategy.md)：
  开发与测试环境策略。
- 新增 [`docs/engineering/test-environments.md`](docs/engineering/test-environments.md)：
  测试环境分层（T0~T3）与成果保护（P-1~P-3）操作细则。
- 新增开发日志 [0008](docs/devlog/0008-2026-09-15-环境资产预置与沙箱后端修正.md)。
- 新增 [学习笔记 `docs/notes/`](docs/notes/)（**基准线任务**）与 [ADR-0009](docs/adr/0009-learning-notes.md)；
  首批收录探针方法论、隔离机制分类、复现性与门禁三篇。
- 新增开发日志 [0009](docs/devlog/0009-2026-09-15-留痕机制调整与资源约束澄清.md)。
- 新增 [ADR-0010](docs/adr/0010-dynamic-hardware-adaptation.md)：动态硬件适配与分层 Harness；
  新增学习笔记 [hardware-probing.md](docs/notes/hardware-probing.md)。
- 预置模型清单新增 **`Qwen3-8B-Q4_K_M`（5.03 GB）** 作为 M 档主力（对应"稍好的硬件条件"）。
- SRS 新增 `REQ-PERF-05`（硬件能力探测）与 `REQ-PERF-06`（档位化动态适配）→ **v0.1.2**。
- 恢复 `docs/engineering/post-build-checklist.md` 第 6/7 步：**重启环境后的验证清单**
  （镜像重建确认、首次构建耗时、资产摘要校验、串行复跑基准、思考模式复核）与
  **DSpark 投机解码实验步骤**（原在分支合并时被 develop 一侧覆盖而丢失，回收时已按
  当前路径与基线口径适配）。
- 新增学习笔记 [`on-device-model-selection.md`](docs/notes/on-device-model-selection.md)、
  [`thinking-mode-and-token-budget.md`](docs/notes/thinking-mode-and-token-budget.md)
  （均由分支合并中丢失的 `docs/learning/` 篇目迁入改写）与
  [`conflict-resolution-and-branch-hygiene.md`](docs/notes/conflict-resolution-and-branch-hygiene.md)。
- 新增开发日志 [0011](docs/devlog/0011-2026-09-16-分支分叉与开发环境重建.md)。
- 新增 [`scripts/check-branch-hygiene.sh`](scripts/check-branch-hygiene.sh) 与 `make branch-status`：
  列出未合入 `develop` 的分支与开放 PR（只读、不使用凭据、无法判定时记为"未知"而非"干净"）。
- 新增 [ADR-0013](docs/adr/0013-branch-model-for-solo-dev.md)：分支模型改为 develop 主干 + 分支卫生自检。
- 新增开发日志 [0010](docs/devlog/0010-2026-09-15-动态硬件适配与分层Harness.md)。
- 新增 [ADR-0011](docs/adr/0011-tier-composition-revision.md)：档位构成修订为
  **S/M/L = 2B / 4B / 8B**，三档**全部可在当前云环境验证**（14B 移出档位矩阵）。
- 预置模型清单新增 **`MiniCPM5-2B-Q4_K_M`（1.56 GB，Apache-2.0）** 作为 S 档（对应移动端）。
- 新增研究笔记 [S/M/L 三档对比](docs/research/2026-09-15-tiers-s-m-l-comparison.md)
  与学习笔记 [evaluation-pitfalls.md](docs/notes/evaluation-pitfalls.md)。
- 新增 [ADR-0012](docs/adr/0012-benchmark-task-set-selection.md) 与
  [研究笔记](docs/research/2026-09-15-benchmark-selection.md)：基准任务集选型。
- **引入基准任务集**（镜像内 `/opt/benchmarks`）：HumanEval+（164）、MBPP+（378）、
  BigCodeBench v0.1.4（1140），合计约 23 MB，均为 Apache-2.0，**离线可跑且每题自带单元测试**。
- 参考资料新增 `harbor`（Terminal-Bench 团队的 agent 评估与优化框架，Apache-2.0）。

### Changed

- **沙箱方案**：由单一用户态命名空间隔离（bwrap/firejail）改为**分层抽象 + 能力探测 + fail-secure 降级**。
- SRS 定向修订至 **v0.1.1**：更新 `REQ-SEC-05` 验收标准、假设项 A-1/A-2、风险 R-4 与复用清单。
- 开发环境：以 `ms-pyright.pyright` 替换 Open VSX 上不存在的 `ms-python.vscode-pylance`；修正默认深色主题 ID 为 `Dark Modern`。
- **隔离能力表述由"档位"改为"逐维度"**：命名空间/mount 类有效、cgroup 类（`--memory`/`--pids-limit`）
  静默失效、setrlimit 类有效；资源限制一律改用 setrlimit。
- 测试环境按影响范围分层为 T0~T3；破坏性测试（T2）在一次性容器内执行，工作区只读挂载。
- **开发日志约定调整**：行数由"硬上限"改为**软性参考**（连贯优先）；
  新增「活待办」（最新一篇的 §7 即当前任务清单，不再另建 list）与「重拾语境」四步流程。
- **`CODEBUDDY.md` / `AGENTS.md`**：新增"维护学习笔记""维护活待办""重拾语境"三项强制义务。
- 澄清资源约束的准确边界：开发容器的 16 GiB / 8 核**被强制执行**；
  受限的是"无法给子容器设 cgroup 限制"（内置 docker 为 rootless + `Cgroup Driver: none`）。
- **"动态适配"收敛为可交付形态**：会话启动时探测一次 + 固定 S/M/L 三档预设 + 全过程留痕；
  明确**不做**运行中持续优化与自动调参（避免范围膨胀）。
- 明确硬件档位（S/M/L）与模型能力档位是**两个正交的轴**，不可互相推导。
- 明确硬件适配的目标范围：**笔记本与个人 PC**；移动端不做适配（仅保留架构约束）。
- **档位机制表述修正**（依据三档实测）：代价随档位成倍递增（内存 1:1.8:3.2、速度 1:0.61:0.35），
  而三项任务上 2B/4B/8B **几乎打平** ⇒ 档位应表述为"按资源预算选可承受的配置"，
  而非"更强硬件给更强模型"。
- **分支模型变更**（[ADR-0013](docs/adr/0013-branch-model-for-solo-dev.md)）：
  `develop` 改为**工作主干**、允许直接提交，仅 `main` 保持分支保护；
  短期分支 → `develop` 的合并方式默认改为 **merge commit**
  （保留 devlog / CHANGELOG 逐条引用的提交哈希）；
  分支卫生自检挂到开发环境启动与 push 流水线（**只报告，不阻断**）。

### Security

- 确立密钥零入库、最小权限、信任边界显式化等强制原则。
- 新增硬规则：**隔离是否生效必须由主动探针判定，禁止以命令退出码判定**（依据：`firejail` 静默失效的实测）。
- 新增硬规则：**降级必须显式记录，禁止静默降级**。

### Fixed

- 对齐 pre-commit 钩子版本至 `uv.lock` 锁定版本（ruff / mypy / commitizen / bandit），
  消除"钩子绿、`make check` 红"的版本分叉；并写明版本对齐规则。
- 修正 `docs/README.md` 文档地图中 devlog 的失效链接。
- **修正开发环境镜像构建失败**：`.ide/fetch-assets.sh` 以"脚本所在目录 + `assets/`"解析清单，
  而 Dockerfile 把脚本与清单平铺进同一目录，构建在第 17 步以"找不到清单文件"中止。
  镜像内改为复刻仓库 `.ide/` 的目录结构（脚本 `/tmp/ide/`、清单 `/tmp/ide/assets/`）。
- **恢复 llama.cpp 版本固定**（原 V-6 修复在分支合并时被 develop 一侧覆盖而丢失）：
  由"跟随 master"改回固定 commit `69eb250`，保证性能基线与 W1 / 三档实测可比。

---

## 版本记录说明

- `Added` 新增功能
- `Changed` 行为变更
- `Deprecated` 即将移除
- `Removed` 已移除
- `Fixed` 缺陷修复
- `Security` 安全相关修复与加固

[Unreleased]: https://cnb.cool/Mybase_Le0n3rd/software_engineering/-/compare/main...develop
