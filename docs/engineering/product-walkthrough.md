# 产品跑测导引（阶段式：执行顺序 · 每步看什么 · 为什么这么设计 · 代价是什么）

- **面向**：项目所有者（人类维护者）。**用途**：在**下一次对话**里被当作脚本使用——
  "按阶段带我跑一遍，并解释每一步背后的取舍"。
- **与手册的分工（重要，本文的价值就在这条分工上）**：
  **命令真源是 [`product-handbook.md`](product-handbook.md)**（§2 五分钟上手 / §3 五个舞台 / §4 调试手册）。
  **本文不重抄任何命令、不复述手册里的数字**，只写四件事：**执行顺序**、**每步看什么**、
  **为什么这么设计**、**常见误读**。
  - **为什么不重抄**：本项目的重复失效模式是"同一事实两处表述 ⇒ 必然漂移"
    （`docs/design/interfaces/README.md` §2 的 `C1`~`C10` 正是为此而写；`C10` 的判据是
    "**文档内部出现分歧时，以正文的类型定义 + 文末『改动清单』为准**"）。命令与实测输出只能有一份。
  - 手册没有覆盖的少数环节（如真模型集成用例的跑法），本文**同样只给"指向哪一节"**，
    不复制命令原文（见"进阶 D"与 §C）。
- **本文不宣称任何实测**：本文**没有一条输出**是本文作者跑出来的。需要"输出长什么样"，
  一律指向手册已记录的那一次，或 `docs/research/` 的取证报告。**禁止**把本文当成第二份实测记录。
- **权威源指针**（本文只引用，不改写）：分层与依赖方向
  [`ADR-0015`](../adr/0015-layering-and-reuse-boundary.md)；字段级契约
  [`docs/design/interfaces/`](../design/interfaces/README.md)；总体架构与实现状态表
  [`architecture.md`](../design/architecture.md)；威胁状态
  [`threat-model/README.md`](../design/threat-model/README.md) §4.1；里程碑判据
  [`sdlc.md`](sdlc.md) §3；测试分层与跑法 [`testing-strategy.md`](testing-strategy.md)。
- **放这里的原因**：`docs/engineering/` 已有同类非规范文档（`post-build-checklist.md`）。
  按"新目录必须先有 ADR"，本文**不新建目录**。

---

## 0. 怎么用这份文档

### 0.1 开新对话的一句话（可直接复制）

> 按 `docs/engineering/product-walkthrough.md` 带我跑**阶段 N**。规则：逐步执行、一次只跑一个阶段；
> 命令我自己敲，我把**原始输出**（含退出码与 stderr）贴回；你不要替我跑、不要替我猜输出；
> 输出与文档"怎么看"不符时**先停下**，不要自行改参数重试。

替换 `N` 的建议顺序是 **1 → 2 → 3 → 4 → 5 → 6**；`进阶 A~D` 按需选做。

### 0.2 逐步执行纪律（四条，写死）

1. **一次一个阶段**。阶段 3/4/5 都要真起模型，属长耗时；把三段挤进一次对话会让会话变长——
   而"长会话是最大隐性开销"（`CODEBUDDY.md` §10.3 规则 1）。
2. **命令由所有者亲自敲**，助手只解释"该看什么"。助手**不得**替跑、**不得**编造输出。
3. 输出与本文"怎么看"相符才继续；**不符就停下**，把**原始**输出（含退出码与 stderr 那一行）贴回。
   **不要**自行改参数"试到绿"——那会把一次可定位的配置问题变成一次不可解释的偶发成功。
4. 跑测**不产生仓库改动**：临时工作目录在 `/tmp`、审计落用户状态目录（手册 §8 的复现提示）。
   审计与 devlog 的落盘职责见阶段 6 与 §C。

### 0.3 每个阶段的固定四栏

本文每个阶段都按同样四栏写，便于对照与评审：

| 栏 | 回答什么 |
| --- | --- |
| ① 目的与判据 | 这一步在验证**哪条需求 / 哪条契约不变式**，**什么算通过** |
| ② 命令 | **指向手册的哪一节**（本文不抄命令） |
| ③ 怎么看输出 | 该看哪几个字段 / 什么算异常 / 异常先怀疑什么 |
| ④ 设计取舍与代价 | 为什么这样设计、代价是什么、**哪个替代方案被否决了**（都给出处） |

---

## 阶段 1：环境与门禁（不需要模型）

### ① 目的与判据

先在**这台机器**上确认"底线护栏是绿的"。判据：`make check` 与 `make test-security` **退出码均为 0**。
这一步**不证明安全**（见 ④），它证明的是"约定要求的东西都在、且现有用例都能过"。

### ② 命令

照手册 **§2.1（环境自检）** 与 **§3 舞台 0（质量门禁）** 执行。本文不重抄。

### ③ 怎么看输出

- `make check` 的组成是 `hooks-check → format-check → lint → typecheck → test → security`
  （`Makefile:147`）。`hooks-check` **排在最前是刻意的**——本地防线缺失时就该**立刻**失败，
  而不是等 100+ 用例跑完（`Makefile:145-146` 的注释）。
- `test` 的口径是 `-m "not benchmark and not slow"`（`Makefile:92`）
  ⇒ **通过数不等于全仓用例数**，少掉的正是基准与真模型用例。
- `security` 由三项组成：bandit + detect-private-key 复用 + pip-audit（`Makefile:111`）。
  ⚠️ bandit **只扫 `src/`**（`Makefile:114`）⇒ `security` 绿灯**不覆盖** `tests/`、`docs/`、`.cnb.yml`。
  这是 `T-06`（安全豁免被静默放宽）"覆盖面不全"的一部分（`threat-model/README.md` §5 的 `T-06` 行）。
- `make test-security` **零用例时会主动失败**（exit 5 → 1，`Makefile:97-106`）。
  ⇒ 若报"没有任何 security 标记的用例"，那是**门禁在按设计工作**，不是环境坏了。
- 最可能的异常：`hooks-check` 失败 = 本地 git 钩子没装。手册 §2.1 的注给了解法。

### ④ 设计取舍与代价

1. **取舍：把"本地防线是否真的存在"做成断言，而不是"记得装"。** 代价：首次跑会被拦下。
   为什么需要它：`$CI` 的**存在性**对"云开发工作区"与"CI 流水线"是同一个值、语义却不同，
   曾导致钩子被静默跳过且长期无人察觉 ⇒ 改为**显式开关** `LOCAL_HOOKS`（默认 1，CI 用 0）
   （`Makefile:23-32` 的注释原文）。
2. **取舍：安全测试层零用例 = 失败（fail-secure）。** 代价：任何标记丢失都变成红灯；
   收益：不会出现"安全测试层其实是空的"这种静默失效（`Makefile:100-105`）。
3. **常见误读（最重要的一条）：`make check` 全绿 ≠ 安全已到位。**
   威胁模型 §0 结论 2 写得很直白：`tests/unit/test_bench_encapsulation.py` 与
   `tests/unit/test_architecture_layers.py` 这类**结构性检查**"**证明代码长成约定要求的样子，
   不证明攻击被挡住了**"（`threat-model/README.md` §0）。

---

## 阶段 2：装配期拒绝（秒级，**不起模型**）

### ① 目的与判据

验证"**配置错误 ⇒ 拒绝启动**，且**与任务失败可区分**"。判据：**退出码 `3`**、
stderr 一条结构化 JSON 诊断、**秒级返回**（模型进程都还没起）。

### ② 命令

照手册 **§3 舞台 1**。本文不重抄（该舞台的要点是"领域包落在允许根之外"）。

### ③ 怎么看输出

- 看 stderr 那条 JSON 的 `error_type`：本舞台应为 `PathNotAllowedError`。
- 看**退出码**：`3` = 装配/配置故障一族（`BenchError` 家族）。退出码表真源
  `interfaces/harness.md` §5.2；实现常量在 `src/agent_sec_perf/cli/app.py:100-105`。
- **什么算异常**：返回码 `5`（未预期异常）或耗时达到"起模型"的量级（说明没在装配期拦下）。

### ④ 设计取舍与代价

1. **为什么"领域包在允许根之外"必须在装配期失败**：`load_pack` 的 `roots` 是
   **必填 keyword-only、无默认值**，且路径**先校验后读**（`interfaces/harness.md` §4.1 的
   `P3`/`P4`）。`P4` 给的理由是：默认值会让"忘了传"退化为"任意路径"。
2. **为什么是退出码 3，而不是 1**：`REQ-UX-01` 要求非交互可进 CI ⇒ **故障类别必须可区分**；
   "把**任务失败**与**环境/配置故障**都返回 1，会让 CI 无法判断该重试还是该修环境"
   （`interfaces/harness.md` §5.2 退出码表下的注）。
3. **为什么装配期故障不进事件流**：装配发生在 `Session.run()` **之前**，
   此时既没有会话也没有事件流；契约明确列了会逃逸到 CLI 的三类
   （`ConfigError`、**审计写入失败**、编程缺陷）（`interfaces/harness.md` §2.9 的错误面表）。
4. **为什么不给 `roots` 一个默认值、为什么不降级为"无 pack 继续跑"**：
   前者 = 放宽白名单（`P4`）；后者 = **静默降级**，被 `ADR-0006` §5.2 的规则 `S-2` 明令禁止
   （`interfaces/harness.md` §4.3 的失败模式表：任何失败**禁止**降级、部分加载、忽略未知键）。
5. **代价**：配置错误会**当场拒绝启动**，用户必须先修配置才能看到任何模型行为——
   换来的是一条不会"跑起来之后才在半路出错"的路径。

**常见误读**：`3` 不等于"模型起不来"。模型起不来也走 `3`（`ModelUnavailableError` 同族），
所以**必须看 `error_type`，不要只看退出码**。另外 `--pack` 接的是**目录**、
不是 `pack.toml` 文件本身（`examples/README.md` §怎么用）。

---

## 阶段 3：真模型多步会话（主舞台）

### ① 目的与判据

验证端到端闭环：真实 `llama-server` + 真实 GGUF，**模型自己决定调用工具**，
工具**真的读到磁盘内容**，退出码表达结果。
可断言的判据（来自 `tests/integration/test_end_to_end.py` 的 docstring 与
`interfaces/harness.md` §2.2 的不变式）：

- `TASK_FINISHED.status is COMPLETED`（退出码 `0`）；
- `seq` **从 0 连续、无空洞**，`TASK_FINISHED` **恰好一条且在最后**（`I6`/`I9`）；
- 至少 2 次工具调用，且**第二步的输入来自第一步的输出**（不是把同一件事说两遍）
  —— 这是 `M2-1`/`M2-4` 的形态。

### ② 命令

照手册 **§2.2（一次最小真实会话）** 与 **§3 舞台 2**。**四个不能省的开关**及其理由表在手册 §2.2，
本文不复制。

### ③ 怎么看输出

- `--output-format json` 时 **stdout 只出 JSONL**：一行一个事件、**逐行 `json.loads` 都必须成功**；
  进度与诊断一律走 **stderr**（`interfaces/harness.md` §5.2 的输出通道两条硬规定）。
- 事件 kind 的顺序语义（`interfaces/harness.md` §2.1 的产出时机表）：
  一次模型往返 = 一条 `model_response`；模型要工具 ⇒ `tool_call`；策略求值 ⇒ `policy_decision`；
  执行结果 ⇒ `tool_result`。**两次 `tool_call` = 两步**（不是一次批量）。
- ⚠️ **字段在嵌套对象里**（本导引里最容易读错的一处）：
  `ok` / `audit_id` 在 `tool_result` 事件的 **`result` 对象内部**（`result.ok` / `result.audit_id`）；
  **最终回答不在 `task_finished`**（它只有 `status` 与 `text`），而在**末条 `model_response` 的
  `response.content`**。口径来源：手册 §2.3 的注（2026-09-21 独立复核指出并更正了呈现层级，
  证据 `docs/research/2026-09-21-handbook-reproducibility-review.md`）。
- **什么算异常**（对照 `interfaces/harness.md` §2.2 的 `I1`~`I10`）：`seq` 有空洞；
  `TASK_FINISHED` 不在最后；`TOOL_CALL` 没有配对的 `TOOL_RESULT`。
  ⚠️ **配对有一条唯一例外**：当"我方不变量/基础设施"故障发生时（审批通路故障 `R3`/`R4`、
  `resolve` 返回 `None`、工具 `audit_id` 为空），**允许悬空 `TOOL_CALL`**，但**必须**伴随
  `ERROR(error_kind=internal)` + `TASK_FINISHED(FAILED)` —— "没有 `TOOL_RESULT` 不允许是静默的"
  （`I1` 的例外条款；机器判据见 `interfaces/harness.md` §6.1 的 `H-1`）。

### ④ 设计取舍与代价

1. **同步 `Iterator` 而不是 `asyncio`**（`interfaces/harness.md` §2.9 的裁决）。理由逐条：
   同一格已规定"单会话单线程、事件流**串行**产出"（异步迭代器在这里自相矛盾）；
   `src/` 下**没有任何 `async def`**（选异步 = 重写 5 个模块）；`llama-server` 默认 `-np 1`、
   `ModelClient` 契约写明**非线程安全**；安全主线偏好"校验 → `decide()` → 审批 → `invoke()`"
   **顺序确定**（并发下 TOCTOU 面更小）；**可回退性**（将来要流式就新增方法或新增 ADR）。
   **被否决**：全量 `asyncio`、`AsyncIterator` + `asyncio.to_thread`（伪异步，与"非线程安全"直接冲突）、
   同时提供 `run()`/`arun()`（两个实现面、两处权威）。
   **代价**：将来做 token 流式渲染只能"新增方法或新增 ADR 改接口"。
2. **工具调用的决策序列只有一个持有者**（`harness/loop.py`）：解析域 → 严格校验 →
   `decide()` → 审批 → `invoke()`（`interfaces/harness.md` §1.3 / §3.3 的六步表）。
   代价：`loop` 变厚；收益：`S1` 有**唯一**验收对象，且"全项目最易实现错的地方"只有一处。
3. **信任边界钉在 HARNESS 侧**：`ToolCallRequest.arguments_json` 是"**原始 JSON 文本：不可信、
   尚未解析**"，解析与校验**只能**发生在 HARNESS（`interfaces/tools.md` §2.1 的"这条'尚未解析'是
   契约的一部分，不是实现细节"；`interfaces/model.md` §2.5 的不变式）。
   代价：`Tool.invoke` **不得**再解析原始 JSON（`interfaces/tools.md` §1.2）。
4. **审计（证据）与事件流（界面载体）是两套记录面，不可互相替代**，靠 `audit_id` / `call_id`
   **显式**关联、**不靠时间戳猜**（`interfaces/harness.md` §1.2）。
5. **`MODEL_RESPONSE` / `ERROR` / `TASK_FINISHED` 不审计**（`interfaces/harness.md` §2.7）。
   理由：`AuditEventKind` 的成员**逐个对应明确需求**，"模型答复了什么"不是权限事件，
   且把模型原文写进长期证据与 `I8` 的取向相悖。
   **代价（真实且要记住）**：**想从审计还原"模型说了什么"做不到** —— 只能靠 stdout 的事件流。
6. **不可信内容只能以数据形态出现，且判据按位置写死**（`I8` 的第十一版澄清表）：
   事件的**独立字段** ❌ / `text`·审计 `detail`·日志 ❌ / `response` **内部** ✅ /
   面向终端的**渲染** ❌（不显示参数值）。**代价**：想看模型到底给了什么参数，
   只能看 `response.tool_calls[].arguments_json`，而它**不会**出现在审计里。

**常见误读**：
- **"退出码 0 说明工具都成功了"——不对**：`0` 只表示会话以 `COMPLETED` 收尾
  （`cli/app.py:116-120` 的 `_EXIT_BY_STATUS`）。手册 §3 舞台 4 记录的形态就是
  **退出码 0 而两次工具调用全部被拒**。
- **"模型不调工具 = 模型不行"**：先按手册 §4.2 的三个坑排查（预算 / 未授予能力 / 允许根），
  再看 `llama-server.log` 里的生成情况。
- **"`arguments_json` 既然进 JSONL，那它是可信的"**：它进 JSONL 只因为它属于 `response` 这一
  **不可信数据整体**，不是因为可信（`I8` 第十一版）。

---

## 阶段 4：审计回放

### ① 目的与判据

验证 `REQ-SEC-06`（"不丢证据 / 可回放"）的**可操作形态**：从**落盘文件**（不是内存里的对象）
按 `event_id` 还原出"谁、什么工具、什么结果"，且能与事件流逐条对齐（`call_id` 一致）。
判据 = `interfaces/harness.md` §2.2 的 **`I4`**：`TOOL_RESULT.audit_id` 非空，且审计中存在一条
`kind=TOOL_CALL`、`call_id` 相同、`event_id == audit_id` 的事件。

### ② 命令

照手册 **§3 舞台 3**。注意它需要把**舞台 2 的 `SID`** 粘进去（该舞台的注解释了这一点）。

### ③ 怎么看输出

审计每行的顶层键（`interfaces/audit.md` §2.3）：
`event_id` / `kind` / `timestamp` / `session_id` / `outcome` / `call_id` / `tool_name` /
`capability` / `risk_level` / `detail`。

- `policy_decision` 行的 `detail`：`requested` **必存在**（升序能力名列表）；
  求值未失败时另有 `missing`；`[]` 有**三种来源**，靠 `error` / `invalid` 两个判别键区分
  （`interfaces/audit.md` §2.3 的 `I2`）。
- `tool_call` 行的 `detail["denied_reason"]`：`DENY` 时**必填**，取值限于五个定长短码
  （`interfaces/harness.md` §2.7 的 `D2`）。
- **什么算异常**：把"同一 `call_id` 上不止一条 `TOOL_CALL` 审计"当成重复写 —— **那是误读**：
  契约允许"调用前后各一条"，判据是"**存在且可回放**"而**不是**"恰好一条"
  （`interfaces/harness.md` §2.7 的 `D4`、§2.2 的 `I4`）。

### ④ 设计取舍与代价

1. **落盘形态是"一行 JSON、只追加"**：崩溃最多影响正在写的那一行；`flush()` = `fsync`，
   是**强持久化点**（`architecture.md` §5.2 的 `AuditEvent` 行）。
2. **`emit` / `flush` 失败必须冒泡，禁止吞异常或降级为告警**（`interfaces/audit.md` §2.4）。
   代价：审计目录不可写 ⇒ 会话直接失败 —— 这是刻意的："静默丢事件等于 `REQ-SEC-06` 验收失败"
   （`interfaces/audit.md` §1.2）。
   - ⚠️ **一处已知的不完全一致（未闭合，须如实说）**：审批通路内的 `sink.emit` 失败会被
     `harness/loop.py::_request_approval` 的 `except Exception` 收敛为 `ERROR(INTERNAL)` + `FAILED`，
     与 §2.4 的"必须冒泡"在该路径上**不完全一致**；因为抛的是 `OSError`/`ValueError` 等**通用类型**，
     "审计基础设施坏了"与"gate 自身故障"**凭类型不可分**，目前只有 CLI 侧的 `_AuditRecorder` 旁路
     把它归到退出码 `4`（`cli/app.py:171-194`、`:283-295`）。
     **落地与否待裁决**，登记为提案 **`P-3`**（`threat-model/README.md` §8.2 的 `P-3`）。
3. **审计落点是白名单内的常量、两层校验、先校验后 `mkdir`**（`interfaces/audit.md` §2.5 的
   `P1`~`P7`，判据 `W1`~`W8`）。理由：项目级 `.lowspec.toml` **跟着仓库走** ⇒ 按 `SECURITY.md`
   口径它是**不可信输入**；不加约束时 `audit.directory` 就是一个"**任意路径追加写**"原语
   （可污染用户文件 / 把审计写到取证看不到处 / DoS）。
   **被否决**：用整个应用状态目录作根（权限面大于需求）、**由配置文件给出根**（等于把白名单
   交给攻击者）、调用方给任意根且**无安全默认**（`interfaces/audit.md` §2.5 的候选表）。
   **代价**："同一份非法配置通常在第一层就被拦下"，第二层是为"绕过配置的调用方"准备的 ——
   两层**不是重复**（同节末段）。
4. **配置期 / 装配期的路径拒绝不进审计**（`interfaces/audit.md` §2.6 的 `COV1`~`COV5`，
   所有者 2026-09-20 裁决）。理由两条：那一阶段**会话与 sink 都不存在**；
   **落点白名单尚未校验** ⇒ 此刻决定"往哪写审计"会与攻击面白名单**形成循环**
   （`COV3`）。
   **代价**：装配期拒绝**没有审计留痕**，只有 stderr 与退出码 ⇒ 所以阶段 2 的证据是 stderr，不是审计。

**常见误读**：
- **审计不在仓库里**：默认在用户状态目录下的 `audit/audit.jsonl`（手册 §4.3 表）
  ⇒ `git status` 干净**不代表**这次没产出。
- 审计**只追加**且**跨会话共享同一个文件** ⇒ 必须用 `session_id` 过滤；
  "找不到我的会话"通常是 SID 粘错，不是没写。
- 审计里**没有模型原文**（见阶段 3 取舍 5）。

---

## 阶段 5：默认拒绝 / fail-secure（最能说明安全取向）

### ① 目的与判据

验证"**没有显式授予 ⇒ 调用不执行**，且拒绝**可观察、可审计**"。
判据：模型**确实请求了**工具，而 `tool_result` 的 `result` 为 **`None`**（未执行）、
审计出现 `policy_decision deny` 与 `tool_call deny`、
`detail["denied_reason"] == "policy_denied"`。

### ② 命令

照手册 **§3 舞台 4**（该舞台的关键动作是**故意不写** `.lowspec.toml`）。本文不重抄。

### ③ 怎么看输出

- `tool_result` 事件：`result is None` ⇒ **未执行**；`text` 是**我方生成**的中文说明
  （`interfaces/harness.md` §2.2 的 `I3`）。
- 审计：`policy_decision` 的 `outcome=deny`，`detail["requested"]` 有值、`detail["missing"]`
  给出**缺哪个能力**；`tool_call` 的 `outcome=deny` 且 `detail["denied_reason"]="policy_denied"`
  （字段层级见阶段 4 的"怎么看"）。
- **退出码**：手册记录的是 **`0`**（会话自己走完）。**不要**因为"全被拒"就期望非 `0`。

### ④ 设计取舍与代价

1. **`allow` / `requires_confirmation` 是四格，不是一格**（`interfaces/policy.md` §2.4）：
   `True/False` 自动放行；`True/True` 自动路径不放行、**须人工确认**；
   `False/True` **可升级**为人工确认（`HIGH` 的保守取值、求值失败）；
   `False/False` **硬拒绝**（`CRITICAL`、能力未授予、`requested` 空集、含非法成员）。
   这四格是 `REQ-SEC-01`（"未授权操作拦截率 100%"）的**可断言形式**（同节末注）。
2. **为什么"所需能力未知"取硬拒绝而不是"可升级拒绝"**：`False/True` 的语义是"可经人工确认后继续"，
   而"所需能力未知 / 不可解析"意味着审批门**没有任何东西可以对照** ⇒
   一旦允许人工放行，人工确认就从"风险确认"退化为**绕过 default-deny 的通道**
   （`interfaces/policy.md` §2.5 的"为什么空集取硬拒绝"）。
3. **为什么"拒绝"是返回值而不是异常**：这样拒绝是**可判定、可断言、可回放**的；
   异常只保留给**审计失败**（`interfaces/policy.md` §1.2、`interfaces/audit.md` §1.2）。
   ⚠️ **两者处置相反**，是本项目最易实现错的地方（`architecture.md` §2.4 的两处"处置相反"）：
   **策略求值失败 ⇒ 收敛为拒绝；审计写入失败 ⇒ 原样冒泡。**
4. **能力粒度刻意粗**（只有 4 个：`read_file` / `write_file` / `execute_command` /
   `network_outbound`）："细粒度危险度"由 `RiskLevel` 表达，而不是把能力枚举组合爆炸
   （`interfaces/policy.md` §2.1 的"刻意的粒度选择"）。
   **代价**：例如"删文件"与"写文件"同属 `WRITE_FILE`，差异只能靠**风险等级 + 确认门**表达；
   新增能力成员会改变权限模型的面积 ⇒ **须另开 ADR**（同节"扩展需 ADR"）。
5. **两条档位轴正交、禁止相互转换**：`--capability-tier` 走的是**模型能力档位**
   （`CapabilityTier`），与**硬件档位** `S/M/L`（`HardwareTier`）是**两条独立的轴**，
   `ADR-0010` §5.2/§5.3 与 `SRS §14` 明文禁止相互转换、也禁止共用字母
   （`interfaces/model.md` §2.3.1/§2.3.2 的硬禁令）。裁剪只**收窄**（`∩`）不放大
   （`interfaces/harness.md` §3.1 / §7.1 第 1 项 `R-1`）。
6. **非交互 = 没有确认通路 ⇒ 一律拒绝**（`interfaces/harness.md` §2.5.5 的 `R1`），
   且**不产生** `APPROVAL_RESULT` 事件 —— 契约的原话是"**没有人被问过，
   伪造一条'用户拒绝'会让审计撒谎**"（`I2`）。
7. **`ALLOW_ALWAYS` 本轮等价于 `ALLOW_ONCE`**，但**必须被接受并如实记录** `allow_always`（`R6`）。
   **不得**表述为"已支持持久授权"。
8. **审批通路故障 ≠ 一次普通拒绝**：`R3`/`R4` 规定"该调用**不执行** + `ERROR(INTERNAL)` + 终止任务"，
   **不得**吞掉异常后当成普通拒绝 —— 那会把**基础设施故障伪装成默认拒绝**
   （`interfaces/harness.md` §2.5.5 的 `R3` 原文）。
9. **被否决的方案（记录以防重复讨论）**：给 `Capability` 加占位成员 `NONE`、只靠上游构造前强制、
   把 `AuditEvent.capability` 改成集合、非法成员走"求值失败"路径、忽略非法成员用合法子集继续求值
   —— 逐条理由见 `interfaces/policy.md` §2.5 的 `R2`~`R6`。
   **采纳的是 `R1`**（修订不变式、显式规定空集与多元素行为）：**不改 schema、不扩权限模型面积
   ⇒ 无需 ADR**。
10. **为什么 `requested` 的"非空 / 元素必须是 `Capability` 实例"在契约层强制不了**：
    `frozenset` 表达不了非空，`contracts/` 又是零行为层（不能加 `__post_init__`）⇒
    只能"入口（`parse_capabilities`）+ 引擎兜底（`1a`/`1b` 分支）"两处配合，
    按威胁模型口径记为**部分缓解**（`architecture.md` §11 的 `G-8`；`interfaces/policy.md` §2.5 的
    分工表）。**实测依据**：裸 `str` 取值合法时（`frozenset({"read_file"})`）曾**被放行**，
    因为 `StrEnum` 与 `str` 的 `==`/`hash` 相等 ⇒ **必须做类型检查、不得做名字检查**（同节）。

**常见误读**：
- **"拒绝 ≠ 失败"**：`result is None`（**未执行**）与 `result.ok is False`（**执行了但失败**）
  在事件、审计、终端显示三处**都必须不同形**（`architecture.md` §5.3 的硬规定一栏、
  `interfaces/harness.md` §1.4）。
- **"多试几次就能绕过拒绝"**：被拒是**策略结论**，不随重试改变；连续失败到上限
  （`--max-consecutive-failures`，默认 3）会话以 `STALLED` → `FAILED`（退出码 `1`）收尾
  （`interfaces/harness.md` §2.4 的 `STALLED` 行）。
- **"`NETWORK_OUTBOUND` 已授予就能出站"**：`ExecutionContext.network_allowed` **本轮恒为 `False`**；
  `NETWORK_OUTBOUND` 已授予**不等于**可以出站，把它当"已可出站"是 fail-open
  （`interfaces/harness.md` §3.3 步 5 的 ⚠️；`T-10` 维持**未缓解**）。

---

## 阶段 6：收尾核对（产物归位与一致性）

### ① 目的与判据

确认"这次跑测没有把工作留在别人看不见的地方"，且**文档与实现没有分叉**。
判据：`git status` 无非预期改动；`make branch-status` 能列出"已产出但未合入 `develop`"的分支与开放 PR；
一致性结论可在 `docs/engineering/doc-consistency-report.md` 追溯到。

### ② 命令

`make branch-status`（`Makefile:156-158`）与 `make check`（同阶段 1）。本文不重抄参数。

### ③ 怎么看输出

- `branch-status` **故意不加入 `check`**：它是**提醒不是门禁**——挂在 CI 上会让它变成永远的红灯
  （`Makefile:155` 的注释）。
- 本项目的"**活待办只有一处**"：最新一篇 devlog 的 **§7**（`CODEBUDDY.md` §2 的系统约定）。
  跑测中发现的新问题**不写进本文**，写进那一节（落盘归**记录员**）。
- ⚠️ **审计落在仓库之外**（阶段 4）⇒ 不要指望 `git status` 反映跑测产物。

### ④ 设计取舍与代价

1. **运行期证据（审计）与工程记录（devlog）分开**，职责不同：审计是"**这次会话做了什么**"
   （机器产出、只追加、可回放）；devlog 是"**这段时间发生了什么、为什么**"（人写、按时间演进）。
   `docs/devlog/` 是**记录员的独占产出域**（`CODEBUDDY.md` §10.1）
   ⇒ **跑测者（含你与我）不直接写它**，只把内容**报告**给记录员或团队领导。
2. **跑测不落盘到基线代码**：临时目录在 `/tmp`、审计落用户状态目录（手册 §8 的复现提示）
   ⇒ 仓库不被污染，`git status` 才有意义。
3. **为什么"未合入分支"必须当成不存在**：ADR-0013 与 `CODEBUDDY.md` §2 把这条写成机制性理由——
   云环境每次从 `develop` 拉起、重启即清空上下文，
   **留在未合入分支上的工作，下次会话等于不存在**（2026-09-16 已因此丢过一批内容）。

**常见误读**：
- **"跑完什么都没变 ⇒ 没产出"**：产出在**审计**与**这次对话的记录**里，不在工作树里。
- **"分支上没人动 ⇒ 阶段性任务不存在"**：恰恰相反（见取舍 3）。

---

## 进阶（可选）

### 进阶 A：退出码矩阵

- **真源**：`interfaces/harness.md` §5.2 的退出码表 + 实现 `cli/app.py:100-120`；
  排障对照表见手册 **§4.1**。
- ⚠️ **退出码 `2` 表示"模型往返步数用尽"（`LIMIT_REACHED`），不是超时**；**超时走 `1`**
  （`ModelUnavailableError`）。这一点在本轮之前曾被写错（手册 §4.1 的注，出处
  `docs/research/2026-09-20-m2-exit-criteria-evidence.md` §A.3）。
- 退出码 `4` 有一条 **CLI 侧旁路**（`_AuditRecorder`）与提案 `P-3`（见阶段 4 取舍 2）：
  "审计坏了"目前只在**经 CLI**的路径上可判定。

### 进阶 B：领域包写法

- **命令/用法**：照手册 **§4.4**。
- **键与形状的真源**：`interfaces/harness.md` **§4.2**；失败模式（逐条 fail-secure）是 **§4.3**；
  可抄的示例 `examples/packs/coding-readonly/pack.toml`（其键就是全部合法键），
  面向用户的说明 `examples/README.md`。
- **必须记住的两条**：
  1. **能力只能收窄，绝不并集**：生效授予 = 用户配置的授予 ∩ `pack.security.capabilities`；
     **只在包里声明能力、不去配置里授予，结果是"该能力仍不可用"** ——
     这不是配置错误，而是"包不能扩权"的体现（`interfaces/harness.md` §4.4 第 1 条；
     `examples/README.md` §怎么用）。
  2. **包目录内出现 `.py` / `.pyc` / `__pycache__` ⇒ 拒绝加载**（`P2`；`ADR-0015` 的 `R5`）。
- ⚠️ **不要把 `run_command` / `write_file` 声明成 `low` 来"让它能跑"**：那是**下调安全默认**，
  必须先走 ADR 并获得批准。示例包刻意只声明只读工具，就是为了不给坏榜样
  （`examples/packs/coding-readonly/pack.toml` 文件头注释、`examples/README.md`）。
  这条边界**有机器兜底**：`tests/unit/test_example_packs.py` 会**真实加载**示例包并断言
  "风险声明不超出只读白名单"，且带**反向断言**（合成一份把 `run_command` 降级进包的配置必须被判红）
  ——即该检查**非恒过**（`ADR-0021` §7 第 1 条）。

### 进阶 C：能力档位裁剪

- **真源**：`interfaces/harness.md` §3.1 的 `trimming.select_tools` / `exposed_tool_names`
  （后者**必须派生自**前者，否则"暴露面"与"解析域"会漂移）与 §7.1 第 1 项。
- **为什么"解析域"要单独存在**：名字**在注册表里但不在本会话解析域** ⇒ `not_exposed`
  （**我们把它裁掉了**）；**两边都没有** ⇒ `unknown_tool`（**模型幻觉**）。
  两者**必须可分**，否则 `REQ-SEC-06` 的可回放性受损（`interfaces/harness.md` §2.7 的 `D2`；
  `interfaces/tools.md` §2.6 的 `T6` 裁决）。
- ⚠️ **`CapabilityTier` 的成员是占位、档数与判定口径未决**（`interfaces/model.md` §2.3 的
  【待定】+ `interfaces/README.md` 的 `U1`）⇒ 现在的 `basic|standard|advanced` **不是已定案的档位**，
  **不要**据此下任何"模型能力"结论。

### 进阶 D：真模型集成用例的两道锁

- **跑法（唯一入口）**：`testing-strategy.md` **§8**（本文不重抄命令）。
- **两道锁互不替代**：
  - **锁一（门禁侧）**：`slow` 已并入默认排除集（`Makefile:92`）⇒ 默认回归不会选中；
  - **锁二（用例侧）**：用例自带环境变量开关，**未设置即 skip**。
    它拦的正是锁一**拦不住**的那条路——显式 `-m integration` 时 `slow` **仍会被选中**。
  - 原文："删掉锁一 ⇒ 丢掉'默认回归不跑真模型'；删掉锁二 ⇒ 丢掉'显式指定 marker 也会被拦'"
    （`tests/integration/test_end_to_end.py` 的 docstring；`testing-strategy.md` §8）。
- ⚠️ 这条用例**真起模型**、属长耗时；跑之前先读它 docstring 里的**可用性参数**说明——
  那里解释了为什么必须显式给出**有界的完成上限**与**足够的请求超时**
  （生成速度慢 ⇒ 无上限的补全必然以 `ModelUnavailableError` 结束）。
  **这不是为让用例变绿而放宽断言**：断言一字未改，改的只是"同一套断言在慢硬件上如何跑完"。

---

## §A 当前做不到什么（如实登记，**不得放大**）

> 口径：以下每条都指向**当前仓库的事实**或**已登记的缺口**。
> ⚠️ **不得把"机制已实现"写成"威胁已缓解"**——两者是两个档位，定义见 `threat-model/README.md` §4.1。

1. **不是"安全已到位"**：威胁模型 13 条 = **已缓解并验证 1 / 部分缓解 10 / 未缓解 2**
   （`threat-model/README.md` §4.1）。且那唯一一条「已缓解并验证」（`T-02`）**只覆盖
   "会话内工具层的路径穿越被拒、且该拒绝留痕可回放"这一条缓解**，
   **不覆盖整体安全**（同处 §4.1 的"证明了什么 / 不证明什么"，必须连读）。
2. **未缓解的两条**：
   - **`T-04`（提示注入与上下文污染）**：现有缓解**多为"降低暴露面"**（工具裁剪
     `harness/trimming.py`、上下文压缩 `harness/context/`），**不阻断**注入攻击路径；
     **输出侧防护为 0**；**持久化变体（检查点完整性）无设计**（§4.1 的适用澄清段）。
   - **`T-10`（网络出站默认拒绝失效）**：`ExecutionContext.network_allowed=False`
     **只是一个标志位**（`harness/loop.py:613` 硬编码），**实际拦截出站的机制不存在**
     （同处）。
3. **没有沙箱**：`security/sandbox/` **未开工**（`architecture.md` §4.2）⇒ 命令执行**没有隔离层**；
   按 `architecture.md` §7.2 的落位表，沙箱对应 **`T-01`**（子进程执行与逃逸，部分缓解）与
   **`T-13`**（隔离机制静默失效，部分缓解）。
   ⚠️ **一处待核对的不一致**：手册 §1.4 把"无沙箱"的后果写成"（`T-04` 仍未缓解）"，
   与 §7.2 的落位（`T-01`/`T-13`）**不一致**。本文按白名单**不改手册**（只允许加一行指针），
   该差异已在本次回报中提请团队领导核对。
   - 📌 **2026-09-23 更正：该差异已闭合，本条不再是悬置项。** 手册 §1.4 已于 **2026-09-21**
     按"更正而非抹掉"修正（拆成两条：沙箱 ⇒ `T-01`/`T-13`；`T-04` 单列并写明**与沙箱无关**），
     发现经过见 [`docs/devlog/0020`](../devlog/0020-2026-09-21-M1出口推进与手册独立复核.md) §3.8。
     ⇒ 保留上面这段原文仅为**留痕**（读者能看出这里**曾经**被提请过核对），
     **不要再据它去核对手册**。
4. **没有出站、没有路由降级**：云端客户端未实现；`model/router.py` 未开工
   ⇒ `ModelUnavailableError` 的既定处置"**路由降级**"**没有载体**——
   契约明写"**不得**把它写成'已降级'"（`interfaces/harness.md` §2.4 的 `UNREACHABLE` 行）。
5. **没有能力探测 / 资产校验 / 检索工具 / 追踪 / 拒答**：`model/probe.py`、`model/assets.py`、
   `tools/search.py`、`observability/tracing.py`、`security/refusal.py` **均未开工**
   （`architecture.md` §4.2）⇒ `REQ-MODEL-06`（能力探测）、`REQ-MODEL-01/02`（GGUF 资产与摘要）、
   `REQ-SEC-08`（拒答）**无载体**。
6. **审计表有两行没有实现侧证据**：`EXECUTION_DEGRADATION` 与 `REFUSAL` **当前没有任何
   `emit` 调用点**（其生产者 `security/sandbox/`、`model/router.py`、`security/refusal.py`
   均未实现）⇒ 只有"表 ↔ 声明一致"这一层证据，**没有"实现真的照着做"的证据**
   （`interfaces/audit.md` §2.2 的"覆盖边界"注）。
7. **注入语料集不存在**：`tests/security/corpus/` 目前**只有路径穿越语料**
   （`testing-strategy.md` §2）。`T-03` 虽已升「部分缓解」，但三条核心攻击路径
   （(a) 观察文本改变工具选择/控制流、(b) `arguments` 拼进命令/路径/正则、
   (c) 工具内二次 `json.loads`）**逐条仍缺专例**（`threat-model/README.md` §5 的 `T-03` 行）。
8. **工具 teardown 无载体**：契约写了四步 teardown（工具 → 模型客户端 → `llama-server` →
   `flush` 审计），但**本轮可观察的只有两步**（`model.close()` → `sink.flush()`）——
   `Tool` Protocol 没有 `close()`（`interfaces/harness.md` §2.9 的"如实登记"，
   "不得为凑四步而发明一个空的工具 teardown"）。
9. **两条契约前置条件在类型层不可强制**：`PolicyRequest.requested` 的"非空"与"元素必须是
   `Capability` 实例"表达不了 ⇒ 只能靠引擎防御分支 + 上游构造点，
   按威胁模型口径记为**部分缓解**（`architecture.md` §11 的 `G-8`）。
10. **`denied_reason` 闭集缺"工具声明缺陷"一档**：`loop` 步 3 的"`spec.capabilities` 为空集"
    与参数校验器的"schema 声明缺陷"都折进 `invalid_arguments` ⇒
    "**是我们的 schema 写错了**"与"**模型给了不合法参数**"在审计里**同形**
    （`REQ-SEC-06` 的可读性受损）。**未落地**，登记为提案 `P-4`
    （`threat-model/README.md` §8.2；`ADR-0020` §5.3 末段）。
11. **审计写入失败的可判定性未闭合**：见阶段 4 取舍 2 与提案 `P-3`。
12. **零性能数据**：`bench/` 子系统与 `make bench-round` 已存在，但**尚无任何性能结论**；
    手册与本文出现的墙钟都是**单次采样**，不得作判据（手册 §6 第 2 条；
    `docs/engineering/benchmark-automation.md`）。
13. **`M1-1`（SRS 成稿）仍未满足**：`M2` 达成**不**使它自动满足；它是 `M1` 唯一未满足项，
    等待所有者逐条裁决（手册 §1.1 / §6 第 6 条；
    `docs/proposals/0004-srs-v0.2-convergence-checklist.md`）。
14. **`CapabilityTier` 未定档**：`--capability-tier` 的三个取值是**占位成员**
    （`interfaces/model.md` §2.3；`interfaces/README.md` 的 `U1`）。
15. **只读可用性只闭合一档**：示例领域包只让**只读**工具在非交互形态下可用；
    写 / 执行类工具的可用性缺口**不受影响**；`DEFAULT_TOOL_RISK` 保持 `HIGH`，
    **没有任何安全默认被改动**（`ADR-0021` §5.1 的 `B-1`/`B-2` 与两条"不得放大"声明）。
16. **`examples/` 不随 wheel 分发**：只装 wheel 的用户**看不到**示例包
    （`ADR-0021` §6 负面后果 1，已实测 wheel 内 `examples/` 条目为 0；
    `architecture.md` §11 的 `G-9`）。另：`src/` 不得 import `examples/` 这一不变性
    **暂无机器检查**（同处第 4 条，**待落地**）。
17. **明确未覆盖的面**：Web/UI 前端、多租户隔离、云端 provider 侧安全、训练侧后门
    （本项目无训练环节）、侧信道（时序/缓存）、aarch64 / Termux 目标设备
    （**未实测环境**）（`threat-model/README.md` §2 的"明确未覆盖"表——
    该表是**范围声明**，不是"已知缺口清单"）。

---

## §B 容易读错的地方（逐条给出处）

| # | 容易读成 | 事实 | 出处 |
| --- | --- | --- | --- |
| 1 | "拒绝 = 失败" | **拒绝**（未执行）是 `result is None`；**失败**（执行了但失败）是 `result.ok is False`。三处（事件 / 审计 / 显示）都**必须不同形** | `architecture.md` §5.3 硬规定栏；`interfaces/harness.md` §1.4 与 `I3` |
| 2 | "退出码 `2` 是超时" | `2` = `LIMIT_REACHED`（**模型往返步数用尽**）；**超时走 `1`** | `interfaces/harness.md` §5.2 退出码表；手册 §4.1 的注（出处 `docs/research/2026-09-20-m2-exit-criteria-evidence.md` §A.3） |
| 3 | "退出码 `0` = 工具都成功了" | `0` 只表示 `TASK_FINISHED.status is COMPLETED`（会话走完）；**被拒也算 0** | `cli/app.py:116-120`；手册 §3 舞台 4 |
| 4 | "退出码 `3` = 模型起不来" | `3` 是**装配/配置故障一族**（含模型起不来）。**必须看 `error_type`** | `interfaces/harness.md` §5.2；`cli/app.py:260-274` |
| 5 | "单次墙钟就是性能数据" | 单次采样**不是判据**；要 P50/P95/P99 + 重复 ≥3 次 + 极差 | 手册 §6 第 2 条；`testing-strategy.md` §6 |
| 6 | "`ok` / `audit_id` 在事件顶层" | 它们在 `tool_result` 的 **`result` 对象内部**；**最终回答在末条 `model_response.response.content`**（`task_finished` 只有 `status`/`text`） | 手册 §2.3 的注（2026-09-21 独立复核） |
| 7 | "`missing` / `requested` / `denied_reason` 在审计顶层" | 分别在 `policy_decision.detail` 与 `tool_call.detail` **之内** | 手册 §3 舞台 4 的注；`interfaces/audit.md` §2.3 `I2`；`interfaces/harness.md` §2.7 `D2` |
| 8 | "参数值 / 原始 `arguments_json` 完全不可见" | 事件的**独立字段**、`text`、审计、终端渲染都不承载它；但它**在 `response` 内部**，随 JSONL **递归展开** | `interfaces/harness.md` §2.2 `I8` 第十一版澄清表 |
| 9 | "`arguments_summary` 里能看到全部参数" | 它**只供展示**：任何控制流 / 权限判定 / 工具选择**不得**读它；非标量以 `<list: 3>` 标注，**不静默略去** | `interfaces/harness.md` §2.5.3 的 `S4` |
| 10 | "`registry.specs()` 是已裁剪的暴露面" | 它是**全量注册集、未裁剪**；裁剪只由 `trimming.select_tools` 承担（否则 `not_exposed` 永远不可达） | `interfaces/tools.md` §2.6 的 `T6` 裁决 |
| 11 | "`--capability-tier` 是已定档位" | 三个取值是**占位成员**；档数与判定口径**未决** | `interfaces/model.md` §2.3；`interfaces/README.md` `U1` |
| 12 | "两条档位轴是一回事" | `CapabilityTier`（模型能力）与 `HardwareTier`（`S/M/L` 硬件）**正交、禁止相互转换**，也不得共用字母 | `interfaces/model.md` §2.3.1/§2.3.2 |
| 13 | "授予 `NETWORK_OUTBOUND` 就能出站" | `ExecutionContext.network_allowed` **本轮恒 `False`** | `interfaces/harness.md` §3.3 步 5 |
| 14 | "`ALLOW_ALWAYS` = 已支持持久授权" | 本轮**等价于** `ALLOW_ONCE`，只是**如实记录** `allow_always` | `interfaces/harness.md` §2.5.5 `R6` |
| 15 | "领域包可以给我加权限" | 能力**只能收窄**（∩）；**只在包里声明、配置里不授予 ⇒ 仍不可用** | `interfaces/harness.md` §4.4 第 1 条；`examples/README.md` |
| 16 | "`--pack` 指向 `pack.toml`" | 它接的是**目录** | `examples/README.md` §怎么用 |
| 17 | "`make test-security` 报错 = 环境坏了" | **零用例时它按设计失败**（fail-secure） | `Makefile:97-106` |
| 18 | "`make check` 全绿 = 安全到位" | 结构性检查只证明"代码长成约定要求的样子"，**不证明攻击被挡住** | `threat-model/README.md` §0 结论 2 |
| 19 | "威胁模型里那条『已缓解并验证』说明安全到位了" | 它**只覆盖会话内工具层的路径穿越**，**不覆盖整体安全**，且自身仍带三条残余风险 | `threat-model/README.md` §4.1 末段（"证明了什么 / 不证明什么"） |
| 20 | "审计在仓库里" | 默认在**用户状态目录**下的 `audit/audit.jsonl`，**只追加、跨会话共享** ⇒ 用 `session_id` 过滤 | 手册 §4.3 表；`interfaces/audit.md` §2.1/§2.3 |
| 21 | "同一 `call_id` 出现两条 `TOOL_CALL` 审计 = 重复写" | 契约**允许**（调用前后各一条）；判据是"**存在且可回放**"，不是"恰好一条" | `interfaces/harness.md` §2.7 `D4`、§2.2 `I4` |
| 22 | "没有沙箱 ⇒ 那是 `T-04`" | 沙箱对应 **`T-01`/`T-13`**；`T-04`（提示注入）**与沙箱无关**。📌 **2026-09-23：手册 §1.4 已于 2026-09-21 修正，两处口径已一致**（本条原文写"两处不一致，已提请核对"，现更正为**已闭合**；发现经过见 `docs/devlog/0020` §3.8） | `architecture.md` §7.2；手册 §1.4（已修正）；`docs/devlog/0020` §3.8 |

---

## §C 怎么用这份文档开新对话

### C.1 开场话术（可直接复制）

```text
按 docs/engineering/product-walkthrough.md 带我跑【阶段 N】。
规则：逐步执行、一次只跑一个阶段；命令我自己敲，我把原始输出（含退出码与 stderr）贴回；
你不要替我跑、不要替我猜输出；输出与文档"怎么看"不符时先停下，不要自行改参数重试。
```

### C.2 每阶段的收尾动作

让助手把这一阶段整理成**三段**：**这次看到了什么** / **与文档不符之处（含原始输出）** /
**新出现的问题**。这三段交给**记录员**落 `docs/devlog/` 的当前篇
（该目录是记录员的独占产出域，见 `CODEBUDDY.md` §10.1）——**跑测者不直接写它**。

### C.3 助手在本次跑测中的硬边界（供你核对）

- 不改 `src/`；不改 `docs/design/`、`docs/adr/`；不写 `docs/devlog/`、`CHANGELOG.md`；
- 不用 `# noqa` / `# nosec` / `--no-verify` / `SKIP=` 绕过门禁；
- **不编造任何输出**；不确定就写"未核实"并给出验证方式；
- 不把"机制已实现"讲成"威胁已缓解"。
