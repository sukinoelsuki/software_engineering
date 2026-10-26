# 阶段产品设计：CLI 交付形态与「离线日志根因定位」演示案例

- **时点**：2026-10-26（成果展示当晚）
- **范围**：本阶段**不做前端包装**，把产品收敛为"一个可上手的 CLI + 一个可机检的端侧 agent 案例"。
- **上游**：需求基线 [`../requirements/srs.md`](../requirements/srs.md)（49 条，编号不改）；
  接口契约 [`interfaces/harness.md`](interfaces/harness.md)、[`interfaces/sandbox.md`](interfaces/sandbox.md)；
  威胁模型 [`threat-model/README.md`](threat-model/README.md) §4.1。
- **实现**：`src/agent_sec_perf/cli/{app,guidance,doctor,demo}.py`、
  `src/agent_sec_perf/security/sandbox/probe.py`、`examples/showcase/incident-01/`。
- **演示脚本**（怎么讲、怎么跑、出问题怎么办）：[`../engineering/demo-showcase.md`](../engineering/demo-showcase.md)。

---

## 1. 本阶段要回答的三个问题

| # | 问题 | 本阶段的答案 | 可核对的形式 |
| --- | --- | --- | --- |
| 1 | 没有前端，产品能不能被人用起来？ | 四条命令形成上手路径：`doctor → demo → demo --run → manual`；无参数启动给引导 | `agent-sec-perf` 首屏；`agent-sec-perf manual` |
| 2 | 端侧模型在受限资源下能做**什么**？ | 能完成**一段完整业务**：读两份现场文件 → 写报告初稿 → 读回报告、按复核意见只改结论行 | `demo --run` 的 11 条机检全绿 |
| 3 | 它**怎么安全地**做？ | 能力默认拒绝；授权 `{read_file, write_file}`，但暴露面只有 `write_report`（目标固定为 `report.md`、参数只有内容）；允许根只有工作区 + 案例根；每次判定与执行可回放；隔离逐维度如实报告 | `doctor` 的隔离矩阵；审计 JSONL |

**一句话口径**：本阶段证明的不是"模型很聪明"，也不是"它调通了一个工具"，而是
**"在受限权限与受限资源下，端侧模型能承担一段有输入、有产出、有复核与修订的真实工作，且每一步都可核对"**。

> 业务闭环比"工具调用"多出的三件事，正好是本案例的三条主线：
> ① **产出**（写 `report.md`，不是一个回答字符串）；② **复核**（人给修改意见）；
> ③ **受控修订**（只改指定那一行，其余逐字不变）。


---

## 2. 需求对照（只列本阶段直接兑现的条目）

| 需求 | 本阶段交付 | 证据 |
| --- | --- | --- |
| `REQ-UX-01` CLI 交互/非交互 | `run` 保持 JSONL 可脚本化；`demo` 是**非交互**调用方 | `demo --run` 退出码 + JSONL |
| `REQ-UX-03` 一条命令安装 | 入口仍是 `agent-sec-perf`；新增子命令不引入新依赖 | `pyproject.toml` 依赖零改动 |
| `REQ-UX-04` 中文优先 | 引导、手册、体检、演示报告全中文 | `manual` / `doctor` 输出 |
| `REQ-UX-06` 低数字素养可理解性 | 首屏四步路径；体检逐项给"怎么办"；演示报告逐条给证据 | `guidance.py`、`doctor.py` 的 `detail` |
| `REQ-HARNESS-07` 可验证任务自动校验 | `verify_stages()` 断言两阶段终态 / `seq` / 工具次数 / 工具执行 / 落盘报告 / **修订保真** / 审计回放 | `tests/unit/test_cli_demo.py` |
| `REQ-HARNESS-08` 领域包 | 新增演示包 `incident-triage`（声明式 TOML，无代码；暴露面 = `read_file` / `list_dir` / `write_report`） | `examples/showcase/incident-01/pack/pack.toml` |
| `REQ-SEC-01` 默认拒绝 | 演示授权集合**写死**为 `{read_file, write_file}`，不读用户级配置；`write_file` 能力只被**目标固定**的 `write_report` 使用，通用 `write_file` 工具不在 allowlist | `demo.DEMO_GRANTED_CAPABILITIES`；`pack.toml` 的 `allowlist` |
| `REQ-SEC-05` 隔离可探测、fail-secure | `probe_isolation()` 用**负向探针**给出逐维度矩阵；L0 拒绝执行 | `doctor --json` 的 `isolation.dimensions` |
| `REQ-SEC-06` 全链路审计 | 每次策略判定 + 工具调用落 JSONL；演示按 `audit_id` 回放 | 机检「审计回放」 |
| `REQ-SEC-08` 能力边界拒答 | 未授权能力的调用不执行、不伪造执行结果；拒绝路径进审计 | 舞台 5 的默认拒绝对照（手册 §3） |

**未兑现、也不在本阶段宣称的**：`REQ-UX-05`（TUI/Web）、`REQ-UX-02` 的交互式确认体验
（`--interactive` 已有，但演示不走该路径）、`REQ-SEC-04`（注入检测）、`REQ-PERF-*`（性能结论）。

---

## 3. 产品形态：四条命令，一条主动线

```text
agent-sec-perf                → 首屏引导（无子命令时）
agent-sec-perf doctor         → 环境体检（模型 / 后端 / 配置 / 审计 / 隔离 / 案例资产）
agent-sec-perf demo           → 准备隔离工作区（不调用模型，秒级）
agent-sec-perf demo --run     → 真跑两阶段（诊断→写初稿→读回→修订）+ 机检 + 审计回放（墙钟见演示手册 §6）
agent-sec-perf manual         → 命令速查 + 关键约定 + 文档索引
agent-sec-perf run "<任务>"   → 用自己的任务跑（既有能力，参数未变）
```

设计取舍：

| 决策 | 理由 | 被否决的替代 |
| --- | --- | --- |
| 无子命令时打印引导，而不是只给 `--help` | `--help` 解释参数，解释不了"先做什么"（`REQ-UX-06`） | 维护一份 README 让人自己找（演示时多一次切换） |
| `doctor` 与 `demo` 分成两条命令 | "环境坏了"与"案例跑挂了"是两类故障，必须能分开定位 | 合成 `demo --run` 一步（失败时无法区分是模型不行还是环境不行） |
| `demo` 默认**不跑模型**，`--run` 才跑 | 准备/检查是秒级、可反复；真跑是分钟级、要挑时机 | 默认就跑（演示前彩排一次要等 5 分钟） |
| 演示授权集合写死在代码里 | 用户级 `config.toml` 可能授予更多能力；演示必须证明"最小授权也能完成工作" | 读 `load_config()`（演示权限可能大于标准配置，结论不可比） |
| 机检写进产品而非只写文档 | "跑完了"≠"跑对了"；模型幻觉只有机检能抓住（见 §4.4 实测） | 人眼看输出（第一版就漏掉了幻觉值 `5000`） |

---

## 4. 案例设计：离线日志根因定位与报告修订（`incident-01`）

### 4.1 场景与输入

一个**离线**的故障现场（合成数据，无真实主机/凭据）：

| 文件 | 内容要点 | 用途 |
| --- | --- | --- |
| `app.log` | 第一条 `ERROR` 是 `timeout_ms=3000 code=E42`；另有 `WARN` / `INFO` 干扰行，以及**第二条取值不同**的 `ERROR`（`code=E99` / `timeout_ms=5000`） | 模型必须定位"第一条 ERROR"并取出两个值；取错第二条会被机检抓住 |
| `service.toml` | `[upstream.payment] timeout_ms = 8000` | 与日志值对照；**不读就答不出** |
| `README.md` | 现场说明（不含答案） | 让人看懂演示，不替模型做题 |
| `report.md` | **不是输入，是产出**：由模型在诊断阶段写入、修订阶段覆盖 | 业务的"交付物"，也是机检的落盘对象 |

### 4.2 两个阶段、两个会话、五步工具调用

任务文本写在 `case.toml` 的 `task_draft` / `task_revise`（唯一声明源）：

| 阶段 | 会话 | 模型要做的事 | 期望产出 |
| --- | --- | --- | --- |
| **A 诊断**（`draft`） | 第 1 个 | `read_file app.log` → `read_file service.toml` → `write_incident_report`（四个字段值，行格式由工具生成） | `report.md` 初稿 |
| **B 修订**（`revise`） | 第 2 个（新会话） | `read_file report.md` 读回初稿 → `revise_report(key="VERDICT", value=...)`，**其余三行由工具原样保留** | `report.md` 终稿 |

```text
CODE=E42
LOG_TIMEOUT_MS=3000
CONFIG_TIMEOUT_MS=8000
VERDICT=配置值 8000 大于日志值 3000，日志侧超时更短
```

**为什么拆成两个会话**（不是设计偏好，是实测约束）：4B 级模型在"一次会话里连做五步"时
容易跳步（2026-10-26 实测：只读一个文件就编出配置值）。拆开后每段判据更窄，
失败能定位到"是诊断错了，还是修订错了"；而"读回—修改"本身就是真实工作流的形态。
**人给意见**的那一步（复核）发生在两个会话之间，由 `task_revise` 承载。

**为什么修订不让模型重写全文（第三次实测后改的）**：先把"只改结论行"写成提示约束，
两次实测都失败——第一次三行只剩 KEY（丢了值），第二次整份报告被重写成 3 行并
**多出一条幻觉行 `STATUS=ACTIVE`**。小模型不擅长"逐字复制其余内容"，
而这件事工具可以保证：`revise_report(key, value)` 读现有报告、只替换那一行、写回，
`key` 必须已存在且只出现一次。⇒ 「修订保真」从"靠模型自觉"变成**结构上不可能违反**。

**为什么写报告也不让模型拼字符串（第六次实测后）**：`write_report(content)` 加了格式闸门后，
模型第一次仍会把 `VERDICT=待复核` 写成只有行名，被拒后**直接放弃**（不再重试）⇒ 整段 `FAILED`。
⇒ 改用**领域工具** `write_incident_report(code, log_timeout_ms, config_timeout_ms, verdict)`：
模型只提交四个字段值，`KEY=VALUE` 行由工具生成。
**"格式写错"这个失效模式从此不存在**——与 `revise_report` 是同一条原则（第三次应用）。

⚠️ 为什么是**显式四字段**而不是"任意键值对象"：项目参数校验器（`harness/arguments.py`，
ADR-0020）要求 `additionalProperties` 显式为 `false`；"任意键对象"表达不了，
用它定义的工具**每次调用都被判参数非法**（2026-10-26 实测：`deny / invalid_arguments`，
工具根本没被调用）。这条约束决定了"领域工具"的形状必须是封闭字段表。

**为什么工具还要校验"值出自文件"（第七次实测后）**：格式对了之后，模型仍**编造取值**——
实测写出 `CODE=PAY001` / `LOG_TIMEOUT_MS=1200`，两份现场文件里都没有这两个值。
⇒ `write_incident_report` 在写入前检查三个取证字段的值**逐字出现在**现场文件里，
否则拒绝并指出哪个字段找不到（`reason=value_not_in_evidence`）。

这不是"工具替模型做题"：工具只判断"这个字符串有没有在文件里出现过"，
**不选行、不推断、不填默认值**——模型仍要自己读、自己定位"第一条 ERROR"、自己挑值。
它拦掉的只是"根本没读就编"。

**为什么两个阶段用两个领域包（第五次实测后）**：工具暴露面是**会话级**固定的
（`trimming.select_tools` 在装配期算一次）。把 `write_report` 与 `revise_report`
放进同一个包时，模型在**诊断阶段**（报告还不存在）误调 `revise_report` 两次，
连续失败三次后整段 `FAILED`。⇒ 诊断包只暴露 `write_report`、修订包只暴露 `revise_report`：
**每个阶段看不到不属于它的动作**。这是同一条原则的第二次应用——
不是加提示，而是把动作从工具面上取消。

### 4.3 机检判据（11 条，缺一不算成功）

| # | 判据 | 抓的是什么失效 |
| --- | --- | --- |
| 1 | **阶段完整**：实际阶段序列 == `draft → revise` | 诊断失败后仍把"修订成功"当整体成功；或漏跑阶段 |
| 2 | `draft` 恰一条 `TASK_FINISHED`、`status=completed`、且在末尾 | 流被截断或重复收尾 |
| 3 | `draft` 的 `seq` 从 `0` 连续无空洞 | 事件流被跳过/拼接 |
| 4 | `revise` 恰一条 `TASK_FINISHED`、`status=completed`、且在末尾 | 同上（修订段） |
| 5 | `revise` 的 `seq` 从 `0` 连续无空洞 | 同上（修订段） |
| 6 | 工具调用 ≥ 5 次，其中 `read_file` ≥ 3、写 ≥ 2 | 只读一个文件就下结论；只写初稿不修订 |
| 7 | 所有 `tool_result` 都是"已执行且成功"，无 `result=None` | 把"被策略拒绝"误当成功 |
| 8 | 落盘 `report.md` 命中 `E42` / `3000` / `8000` / 结论句，且**不含**"待复核"（**比较时忽略空白**） | **模型幻觉**；只写初稿没修订；把排版差异误判成内容差异 |
| 9 | **修订保真**：`CODE` / `LOG_TIMEOUT_MS` / `CONFIG_TIMEOUT_MS` 初稿与终稿**逐字相同**，`VERDICT` 确实变了，且每个 key 在终稿恰出现一次 | 用"重新生成一份"冒充"按意见修订"；把旧值留一行、正确值另起一行。**工具层已保证结构**（`revise_report`），本判据兜住"工具被绕过/被误用" |
| 10 | 每个 `tool_result.audit_id` 都能从落盘 JSONL `query_by_id` 还原 | 审计断链、证据不可回放 |
| 11 | 隔离降级已写入审计（每个会话一条，机检汇总为一行） | 静默降级（`ADR-0006` 规则 S-2） |

判据 1、9 是"完整业务"相对"单次工具调用"多出来的部分：
**没有判据 1，"只跑了一半"会被当成成功；没有判据 9，"改了全部"会被当成"只改该改的"。**

### 4.4 为什么这些判据是必要的（两次真实失败）

**失败一（只读一个文件就编）**：第一版任务只说"读取日志、对照配置"，Qwen3-4B **只调用了一次**
`read_file`（读 `app.log`），随后直接输出 `CONFIG_TIMEOUT_MS=5000` —— 一个**看似合理、实际编造**的值。

- 人眼看输出：格式对、语气对、结论"不一致"也对 ⇒ **会误判为成功**；
- 机检：调用次数与事实命中**同时失败** ⇒ 演示当场暴露问题；
- 处置：把任务改成"两步取证 + 未读第二个文件不得输出该行"，并在领域包里加一条
  "少读一个即视为未完成"的数据片段。

**失败二（一次会话做五步会跳步）**：把"读两份 + 写 + 读回 + 改"塞进一个会话后，
同一个模型会跳过中间步骤（写报告前就想收尾，或修订时直接凭记忆重写全文）。
处置：**拆成两个会话**，并把"只改结论行"变成判据 7（比对初稿快照与终稿）。
这不是"模型不行"的托词——它把失败从"不可定位"变成"能说是哪一段的问题"。

**结论**：`REQ-HARNESS-07` 不是"锦上添花的校验"，它是**唯一能区分**
"会说话"与"读过文件"、"重新生成"与"按意见修订"的机制。

### 4.5 失败模式与处置

| 现象 | 根因 | 处置 |
| --- | --- | --- |
| 只调用 1 次工具 | 提示里没有"少读一个就无效"的硬约束 | 加强 `task_draft`；不要靠加大模型解决 |
| 只写初稿、没修订 | 模型跳步，或修订阶段没被触发 | 机检判据 6 抓"待复核"残留；诊断失败时**不跑**修订（不掩盖） |
| 修订时重写全文 / 改到取证行 / 多出幻觉行 | 小模型无法"逐字复制其余内容" | **改用 `revise_report`**（只替换一行，其余由工具保留）；判据 7 再逐字比对兜底 |
| 复用工作区时"通过" | 上一轮的 `report.md` 还在 | `prepare_workspace` 显式删除旧报告（`_remove_stale_report`） |
| 报告写到别处 | 模型想用通用 `write_file` | `write_file` 不在 allowlist（等同不存在）；`write_report` 目标固定 |
| 照抄提示里的尖括号占位符 / 改写时只写 KEY | 小模型把模板当字面量、改写时丢值 | **工具的格式闸门**在写入时拒绝（`reason=invalid_report_format`）；提示同时改成"等号右边只写值本身" |
| 输出 `LENGTH` 截断 / 无工具调用 | `max_completion_tokens` 太小（思考模型） | 案例默认 `1536`（每阶段独立预算）；换模型必须重测 |
| 单次补全超时 | 弱硬件生成速度约 2~4 tok/s | 案例默认 `request_timeout_s=600` |
| 工具全被拒 | 没授予能力 / 领域包不在允许根内 | `demo` 自动写 `.lowspec.toml`；允许根含案例根 |
| 装配期退出码 `3` | 模型路径缺失 / 案例资产缺失 / 领域包路径越界 | 先跑 `doctor`；看 stderr 的 `error_type` |

### 4.6 档位与暴露面：**授权 ≠ 暴露**（一次真实失败）

2026-10-26 把案例改成两阶段后，第一轮用 `RunRequest` 的默认档位跑，结果是：
模型**连续 7 次** `read_file`、**0 次**写，最终 `LIMIT_REACHED`。

根因不是提示词，也不是模型不会写：`CapabilityTier.BASIC` 的能力预算只有
`READ_FILE`（`harness/trimming.py::TIER_CAPABILITY_BUDGET`），而 `write_report`
声明的是 `WRITE_FILE` ⇒ **它从未出现在模型可见的工具列表里**。
授权集合里有 `write_file` 也没用：**授权（能不能做）与暴露（看不看得见）是两层**，
任何一层不满足，模型都只能空转。

处置：

- `demo.DEMO_CAPABILITY_TIER = CapabilityTier.STANDARD`（预算 = 读 + 写）；
- 用例 `test_demo_tier_budget_exposes_write_report` 钉死"演示档位必须覆盖写"，防止改回 `BASIC`；
- `demo` 的启动信息里显示档位，让"这一层"在演示时是可见的。

⚠️ **提高档位不是扩权**：授权仍写死为 `{read_file, write_file}`，领域包 allowlist 仍只有
三个工具。档位轴是"模型能力"，不是"权限"（`contracts/model.py` 明令两条轴正交）。

---

## 5. 隔离设计：先有负向证据，才有"已隔离"

### 5.1 2026-10-26 云环境实测

| 机制 | 结果 | 判据 |
| --- | --- | --- |
| user namespace（`unshare -Ur`） | **不可用**：`Operation not permitted` | 内核/容器安全策略禁用 |
| `bwrap` 最小命令 | **不可用**：`Creating new namespace failed` | 同上；"装了 bwrap"≠"bwrap 能用" |
| `firejail --net=none` | **fail-open**：网络仍可达、`CapEff` 未变、`/etc` 可写 | 正是 `ADR-0007` §3.1 记录的形态；**不得**据此宣称隔离 |
| `proc.run(isolation="user")` | **可用**：uid=65534 | 负向探针：子进程回读 uid |
| `RLIMIT_AS` | **可用**：3 GiB 分配被拒 | 负向探针：`MemoryError` |
| `RLIMIT_FSIZE` | **可用**：65 MiB 写入被拒 | 负向探针：写入失败 |

⇒ 本次演示的真实档位是 **L1**，有负向证据的维度：`user` / `address_space` / `file_size`。
其余维度（`filesystem` / `network` / `capabilities` / `cpu_time` / `process_count`）
**如实记为未隔离**，并在审计里落一条 `execution_degradation`。

### 5.2 演示用到的五层约束

| 层 | 机制 | 谁强制 | 演示里怎么看得见 |
| --- | --- | --- | --- |
| 授权 | 能力默认拒绝；授予 `read_file` + `write_file` | `PolicyEngine` | `policy_decision` 事件 / 审计 `allow` |
| 面 | 领域包（按阶段两个）+ 档位裁剪：诊断阶段只暴露 `read_file` / `list_dir` / `write_report`，修订阶段只暴露 `read_file` / `revise_report`；通用 `write_file` **不可见** | `trimming` + `load_pack` | 模型请求其它工具 ⇒ `not_exposed` + 拒绝 |
| 写 | `write_report` 目标固定为工作区 `report.md`（参数只有 `content`）；`revise_report` 只替换**已存在且唯一**的一行（参数 `key` + `value`） | `tools/files.py::WriteReportTool` / `ReviseReportTool` | 模型无法指定路径、无法新增行；每次写与修订各留一条 `TOOL_CALL` 审计 |
| 路径 | `allowed_roots = (工作区, 案例根)` | `foundation.paths.resolve_within` | 越界读写 ⇒ `PathNotAllowedError` / `ok=False` |
| 进程 | 非特权 uid + rlimit + 最小环境 | `foundation.proc` | `doctor` 的隔离矩阵（负向证据） |

⚠️ **授权的 `write_file` 不是"给模型开放了任意写"**：能力是粗粒度的（`WRITE_FILE`），
真正收窄影响面的是**工具**——`write_report` 的目标路径不来自模型输入。
这也是本阶段唯一涉及"写"的设计决策，理由与"为什么不能把通用 `write_file` 声明成 `low`"
写在 `examples/showcase/incident-01/pack/pack.toml` 的注释里。

⚠️ **不得表述为"已隔离文件系统/网络"**：L1 下这两项是**应用级拦截**，
不是内核隔离。真实表述见 [`interfaces/sandbox.md`](interfaces/sandbox.md) §3 的诚实结论。

### 5.3 契约缺口：`IsolationMechanism` 增加 `PRIVDROP`

`setuid` 既不是 `SETRLIMIT`、也不是 `NAMESPACE`/`CGROUP`。在补这个成员之前，
`USER` 维度只有两个选择：**误标**成 `SETRLIMIT`，或把已通过的探针记成未隔离。
两者都违反"证据必须准确"。故 2026-10-26 新增 `PRIVDROP = "privdrop"`，
由 `probe.py` 的 `uid=65534` 负向探针使用；留痕写在
[`interfaces/sandbox.md`](interfaces/sandbox.md) §5。**这是本阶段唯一的契约增量**，
且是**加成员**（不改既有语义、不移除任何值）。

---

## 6. 范围收窄的理由（为什么不做前端）

| 候选 | 今晚成本 | 风险 | 结论 |
| --- | --- | --- | --- |
| 轻量 Web/TUI（`REQ-UX-05`） | 高（新依赖/新装配点/新测试面） | 演示时多一个可能挂的环节 | ❌ 不做 |
| CLI 引导 + 体检 + 一键演示 | 中 | 低（复用既有 `execute()`） | ✅ 本次做 |
| 只写文档、不做命令 | 低 | 演示时靠手敲 6 条命令，易错 | ❌ 不够 |

**取舍**：把"能被人用起来"做进产品（`doctor`/`demo`），把"讲清楚"做进文档
（`demo-showcase.md`）。`REQ-UX-05` 仍是 Should 级未兑现项，不在本阶段宣称完成。

---

## 7. 已知边界（展示时不得放大）

1. **隔离是 L1**：文件系统 / 网络**没有内核级隔离**；`T-10`（未授权出站）仍未缓解。
2. **单机、单模型、无出站**：没有模型路由、没有云端客户端、没有检索工具。
3. **案例的写是受限的**：只演示 `write_report`（目标固定 `report.md`）；通用 `write_file`
   与 `run_command` 的风险默认仍要求人工确认，演示**不**触碰这两条线。
4. **耗时是观测值**：单阶段版本曾实测 **489 s**；两阶段版本为两次会话、五步工具调用，
   墙钟见 [`../engineering/demo-showcase.md`](../engineering/demo-showcase.md) §6。
   受机器负载影响，**不是**性能结论。
5. **`REQ-UX-05`（TUI/Web）未兑现**；本阶段只交付 CLI。
