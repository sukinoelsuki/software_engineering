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
| 2 | 端侧模型在受限资源下能做**什么**？ | 能完成"读多个现场文件 → 对照 → 给出可复核结论"的取证任务 | `demo --run` 的 8 条机检全绿 |
| 3 | 它**怎么安全地**做？ | 能力默认拒绝；只授予 `read_file`；允许根只有工作区 + 案例根；每次判定与执行可回放；隔离逐维度如实报告 | `doctor` 的隔离矩阵；审计 JSONL |

**一句话口径**：本阶段证明的不是"模型很聪明"，而是
**"在受限权限与受限资源下，端侧模型仍能承担一段真实工作，且每一步都可核对"**。

---

## 2. 需求对照（只列本阶段直接兑现的条目）

| 需求 | 本阶段交付 | 证据 |
| --- | --- | --- |
| `REQ-UX-01` CLI 交互/非交互 | `run` 保持 JSONL 可脚本化；`demo` 是**非交互**调用方 | `demo --run` 退出码 + JSONL |
| `REQ-UX-03` 一条命令安装 | 入口仍是 `agent-sec-perf`；新增子命令不引入新依赖 | `pyproject.toml` 依赖零改动 |
| `REQ-UX-04` 中文优先 | 引导、手册、体检、演示报告全中文 | `manual` / `doctor` 输出 |
| `REQ-UX-06` 低数字素养可理解性 | 首屏四步路径；体检逐项给"怎么办"；演示报告逐条给证据 | `guidance.py`、`doctor.py` 的 `detail` |
| `REQ-HARNESS-07` 可验证任务自动校验 | `verify_events()` 断言终态 / `seq` / 工具执行 / 结论事实 / 审计回放 | `tests/unit/test_cli_demo.py` |
| `REQ-HARNESS-08` 领域包 | 新增**只读**演示包 `incident-triage`（声明式 TOML，无代码） | `examples/showcase/incident-01/pack/pack.toml` |
| `REQ-SEC-01` 默认拒绝 | 演示授权集合**写死**为 `{read_file}`，不读用户级配置 | `demo.DEMO_GRANTED_CAPABILITIES` |
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
agent-sec-perf demo --run     → 真跑 + 机检 + 审计回放（本次实测 489 s）
agent-sec-perf manual         → 命令速查 + 三条约定 + 文档索引
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

## 4. 案例设计：离线日志根因定位（`incident-01`）

### 4.1 场景与输入

一个**离线**的故障现场（合成数据，无真实主机/凭据）：

| 文件 | 内容要点 | 用途 |
| --- | --- | --- |
| `app.log` | 第一条 `ERROR ... code=E42 timeout_ms=3000`；另有 `WARN` / `INFO` 干扰行 | 模型必须定位"第一条 ERROR"并取出两个值 |
| `service.toml` | `[upstream.payment] timeout_ms = 8000` | 与日志值对照；**不读就答不出** |
| `README.md` | 现场说明（不含答案） | 让人看懂演示，不替模型做题 |

### 4.2 任务与期望输出

任务文本写在 `case.toml` 的 `[case].task`（唯一声明源），要求**两步取证**：
先 `read_file app.log` → 再 `read_file service.toml` → 只输出四行：

```text
CODE=E42
LOG_TIMEOUT_MS=3000
CONFIG_TIMEOUT_MS=8000
VERDICT=<一句中文，说明是否一致>
```

### 4.3 机检判据（8 条，缺一不算成功）

| # | 判据 | 抓的是什么失效 |
| --- | --- | --- |
| 1 | `execute()` 返回 `0` | 任务失败 / 装配失败 / 审计失败 |
| 2 | 恰一条 `TASK_FINISHED`、`status=completed`、且在末尾 | 流被截断或重复收尾 |
| 3 | `seq` 从 `0` 连续无空洞 | 事件流被跳过/拼接 |
| 4 | 工具调用 ≥ 2 次，且含 `read_file` | 只读一个文件就下结论 |
| 5 | 所有 `tool_result` 都是"已执行且成功"，无 `result=None` | 把"被策略拒绝"误当成功 |
| 6 | 最终回答包含 `E42` / `3000` / `8000` | **模型幻觉**（没读到就编） |
| 7 | 每个 `tool_result.audit_id` 都能从落盘 JSONL `query_by_id` 还原 | 审计断链、证据不可回放 |
| 8 | 隔离降级已写入审计（`tier=L1` + 原因） | 静默降级（`ADR-0006` 规则 S-2） |

### 4.4 为什么这 8 条是必要的（一次真实失败）

2026-10-26 第一版任务只说"读取日志、对照配置"，Qwen3-4B **只调用了一次** `read_file`
（读 `app.log`），随后直接输出 `CONFIG_TIMEOUT_MS=5000` —— 一个**看似合理、实际编造**的值。

- 人眼看输出：格式对、语气对、结论"不一致"也对 ⇒ **会误判为成功**；
- 机检：判据 4（调用次数）与 6（事实命中）**同时失败** ⇒ 演示当场暴露问题；
- 处置：把任务改成"两步取证 + 未读第二个文件不得输出该行"，并在领域包里加一条
  "少读一个即视为未完成"的数据片段；重跑后 8 条全绿（证据见 devlog 0031）。

**结论**：`REQ-HARNESS-07` 不是"锦上添花的校验"，它是**唯一能区分"会说话"与"读过文件"**的机制。

### 4.5 失败模式与处置

| 现象 | 根因 | 处置 |
| --- | --- | --- |
| 只调用 1 次工具 | 提示里没有"少读一个就无效"的硬约束 | 加强 `[case].task`；不要靠加大模型解决 |
| 输出 `LENGTH` 截断 / 无工具调用 | `max_completion_tokens` 太小（思考模型） | 案例默认 `1536`；换模型必须重测 |
| 单次补全超时 | 弱硬件生成速度约 2~4 tok/s | 案例默认 `request_timeout_s=600` |
| 工具全被拒 | 没授予 `read_file` / 领域包不在允许根内 | `demo` 自动写 `.lowspec.toml`；允许根含案例根 |
| 装配期退出码 `3` | 模型路径缺失 / 领域包路径越界 | 先跑 `doctor`；看 stderr 的 `error_type` |

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

### 5.2 演示用到的四层约束

| 层 | 机制 | 谁强制 | 演示里怎么看得见 |
| --- | --- | --- | --- |
| 授权 | 能力默认拒绝；只授予 `read_file` | `PolicyEngine` | `policy_decision` 事件 / 审计 `allow` |
| 面 | 领域包 + 档位裁剪：只暴露 `read_file` / `list_dir` | `trimming` + `load_pack` | 模型请求其它工具 ⇒ `not_exposed` + 拒绝 |
| 路径 | `allowed_roots = (工作区, 案例根)` | `foundation.paths.resolve_within` | 越界读 ⇒ `PathNotAllowedError` / `ok=False` |
| 进程 | 非特权 uid + rlimit + 最小环境 | `foundation.proc` | `doctor` 的隔离矩阵（负向证据） |

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
3. **案例是只读的**：不演示写文件 / 执行命令；这两类工具的风险默认仍要求人工确认。
4. **耗时是观测值**：2026-10-26 一次真跑 **489 s**，受机器负载影响；**不是**性能结论。
5. **`REQ-UX-05`（TUI/Web）未兑现**；本阶段只交付 CLI。
