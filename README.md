# 智能体系统 · 安全与加速工程实践

> **项目代号：TBD** —— 待 base project 选型冻结后定名，见 [`docs/proposals/`](docs/proposals/)。

一个围绕**原生智能系统（Native Intelligent Systems）**的软件工程综合实验项目，主线为
**系统安全（System Security）** 与 **模型/系统加速与优化（Acceleration & Optimization）**。
项目在既有开源前沿项目之上做增量研发，最终交付一个**完整、规范、便捷、安全、高效**的软件系统。

| 项目信息 | 内容 |
| --- | --- |
| 归属 | 华中科技大学 · 计算机科学与技术学院 · 软件工程综合实验 |
| 组织形式 | 单人独立开发（决策、实现、评审均由本人完成，AI 作为实现与决策辅助） |
| 周期 | 2026-09 ~ 2026-12（约 2~3 个月，多迭代） |
| 仓库 | <https://cnb.cool/Mybase_Le0n3rd/software_engineering> |
| 当前阶段 | **Phase 0 · 工程初始化**（详见 [里程碑](docs/engineering/sdlc.md)） |

---

## 1. 项目简介

本项目不接受"写一个 demo 交作业"的定位，而是**按工业级软件工程流程**推进：

- **过程规范**：需求 → 设计 → 实现 → 测试 → 评审 → 发布的完整生命周期，每次决策留痕（ADR）。
- **版本规范**：SemVer + Conventional Commits + Keep a Changelog，分支模型与合并策略显式定义。
- **质量规范**：静态检查、类型检查、自动化测试、安全扫描、CI 门禁缺一不可。
- **安全规范**：威胁建模前置、最小权限、密钥零入库、供应链可追溯。

项目的技术落点（研究方向）将在 base project 选型后冻结，当前候选方向见
[`docs/proposals/0001-base-project-selection.md`](docs/proposals/0001-base-project-selection.md)。

---

## 2. 研究方向

三条相互支撑的主线：

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
├── .codebuddy/rules/        # 项目级 Agent 准则（随代码库版本化共享）
├── CODEBUDDY.md             # Agent 主准则（对话开始时自动加载）
├── AGENTS.md                # 面向其他 Agent 工具的等价准则
├── docs/
│   ├── adr/                 # 架构决策记录（Architecture Decision Records）
│   ├── design/              # 设计与架构文档
│   ├── engineering/         # 工程流程：Git 工作流、生命周期、DoD、测试策略
│   ├── proposals/           # 立项与选型提案
│   ├── requirements/        # 需求规格与用例
│   └── research/            # 前沿 AI 生态研究笔记
├── src/                     # 项目源码（base project 确定后落位）
├── tests/                   # 测试（单元 / 集成 / 安全 / 性能）
├── scripts/                 # 开发与运维脚本
├── CONTRIBUTING.md          # 贡献与协作规范
├── SECURITY.md              # 安全策略与漏洞披露流程
└── CHANGELOG.md             # 变更日志（Keep a Changelog）
```

> `src/` 与 `tests/` 的具体分层结构将在 base project 选型确定后，通过新的 ADR 定义。

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

---

## 8. 上游与致谢

本项目基于开源社区的前沿工作展开。选定的 base project 及其依赖将在选型冻结后列于此处，
并严格遵守其许可证要求（见 [`LICENSE`](LICENSE) 与相关 ADR）。

---

## 9. 许可

本项目采用 [Apache License 2.0](LICENSE) 授权。

> 注意：若 base project 的许可证要求衍生作品采用其他许可（如 GPL 系），本项目将按其要求调整，
> 相关变更将记录于 `docs/adr/`。
