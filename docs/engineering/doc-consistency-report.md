# 文档一致性核查报告

> **产出方式**：由只读探索代理（`code-explorer`，2026-09-18）执行全库扫描后落盘；
> 落点由主 Agent 指定（见 `CODEBUDDY.md` §10.1 的记录员例外条款）。
> **性质**：核查结论，**不是决策**——每条问题的处置需要对应角色（架构师/记录员）执行后再更新本文状态。
>
> 核查范围：75 份 Markdown + `Makefile` / `scripts/` / `.cnb.yml` / `src/` / `tests/`。
> 一句话结论：**工程流程文档质量很高且与实现基本咬合；失控的是"项目状态类"文档**
> ——base project 状态在 4 处互相矛盾、CONTRIBUTING 的 Git 规则被 ADR-0013 取代后未同步、
> 需求/设计/威胁模型/测试分层四项"宣称已有"但实际为空。

> **修复状态（2026-09-18 更新；本轮依据 `e41af26` / `1240806` / `3d8edc8`，另含队友提交 `7b6356f` / `39ec073`）**
>
> - **已修**（提交 `81282ea`）：A-2、A-3、A-4、A-5、A-7、A-8、A-9、A-10
> - **A-9 处置已被替换（2026-09-18 第二轮，`a39ad48`）**：原"返回 0 + 提示"的处置（`81282ea`）被推翻——`make test-security` 在零 `security` 用例时改为 fail-secure `exit 1`（"安全测试层是项目基线的一部分，零用例说明缺失或标记丢失"）。理由：返回 0 会让安全测试层缺失时门禁假绿。A 表原始描述（"退出码 5 ⇒ 必然失败" + "返回 0"处置）保留作历史证据，不改写。
> - **本轮新增已修**：
>   - **A-1**（ADR-0003 §6~§8 历史残留）：由 `ADR-0015` §5.5 登记为历史快照、其"AIOS 源码核验门槛"作废（未改写 ADR-0003 正文，`3d8edc8`）
>   - **A-6**（需求编号规范与 SRS 分组对齐）：`e41af26` 回写 `docs/requirements/README.md` 前缀，删 `KERNEL`
>   - **A-12**（`REQ-PERF-04` 验收入口）：`e41af26` 改为实际入口 `make bench-round`（证据 `Makefile:138` + `rounds.py:602`）
>   - **A-14**（`bench/nightly`/`bench/data` 分支表缺口）：`1240806` 在 `ADR-0013` §9 + `git-workflow.md` §2 补登，附例外条件
>   - **A-2 残留项**（`docs/proposals/0003` 头部状态栏仍为「待决策」）：架构师复核时发现，`7b6356f` 已同步为采纳结论
>   - **B1 / B4 现状盘点路径修正（记录员，依据 `617f564`）**：`bench/{proc,paths,errors}.py` 经 `git mv` 提升为 `src/agent_sec_perf/foundation/`；B1 盘点表（line 60 / 65）与 B4 框架缺口表（line 95）里的旧路径已同步为 `foundation/...`（`bench/evaluate.py` 未动、仍在 `bench/`）。**A 表原始描述保留作历史证据，未改写**（本段只承载处置状态）。
> - **已随收篇处理**：A-13（0012 / 0002 / 0004 收篇，0005 明确为"待人类逐条确认的清单"）
> - **A-11 重大更正（2026-09-18 第二轮，实测）**：原处置（"把 SECURITY 的『CI 密钥扫描』改为『pre-commit（本地）』"）**事实上从未生效**——本轮回查发现 `.git/hooks/` 在相当长时间内为空、本地 pre-commit 钩子从未安装（`.pre-commit-config.yaml` 的 `detect-private-key` 与 commit-msg 校验实际未运行；`Makefile:38-43` 在 `CI=true` 时故意跳过安装，本工作区处于 CI 上下文、准备后未再跑 `make setup`）。⇒ 当时"密钥扫描"既不在 CI、也不在本地运行，**A-11 实质未缓解**。影响：本轮所有提交都未经任何本地钩子，唯一本地防线是"成员自觉跑 `make check`"。当前状态：团队领导已手动恢复安装钩子（`pre-commit`/`commit-msg` 已就位）；**根因处置待所有者裁决**——是否在 `.cnb.yml` 增加密钥扫描、以及 CI 是否补 `detect-private-key`。远端仍有门禁：`.cnb.yml` 的 `make check`（push 时生效）。详见 [devlog 0014 §3/§5](../devlog/0014-2026-09-18-威胁模型与安全修复.md)。
> - **A-11 / A-15 已处置（2026-09-18 第三轮，实测）**：所有者裁决"按推荐两条都做"后落地——
>   ① `Makefile` 的钩子安装由 `$CI` **推断**改为**显式开关** `LOCAL_HOOKS`（默认 `1`；CI 流水线显式写 `LOCAL_HOOKS=0`）；
>   ② "本地钩子存在"做成**可执行断言**：`make hooks-check`（`scripts/check-local-hooks.sh`，
>   断言存在 / 可执行 / 是 pre-commit 生成的 / 指向本仓库配置 / 钩子类型正确），
>   且它现在是 **`make check` 的第一步**；
>   ③ CI 增加**具名 `secret-scan` stage**（`make security-secrets`，复用 pre-commit 的
>   `detect-private-key`，不另写一套扫描），并由 `tests/unit/test_cnb_config.py` 的计数断言
>   保证"每个门禁流水线都有该阶段"；
>   ④ 检查本身**非恒过**：`tests/unit/test_local_gates.py` 用临时 git 仓库复现
>   "缺失 / 不可执行 / 被顶替 / 指向别的配置 / 钩子装串"五种状态并逐一断言报红。
>   ⇒ **S-1（密钥零入库）现在有三处可实测的防线**（提交时钩子 / `make check` 的 `security-secrets` / CI 的 `secret-scan`），
>   S-8（"声称已生效的缓解措施必须可实测"）作为新红线写入 `SECURITY.md` §3。
>   残余：**CI 侧未经一次真实流水线验证**（本地无 CNB runner），待下次 push 核对；
>   `pre-commit run --all-files` 不覆盖未跟踪文件（提交时钩子覆盖暂存区）。
>   详见 `devlog 0014` §3/§7、[`security-scan-gate-config.md`](security-scan-gate-config.md) §7。
> - **新增 A-16（2026-09-18 第三轮，实测）**：**文档允许的提交类型 `security` 被门禁拒绝**——
>   `git-workflow.md` §3 的类型表、`.codebuddy/rules/` 摘要与 `CODEBUDDY.md` / `AGENTS.md`
>   都写着 `type ∈ {… security}`，而 `.pre-commit-config.yaml` 调用的 `cz check` 实际 schema 为
>   `(build|bump|chore|ci|docs|feat|fix|perf|refactor|revert|style|test)`，**不含 `security`**
>   （本次实测：以 `security(ci): …` 提交被 `commitizen check` 挡下，`exit code 14`）。
>   ⇒ 与 A-3/A-11/A-15 同族（**文档声明 ≠ 门禁执行**），只是发生在**提交规范**上。
>   **处置需裁决**（改门禁 schema 或改文档口径），未决前安全类提交只能借用 `fix`/`ci`。
> - **引文更正（A-3）**：A-3 行（line 34）所引 `git-workflow §3.3` 系改版前编号；2026-09-18 核对后实际为 **§4「Pull Request → 合并策略」**（§3 为提交规范）。ADR-0013 §5.5 同名错误引用已由其在 **§9.4** 更正。A 表原始描述保留作历史证据，本段承载更正。
> - 其余新增发现（`.codebuddy/rules/git-workflow/` 分支摘要未列 `bench/*`、`ruff` 格式化 `docs/` 代码块等）已登记于 devlog 0013 §7，由对应角色处理。
> - **新增 A-15（2026-09-18 第二轮）**：记录在 A 表——"一个被误认为已生效的缓解措施"这一类问题（文档/配置声称的保护与实际不符）。本轮实例即上述 A-11 的本地 pre-commit 钩子。
> - 下方的 A 表保留**核查当时**的原始描述（它是历史证据，不改写）；处置状态以本段为准。

---

## A. 一致性问题（按影响面降序）

| # | 问题位置（文件 + 原文摘录） | 冲突的另一方 | 影响判断 | 建议处置 |
| --- | --- | --- | --- | --- |
| **A-1** | `docs/adr/0003-select-base-project.md` §0「实际定位应为『组装开源组件』：约 70% 复用…约 30% 自研」、§5.1「复用成熟开源组件作为积木」 | 同文 §6「需要对上游代码做**较多重构**」「AIOS 上游质量…**尚未核验**」、§7「**核验门槛（决策转正条件）**：完成对 `agiresearch/AIOS` 源码核验」、§8 同名待办 | 读者会以为项目仍是"fork AIOS 重写内核"，并以为存在"必须先完成 AIOS 核验"的前置条件（该核验从未做过） | 新增 ADR 登记"复用组件与自研边界"，并声明 §6~§8 为历史残留（ADR 只增不改） |
| **A-2** | `docs/adr/README.md` 索引「0003…（**待决策**）\| 提议中」；`docs/proposals/README.md`「0001 base project 选型分析 \| **待决策**」；`CODEBUDDY.md` §1/§9「**尚未冻结**」；`README.md` 第 15 行「Phase 0 · 工程初始化**已完成**」 | `docs/adr/0003-…md` 第 3 行「状态：**已接受**（2026-09-14）」 | 同一事实有 **"提议中 / 待决策 / 未冻结 / 已完成"四种表述**，无法判断 Phase 0 是否收口，直接决定能否开始 Phase 1 | 统一为单一状态，一次改齐五处（ADR 正文、ADR 索引、提案索引、CODEBUDDY/AGENTS §1+§9、README） |
| **A-3** | `CONTRIBUTING.md` §2/§5「功能分支 → `develop`：**Squash merge**」 | `docs/engineering/git-workflow.md` §3.3「**默认为 merge commit，不用 squash**：devlog 与 CHANGELOG 逐条引用提交哈希，squash 会让引用指向不存在的对象」；`ADR-0013` §5.5 同 | 按 CONTRIBUTING 执行会**批量作废** devlog/CHANGELOG 里写死的哈希（如 `63e6888`、`647e7db`）⇒ 证据链断裂 | 按 ADR-0013 改写 CONTRIBUTING §2/§5，并标注被取代 |
| **A-4** | `CONTRIBUTING.md` §4 分支表「`develop` \| 永久」（全文未提"可直推"） | `ADR-0013` §5.2「`develop` **取消禁止直推，定位为工作主干**」；`git-workflow.md` §2/§5；`CODEBUDDY.md` §2；`.pre-commit-config.yaml` `--branch=main` | 会以为 develop 仍需走 PR，与"云环境从 develop 拉起、未合入即丢失"的现实冲突，可能重现 2026-09-16 的内容丢失 | 补"develop 为工作主干、允许直推、仅 main 保护" |
| **A-5** | `testing-strategy.md` §6 / `definition-of-done.md` §3 / `sdlc.md` §6 / `git-workflow.md` §3 示例：基准数据归档到 **`reports/bench/`** | `test-environments.md` §4「写入 `docs/research/reports/<date>-<topic>/`」；实际数据落 **`bench/data` 分支**（ADR-0014 §2.1）；且 `reports/` 目录**不存在** | 基准数据有**三套互不兼容的归档约定**：按 DoD 检查会判"未归档"，或放错位置导致 CI 取不到 | 统一为：机器数据 → `bench/data` 分支；人工归档 → `docs/research/reports/`；清理 `reports/bench/` 引用 |
| **A-6** | `docs/requirements/README.md` 编号规则「模块示例：…**`KERNEL`**（内核/运行时）…」 | `srs.md` §7 实际分组：`MODEL/HARNESS/SEC/PERF/TOOL/UX/OBS/PLAT/OPS`（49 条） | `KERNEL` 从未使用，`MODEL/HARNESS/UX/PLAT` 未在规范中定义 ⇒ 新增需求的模块前缀无据可依 | 以 SRS 实际分组回写 README |
| **A-7** | `docs/design/threat-model/` 被 **6+ 处**当作既有路径引用：`CODEBUDDY.md` §7、`AGENTS.md` §7、`definition-of-done.md` §2（**DoD 勾选项**）、`sdlc.md` §5/§6、`SECURITY.md` §4、`ADR-0006`/`0007` 后续行动 | `docs/design/` 实际**只有 `README.md`**（自称"计划结构…将在 Phase 1 建立"） | DoD 里存在**永远无法勾选**的必选项；安全相关 PR 会被卡住，或被迫造一个空目录充数 | 改为"若威胁模型已建立则更新"，或明确标注"未建立（Phase 1 产出）" |
| **A-8** | `docs/adr/README.md` 索引表**止于 0013**（无 0014）；`docs/proposals/README.md` 提案表**只有 0001** | `docs/adr/0014-benchmark-automation.md` 存在且被 `.cnb.yml` 头部、`Makefile`、`CHANGELOG.md` 多处引用；`proposals/0002`、`0003` 存在且被 ADR-0003 采纳 | 索引是目录入口，缺项会让读者（与代理）以为这些文档不存在 ⇒ 重复决策或漏读关键论证 | 补索引：ADR 表加 `0014`；提案表补 `0002`/`0003` 及状态 |
| **A-9** | `testing-strategy.md` §2/§5.2/§8 定义四层测试（`tests/integration/`、`tests/security/`（含 `corpus/`）、`tests/benchmark/`）与 `make test-security` | `tests/` 实际**只有 `unit/`**（9 个模块全为 `@pytest.mark.unit`）；`make test-security`（`pytest -m security`）在无用例时退出码为 **5** ⇒ 该目标必然失败 | ①"安全测试层已存在"是错觉，`REQ-SEC-09` 无载体；②该目标一旦接入 `check`/CI 立即红灯 | 标注"分层为目标态，当前仅 unit"；给 `make test-security` 加显式处理，或建目录占位 |
| **A-10** | `README.md` §4「`src/` # 项目源码（**base project 确定后落位**）」、§结构图只列 6 个 docs 子目录 | 实际 `src/agent_sec_perf/bench/` 已有 12 个模块 + 4 个夹具；`docs/README.md` 目录表**含** `devlog/`、`notes/` | 读者以为 `src/` 是空的、不知道有 12 篇 devlog 与 10 篇笔记（项目研究性证据的核心） | 更新 README 结构图与说明 |
| **A-11** | `README.md` §5「`make security` # **依赖与密钥安全检查**」；`SECURITY.md` §3「…**CI 密钥扫描**」 | `Makefile` `security: security-bandit security-audit`（仅静态扫描 + 依赖漏洞）；`.cnb.yml` 的 `push`/`pull_request` **无密钥扫描 stage**（`detect-private-key` 只在本地 pre-commit） | 会以为 CI 会拦密钥泄漏，而 CI 实际不查 | README 改为"静态安全扫描 + 依赖漏洞审计"；SECURITY 的"CI 密钥扫描"改为"pre-commit（本地）" |
| **A-12** | `srs.md` §7 `REQ-PERF-04` 验收标准「**`bench` 命令**产出结构化报告」 | 实际入口是 `make bench-round` / `python -m agent_sec_perf.bench.rounds`（无 `bench` 子命令） | 验收标准指向不存在的命令，需求无法按字面验证 | 改为实际入口 |
| **A-13** | `docs/devlog/README.md` 索引中有 **4 篇同时"进行中"**（0002/0004/0005/0012） | `CODEBUDDY.md` §2/§10.2「**最新一篇 devlog 的 §7 是唯一任务清单**」+ devlog README「议题讲完即收篇…旧篇成为封闭记录」 | 无法判断当前该只看 0012 §7，还是仍有多个尾巴 | 收口 0002/0004/0005（标注"已收篇/已被修正"或把未决项并入 0012 §7） |
| **A-14** | `ADR-0014` §2.1 把 `bench/nightly`、`bench/data` 定为**长驻分支**（"对 ADR-0013 的显式例外"）；devlog 0012 §7 自认需"登记到 ADR-0013 分支表" | `ADR-0013` §5.3「短期分支必须合回 develop」，全篇无 bench 分支；`git-workflow.md` §2 分支表也无 | 分支模型权威文档与实际运行的两条长驻分支不一致 ⇒ 按 ADR-0013 会误判它们"该被合回/删除" | 在 ADR-0013 分支表登记这两个分支及例外条件 |
| **A-15** | `docs/design/**` / `SECURITY.md` / `.pre-commit-config.yaml` 声称"本地 pre-commit 提供密钥扫描 / 提交规范校验"（即 A-11 原处置"改为 pre-commit（本地）"所依赖的前提） | 实际 `.git/hooks/` 长期为空、钩子从未安装（`Makefile:38-43` 在 `CI=true` 下跳过安装，本工作区处于 CI 上下文、准备后未再跑 `make setup`）；`detect-private-key` 与 commit-msg 校验实际未运行 | **文档/配置承诺的保护不真实存在**——密钥泄漏的本地防线从未运行、且长期无人察觉（与 A-11 同源：A-11 的"本地扫描"前提本身不成立） | 把"声称已生效的缓解措施"做成**可执行检查**：① CI 是否真有 `detect-private-key` stage；② 启动期断言 `.git/hooks/pre-commit` 存在；当前钩子已由团队领导手动恢复，根因处置（CI 是否补密钥扫描）待所有者裁决 |
| **A-16** | `docs/engineering/git-workflow.md` §3 提交类型表、`.codebuddy/rules/` 摘要、`CODEBUDDY.md` / `AGENTS.md`：`type ∈ {… security}`；`SECURITY.md` §5「安全修复走 `security/<issue>-<slug>` 分支」 | `.pre-commit-config.yaml` 实际调用的 `cz check`（`cz_conventional_commits`）schema 为 `(build\|bump\|chore\|ci\|docs\|feat\|fix\|perf\|refactor\|revert\|style\|test)`，**不含 `security`** | 按文档写的 `security(x): …` 会被 `commitizen check` 挡下（2026-09-18 实测 `exit code 14`）⇒ 安全类改动**只能借用 `fix` / `ci`**，与"安全改动走 security 分支"的口径不一致，会持续误导 | **需裁决**（二选一）：① 改门禁——换 `cz_customize` 并写 schema 纳入 `security`；② 改文档——从三处类型列表删掉 `security`。未决前一律用 `fix`/`ci`，并在 devlog 记明 |

---

## B. 现状盘点

### B1. 已实现的东西（读代码得出）

`src/agent_sec_perf/` 下**唯一有实现的子系统是 `bench/`**（12 个模块 + 4 个夹具）：

| 模块 | 实际职责 | 测试 |
| --- | --- | --- |
| `bench/protocol.py` | 协议常量、S/M/L→模型映射、任务集与夹具白名单、`RunParams.validate()`、`server_argv()` | ✅ |
| `bench/assets.py` | 模型清单解析（sha256 唯一真源）、流式哈希 | ✅ |
| `foundation/proc.py` | **全项目唯一子进程封装层**（非特权 uid + rlimit + 最小环境） | 由 AST 测试强制 |
| `bench/runner.py` | llama-server 生命周期 + `chat()` + 日志解析（prefill/gen 速率） | ✅ |
| `bench/evaluate.py` | 产物客观判定（AST + mypy --strict + 行为等价 + pytest + 变异测试） | ✅ |
| `bench/stats.py` / `bench/store.py` / `bench/report.py` | 统计汇总 / schema 校验与索引 / 人读报告与比较签名 | ✅ |
| `bench/rounds.py` | 一轮编排 + CLI（`--validate-only`、`--merge-into`） | 无专属单测（手工验证 + 静态校验） |
| `foundation/paths.py` / `foundation/errors.py` | 路径白名单（防穿越）/ 异常层次 | 间接覆盖 |

测试基建：`tests/unit/` 9 个模块 + `conftest.py`，**全部为 `unit` 标记**；
其中 `test_bench_encapsulation.py`（源码级 AST 约束）与 `test_cnb_config.py`（流水线约束）是"把约定做成机器检查"的载体。

### B2. 文档宣称已有、实际不存在的东西

| 项 | 宣称 | 实际 |
| --- | --- | --- |
| 需求（SRS） | 49 条 REQ（MODEL 6 / HARNESS 8 / SEC 9 / PERF 8 / TOOL 3 / UX 6 / OBS 2 / PLAT 3 / OPS 4） | 条目齐全；但 `requirements/README.md` 要求的"用例（参与者→主流程→**滥用场景**）"**一条未产出** |
| 设计 | `design/README.md` 计划 6 类：`architecture.md`、`modules/`、`interfaces/`、`diagrams/`、`threat-model/`、`security-model.md` | **只有 `README.md` 一份** |
| 威胁模型 | `SECURITY.md` §4「**前置交付物**」；sdlc 列为 Phase 1~3 工件；DoD 勾选项 | **0 条威胁条目**，目录不存在 |
| 测试分层 | unit / integration / security（含 corpus）/ benchmark | **只有 unit** |
| 基准数据归档 | `reports/bench/` | 目录不存在；实际在 `bench/data` 分支 |

### B3. Phase 0 四个待办的真实状态

| 待办项 | 真实状态 |
| --- | --- |
| 工程骨架与流程规范 `[x]` | **已完成**（Makefile / .cnb.yml / pre-commit / pyproject / 工程文档齐备） |
| base project 选型冻结 `[ ]` | **未冻结且状态自相矛盾**：方向已定为"组装开源组件"，但**没有任何文档列出"复用哪些组件 + 自研边界清单"**；ADR-0003 §6~§8 仍是旧文；四处状态打架（A-1/A-2） |
| 需求规格 + 威胁模型初稿 `[ ]` | SRS 已有；**威胁模型 0%** |
| 目录结构与模块划分 ADR `[ ]` | **该 ADR 不存在**；`src/` 只有事实上的 `agent_sec_perf/bench/`；`pyproject.toml` 运行期依赖**保持为空** |

### B4. 关键判断：产品主体框架的地基与缺口

| 框架层 | 已有可复用的地基 | 缺的基础件 |
| --- | --- | --- |
| **模型层** | `/opt/models` + `.ide/assets/models.txt` + `.ide/fetch-assets.sh` + `bench/runner.py`（llama-server 生命周期与 chat）+ `bench/protocol.py`（档位映射） | 统一模型抽象（本地/云端可路由、能力可探测）⇒ `REQ-MODEL-03/05/06` 无载体 |
| **Harness** | 仅 `bench/protocol.py` 的**评测提示词**（不是 agent 循环） | 全部：ReAct 循环、工具裁剪、提示分级、检查点 ⇒ `REQ-HARNESS-01~08` 无载体 |
| **安全层** | `foundation/proc.py`（隔离 + rlimit + 最小环境 + 唯一进程入口）、`foundation/paths.py`（路径白名单）、`foundation/errors.py`、`bench/evaluate.py`（不可信产物执行边界） | 权限/能力模型、策略引擎、审计、拒答；`docs/design/threat-model/` ⇒ `REQ-SEC-01~09` 无载体 |
| **工具层** | 无 | 全部（文件读写/命令执行/检索）⇒ `REQ-TOOL-01` 无载体 |
| **UX / CLI** | 仅 `bench/rounds.py` 的 argparse 入口（基准专用） | 产品 CLI（Typer）+ 权限确认交互 ⇒ `REQ-UX-01~04` 无载体 |
| **可观测** | `bench/store.py`（结构化 JSON）、`bench/report.py`、`rounds.configure_logging` | 产品级结构化日志 + 审计查看器 ⇒ `REQ-OBS-01` 无载体 |
| **工程地基** | Makefile / .cnb.yml / pre-commit / pyproject（ruff+mypy+pytest）/ tests/unit / 分支卫生脚本 / 发布脚本 / SECURITY.md / 全套 docs | 测试分层目录、`docs/design/*`、威胁模型 |

> **结论**：当前仓库是"**一流的工程地基 + 一个完整可跑的基准评测子系统**"，**不是产品主体**。
> 可复用地基集中在"工程流程 + 安全执行封装（proc/paths/errors）+ 模型服务客户端（runner）"；
> 产品六层里有五层**只有零散零件、没有骨架**，且缺"威胁模型 + 模块划分 ADR"这两个设计前置件。

---

## C. 待核实（缺证据，不下结论）

1. **`make test-security` 的失败退出码**：判断基于 pytest 在"无匹配用例"时返回 5（`NO_TESTS_COLLECTED`），本次为只读核查未实际执行。
2. **CNB `crontab` 事件的令牌是否含 `repo-code:rw`**：devlog 0012 §5 自认仍是未知项（首夜在 `git push` 之前就 SIGPIPE 中止）。缺一次成功发布的 CI 日志或事件权限表。
3. **"base project 已冻结"的判定标准**：ADR-0003 §5.1 未给具体组件清单，因此无法判定 §9 的"选型冻结"是否算完成。缺一份"复用组件 + 自研边界"的 ADR。
4. **`docs/research/reports/*/HARNESS.md` 里的数值**与当前 `bench-v2` 协议口径是否同源：本次只做路径与文内一致性核查，未逐项复算。

---

## D. 本报告的处置

本文**只是核查结论**。每条问题的修复属于对应角色的产出域：

| 问题类 | 归属角色 | 落点 |
| --- | --- | --- |
| A-1/A-2/A-14（决策与状态） | 首席架构师 | 新增 ADR + 索引 |
| A-3/A-4（流程规范） | 首席架构师 | `docs/engineering/git-workflow.md`、`CONTRIBUTING.md` |
| A-5/A-6/A-9/A-11/A-12（文档与实现不符） | 记录员（提出）→ 架构师（裁定） | 各自源文件 |
| A-7/A-8/A-10/A-13（依赖与入口） | 记录员 | 各自源文件 |
| 产品骨架缺口（B4） | 首席架构师 | 新增"分层与自研边界"ADR + `docs/design/architecture.md` |

> 优先级建议：**A-2 / A-3 / A-7 / A-9** 先修（它们会直接误导决策，或让门禁出现永远无法满足的条目）。
