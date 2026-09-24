# ADR-0023：CI 从「全自动跑测」降级为「人工触发 + 环境内自动化」

- 状态：已接受（2026-09-24）
- 决策者：项目所有者（2026-09-24 指令，含 8 步明确要求）／AI 代理（细化、落地与证据）
- 关联：
  [ADR-0014 基准自动化](0014-benchmark-automation.md)（**被本 ADR 部分修订**：§2.2 的触发面、
  §2.3 的预算基数；**正文原文保留**）、
  [ADR-0016 远端写入授权分级](0016-cnb-platform-integration-and-remote-write-authorization.md)
  （**§5.2 的 E 类行被本 ADR 修订**为"只由跑测脚本写"）、
  [ADR-0013 分支模型](0013-branch-model-for-solo-dev.md)（`test/<slug>` 的例外条件）、
  [ADR-0022 多会话并发纪律](0022-multi-session-concurrency-discipline.md)
- 证据：[`../research/2026-09-24-ci-consumption-summary.md`](../research/2026-09-24-ci-consumption-summary.md)
  （盘点表、轮次台账、四道闸门判据、9 项未验证内容）
- 运行手册：[`../engineering/benchmark-automation.md`](../engineering/benchmark-automation.md) §2/§3

---

## 1. 背景与问题

到 2026-09-24，本项目的 CI 承担两件事：**轻量质量门禁** 与 **全自动跑测**
（夜轮 / 深跑 / 推送即时轮 / 页面手动补跑，见
[ADR-0014 §2.2](0014-benchmark-automation.md)）。

问题来自两条**同时成立**的事实：

1. **额度口径与当初的假设不一致。** [ADR-0014 §2.3](0014-benchmark-automation.md)
   按"配额 ≈17600 核时/月"算出月消耗 ≈310 核时、占 **1.8%**、"余量充足"。
   而 CNB 的官方计费表把「云原生**构建**-CPU」与「云原生**开发**-CPU」列为
   **两个独立计费项**，构建侧免费额度只有 **160 核时/月**，且**按顶级组织结算**
   ⇒ 同组织下的其它项目与本项目**共享**这 160。按 160 计，310 核时/月 = **194%**
   ——**不是"余量充足"，是必超**。口径分歧详见证据文档 §4.5。
2. **跑测的学习目标已经达成。** 已积累 **5 条同签名（可比）nightly 轮次**
   （另有 2 条 push 轮次）、**678 个入库文件**、实测 **35.973 核时**，
   并验证了"四道可信闸门"这套方法（证据文档 §4.1/§5.2）。
   继续每日一轮**不再带来新信息**，却持续占用组织共享额度。

⇒ 所有者决定：**自动跑测全部停掉**，把额度让给新项目；跑测改为
**在云原生开发环境里人工一键触发**。

**真正的问题因此不是"怎么删流水线"，而是**：

> 停掉 CI 跑测之后，凭什么说新跑出来的数据**仍然可信**？

## 2. 决策

### 2.1 停用全部跑测类流水线（`.cnb.yml`）

| 移除项 | 原触发 | 说明 |
| --- | --- | --- |
| `bench-push` | `bench/nightly` 的 `push` | 推送即时轮 |
| `bench-nightly` | `crontab: 0 4 * * 2-6,0` | 夜轮 |
| `bench-deep` | `crontab: 0 4 * * 1` | 深跑 |
| `bench-manual` | `web_trigger_bench` | 页面手动补跑 |

**`crontab` 触发一条不留**——它是**最不可控**的消耗源：不等人、不看当天有没有别的事。
`.cnb.yml` 头部新增硬规则 5~7 说明下线理由，`tests/unit/test_cnb_config.py`
把"**不得再出现任何自动跑测入口**"钉成机器检查（该断言**替换**了原先
"两条 crontab 键必须在"的那条，**门槛未降、靶子换了**）。

⚠️ **未做的事（刻意的）**：不删除 `bench/nightly` 与 `bench/data` 分支
（D 类禁止删远端分支）、不改写已推送历史、不强推。

### 2.2 保留最小门禁，并且真的"最小"

门禁（`ci` / `ci-pr`）保留，但三条**省额度**的约束必须显式写出来：

| 约束 | 值 | 为什么 |
| --- | --- | --- |
| `runner.cpus` | **1** | **不声明即按平台默认 8 核计费**——这是盘点里最容易漏算的一项 |
| `ifModify` | 8 条代码/配置路径 | 纯文档提交不再消耗额度 |
| `lock` | `{key: ci-gate, wait: true, cancel-in-wait: true}` | 排队而非堆积（**排队期间同样计费**），且只保留最新一条 |

`tests/unit/test_cnb_config.py::test_light_gate_is_single_core_path_scoped_and_debounced`
把这三条钉住，并断言两处 `ifModify` 清单**逐字一致**。

### 2.3 跑测改为"环境内一键"：`make bench`

```text
make bench
  └─ scripts/bench/run.sh
       ├─ 0  前置检查（llama-server / uv / 模型目录 / repeats ≥ 3）
       ├─ 0.5 闸门①-A 跑前清场：终止**同一份可执行文件**的 llama-server 残留
       ├─ 1  跑一轮（闸门①-B：轮次内每次重复一个全新服务进程 ⇒ KV 全空）
       ├─ 2  四道可信闸门（scripts/bench/gates.py，全过才允许往下）
       ├─ 3  只打印报告的「中位数 + 极差」一节
       └─ 4  发布到数据分支（唯一持久化出口）
```

### 2.4 **降级后如何保持可信性：闸门从 CI 的结构迁移到脚本的检查**（本 ADR 的核心）

这是本次决策**唯一**的正当性来源。四道闸门**逐条**对照如下——
**承载位置变了，判据一条没变**：

| # | 闸门 | 判据（不变） | 原承载（CI） | 新承载（脚本） | 判据的真源 |
| --- | --- | --- | --- | --- | --- |
| ① | **跑前重启服务进程清 KV** | 每次重复一个**全新** `llama-server`；`valid_prefill_repeats == repeats`，且本轮日志份数 == repeats | 流水线每次都从干净容器起（**顺带**成立，从未被断言） | `run.sh` 第 0.5 步清场 + `gates.py --gate kv` 断言 | `bench/rounds.py::_run_tier` 的 `with runner.LlamaServer(...)` 循环 |
| ② | **最小 token 闸门（≥32）且计时行数 == 3** | 每份日志 prefill / gen 各**恰好** 3 行；每个 prefill 的被评估 token 数 ≥ 32 | `rounds.py::_repeat_timings`（**跑测时**生效，但不单独报告） | `gates.py --gate tokens`**逐份日志独立复核**并逐条打印 | `protocol.MIN_PREFILL_TOKENS` / `EXPECTED_TIMING_LINES_PER_REPEAT` |
| ③ | **只暴露中位数 + 极差** | `index.json` 条目里**不得**出现单次采样（`values`）；对外读数只给中位数与极差 | `report.py::index_entry`（**无断言**，靠实现正确） | `gates.py --gate exposure` 递归扫描索引条目 + `run.sh` 只打印报告 §1 | `stats.Series` / `report.py::index_entry` |
| ④ | **入库前 schema 校验** | `store.validate_round` 不通过即拒绝入库 | `publish.sh` 调 `rounds --validate-only` | `gates.py --gate schema` 调**同一个** `rounds.validate_latest` | `bench/store.py::validate_round` |

**这次迁移实际上让 ②③ 变强了，如实登记（不美化也不贬低）**：

- **②变强**：CI 时代它们只在 `rounds.py` 内部生效、**不产生独立报告**；
  现在逐份日志复核并逐条打印，**口径已变**这类问题会当场可见。
- **③变强**：CI 时代索引里有没有单次采样**没有任何断言**（靠实现正确）；
  现在有递归扫描断言。
- **①属于"补上断言"**：CI 时代"每次跑测都在干净容器里"是**环境顺带保证**的，
  从不是一条被断言的性质；现在它既是脚本动作（清场）也是断言（`valid_prefill_repeats`）。
- **未变强、仍靠纪律的一项**：**并发控制**。CI 时代由 `lock: bench-cpu` 机械保证
  "同一时刻只有一轮在跑"；现在只能靠"**人工一次只起一个环境**"。
  它是**数据有效性**的要求（并发测量会互抢 CPU，吞吐数字失去可比性），
  而现在**没有任何机制**能拦住两个并发环境 —— 属**部分缓解**。

### 2.5 `bench/data` 的写入口与来源分支白名单

- 写入者由"CI"改为 **`make bench` → `scripts/bench/publish.sh`**；
- 来源分支白名单由单值 `bench/nightly` 改为 **ERE `^bench/nightly$|^test/`**；
- **`develop` / `main` 仍不在白名单内**（"人手触发"≠"可以在主干上顺手发布数据"）；
  `tests/unit/test_cnb_config.py::test_publish_script_restricts_source_branches`
  把脚本里的正则**取出来在 Python 里实跑**，逐个候选分支验证放行/拒绝
  （防 `^test/|develop` 这类写法骗过文本断言）；
- `bench/data` **只由跑测脚本写、人手不得直接提交、禁止把测试数据推到代码分支**
  ⇒ [ADR-0016 §5.2](0016-cnb-platform-integration-and-remote-write-authorization.md)
  的 **E 类**口径随之修订（5 处载体同步，见 §3）。

### 2.6 `test/<slug>`：跑测专用分支（**零提交**）

`test/<slug>` 是本工作流里**唯一"零提交"的分支**：只用于借一台云原生开发环境。

| 项 | 规则 |
| --- | --- |
| 提交 | **绝不允许**（不 `add` / `commit` / `push`；用完即弃） |
| 数据出口 | 只有 `bench/data`；大文件走制品库 |
| 环境来源 | 必须**与 `develop` 一致的提交** ⇒ 命中镜像缓存 ⇒ **不额外消耗构建桶** |
| 推论 | **`.ide/Dockerfile` 不要频繁改**——每改一次，所有环境下次拉起都要重建镜像 |

## 3. 备选方案与取舍

| 方案 | 为何不选 |
| --- | --- |
| 只把夜轮改成每周一次（保 CI 全自动） | 仍要按 8 核计费（一轮 ≈6.1~7.3 核时），且 `crontab` 的**不可控性**没解决；组织共享额度下"每周 1 轮"仍会持续挤占新项目 |
| 保留 `web_trigger`（页面手动补跑） | 与 `make bench` 功能重复，却仍要按 8 核起一条流水线；且它把"跑测入口"分散到两处 |
| **只把流水线改成 `cpus: 1`** | 跑测**必须** 8 核 16 GiB（L 档常驻 8.70 GiB，4 核会 OOM）⇒ 降核不可行；降核与"跑得动"直接冲突 |
| 把跑测搬进轻门禁（`ifModify` 命中就跑） | 把 6~7 核时的任务挂到"每次推送代码"上 ⇒ 比现在还贵；且 PR 事件最频繁 |
| 彻底不跑测，只留历史数据 | 放弃"可复现的测量能力"；日后任何性能改动都没有基线可比（`REQ-PERF-05/06` 直接失去载体） |
| 用制品库代替 `bench/data` 分支 | 跨分支读取不便、制品有保留期；且既有 5 轮序列就在数据分支上（迁移成本高、收益为零） |

**采纳**：**停用自动跑测 + 四道闸门迁进脚本 + 单核门禁 + `test/<slug>` 借环境**。

## 4. 后果

### 正面

- **额度让出**：构建桶的持续消耗降为"只有轻门禁"，且门禁本身从 8 核降到 1 核
  （同一份门禁的核时降到约 **1/8**）；
- **闸门没丢，②③还变强**（见 §2.4）；
- **跑测入口收敛为一处**（`make bench`），不再有"CI 里一套、本地一套"的分叉风险；
- **可比性纪律不变**：`PROTOCOL_VERSION` + 比较签名 + 最小重复次数 + 报极差，
  全部仍在生产代码里由测试与 schema 钉住。

### 负面后果与已知限制（**必须逐条读**）

1. **时间序列会变稀。** 手动触发的频率必然低于每日一次 ⇒ 趋势判断更弱。
   **可比性不因承载方式改变而下降**，下降的是**样本点数量**。
2. **并发控制从机制退化为纪律。** CI 的 `lock` 能机械保证"同时只有一轮"；
   现在只有"人工一次只起一个环境"。⇒ 记**部分缓解**，**不得**表述为机制。
3. **纯文档提交不再经 CI 的 `format-check`。** `ifModify` 不含 `docs/**`，
   而 `make check` 的 `format-check` 覆盖 `docs/`（ruff 会格式化文档里的 python 围栏）。
   替代是**本地 pre-commit 的 `ruff-format`**（不限路径、覆盖暂存文件）
   ⇒ 钩子没装（`make setup` 未跑）时该缺口**完全敞开**。属**部分缓解**。
4. ⚠️ **安全：不可信产物的执行宿主变了。** 模型产物（LLM 生成的代码）
   从"短时 CI 容器"改为在"**云原生开发环境**"里被判定执行。
   隔离机制未变（非特权 uid + rlimit + 最小环境 + 一次性工作目录），
   但宿主是**长时会话且持有 `CNB_TOKEN`** ⇒ `T-01` / `T-08` 的**残余风险需重估**。
   **当前状态：已登记、未评估、未缓解**（登记见 `SECURITY.md` §3 的 S 表后注，
   待办见最新一篇 devlog 的 §7）。**在威胁模型更新之前，不得声称"迁移后安全性与从前等效"。**
5. **`bench/nightly` 变成一条"看起来还在用"的死分支。** 它不再被任何流水线读取，
   但 **D 类禁止删远端分支**、且它是历史数据来源之一 ⇒ 只能保留并在文档里标注
   "不要再往里推"。这是一处**长期的认知噪声**。
6. **保留期清理仍无执行者**（原欠账，性质未变、原因变了）：`BENCH_KEEP_DAYS`
   只对**本地**数据根生效，而跑测环境的 `.bench-data` 通常也只有本轮。
7. **跑测现在吃的是「开发」额度**（1600 核时/月，组织共享）。
   虽然比构建额度宽裕，但**不是无限**——"需要时才跑"这条纪律同样适用。
8. **CI 侧核时没有实测数据。** §2.1/§2.2 的收益是按"cpus × 估时"推的（证据文档 §4.3），
   门禁真实时长**未取到**。⇒ 额度结论**未经账户核对**（`用量管理` 页面需人工查看）。
9. **`.cnb.yml` 的 `vscode` 位于 `"**"` 与 CNB 官方校验器的规则冲突**
   （应放 `$` 兜底分支）——**这是本次改动前就存在的**，已用改动前的文件实测确认为既有；
   本次**未动**它（改动 vscode 事件位置必须**实测一次环境拉起**才能确认不破坏开发环境，
   属"必须验证才能动"的改动）。

## 5. 验证方式（**可执行；无验证视为未实现**）

| # | 检查 | 命令 / 位置 | 通过判据 |
| --- | --- | --- | --- |
| V1 | 不再有自动跑测入口 | `uv run pytest tests/unit/test_cnb_config.py -q` | `test_no_pipeline_runs_benchmarks_automatically` 通过（黑名单：`crontab:` / `bench/nightly:` / `web_trigger_bench` / `bench-round` / `bench-publish`） |
| V2 | 轻门禁单核 + 路径过滤 + 去抖 | 同上 | `test_light_gate_is_single_core_path_scoped_and_debounced` 通过（含两处 `ifModify` 逐字一致） |
| V3 | 发布来源分支受限 | 同上 | `test_publish_script_restricts_source_branches` 通过（正则**实跑**：放行 `test/*`、`bench/nightly`；拒绝 `develop`/`main`/`master`/`feature/*`） |
| V4 | 四道闸门端到端生效 | `BENCH_DRY_RUN=1 BENCH_TIERS=S BENCH_REPEATS=3 make bench` | 逐道 `[gate ①..④][OK]`，退出码 0 |
| V5 | 闸门**非恒过**（变异探针） | 在数据根副本上分别篡改：有效样本数 / 计时行数 / prefill token 数 / 索引中的 `values` / `status` | **五次全部 exit=1** 并给出对应 FAIL（2026-09-24 已实测，见证据文档 §5.2 与本 ADR §2.4） |
| V6 | 口径一致（E 类 5 处） | `uv run pytest tests/unit/test_remote_write_authorization_consistency.py -q` | 6 passed（含 3 个变异探针，证明检查本身非恒过） |
| V7 | 仓库门禁全绿 | `make check` | 全绿（2026-09-24 实测 959 passed / 1 skipped） |
| V8 | 配置合法性 | CNB 官方校验器（`cnb-pipeline` Skill 的 `validator/`） | YAML 通过；语义仅剩**既有**项（`vscode` 位置、`apt install` 建议）——已用**改动前**的文件实测确认为既有 |

**待验证（本轮未取到，不得当作已成立）**：

- **V9**：构建桶实际扣减与剩余额度 —— 需在「组织 → 设置 → 用量管理」**人工**核对；
- **V10**：停用后 `push` / `pull_request` 上确实不再出现 bench 类构建 —— 需看构建历史（**不轮询**）；
- **V11**：CI 侧门禁的真实核时 —— 需读一次带时间线的构建记录；
- **V12**：`make bench` 在**新借来的** `test/<slug>` 环境里跑通并成功**发布**（本次只验证到 `BENCH_DRY_RUN=1` 为止）——
  发布路径需 `CNB_TOKEN` + `CNB_REPO_URL_HTTPS`，未在本环境实测。

## 6. 与既有 ADR 的关系（**指针，不改旧正文**）

| 旧 ADR | 关系 |
| --- | --- |
| [ADR-0014](0014-benchmark-automation.md) §2.2（触发面） | **已被本 ADR 修订**：三种触发 + `lock` 全部移除。**§2.1 的两条长驻分支保留不动**（`bench/nightly` 只是不再被触发） |
| [ADR-0014](0014-benchmark-automation.md) §2.3（预算） | **基数被本 ADR 更正**：≈17600 → **160（构建桶）**；"余量充足"的结论在构建桶口径下**不成立** |
| [ADR-0014](0014-benchmark-automation.md) §2.5（测量纪律） | **不变**，且其中第 1~3 条被提升为脚本里可断言的闸门①② |
| [ADR-0016](0016-cnb-platform-integration-and-remote-write-authorization.md) §5.2 的 **E 类行** | **已被本 ADR 修订**：`bench/data` 由"只由 CI 写"改为"**只由跑测脚本写**（`make bench`），人手不得直接提交，禁止把测试数据推到任何代码分支"；5 处载体同步（`tests/unit/test_remote_write_authorization_consistency.py` 断言） |
| [ADR-0013](0013-branch-model-for-solo-dev.md) §9（长驻分支例外） | **不变**；新增一类例外：`test/<slug>` 的"**零提交**"（见 `git-workflow.md` §2「关于 `test/*`」） |

> 按 [ADR README](README.md) 的约定，**旧 ADR 正文不改**；与现状冲突之处以本 ADR 为准。
> 上述两条修订**只登记指针**，不重写 ADR-0014 / ADR-0016 的论证。
