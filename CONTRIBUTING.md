# 贡献与协作规范

> 本项目为**单人独立开发**项目。"贡献者"在本项目中主要指**项目负责人本人**，
> 以及受其委托的自动化代理（AI Agent）。因此本规范的强制对象是**流程与产物本身**，
> 目的是保证即使只有一个人，也能形成可追溯、可评审、可复现的工程证据链。

---

## 1. 前置阅读

开始任何改动前，请先阅读：

- [`docs/engineering/git-workflow.md`](docs/engineering/git-workflow.md) —— 分支模型、提交规范、合并策略
- [`docs/engineering/definition-of-done.md`](docs/engineering/definition-of-done.md) —— 什么算"做完了"
- [`SECURITY.md`](SECURITY.md) —— 安全红线与漏洞流程
- [`CODEBUDDY.md`](CODEBUDDY.md) —— 与 AI 代理协作时的行为边界

---

## 2. 工作流总览

```text
Issue（需求/缺陷/研究任务）
   ↓  登记并编号，明确验收标准
分支（feat/* / fix/* / docs/* / exp/* / chore/*）
   ↓  小步提交，Conventional Commits
本地自检（make lint typecheck test security）
   ↓  必须全绿
Pull Request
   ↓  使用 PR 模板，关联 Issue，逐条勾选检查清单
评审（CI 门禁 + 自评审 + AI 辅助评审）
   ↓  不允许自评自合时绕过门禁
合并（**merge commit** 合入 develop；发布时合入 main 并打标签）
   ↓
CHANGELOG 更新 + 版本标签
```

> 单人项目最容易退化为"直接推 main"。本规范明确禁止这一做法，理由：**变更单元化 + 门禁自动化
> 是保证可回滚、可追溯的关键**，也是本项目"规范"属性的直接体现。

---

## 3. 提交规范（强制）

采用 [Conventional Commits 1.0.0](https://www.conventionalcommits.org/)：

```text
<type>(<scope>): <subject>

<body>

<footer>
```

- **type**：`feat` `fix` `docs` `refactor` `perf` `test` `build` `ci` `chore` `revert` `security`
- **scope**：模块名，如 `kernel` `sandbox` `policy` `bench` `ci` `docs`
- **subject**：祈使句、小写开头、结尾不加句号、≤ 72 字符
- **破坏性变更**：`feat!:` 或在 footer 写 `BREAKING CHANGE: ...`
- **必须关联 Issue**：footer 写 `Refs: #12` 或 `Closes: #12`

提交信息由 `make commit-check`（基于 commitizen）自动校验，不符合规范将被拒绝。

示例：

```text
feat(sandbox): 增加基于 seccomp 的系统调用白名单

为工具执行引入最小权限过滤，降低逃逸面。

Refs: #7
```

---

## 4. 分支规范

| 分支 | 用途 | 生命周期 |
| --- | --- | --- |
| `main` | 稳定发布线，任何提交都应是可发布状态 | 永久 |
| `develop` | **工作主干**：下一版本的内容汇聚于此，**允许直接提交**（仅 `main` 受保护，见 [ADR-0013](docs/adr/0013-branch-model-for-solo-dev.md)） | 永久 |
| `feat/<issue>-<slug>` | 新功能 | 短期 |
| `fix/<issue>-<slug>` | 缺陷修复 | 短期 |
| `perf/<issue>-<slug>` | 性能优化 | 短期 |
| `security/<issue>-<slug>` | 安全加固（优先级最高） | 短期 |
| `docs/<slug>` | 文档 | 短期 |
| `exp/<slug>` | 探索性实验（**允许失败**，结论必须归档） | 短期 |
| `release/<version>` | 发布准备 | 短期 |
| `hotfix/<version>` | 线上紧急修复，可绕过 `develop` 直接进 `main` | 短期 |

**命名规则**：全小写，单词用 `-` 连接，禁止中文与空格。

> `exp/*` 是单人研究型项目的重要出口：允许"失败"的分支存在，但必须把**结论**回写到
> `docs/research/` 或 ADR，否则视为未完成。

---

## 5. Pull Request 规范

每个 PR 必须：

1. 使用 [PR 模板](.cnb/pull_request_template.md)，完整填写变更动机、方案、验证方式与风险。
2. 关联对应 Issue，并在描述中给出**验收标准的逐条对照**。
3. 通过全部 CI 门禁（见 `.cnb.yml`）：提交信息校验、静态检查、类型检查、测试、安全检查。
4. 满足 [`DoD`](docs/engineering/definition-of-done.md) 的全部条目。
5. **禁止**包含密钥、令牌、真实用户数据、大体积二进制文件。

### 自评审清单（Self-Review）

单人项目中，"评审"环节转换为**显式自评审 + AI 辅助评审**：

- [ ] 切换视角通读 diff：假设这是别人写的代码，能否在 5 分钟内理解意图？
- [ ] 变更是否最小化？是否存在顺手的无关重构？
- [ ] 新增逻辑是否都有对应测试？是否覆盖异常路径？
- [ ] 是否有新增的信任边界、外部输入、权限提升？若有，是否更新威胁模型？
- [ ] 是否有性能敏感路径？是否给出基准数据？
- [ ] 文档与 CHANGELOG 是否同步？

### 合并策略

- 功能分支 → `develop`：**merge commit**（**不用 squash**）——devlog 与 CHANGELOG 逐条引用
  具体提交哈希，squash 会让这些引用指向不存在的对象，**证据链断裂**。
  只有"该分支仅一个提交、且其哈希未被任何文档引用"时才可 squash。
  依据：[`docs/engineering/git-workflow.md`](docs/engineering/git-workflow.md) §3.3、【ADR-0013】。
- `develop` → `main`：**Merge commit**（保留发布节点，便于回溯）。
- 合并后立即删除已合并的源分支。

---

## 6. 代码与文档规范

- **语言**：代码、注释、标识符用英文；面向人的文档、Issue、PR、提交信息正文用中文（术语保留英文）。
- **格式**：统一由格式化工具决定，禁止手工争论风格（Python 使用 `ruff format`）。
- **类型**：公开接口必须有类型标注，且通过 `mypy` 检查。
- **注释**：解释"为什么"，不解释"是什么"；不写与代码重复的注释。
- **文档**：架构级改动必须附带 ADR；流程级改动必须更新 `docs/engineering/`。

---

## 7. 安全约束（红线）

以下行为**一律禁止**，违反即视为最高优先级事故：

1. 提交任何形式的密钥、口令、令牌、证书私钥（含 `.env`、`*.pem`、`*.key`）。
2. 在代码中硬编码生产环境地址、账号或内部系统信息。
3. 引入未审计来源的依赖、模型权重或二进制产物。
4. 关闭或绕过安全相关的检查（如 `# noqa` 绕过、删除安全检查步骤）而不留 ADR 记录。
5. 在测试中使用真实敏感数据代替合成/脱敏数据。

发现安全问题的处理流程见 [`SECURITY.md`](SECURITY.md)。

---

## 8. 需要 AI 代理协助时

本项目大量使用 AI 代理（CodeBuddy 等）作为实现与决策辅助。协作准则见
[`CODEBUDDY.md`](CODEBUDDY.md) 与 [`.codebuddy/rules/`](.codebuddy/rules/)。核心原则：

- 代理**不得**在未经确认的情况下引入新依赖、新服务或扩大权限。
- 代理产出的**每一个非平凡改动都必须有据可查**（引用文档/上游代码/基准数据），禁止臆测。
- 代理不得自行执行 `git push`、`git reset --hard`、改写历史等破坏性操作。
