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

### Security

- 确立密钥零入库、最小权限、信任边界显式化等强制原则。

---

## 版本记录说明

- `Added` 新增功能
- `Changed` 行为变更
- `Deprecated` 即将移除
- `Removed` 已移除
- `Fixed` 缺陷修复
- `Security` 安全相关修复与加固

[Unreleased]: https://cnb.cool/Mybase_Le0n3rd/software_engineering/-/compare/main...develop
