# Git 工作流

> 本文是该主题的**权威定义**。Agent 侧摘要见 [`.codebuddy/rules/git-workflow/`](../../.codebuddy/rules/git-workflow/)。
> 修改本文即修改流程，需同步更新摘要并记录 ADR。

---

## 1. 设计目标

| 目标 | 具体要求 |
| --- | --- |
| 可追溯 | 每个变更都能回答：为什么做、做了什么、怎么验证、如何回滚 |
| 可回滚 | 任何一次发布都能被精确定位与安全回退 |
| 线性可读 | 主干历史易于阅读，避免"合并地狱" |
| 门禁自动 | 质量与安全约束由机器执行，不依赖人的自觉 |
| 适配单人 | 流程必须能在"一个人既是开发者又是评审者"的前提下仍然成立 |

> **单人项目的最大风险是流程退化**——因为没有同事盯着，很容易演变成"直接推 main"。
> 本工作流的每一条规则都是为了对抗这种退化：把"评审"和"门禁"从**人际约束**转移为**机器约束**。

---

## 2. 分支模型

采用**简化 Git Flow**（主干 + 集成 + 短期分支），理由见 [`../adr/0002-adopt-git-workflow.md`](../adr/0002-adopt-git-workflow.md)。

```text
        ┌─────────────────────────────────────────────┐
        │  main        稳定发布线（保护）              │
        │   ▲                                          │
        │   │ merge commit（发布节点，可打标签）        │
        │   │                                          │
        │  develop     工作主干（可直推）               │
        │   ▲   ▲   ▲                                  │
        │   │   │   │ squash merge                     │
        │  feat/* fix/* perf/* security/* docs/* ...   │
        └─────────────────────────────────────────────┘
```

| 分支 | 来源 | 合入 | 合并方式 | 生命周期 |
| --- | --- | --- | --- | --- |
| `main` | — | — | — | 永久 |
| `develop` | `main` | `main`（发布时） | merge commit | 永久 |
| `feat/<issue>-<slug>` | `develop` | `develop` | merge commit（默认） | 短期 |
| `fix/<issue>-<slug>` | `develop` | `develop` | merge commit（默认） | 短期 |
| `perf/<issue>-<slug>` | `develop` | `develop` | merge commit（默认） | 短期 |
| `security/<issue>-<slug>` | `develop` | `develop` | merge commit（默认） | 短期 |
| `docs/<slug>` | `develop` | `develop` | merge commit（默认） | 短期 |
| `chore/<slug>` | `develop` | `develop` | merge commit（默认） | 短期 |
| `exp/<slug>` | `develop` | — | 归档结论后关闭 | 短期 |
| `release/<version>` | `develop` | `main` | merge commit | 短期 |
| `hotfix/<version>` | `main` | `main` + 回合 `develop` | merge commit | 短期 |
| `bench/nightly` | `develop` | **不合入** | — | **长驻**（例外，见下） |
| `bench/data` | `bench/nightly`（CI 推入） | **不合入** | CI 快进推送，**禁止 force** | **长驻**（例外，见下） |

> **表下注（合并方式）**
>
> - 标"**（默认）**"的行：短期分支合入 `develop` **默认用 merge commit、不用 squash**
>   ——devlog 与 CHANGELOG 会逐条引用具体提交哈希，squash 会让这些引用指向不存在的对象。
>   唯一的例外是**只有单个提交、且没有任何文档引用它**的分支，此时可用 squash。
> - 其余行（`develop → main`、`release/*`、`hotfix/*`）的 merge commit 是该分支的
>   **规定动作**，**不适用**上述 squash 例外。
> - 依据：[ADR-0013 §5.5](../adr/0013-branch-model-for-solo-dev.md)；完整策略见本文 §4「合并策略」。

### 命名规则

- 全小写，单词用 `-` 连接；禁止中文、空格、下划线。
- 必须携带 Issue 编号（`docs/*`、`chore/*` 可省略）。
- 示例：`security/42-tool-call-policy`、`perf/58-scheduler-p95`、`feat/61-capability-model`。

### 关于 `exp/*`（探索性分支）

本项目带有研究性质，必须允许"探索失败"。约定：

- `exp/*` 分支**允许不产生可合并代码**；
- 但必须把**结论**（成功或失败）归档到 `docs/research/`，并关闭对应 Issue；
- 没有归档结论的 `exp/*` 分支视为任务未完成，**不得**直接删除分支了事。

### 关于 `bench/*`（基准长驻分支，**例外**）

`bench/nightly` 与 `bench/data` 是本工作流里**唯一的两条长驻非主干分支**，
依据 [ADR-0014 §2.1](../adr/0014-benchmark-automation.md) 建立，
例外条件登记在 [ADR-0013 §9](../adr/0013-branch-model-for-solo-dev.md)。要点：

- **它们不合入 `develop`，也不得删除**——这与 §2 表中其他分支的"短期 + 合回"规则**相反**，
  是刻意的例外，不是遗漏；
- `bench/data`（机器产出：JSON / 报告 / 日志 / 模型产物）**只由 CI 机器人写入**，
  以快进推送更新、**禁止 force**，来源分支白名单为 `bench/nightly`；**人手不得直接提交**；
- `bench/nightly`（基准代码）**不回写代码到 `develop`**；要进产品线的改动另开普通提交；
- **例外不外溢**：新增任何长驻分支都必须先新增 ADR 说明理由；
  其余分支仍严格遵守 §2 的"当次会话合回 `develop`"。

> 背景：[ADR-0014 §2.1](../adr/0014-benchmark-automation.md) 的三条理由 ——
> `crontab` 只支持单一明确分支名；定时任务取**该分支 HEAD 的代码**，故测试代码必须与
> 日常开发分支分离（协议一变序列即断）；数据分支不被任何事件触发，避免
> "CI 推数据 → 又触发一轮测试"的死循环。

### 摘要与完整文档的分工（口径）

本主题有**两份载体**：本文件（**权威定义**）与
[`.codebuddy/rules/git-workflow/RULE.mdc`](../../.codebuddy/rules/git-workflow/RULE.mdc)
（**常驻摘要**——每次会话自动加载，因此必须短）。

摘要**允许**省略内容，但省略必须满足同一条判据：

> **省略后，按摘要里的默认规则推导出的动作仍然正确——才允许省略。**
> 若省略会推导出**相反的错误动作**，则该内容**必须**出现在摘要里。

按这条判据逐项判定：

| 内容 | 摘要中是否必须出现 | 理由 |
| --- | --- | --- |
| `feat/*` `fix/*` `perf/*` `security/*` `docs/*` `chore/*` | ✅ 出现 | 日常最常用；且它们的默认行为就是"合回 `develop`" |
| `release/*` / `hotfix/*` 的**流程细节** | ❌ 可省略（摘要只留一行"发布相关"） | 它们最终**仍会合回主干**，与摘要的默认叙述**方向一致**；省略只是少细节，不会推出反向动作 |
| **`bench/*` 的例外** | ✅ **必须出现（一行）** | 摘要的默认叙述是"短期分支 → 合回 `develop`"，而 `bench/nightly`、`bench/data` 恰恰**不合回、不得删除**；省略会让默认推导得出**相反且具破坏性**的动作（把机器产出合进主干 / 删掉唯一的可比时间序列）。这正是 A-14 一类事故的机制 |
| 完整分支矩阵、合并策略表、PR 与发布全流程、远端授权细则 | ❌ 不在摘要 | 完整定义以**本文**与 [ADR-0013](../adr/0013-branch-model-for-solo-dev.md) 为准 |

**摘要的定位**：只写"**日常会用到 + 省略会误导**"两类内容。它**不是本文件的副本**，
也**不是第二权威源**；两份载体出现分歧时，**一律以本文件与 ADR-0013 为准**。

> 这条口径是为将来的"**跨文件一致性机器检查**"准备的
> （见 [devlog 0013 §7](../devlog/0013-2026-09-18-一致性核查与地基修复.md) 优先级 D）：
> 检查器必须知道"摘要**可以**省略什么"，否则会把**有意的省略**报成漂移。
> ADR-0013 §7 的验证条目曾经"写进文档但从未执行"（见该 devlog §5），
> 同一类错误（把约定写成文字就以为它生效了）不应重复。

---

## 3. 提交规范

采用 [Conventional Commits 1.0.0](https://www.conventionalcommits.org/zh-hans/v1.0.0/)。

```text
<type>(<scope>): <subject>

<body>

<footer>
```

| 字段 | 规则 |
| --- | --- |
| `type` | `feat` `fix` `docs` `refactor` `perf` `test` `build` `ci` `chore` `revert`（**`security` 不是 type**，见下表注） |
| `scope` | 模块名（英文小写），如 `kernel` `sandbox` `policy` `bench` `ci` `docs`；可选但推荐 |
| `subject` | 祈使句、小写开头、结尾不加句号、≤ 72 字符 |
| `body` | 说明**动机与影响**，而非复述 diff；每行 ≤ 100 字符 |
| `footer` | `Refs: #12` / `Closes: #12` / `BREAKING CHANGE: <说明>` |

> **表注（`security` 为什么不在 `type` 里 · 2026-09-18 更正，依据 devlog 0014 §5 的 A-16）**：
> `type` 的**合法集合不由此表决定**，而是由**门禁实际接受的集合**决定——
> `.pre-commit-config.yaml` 的 `commitizen` 钩子（`cz check`）用的是 `cz_conventional_commits`
> 插件，其类型集**硬编码**在 `commitizen/cz/conventional_commits/conventional_commits.py`
> 的 `schema_pattern()` 里，**不含 `security`**，且**无法通过 `pyproject.toml` 配置扩展**。
> 本表原先列出 `security` ⇒ 按本表写 `security(ci): …` 会被门禁直接拒绝（实测 `exit code 14`）。
>
> **安全类改动怎么写**（语义一点不丢，三处承载）：
>
> | 承载位置 | 写法 |
> | --- | --- |
> | 提交 `type` + `scope` | **`fix(security): …`**（缺陷类加固）或 **`feat(security): …`**（新增安全能力） |
> | 分支名 | `security/<issue>-<slug>`（**不变**） |
> | CHANGELOG | `### Security` 段落（人工维护，`CHANGELOG.md` 头部已说明 `[Unreleased]` 可人工补充） |
>
> 门禁实际接受的 type 集由 `tests/unit/test_commit_message_contract.py` **逐一实测钉住**
> （文档列出的每个 type 都必须被门禁接受；`security` 必须被拒绝）——本表与门禁**不得再各写一份**。

**破坏性变更**：`type!:` 或在 footer 写 `BREAKING CHANGE:`。触发次版本 → 主版本提升。

**好的示例**：

```text
perf(scheduler): 用优先级队列替换 FIFO 调度

FIFO 在高并发短任务场景下 P95 延迟劣化明显。改为多级反馈队列后，
在 32 并发基准下 P95 从 412ms 降至 168ms，吞吐提升 1.6x。

基准数据：reports/bench/2026-10-02-scheduler.md
Refs: #58
```

**反例**：`update code`、`fix bug`、`修改`、`WIP`、没有任何信息的 `feat: 优化`。

**校验**：`make commit-check`；`pre-commit` 的 `commit-msg` 钩子会自动拦截不规范提交。

> **禁止** `--no-verify` 跳过钩子。确需跳过的场景必须有 ADR 记录理由。

---

## 4. Pull Request

### 强制要求

1. 套用 [PR 模板](../../.cnb/pull_request_template.md)，**完整**填写（含"未完整填写不予合并"的前提）。
2. 关联 Issue，并逐条对照验收标准。
3. CI 门禁全绿（`.cnb.yml`）。
4. 满足 [`definition-of-done.md`](definition-of-done.md) 全部条目。
5. 不含密钥、真实数据、大体积二进制文件。

### 自评审（Self-Review）

单人项目中，评审分两个互补手段：

| 手段 | 作用 | 做法 |
| --- | --- | --- |
| **自评审** | 强制切换视角，发现"作者盲区" | 提交前逐条走 PR 模板第 10 节清单；隔一段时间后再读一次 diff |
| **AI 辅助评审** | 补充机械性检查与模式识别 | 让 AI 以"评审者"身份审查 diff，重点看安全边界与错误处理 |

> 注意：AI 评审**不能替代**自评审，也**不能**作为合并的唯一依据。AI 的结论同样需要人工判断。

### 远端操作授权（**按可回退性分级**）

远程写入**不再"一刀切"**——授权级别由**操作自身的可回退性**决定（判据见下），
分级集合为 **A~F**（决策与论证见 [ADR-0016](../adr/0016-cnb-platform-integration-and-remote-write-authorization.md) §5.1/§5.2）：

| 类 | 范围 | 规则 |
| --- | --- | --- |
| **A** | `develop` / 短期分支上的**代码·文档·测试·配置** | **推送后立即报告** |
| **B** | `main` | **事先批准** |
| **C** | **凭据入库**（`*.env` / `*.key` / token / 证书） | **事先拦截 + 禁止** |
| **D** | **强推 / 改写已推送历史 / 删远端分支** | **禁止** |
| **E** | `bench/data` | **只由 CI 写**（人手不得直接提交） |
| **F** | **新增依赖 / 改 CI 门禁强度 / 规则本身** | **事先确认** |

> **A~F 分级集合必须在 5 处口径一致**：[`CODEBUDDY.md`](../../CODEBUDDY.md) §2、
> [`AGENTS.md`](../../AGENTS.md) §2、[`.codebuddy/rules/git-workflow/RULE.mdc`](../../.codebuddy/rules/git-workflow/RULE.mdc)、
> 本文 §4、[`CONTRIBUTING.md`](../../CONTRIBUTING.md) §8。
> 三者**定位不同**（本文是**权威源**、`.codebuddy/rules/*` 是**常驻摘要**、
> `CODEBUDDY.md` / `AGENTS.md` 是**常驻结论**），**不是互为副本**，
> 但**分级集合必须逐项一致**；出现分歧时以本文与 ADR-0016 为准。

**A 类的附加不变量（不变）**：`make check` 全绿是推送前的**必要条件**；
**事后报告不得替代门禁**，也不得替代上文"提交粒度单一、可回滚"的要求。

#### 可回退性判据（分级依据）

> 一个**已推送**的变更属于「**可回退**」，当且仅当同时满足三条：
> **(1)** 存在一条**语义等价**的逆操作（`git revert` 或等价手段）；
> **(2)** 逆操作的代价与**一次普通提交**同量级；
> **(3)** 逆操作后，该内容**不残留于任何其它可达载体**（远端对象库、平台缓存、他人克隆/fork、CI 产物）。

逐类对照（依据 ADR-0016 §5.2）：

| 类别 | (1) 语义等价逆操作 | (2) 一次提交量级 | (3) 无其它载体残留 | 结论 |
| --- | --- | --- | --- | --- |
| A · 代码/文档/测试/配置 | ✅ | ✅ 一次 `revert` | ✅（内容不机密，残留不构成损失） | **可回退** ⇒ 事后报告足够 |
| C · 凭据类 | ✅ | ✅ | ❌ revert 只在新提交里删除它，远端历史 / 平台缓存 / 他人 fork 均留存 | **不可回退** ⇒ 必须事先拦截 |
| D · 改写已推送历史 | ❌ 逆操作不存在 | — | — | **不可回退** |
| B · `main` | ✅ | ❌ 撤销会改变"已发布"语义 | — | 不列为可回退 ⇒ 事先批准 |
| E · `bench/data` | ❌ 禁止 force，只能前进 | — | ❌ 坏点永久污染"同签名序列" | 不适用 ⇒ 只由 CI 写 |
| F · 依赖 / 门禁强度 / 规则 | — | — | — | 不是 git 操作 ⇒ 独立规则（事先确认） |

#### 事后报告的载体与格式（**仅 A 类**）

1. **落点**：本次推送所在会话的 `docs/devlog/` **当前篇**新增一条记录，含四项：
   **分支名 / 提交列表（`A..B`）/ 文件清单 / 验证方式（`make check` 结果）**；
2. **时点**：推送后**同一会话内立即**落盘；**不得晚于下一次远端写入**；
3. **可核对性**：拿报告里的提交列表与 `git log` 实际区间比对——
   **报告与仓库不一致即视为未报告**；
4. **回退**：所有者不同意时用 `git revert`（A 类永远是"一次提交量级"的操作）。

#### 已知边界（**不得省略，也不得表述为机制**）

> **本规则不可由 CI 强制**：CI 看不到"是否事先批准"，也看不到"是否事后报告"
> ⇒ A 类规则的**唯一护栏是事后可核对**，属"流程纪律"而非"机制"。
> 按威胁模型的口径（`docs/design/threat-model/README.md` §4.1），
> 这类缓解只能记为**部分缓解**，**不得**表述为"已被机制保障"/"已由 CI 强制"。
> 若将来出现"A 类推送但无报告"或"报告与仓库不一致"，应回到"事先批准"或补机器检查，
> 并**新增 ADR**（ADR 只增不改）。

#### 本地操作与不可回退操作

| 操作 | 是否需要事先批准 | 说明 |
| --- | --- | --- |
| 在本地短期分支上 `add` / `commit` | 不需要 | 但必须在独立分支上，且提交粒度单一、可回滚 |
| 在 `develop` 上直接提交 | 不需要批准 | `develop` 已改为工作主干（[ADR-0013](../adr/0013-branch-model-for-solo-dev.md)），但仍需 Conventional Commits、pre-commit 全绿与 `make check` |
| 合并到 `main`（即使仅本地） | **需要** | `main` 是受保护的发布线（B 类） |
| 强推 / 改写已推送历史 / 删除远端分支 | **不允许** | 无论是否批准（D 类） |

**对 AI 代理的要求**：

1. **A 类**：推送**后**立即按上述格式在 `docs/devlog/` 当前篇落报告；
   `make check` 全绿仍是推送前的必要条件。
2. **B ~ F 类**：**仍须事先批准 / 确认**，**禁止**把 A 类的"事后报告"外推——
   `main` 事先批准；凭据入库禁止；强推/改写已推送历史/删远端分支禁止；
   `bench/data` 只由 CI 写；新增依赖/改门禁强度/改规则本身事先确认。
3. 代理**不得**自行执行 `git reset --hard`、改写已推送历史、强推、删除远端分支等破坏性操作。

### 合并策略

| 方向 | 策略 | 理由 |
| --- | --- | --- |
| 短期分支 → `develop` | **Merge commit**（默认） | 保留提交哈希：devlog / CHANGELOG 会引用它们，squash 会使引用失效 |
| 单提交且无文档引用的分支 → `develop` | 可 Squash merge | 无哈希引用时，线性历史更整洁 |
| `develop` → `main` | **Merge commit** | 保留发布节点，便于 `git describe` 与回溯 |
| `hotfix/*` → `main` | Merge commit + 回合 `develop` | 防止修复在集成线丢失 |

合并后**立即删除**已合并的源分支。

---

## 5. 版本与发布

### 版本号（SemVer 2.0.0）

- `MAJOR`：不兼容的接口/行为变更
- `MINOR`：向后兼容的新功能
- `PATCH`：向后兼容的缺陷修复
- 安全修复同样走 `PATCH`（除非同时含不兼容变更）

**Phase 0 ~ 首个稳定版之前**统一使用 `0.x.y`，此阶段 `MINOR` 可以包含不兼容变更，
但必须在 `CHANGELOG.md` 中显著标注。

### 发布流程

```bash
# 1. 从 develop 切出发布分支
git switch -c release/0.2.0 develop

# 2. 收敛版本号与变更日志（只做这些，不夹带功能）
make bump          # commitizen 生成版本号、CHANGELOG 与标签
# 或人工确认后：
#   uv run cz bump --dry-run

# 3. PR 合入 main（merge commit）
# 4. 在 main 上创建带注释标签
git tag -a v0.2.0 -m "Release v0.2.0"

# 5. 回合 main 到 develop，删除 release 分支
```

### 分支保护（平台侧设置）

在 CNB 仓库设置中**只对 `main` 启用**（`develop` 已改为工作主干、允许直推，
见 [ADR-0013](../adr/0013-branch-model-for-solo-dev.md)）：

- [x] 禁止直接推送（必须通过 PR）——**仅 `main`**
- [x] 要求状态检查通过（`ci` 流水线）
- [x] 禁止强推与删除
- [ ] （可选）要求至少 1 个评审批准

> 由于是单人项目，"评审批准"由自评审 + CI 门禁承担。
>
> **`develop` 为什么不再保护**：云开发环境每次从 `develop` 拉起、重启即清空上下文，
> 于是"工作留在短期分支上"等于"下次会话看不见"——2026-09-16 已因此丢过一批内容。
> 保护的收益（强制走 PR）小于它造成的分叉成本。防误提交的职责改由
> Conventional Commits、pre-commit 全量钩子、`make check` 与 CI 承担；
> **未合入分支**由 `make branch-status`（开发环境启动与 CI 均会执行）兜底。

---

## 6. 常见问题

**Q：一个人开发，还要走 PR，是不是形式主义？**

不是。PR 的价值不在"别人看"，而在：

1. **强制变更单元化**——一个 PR 一个目的，出问题能精确回滚；
2. **强制留下验证记录**——验收标准对照、性能数据、安全评估都有地方写；
3. **强制通过门禁**——CI 不绿就合不了，避免"先推上去再说"。

**Q：紧急修复也要走流程吗？**

`hotfix/*` 是流程的一部分，只是更短：可以直接从 `main` 切出、可减少文档要求，
但**仍必须**通过 CI 门禁与 `CHANGELOG` 记录。

**Q：实验结果不理想，分支白写了？**

把结论写进 `docs/research/` 就是有效产出。**"验证了某方案不可行"本身就是结论**，
它能防止以后重复踩坑；同时它也是项目"研究性"的直接证据。
