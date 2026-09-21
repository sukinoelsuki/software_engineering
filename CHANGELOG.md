# 变更日志

本项目所有值得注意的变更都记录在此文件中。

格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本 2.0.0](https://semver.org/lang/zh-CN/)。

> 本文件版本段由记录员依据提交历史**手纂**（用户视角、不抄流水账）；`make changelog`（commitizen）在本仓库**实际不可用**：
> 无版本标签时无有效区间（`cz changelog <ver>` 报 “Could not find a valid revision range”）、
> `--incremental` 会按标签重建并覆盖已发布的 0.1.0/0.0.1 段落、默认模板还会漏掉 `docs`/`test`/`chore` 提交。
> **请勿手工调整已发布版本的条目**；`[Unreleased]` 段落可由人工补充说明。

---

## [Unreleased]

## [0.2.0] - 2026-09-21

> 本版以文档、研究与流程收敛为主，不引入新的代码层安全缓解；威胁模型文档有更新
> （T-08 陈旧陈述更正、新增提案 P-3/P-4），威胁状态以 `docs/design/threat-model/README.md` §4.1
> 为唯一真源。

### Added

- 新增产品使用与演示手册（现状报告 + 上手 + 演示 + 调试），并经独立复核更正三处呈现口径。
- 新增多步工具调用会话的端到端集成测试与审计回放断言（`tests/integration/test_end_to_end.py`）。
- 威胁模型新增提案 P-3/P-4（见 Security）。

### Changed

- 仓库远端改名为 `Native-Intelligent-Systems`：README 与 Issue 模板路径同步，CHANGELOG 比较链接更新为现名。
- 落地 ADR-0021 批准、M1 出口准则生效与 M2 准则，并建立 0.2.0 里程碑台账。

### Fixed

- 更正 M2 出口判据独立取证报告中的事实性失实：拒绝路径「落到 `ok=false`、会话继续」的误述，
  以及虚构的退出码机制（实为 `EXIT_OK..EXIT_UNEXPECTED` 加 `_EXIT_BY_STATUS`）。

### Security

- 威胁模型文档更新：更正 T-08 陈旧陈述，并新增提案 P-3/P-4（均为文档性变更，未引入新的代码层缓解）。

## [0.1.0] - 2026-09-20

> 本版不构成任何安全缓解；威胁模型状态不变（`docs/design/threat-model/README.md` §4.1 为唯一真源，本文不列具体计数）。

### Added

- 仓库内置两个**只读**示例领域包 `examples/packs/{coding-readonly,tech-manual-qna}`，示范领域包机制的正规通路（不配包时所有工具取 `DEFAULT_TOOL_RISK = HIGH`，非交互会话一个工具都执行不了）；并新增 `tests/unit/test_example_packs.py`（真实加载 + 只读边界反向断言）。

### Changed

- `README.md` 补齐「跑一次真实会话」：配置、`--pack`、退出码表、审计落点、四个不能省的参数，并刷新仓库结构说明。

### Fixed

- `make release` 无法落到台账最高一级：`tests/unit/test_release_policy.py` 对空序列取 `min()`；本次实测拦下 `0.1.0` 发布并按设计回滚，随后修复。
- 更正 `scripts/release.sh` 末段提示，使其与现行发布授权口径一致（合并到 `main` 恒为所有者动作；开 PR / 打标签 / 回合 `develop` 可由代理代执行）。

## [0.0.1] - 2026-09-19

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
- **新增基准自动化数据流水线**（[ADR-0014](docs/adr/0014-benchmark-automation.md) +
  [运行手册](docs/engineering/benchmark-automation.md)）：`make bench-round` 一轮跑三档
  × N 次重复并产出数据、报告与索引；`bench/nightly` 上挂三种触发（推送即时轮 /
  每日 04:00 夜轮 / 周一深跑 / 页面手动补跑），结果由 CI 发布到 `bench/data` 数据分支，
  **任意分支可 `git fetch` 取数**。
- 新增 `src/agent_sec_perf/bench/`（协议、夹具、隔离执行、客观判定、统计、schema 校验、
  报告）与对应单测；测量脚手架自此随仓库走，不再因环境重建而丢失。
- SRS 新增 `REQ-PERF-07`（基准自动化与可比时间序列）、`REQ-PERF-08`（测量纪律）。
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
- **完成开发环境第二轮"重建后验证"**（[post-build-checklist](docs/engineering/post-build-checklist.md) §6）：
  镜像侧三处修复（llama.cpp 固定 `69eb250`、`ms-pyright.pyright`、预置资产摘要）全部生效；
  三档基准复跑确认环境与基线可比（常驻内存三档逐位一致）。
- 新增 [ADR-0015](docs/adr/0015-layering-and-reuse-boundary.md)：分层模型
  （四层纵向 `UX → HARNESS → CAPABILITY → FOUNDATION` + 两横切层 `SEC`/`OBS`
  + 零行为契约层 `contracts/`）、复用组件清单（按 8 类给出，已核验项注明 PyPI 出处、
  未核验标【待核验】）与自研边界（5 项落到具体层 + 13 项不自研清单）；状态「提议中」，
  选定组件须所有者逐条确认。**回应 `CODEBUDDY.md` §9 两项待办（base project 选型冻结、
  目录结构与模块划分 ADR），并登记 A-1**。
- 新增 [`scripts/check-local-hooks.sh`](scripts/check-local-hooks.sh) 与 `make hooks-check`：
  **断言本地 git 钩子层真的已安装**（存在 / 可执行 / 是 pre-commit 生成的 / 指向本仓库配置 /
  钩子类型正确），并已成为 `make check` 的第一步。
- 新增 `make security-secrets`：复用 pre-commit 的 `detect-private-key` 做密钥扫描
  （纳入 `security` 聚合，本地与 CI 同一实现，不另写一套）。
- 新增 `tests/unit/test_commit_message_contract.py`：把"提交 `type` 集"的**文档 ↔ 门禁**
  一致性做成机器检查——5 处文档的类型集必须一致、文档列出的每个 `type` 必须被 `cz check`
  实测接受、`security` 必须被实测拒绝、门禁接受的完整类型集被钉住（关联一致性报告 A-16）。

### Changed

- **版本号改为「里程碑驱动」**（一致性报告 A-17 的处置；决策见
  [ADR-0019](docs/adr/0019-release-and-version-policy.md)）：版本号不再由提交历史推导，
  只能取 `pyproject.toml` 的 `[tool.lowspec.releases]` 台账里列出的里程碑值，
  由 `make release VERSION=x.y.z` 落定；`make bump` 改为**拒绝执行**（避免误把版本交给
  提交历史），并置 `major_version_zero = true` 作安全网（破坏性变更只动 MINOR，
  越不过 `0.x`）。**当前版本由 `0.1.0` 更正为 `0.0.0`**——按阶梯 `0.0.1` 才有判据
  （"框架搭完"），而框架尚未搭完。版本号的 4 处副本（`[project]` / `[tool.commitizen]` /
  包内 `__version__` / `uv.lock`）改由 `tests/unit/test_release_policy.py` 机器检查一致性。
- **`security` 不再是提交 `type`**（一致性报告 A-16，所有者裁决"文档对齐门禁"）：
  `type` 的合法集合**由门禁实际接受者决定**，而 commitizen 插件的类型集是硬编码的、
  不含 `security` 且无法通过配置扩展。5 处文档（`CONTRIBUTING.md` / `CODEBUDDY.md` /
  `AGENTS.md` / `git-workflow.md` §3 / `.codebuddy/rules/` 摘要）已同步为 10 个 `type`；
  安全类改动改用 **`fix(security): …` / `feat(security): …`**（`security` 放在 **scope** 上），
  分支名 `security/<issue>-<slug>` 与 CHANGELOG `### Security` 段落**不变**。
- **本地钩子安装改为显式开关 `LOCAL_HOOKS`**（默认 `1` = 安装并**断言**；`0` = 不安装、不断言，
  仅 CI 流水线使用）：原实现按 `$CI` 环境变量的**存在性**推断，而"云开发工作区"与"CI 流水线"
  在该变量上可能同值、语义却相反，导致本地钩子被静默跳过。`.cnb.yml` 的 `make setup` /
  `make check` 均显式写 `LOCAL_HOOKS=0`。
- **`make check` 增加"本地钩子层存在性"断言**（`hooks-check`，置于最前）并新增密钥扫描
  （`security-secrets`）；CI 的 `push` / `pull_request` 流水线各增一段**具名 `secret-scan` stage**。
- **`SECURITY.md` §3 新增红线 `S-8`**：声称已生效的缓解措施必须能被**实测**（钩子 / CI stage /
  探针必须可验证确实在运行），只有文档描述不算；`S-1` 的检查方式同步为三处可实测防线。
- **安全门禁 `bandit` 口径统一为"不读配置"**：`pre-commit` 去掉 `-c`、`pyproject.toml` 失效的 `[tool.bandit]`（`skips=["B101"]`）删除，两处门禁规则集一致；净效果因不再跳过 `B101` 而**更严**（`9804a0a`）。
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
- **基准判据的执行方式修正**：`> 10%` 判据**不得靠单次采样**执行——S 档 prefill 重复三次为
  108.69 / 117.77 / 116.16 tok/s（极差 8.4%，首次被单个请求的 95.11 tok/s 拉低）
  ⇒ 至少重复 3 次并报极差，且优先看**与存储/调度无关**的指标（常驻内存）。
- 明确 `settings.json` 的**实际生效文件也会被平台后置改写**部分键
  （`workbench.colorTheme`、`extensions.autoUpdate`）：**"改了文件" ≠ "设置生效"**，须逐项核对。
- **项目定位表述更新**（所有者 2026-09-19 指令）：定性由"软件工程综合实验"改为**单纯的工程实践项目**；
  **核心语义明确为「端侧模型能力探索 + 综合治理」**——能力探索回答"它能做什么"（能力边界探测与按边界适配），
  综合治理回答"如何安全地做"（策略、审批、沙箱、审计、资源约束）；**系统安全**与
  **模型/系统加速与优化**改为承载这两条核心问题的**技术主线**。
  同步位置：`README.md`（标题、项目简介、研究方向）、`CODEBUDDY.md` / `AGENTS.md` §1、
  `.codebuddy/rules/project-conventions/RULE.mdc`；并已按"反向搜索"清理 `docs/engineering/sdlc.md`
  与 `docs/requirements/srs.md` 的风险表里"课程"式表述。
  **不改**：`docs/devlog/`、`docs/research/`、`docs/proposals/` 与已接受 ADR 的**历史快照**（项目规则明列为例外）。

### Security

- 确立密钥零入库、最小权限、信任边界显式化等强制原则。
- 新增硬规则：**隔离是否生效必须由主动探针判定，禁止以命令退出码判定**（依据：`firejail` 静默失效的实测）。
- 新增硬规则：**降级必须显式记录，禁止静默降级**。
- **`T-02` 路径穿越的对抗性覆盖补强**：新增 7 例（4 种**前缀欺骗**——`/tmp/foo` vs `/tmp/foobar`，
  与 3 种"位于根内、文件名与根同前缀"的边界正确性），`tests/security/test_path_traversal_rejected.py`
  由 11 例增至 **18 例**。**变异验证**：把判据换成字符串前缀式后 4 个欺骗用例必红、3 个边界用例仍绿，
  恢复后 `git diff src/` 为空 ⇒ 断言精准且非恒过。**威胁状态未升级**（`T-02` 仍需"且留审计"半的载体
  `AuditSink`）。
- **修复常驻子进程凭据继承（T-08）**：`foundation/proc.py` 的 `spawn` 在 `env is None` 时由"继承 `os.environ`"改为最小环境（默认拒绝），调用点 `bench/runner.py` 显式传 `proc.minimal_env(...)`；`_isolated_env` 兼容别名删除。定性：实现向既有契约 `sandbox.md` §2.5「不得继承」收敛，**非接口语义变更**。验证：`tests/security/` 用例覆盖调用点实际收到的 env，含变异探针（默认改回继承 ⇒ 失败）。

### Fixed

- **修复"声称已生效的本地防线实际从未运行"的根因**（一致性报告 A-11 / 新增 A-15）：
  `make setup` 原先在 `CI=true` 时**静默跳过**钩子安装，而云开发工作区恰好带着 `CI=true`
  ⇒ `.git/hooks/` 长期为空，`.pre-commit-config.yaml` 声明的 `detect-private-key`
  与 commit-msg 校验从未运行，且**没有任何检查会因此失败**。现改为显式开关 + 可执行断言
  （见上方 Added / Changed），并追加 5 个"钩子被移除/被顶替"场景的回归用例
  （`tests/unit/test_local_gates.py`，用临时 git 仓库真实复现）。
- **修正隔离模式的静态守卫缺失**：新增 `tests/unit/test_isolation_mode_guard.py`，
  机器断言 `Makefile` 默认 `BENCH_ISOLATION ?= user`，且 `Makefile` 与 `.cnb.yml` 中
  **不得出现**把隔离选成 `root` 的写法（T-08 残余项；裁决见 `devlog 0014` §7）。
- **`make test-security` 在零 `security` 用例时 fail-secure**：原实现把 pytest 退出码 5（无匹配用例）当正常并 `exit 0`，安全测试层缺失或标记丢失时门禁假绿；现改为 `exit 1` 并说明原因（`a39ad48`，关联一致性报告 A-9）。`tests/security/` 现有 13 个用例，不会误伤。
- **修正基准数据发布的 refspec**：数据分支首次创建时必须用**全限定引用名**
  （`HEAD:refs/heads/bench/data`）——远端已存在带斜杠的分支时，短写法
  `HEAD:bench/data` 会被 git 拒绝（`not a full refname`），这正是首轮 push
  "流水线绿、但 `bench/data` 不存在"的原因。
- **去掉发布阶段的错误吞并**：`make bench-publish || echo "[warn] ..."`
  会把发布失败降级成警告，使构建**显示成功而数据一条也没有**；
  现在失败即把构建标红。
- **修正跨环境可比性**：比较签名加入机器标识（CPU 型号）并记入 `env.json`。
  实测同一份协议在 CI runner 与开发容器上的吞吐相差 25% 以上
  （S 档 prefill ≈83 vs ≈115 tok/s），不加区分会把两个环境的数据混成一条序列。
- **修正基准流水线的 stage 脚本在 dash 下失败**：脚本由镜像的 `/bin/sh` 执行，
  而 `set -euo pipefail` 是 bash 专有语法（Debian 12 的 dash 不支持），
  导致 `bench-push` 第一行即以退出码 2 中止；`.cnb.yml` 的脚本统一改为 `set -eu`。
- **把 CI 镜像由浮动标签改为钉住发行版**：`python:3.12` 已从 Debian 12 漂到 Debian 13
  （dash 版本随之变化，同一份配置因此在不同时间行为不同、并与开发镜像分叉），
  现统一为 `python:3.12-bookworm`。两条规则已写入 `.cnb.yml` 头部并由
  `tests/unit/test_cnb_config.py` 检查。
- 对齐 pre-commit 钩子版本至 `uv.lock` 锁定版本（ruff / mypy / commitizen / bandit），
  消除"钩子绿、`make check` 红"的版本分叉；并写明版本对齐规则。
- 修正 `docs/README.md` 文档地图中 devlog 的失效链接。
- **修正开发环境镜像构建失败**：`.ide/fetch-assets.sh` 以"脚本所在目录 + `assets/`"解析清单，
  而 Dockerfile 把脚本与清单平铺进同一目录，构建在第 17 步以"找不到清单文件"中止。
  镜像内改为复刻仓库 `.ide/` 的目录结构（脚本 `/tmp/ide/`、清单 `/tmp/ide/assets/`）。
- **恢复 llama.cpp 版本固定**（原 V-6 修复在分支合并时被 develop 一侧覆盖而丢失）：
  由"跟随 master"改回固定 commit `69eb250`，保证性能基线与 W1 / 三档实测可比。
- **修正开发环境镜像第 7 步构建失败**（llama.cpp 段）：版本断言误写成 `$$(git ...)`——
  `RUN` 由 shell 执行，`$$` 展开为 shell PID、命令替换不执行，字符串变为
  `"<PID>(git -C ... rev-parse HEAD)"`，断言恒定失败并以退出码 1 中止构建。
  改为 `$(git ...)`，并把该写法约束写进 Dockerfile 注释。

---

## 版本记录说明

- `Added` 新增功能
- `Changed` 行为变更
- `Deprecated` 即将移除
- `Removed` 已移除
- `Fixed` 缺陷修复
- `Security` 安全相关修复与加固

[0.2.0]: https://cnb.cool/Mybase_Le0n3rd/Native-Intelligent-Systems/-/compare/v0.1.0...v0.2.0
[Unreleased]: https://cnb.cool/Mybase_Le0n3rd/Native-Intelligent-Systems/-/compare/v0.2.0...develop
