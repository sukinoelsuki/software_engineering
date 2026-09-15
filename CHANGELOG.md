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

### Changed

- **沙箱方案**：由单一用户态命名空间隔离（bwrap/firejail）改为**分层抽象 + 能力探测 + fail-secure 降级**。
- SRS 定向修订至 **v0.1.1**：更新 `REQ-SEC-05` 验收标准、假设项 A-1/A-2、风险 R-4 与复用清单。
- 开发环境：以 `ms-pyright.pyright` 替换 Open VSX 上不存在的 `ms-python.vscode-pylance`；修正默认深色主题 ID 为 `Dark Modern`。
- **隔离能力表述由"档位"改为"逐维度"**：命名空间/mount 类有效、cgroup 类（`--memory`/`--pids-limit`）
  静默失效、setrlimit 类有效；资源限制一律改用 setrlimit。
- 测试环境按影响范围分层为 T0~T3；破坏性测试（T2）在一次性容器内执行，工作区只读挂载。

### Security

- 确立密钥零入库、最小权限、信任边界显式化等强制原则。
- 新增硬规则：**隔离是否生效必须由主动探针判定，禁止以命令退出码判定**（依据：`firejail` 静默失效的实测）。
- 新增硬规则：**降级必须显式记录，禁止静默降级**。

### Fixed

- 对齐 pre-commit 钩子版本至 `uv.lock` 锁定版本（ruff / mypy / commitizen / bandit），
  消除"钩子绿、`make check` 红"的版本分叉；并写明版本对齐规则。
- 修正 `docs/README.md` 文档地图中 devlog 的失效链接。

---

## 版本记录说明

- `Added` 新增功能
- `Changed` 行为变更
- `Deprecated` 即将移除
- `Removed` 已移除
- `Fixed` 缺陷修复
- `Security` 安全相关修复与加固

[Unreleased]: https://cnb.cool/Mybase_Le0n3rd/software_engineering/-/compare/main...develop
