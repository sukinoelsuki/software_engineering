# 端侧模型能力探索与综合治理

> **项目代号：TBD** —— 待 base project 选型冻结后定名，见 [`docs/proposals/`](docs/proposals/)。

一个以**端侧模型能力探索**与**综合治理**为核心的**工程实践**项目：

- **能力探索**回答"**它能做什么**"——能力受限的模型（4B/8B 级、CPU-only、无 GPU）在资源与网络受限的
  环境中，其**能力边界**在哪、如何**探测**它、如何按边界**适配**（提示分级、工具裁剪、上下文工程）；
- **综合治理**回答"**如何安全地做**"——用系统手段把它的行为约束在**授权与资源边界**内：
  能力边界判定、权限与审批门（default-deny）、沙箱执行与最小权限、审计与可回放、资源约束。

**系统安全**与**模型/系统加速与优化**是承载这两条核心问题的两条**主线**（前者是治理机制的载体，
后者是"受限资源下仍能可用"的前提），二者在设计上必须同时成立。
项目在既有开源前沿项目之上做增量研发，最终交付一个**完整、规范、便捷、安全、高效**的软件系统。

| 项目信息 | 内容 |
| --- | --- |
| 归属 | 华中科技大学 · 计算机科学与技术学院（工程实践项目） |
| 组织形式 | 单人独立开发（决策、实现、评审均由本人完成，AI 作为实现与决策辅助） |
| 周期 | 2026-09 ~ 2026-12（约 2~3 个月，多迭代） |
| 仓库 | <https://cnb.cool/Mybase_Le0n3rd/software_engineering> |
| 当前阶段 | **`M0`（工程初始化）已达成**（2026-09-19，判据 `G1`~`G9` 全绿）；当前在 **Phase 1（需求与设计）** 上向 **`0.1.0`「首个可用版本」**推进。里程碑判据见 [里程碑与迭代流程](docs/engineering/sdlc.md) §3 |
| 开发日志 | **最新一篇的 §7 就是当前唯一的任务清单** —— 见 [`docs/devlog/`](docs/devlog/) |

> ## 现在能跑什么（如实说明，别把它读成"已完成"）
>
> **已实现**：`contracts/`（6 模块）· `foundation/`（5 件）· `security/{capabilities,policy}` ·
> `observability/audit` · `model/client`（**仅本地回环**）· `tools/{registry,files,shell}` ·
> `harness/`（9 件）· `cli/`（3 件）· `examples/packs/`（示例领域包）。
>
> **未开工**：`security/sandbox/` · `security/refusal.py` · `model/{router,probe,assets}.py` ·
> `tools/search.py` · `observability/tracing.py`。逐项依据见
> [总体架构](docs/design/architecture.md) §4 的实现状态表。
>
> ⚠️ **两条限定不得放大**：
>
> 1. **"已实现" ≠ "已跑通"**：端到端闭环的实跑结果以 [`docs/devlog/`](docs/devlog/) 的记录为准；
> 2. **"能跑" ≠ "安全已到位"**：威胁模型（13 条）当前**只有 1 条**达到「已缓解并验证」，
>    分布与判据以 [`threat-model/README.md`](docs/design/threat-model/README.md) §4.1 为**唯一真源**。

---

## 1. 项目简介

本项目按**工业级工程流程**推进，不以"能跑就行"的演示型交付为目标：

- **过程规范**：需求 → 设计 → 实现 → 测试 → 评审 → 发布的完整生命周期，每次决策留痕（ADR）。
- **版本规范**：SemVer + Conventional Commits + Keep a Changelog，分支模型与合并策略显式定义。
- **质量规范**：静态检查、类型检查、自动化测试、安全扫描、CI 门禁缺一不可。
- **安全规范**：威胁建模前置、最小权限、密钥零入库、供应链可追溯。

当前形态为**低资源受限环境下的能力感知智能体运行时**（单专家调度 + 能力包）：
**能力探索**决定"它能做什么"，**综合治理**决定"它被允许做什么、以及凭什么被允许"。
完整需求见 [`docs/requirements/srs.md`](docs/requirements/srs.md)，
决策与论证过程见 [`docs/adr/`](docs/adr/) 与 [`docs/proposals/`](docs/proposals/)。

---

## 2. 研究方向

**两条核心问题**（本项目要回答的东西）：

1. **端侧模型能力探索**：能力受限的模型在受限环境中，能力边界在哪、怎么探测、怎么按边界适配；
2. **综合治理**：如何用系统手段（策略、审批、沙箱、审计、资源约束）把它的行为收在授权边界内。

下表的三条主线是承载这两条核心问题的**技术手段**，它们相互支撑：

| 主线 | 关注问题 | 典型产出 |
| --- | --- | --- |
| **原生智能系统** | 智能体作为一等公民的系统抽象：运行时、调度、上下文/记忆、工具与资源管理 | 系统架构、内核/运行时模块 |
| **系统安全** | 隔离与最小权限、指令与数据分离、工具与供应链可信、可审计与可回放 | 威胁模型、策略引擎、安全测试 |
| **加速与优化** | 调度与并发、缓存复用、内存与 I/O 效率、推理加速 | 基准测试、性能剖析、优化实现 |

**核心要求：安全与性能在设计上必须同时成立**（安全措施不得以牺牲可用性能为代价，反之亦然），
所有安全断言与性能断言都需要可复现的量化证据（见 [`docs/engineering/`](docs/engineering/)）。

---

## 3. 目标与非目标

### 目标

- 交付一个可安装、可运行、可测试、可观测、可审计的完整系统。
- 在选定的开源基线上做出**可被上游或第三方独立复现的实质增量**。
- 形成完整的工程证据链：需求、设计、威胁模型、测试报告、性能基准、发布记录。

### 非目标

- 不追求规模化的数据/算力实验；优先保证**系统工程质量**而非刷榜。
- 不做与主线无关的功能堆砌；超出范围的需求一律进入 backlog 并显式记录。

---

## 4. 仓库结构

```text
.
├── .cnb/                    # CNB 平台协作配置（Issue / PR 模板等）
├── .cnb.yml                 # CNB 云原生构建流水线（CI 门禁）
├── .codebuddy/              # Agent 准则（rules/）、角色定义（agents/）、流程型 Skill（skills/）
├── CODEBUDDY.md             # Agent 主准则（对话开始时自动加载）
├── AGENTS.md                # 面向其他 Agent 工具的等价准则
├── docs/
│   ├── adr/                 # 架构决策记录（Architecture Decision Records，**只增不改**）
│   ├── design/              # 总体架构 · 接口契约（interfaces/）· 威胁模型（threat-model/）
│   ├── devlog/              # 开发日志（按议题分篇；**活待办 = 最新篇 §7**）
│   ├── engineering/         # 工程流程：Git 工作流、生命周期、DoD、测试策略
│   ├── notes/               # 学习笔记（按主题累积，无证据不成条）
│   ├── proposals/           # 立项与选型提案
│   ├── requirements/        # 需求规格（SRS）
│   └── research/            # 研究结论与实验记录（含 reports/）
├── examples/                # 示例领域包（**声明式 TOML**，产品正规通路的一部分）
├── src/agent_sec_perf/      # 产品源码：contracts/ foundation/ security/ observability/ model/
│                            #            tools/ harness/ cli/（+ 独立评测子系统 bench/）
├── tests/                   # unit/（单测）· security/（对抗性用例）· integration/（真模型端到端，默认不跑）
├── scripts/                 # 开发与运维脚本
├── CONTRIBUTING.md          # 贡献与协作规范
├── SECURITY.md              # 安全策略与漏洞披露流程
└── CHANGELOG.md             # 变更日志（Keep a Changelog）
```

> `src/` 的分层、依赖方向（`R1`~`R5`）与逐模块实现状态以
> [`ADR-0015`](docs/adr/0015-layering-and-reuse-boundary.md) 与
> [`docs/design/architecture.md`](docs/design/architecture.md) 为准（**本文件不重复其结论**）。
> `tests/` 的分层口径与"默认不跑哪些"见
> [`docs/engineering/testing-strategy.md`](docs/engineering/testing-strategy.md)。

---

## 5. 快速开始

> **基线工具链假设**：Python ≥ 3.12 + [`uv`](https://docs.astral.sh/uv/) + `make`。
> 若最终 base project 采用其他主语言，本节与 CI 将同步调整（见 `docs/adr/`）。

```bash
# 1. 获取代码
git clone https://cnb.cool/Mybase_Le0n3rd/software_engineering.git
cd software_engineering

# 2. 安装开发环境（含提交规范、代码检查、测试依赖）
make setup

# 3. 常用命令
make lint        # 代码风格与静态检查
make typecheck   # 类型检查
make test        # 运行测试
make security    # 依赖与密钥安全检查
make help        # 查看全部可用命令
```

### 5.1 跑一次真实会话（产品用法）

> **前置**：一个 `llama-server` 可执行文件与一个本地 GGUF 模型（本开发环境已预置
> `/opt/llama.cpp/build/bin/llama-server` 与 `/opt/models/*.gguf`；其它机器请自备）。
> **前置**：一条命令即可安装 —— `uv sync` 会把 `agent-sec-perf` 装进虚拟环境
> （`[project.scripts]` 入口，`REQ-UX-03`）。

```bash
# ① 授予能力：default-deny 的默认授予是**空集**，所以"允许做什么"必须在配置里显式写出。
#    工作目录下的 .lowspec.toml（项目级；用户级为 ~/.config/lowspec/config.toml）。
cat > .lowspec.toml <<'EOF'
[policy]
granted_capabilities = ["read_file"]

[logging]
level = "INFO"
EOF

# ② 跑一次会话（本环境实测跑通的一整套参数，证据见
#    docs/research/2026-09-20-v0.1.0-e2e-evidence.md：exit 0 / 6 条事件 / 审计可回放 / 197 s）
uv run agent-sec-perf run \
  "请读取工作目录下的 hello.txt，把内容原样一字不差地作为最终回答返回；必须先调用 read_file 工具，不能凭猜测编造" \
  --pack examples/packs/coding-readonly \
  --model-path /opt/models/Qwen3-4B-Q4_K_M.gguf \
  --model-request-timeout-s 600 \
  --max-completion-tokens 1536
```

**四个参数各自为什么不能省**（省了会以"看起来像模型不行"的方式失败）：

| 参数 | 不省的后果 |
| --- | --- |
| `--pack <目录>` | 不配领域包时，**所有**工具取保守默认 `DEFAULT_TOOL_RISK = HIGH` ⇒ 每次调用都需人工确认；非交互模式没有确认通路 ⇒ 一律拒绝（fail-secure）⇒ **一个工具都执行不了** |
| `[policy] granted_capabilities` | default-deny：默认**什么都不授予**；领域包只能在授予集合上做**收窄**（`∩`），不能替你授予 |
| `--max-completion-tokens` | **给少了任务直接失败**：`Qwen3-4B` 是思考模型，预算不足时会把 token 全耗在推理上、**一个工具调用都不发** ⇒ `content` 与 `tool_calls` 皆空 ⇒ 客户端按契约抛 `ModelProtocolError` ⇒ 任务 `FAILED`。**实测**：本配方 `384` 失败、`1536` 通过（`docs/research/2026-09-20-v0.1.0-e2e-evidence.md` §1.1）。换模型要**重新实测**这个预算，不要照抄 |
| `--model-request-timeout-s` | 弱硬件生成速度实测约 **3.4 tok/s**：预算放大后一次补全可达 **450 s 以上**，会超过协议默认的 `60 s` ⇒ 请求超时 ⇒ 重试耗尽 ⇒ 任务 `FAILED` |

其它常用开关：`--interactive`（需要 TTY，启用人工确认通路）、`--output-format json`
（stdout 只出 JSONL，可脚本化）、`--working-dir` / `--allowed-root`（会话可访问的根，省略即只允许工作目录）。

**运行完看什么**：

- **退出码**：`0` 完成 · `1` 任务失败 · `2` 达到步数上限 · `3` 装配/配置故障 · `4` 审计写入失败 · `5` 其它未预期异常；
- **审计**：默认落 `[audit] directory`（用户状态目录下的 `audit/`，**唯一允许的根**，常量不接受配置指定），一行一个 JSON 事件、只追加，可回放（`REQ-SEC-06`）。

> ⚠️ **示例领域包不是安全默认的替代品**，它只是"产品正规通路"的一个可读示例；
> 领域包机制本身见 [`examples/README.md`](examples/README.md) 与
> [`docs/design/interfaces/harness.md`](docs/design/interfaces/harness.md) §4。

---

## 6. 工程规范

所有开发行为受以下规范约束，**提交前请确认符合 [`docs/engineering/definition-of-done.md`](docs/engineering/definition-of-done.md)**：

| 规范 | 位置 |
| --- | --- |
| Git 分支模型与提交规范 | [`docs/engineering/git-workflow.md`](docs/engineering/git-workflow.md) |
| 软件生命周期与迭代流程 | [`docs/engineering/sdlc.md`](docs/engineering/sdlc.md) |
| 完成定义（DoD） | [`docs/engineering/definition-of-done.md`](docs/engineering/definition-of-done.md) |
| 测试策略 | [`docs/engineering/testing-strategy.md`](docs/engineering/testing-strategy.md) |
| 安全策略 | [`SECURITY.md`](SECURITY.md) |
| 贡献与评审流程 | [`CONTRIBUTING.md`](CONTRIBUTING.md) |
| Agent 协作准则 | [`CODEBUDDY.md`](CODEBUDDY.md) |

---

## 7. 文档地图

| 目录 | 用途 |
| --- | --- |
| [`docs/adr/`](docs/adr/) | **决策留痕**：每个不可逆或影响面大的技术决策一份记录 |
| [`docs/proposals/`](docs/proposals/) | 立项/选型提案与论证 |
| [`docs/requirements/`](docs/requirements/) | 需求规格说明书（SRS）与用例 |
| [`docs/design/`](docs/design/) | 架构设计、模块设计、威胁模型、接口契约 |
| [`docs/engineering/`](docs/engineering/) | 过程规范（工作流、生命周期、DoD、测试） |
| [`docs/research/`](docs/research/) | 前沿 AI 生态调研与论文笔记 |
| [`docs/devlog/`](docs/devlog/) | **开发日志**（按议题/阶段分篇，过程记录） |
| [`docs/engineering/post-build-checklist.md`](docs/engineering/post-build-checklist.md) | **构建后待办清单**（下一步行动） |

---

## 8. 上游与致谢

本项目基于开源社区的前沿工作展开。选定的 base project 及其依赖将在选型冻结后列于此处，
并严格遵守其许可证要求（见 [`LICENSE`](LICENSE) 与相关 ADR）。

---

## 9. 许可

本项目采用 [Apache License 2.0](LICENSE) 授权。

> 注意：若 base project 的许可证要求衍生作品采用其他许可（如 GPL 系），本项目将按其要求调整，
> 相关变更将记录于 `docs/adr/`。
