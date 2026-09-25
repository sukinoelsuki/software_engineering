# 0028. 跑测直接跑在 `develop` 上（取消 `test/<slug>` 环境分支）

- **状态**：已接受
- **日期**：2026-09-25
- **决策者**：Le0n3rd（2026-09-25 重估："我们是不是只需要在 `develop` 上进行不就行了……
  `test` 分支属于是我们之前认为不能用 CI 的遗物了"）／AI 代理（论证与落地）
- **相关**：
  **取代** [ADR-0027](0027-test-branch-ref-must-track-the-commit-under-test.md)（环境分支引用快进）
  与 [ADR-0025](0025-benchmark-automation-moves-to-dev-bucket.md) **§2.2 的"按分支名分派机器"**
  （两处正文按"只增不改"保留，指针见 §8）；
  [ADR-0013](0013-branch-model-for-solo-dev.md)（分支模型）、
  [ADR-0026](0026-benchmark-round-directory-keyed-by-round-id.md)（数据落盘）、
  [ADR-0023](0023-ci-downgrade-to-manual-trigger.md)（四道闸门**不变**）；
  证据：`trigger-rule.md` / `grammar.md`（2026-09-25 访问）、
  [`research/2026-09-25-cnb-platform-behavior-facts.md`](../research/2026-09-25-cnb-platform-behavior-facts.md)

---

## 1. 背景与问题

ADR-0025 §2.2 把机器规格**按分支名分派**：`.cnb.yml` 用精确分支键 `test/amd64-8:` 声明
`runner.tags` / `runner.cpus`，触发时用 `--branch test/amd64-8`，好处是"分支上零提交、配置集中写在 `develop`"。

这套设计接着带出两处成本：

| # | 成本 | 实测后果 |
| --- | --- | --- |
| 1 | **`test/<slug>` 零提交 ⇒ 它的引用不会自己前进** | `test/amd64-8` 停在落后 `develop` 3 笔的提交上；不察觉就会"**用旧代码测新修复**"，而日志 / 流水线状态 / 报告**全都看不出区别** |
| 2 | 于是需要**额外机制**补上新鲜度 | [ADR-0027](0027-test-branch-ref-must-track-the-commit-under-test.md)（触发前快进引用）+ 候选硬化（流水线内断言） |

而 `test/<slug>` 这套东西的**来由**是"当年以为不能用 CI"：借一台环境、在里面手动 `make bench`。
`ADR-0025` 之后跑测**本来就是自动化触发**的，环境由平台按配置拉起 ⇒ 这条分支**不再承担任何能力**。

## 2. 决策驱动因素

| 因素 | 说明 | 类型 |
| --- | --- | --- |
| **消除失效模式 > 管理失效模式** | "引用陈旧"是**静默**失败；能不引入就不引入 | **硬约束** |
| 配置集中写在 `develop` | ADR-0025 立这条时的**目的**（配置不随分支走） | 硬约束 |
| 保留多机器 / 多架构能力 | arm64 是端侧目标平台（ADR-0025 §2.7），将来要用 | 硬约束 |
| 不新增远端分支 | D 类**禁止删远端分支** ⇒ 分支只增不减 | 硬约束 |
| 可回退 | 改动集中在 `.cnb.yml` 一个键 + 少量文档 | 偏好 |

**关键事实（2026-09-25 查官方文档确认）**：

| 事实 | 出处 | 对本题的意义 |
| --- | --- | --- |
| 多个分支键同时匹配 ⇒ 这些键的流水线**并行执行**；但只有**声明了该事件**的键才产出流水线 | `trigger-rule.md` | 挂 `develop:` **只放跑测事件** ⇒ `push` 门禁**不会**被跑两遍 |
| 分支键下是「**事件名 → Pipeline**」的映射，**不同事件名互不冲突** | `grammar.md` | **事件名**可以当分派键 ⇒ 多机器不必多分支 |
| `runner.tags` / `runner.cpus` **只能在 Pipeline 级** | `grammar.md` | 每台机器一条 pipeline，各自带独立事件名 |
| `api_trigger` 的配置**从触发时指定的分支/版本读取** | `trigger-rule.md`（可指定版本的事件） | 触发 `--branch develop` ⇒ 环境必然是 `develop` 的 tip |

## 3. 候选方案

### 方案 A：**挂在 `develop:` 键下 + 事件名分派机器**

- 简述：`.cnb.yml` 把跑测事件搬到 `develop:` 键；触发 `--branch develop --event api_trigger_bench`。
  将来加 arm64 就在**同一个键**下再加一个独立事件（`api_trigger_bench_arm64`）。
- 优点：**环境必然是 `develop` 的 tip** ⇒ 新鲜度**自动成立**，"引用陈旧"整类失效模式消失；
  不再有 `test/<slug>` 的零提交纪律，也不需要 ADR-0027 的快进步骤；不新增分支；
  配置仍然集中在 `develop`（甚至比以前更集中）。
- 缺点：**只能测 `develop` 的 tip**——不能再测未合入的提交；
  `api_trigger` 的声明位置在官方文档里**没有示例**（见 §6 风险）。

### 方案 B：维持 `test/<slug>` + 每次快进引用（= ADR-0027 的现状）

- 简述：保留环境分支，靠"触发前 `git push origin HEAD:refs/heads/test/<slug>`"维持新鲜。
- 优点：可以测任意提交（含未合入的）。
- 缺点：靠人执行 ⇒ 忘了就**静默**退化成"测旧代码"；多一条分支纪律要维护；
  而它换来的能力（测未合入提交）在本项目**没人需要**——`develop` 是工作主干且允许直推。

### 方案 C：挂在 `"**"` 键下

- 简述：把跑测事件写进通配键，任何分支触发都能跑。
- 优点：不用动键结构。
- 缺点：**被测代码变成"触发时那个分支"** ⇒ "从与 `develop` 一致的提交拉起"这条前提失效，
  可比性与镜像缓存都不再有保证；且与门禁共用一个键，配置意图混杂。

### 方案 D：每次触发前新建一条 `test/<slug>` 分支

- 简述：新分支天然新鲜。
- 优点：新鲜度自动。
- 缺点：**D 类禁止删远端分支** ⇒ 分支只增不减；且机器分派是按精确分支名的 ⇒ 还得往配置里加键。

## 4. 权衡对比

| 评估维度 | 权重 | A | B | C | D |
| --- | --- | --- | --- | --- | --- |
| 新鲜度（"从 develop 一致提交拉起"） | 高 | ✅ **自动** | ⚠️ 靠人 | ❌ 不确定 | ✅ |
| 消除静默失效模式 | 高 | ✅ | ❌（引入一条） | ❌ | ✅ |
| 不新增远端分支 | 中 | ✅ | ✅ | ✅ | ❌ |
| 能否测未合入提交 | 中 | ❌（代价） | ✅ | ✅ | ✅ |
| 配置集中 / 改动面 | 中 | ✅ 最小 | ⚠️ 多一条纪律 | ✅ | ⚠️ 配置要加键 |

## 5. 决策

**决定采用：方案 A**

理由：

1. **能消除失效模式就不要管理它**：ADR-0027 是对"引用会陈旧"的**管理**（快进纪律，且靠人执行），
   而方案 A 让陈旧**不可能发生**——后者是机制，前者是纪律；
2. **多机器能力没有丢**：分派键从分支名换成**事件名**（官方语义支持），arm64 将来照样能加；
3. `develop` 正是"最新模版"，本项目 `develop` 是工作主干且允许直推，
   "只能测 tip"这一代价**换成的是"测的就是主干"**——对可比性反而更有利。

## 6. 后果

### 正面

- **"引用陈旧 ⇒ 用旧代码测新修复"整类失效模式消失**（连同它的静默性与它的纪律）；
- 少一条分支纪律、少一个 `test/<slug>` 概念、少一处 `--branch` 与提交不一致的可能；
- 配置更集中：跑测键与门禁键同在 `develop` 的 `.cnb.yml` 里（且**事件不重叠**，不会互相触发）。

### 负面（必须填写）

1. **跑测只能测 `develop` 的 tip**：想测未合入的改动，必须先合入 `develop`（本项目可接受：
   允许直推、且"测主干"是更可比的口径）。⚠️ 这条**缩小**了实验自由度，不得当"没有代价"。
2. **`test/<slug>` 的远端分支不能删**（D 类）⇒ `test/amd64-8`、`test/keepalive-probe`、
   `test/quota-probe` 成为**遗留分支**（与 `bench/nightly` 同类），需登记在案，
   否则后人会以为它们仍在用。
3. **一次性探针的载体变了**：以后探针也挂 `develop` 键 + 独立事件名（`api_trigger_probe_*`）；
   `.cnb.yml` 里原来那段探针配置已删除（其内容与结果归档在
   [`research/2026-09-25-bucket-attribution-probe.md`](../research/2026-09-25-bucket-attribution-probe.md) §7.4/§8）。
4. **旧记录里有一处"半对"的表述被更正**：此前写"加 `develop:` 键会让门禁跑两遍"——
   只在**该键也声明了 `push`** 时成立；只放跑测事件时不会（见 §2 的官方语义）。

### 风险与缓解

| 风险 | 影响 | 缓解措施 |
| --- | --- | --- |
| `api_trigger` 的**声明位置**在官方文档里没有示例（只写了"从触发时指定的分支读取"） | 可能"配了但不触发" | **首次触发实测确认**（§7 `V3`）；失败则回退：① 用 `start-build --config` 内联配置；② 恢复 `test/<slug>` 键（本 ADR 可被新的 ADR 回退） |
| 有人把 `push` 也搬进 `develop:` 键 | 门禁被同一个 push 跑两遍（白花 1 核 × 2） | 由 §7 `V1` 的键归属检查与代码评审挡住；`.cnb.yml` 头部硬规则 8 已写明反例 |
| 把跑测事件挂回通配键 | 被测代码不确定（方案 C 的缺点） | §7 `V1` 的机器检查直接禁止 |

## 7. 验证方式

| # | 判据 | 命令 / 位置 | 现状 |
| --- | --- | --- | --- |
| `V1` | 跑测事件**必须**挂在 `develop:` 键下 | `tests/unit/test_cnb_config.py::test_bench_pipeline_is_declared_on_develop_not_on_a_glob_key` | ✅ 落地 |
| `V2` | 来源分支白名单放行 `develop`、仍拒 `main`/`master` | `tests/unit/test_cnb_config.py::test_publish_script_restricts_source_branches`（**已按本 ADR 重写**） | ✅ 落地 |
| `V3` | 触发 `--branch develop --event api_trigger_bench`：① 跑测流水线起来；② **门禁没有被重复触发**（同一次触发只出现一条流水线）；③ 该轮 `env.json.trigger.branch == develop` | `cnb build start-build` + `get-build-logs --sn` + 数据分支的 `env.json` | ✅ **已实测**（`sn=cnb-1oj-1k3bbs9na`：**只有 1 条流水线**；`trigger.branch=develop`；走开发桶；独立 label `devkey-check` ⇒ 索引 10 轮且现役条目未被覆盖。详见 devlog 0024 §4.7） |
| `V4` | 环境取自 `develop` 的 tip | 该轮 `env.json.trigger.commit` == `git rev-parse develop` | ✅ `trigger.commit=7fa91587…`（= 当时 `develop` 的 tip）；**无需任何"快进引用"步骤** |
| `V5` | 不再需要"触发前快进引用" | 运行手册 §3 已删除该步骤（ADR-0027 的 `V1` 随之失效） | ✅ 文档已改 |
| `V6` | 白名单**不再是假控制** | `run.sh` 不再无条件设 `BENCH_ALLOW_LOCAL=1`（此前它让白名单对 `make bench` 完全失效） | ✅ 落地 |

## 8. 与既有 ADR 的关系（**指针，不改旧正文**）

| 旧 ADR | 关系 |
| --- | --- |
| [ADR-0027](0027-test-branch-ref-must-track-the-commit-under-test.md) | **整体被取代**（"引用必须快进"这一纪律随 `test/<slug>` 一起取消）；其状态行已标"已被 0028 取代" |
| [ADR-0025](0025-benchmark-automation-moves-to-dev-bucket.md) | **§2.2 的"按分支名分派机器"被取代**（改为事件名分派）；§2.1 走开发桶、§2.3/§2.4 生命周期、§2.5 分片外推**均不变** |
| [ADR-0023](0023-ci-downgrade-to-manual-trigger.md) | 四道闸门**不变**；`test/<slug>` 这一"人工入口的载体"随之取消（人工入口仍是 `make bench`） |
| [ADR-0013](0013-branch-model-for-solo-dev.md) | 分支矩阵里的 `test/<slug>` 一节**失效**；其余不变 |

## 9. 后续行动

- [x] `.cnb.yml`：跑测事件从 `test/amd64-8:` 搬到 `develop:`；删掉已完成的探针段
- [x] `publish.sh` 白名单改为 `bench/nightly` + `develop`；`run.sh` 删掉 `BENCH_ALLOW_LOCAL=1` 的**无条件**打开
- [x] 机器检查 `V1`/`V2` + 规则联动（`CODEBUDDY.md`/`AGENTS.md`/`.codebuddy/rules/`/`git-workflow.md`/运行手册）
- [x] **`V3`/`V4` 实测**（`sn=cnb-1oj-1k3bbs9na`：只有 1 条流水线、`trigger.branch=develop`、
      `trigger.commit` = develop tip、走开发桶、独立 label 未覆盖现役条目）
- [ ] **登记遗留远端分支**（D 类禁止删除）：`test/amd64-8`、`test/keepalive-probe`、`test/quota-probe`
      —— 是否在 `branch-status` 里标为"已停用"
- [x] ~~**【附带发现】数据分支的 `ci` 门禁必然报红**~~ ⇒ **已修（2026-09-25）**：
      根因**不是** develop 的配置，而是**数据分支自己带着一份陈旧源码树 + 它自己的 `.cnb.yml`**
      （首次创建该分支时脚本用的 `git worktree add --detach <dir>` **未指定 commit ⇒ 取当前 HEAD**）
      ⇒ 平台对该分支 push 事件读的是**该分支自身**的配置 ⇒ 命中其中的 `"**"` 门禁
      ⇒ `format-check` 在数据分支上必失败（模型产物故意不满足格式）。
      修法：`publish.sh` 发布时**清掉 `bench/` 以外的全部文件**（数据分支从此**没有 `.cnb.yml`**
      ⇒ 其 push **不再产出任何流水线**）。⚠️ 这纠正了我先前的两个候选方案
      （改 glob/加 stage `if`）——它们都改在 `develop` 上，**管不到**这条分支。
      判据由 `tests/unit/test_cnb_config.py::test_publish_script_strips_non_data_files_from_the_data_branch` 钉住。
