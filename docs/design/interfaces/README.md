# 接口契约（`contracts/` 的字段级定义）

本目录是**实现者据以写代码的契约**：ADR-0015 §5.1.2 规定了"边界处接口的**形态**"
（签名、错误语义、并发假设、资源生命周期），本目录把其中**被引用但未定义**的类型
**逐字段定死**，使实现工程师**只读契约即可写出 fake/stub 并跑通单测**（ADR-0015 §7.3
的"接口是否无歧义"实操判据）。

> **为什么独立成文，而不是埋在 ADR 的表格里**：ADR 记录**决策**（只增不改、面向历史），
> 契约是**可被逐条引用、会被实现反复查阅**的现行规范。两者生命周期不同——
> 决策变更是"新增 ADR"，契约澄清是"修订本目录"。ADR-0015 §9 已把
> "编写 `docs/design/interfaces/*.md`（契约级细节）"列为后续行动，本目录即其落地。

---

## 1. 与 ADR-0015 的分工

| 层级 | 权威内容 | 权威位置 |
| --- | --- | --- |
| 决策 | 分层模型、依赖方向 R1~R5、目录结构、组件选型 | [ADR-0015](../adr/0015-layering-and-reuse-boundary.md) |
| 接口 | 边界处**签名与语义**（`chat`/`decide`/`emit`/`invoke` 等） | ADR-0015 §5.1.2 表 |
| **契约** | 每个类型的**字段名 + 字段类型 + 语义 + 不变式** | **本目录** |

**冲突时**：分层与依赖方向以 ADR 为准；**字段级以本目录为准**。两者若有矛盾，
视为待修冲突，**先报架构师**（不得由实现者自行取舍）。

---

## 2. 通用约定（所有契约类型共同遵守）

| # | 约定 | 理由 |
| --- | --- | --- |
| C1 | **只依赖标准库**，不依赖任何第三方包、也不依赖本项目其它模块（R3） | 契约必须零信任面、零版本耦合；由 `tests/unit/test_architecture_layers.py` 的 V1 机器检查 |
| C2 | 数据结构用 `@dataclass(frozen=True)`；行为接口用 `typing.Protocol` | 数据不可变 ⇒ 天然线程友好、可安全回放；Protocol ⇒ 实现可替换、测试可注入 fake |
| C3 | 枚举用 **`enum.StrEnum`**（`from enum import StrEnum`），值用**小写英文** | 可直接 JSON 序列化进审计与消息；`str(member)` 返回**值**本身。**不要**写成 `(str, Enum)`——Python ≥3.12 下会被 ruff `UP042` 判为应升级（2026-09-18 实测：照抄旧写法即被门禁拦下） |
| C4 | 集合字段用 `tuple` / `frozenset` / `Mapping`，**不用** `list` / `set` / `dict` | 与 `frozen=True` 一致；防止共享可变状态被就地篡改 |
| C5 | 函数签名的**可变参数一律 `keyword-only`**（`*` 之后） | 避免调用点位置参数错位；`chat`/`invoke`/`run` 均遵循 ADR-0015 §5.1.2 的写法 |
| C6 | 面向"是否允许 / 是否需要确认"的字段，默认值取**最保守**的一方 | fail-secure（`SECURITY.md` 第 4 问）；default-deny 不能靠调用方记得传参 |
| C7 | 类型标注用 `object` 而非 `Any` | `mypy --strict` 下 `Any` 会静默污染调用链 |
| C8 | 契约**不含凭据字段**，且任何字段**不得**承载密钥/令牌 | 凭据只经环境变量注入；`REQ-SEC-07` |
| C9 | 契约里出现的字符串字段，若来源是模型/用户/外部内容，一律按**数据**处理 | `REQ-SEC-03`：不可信内容不得影响控制流与权限决策 |
| C10 | **文档内部出现分歧时，以"正文的类型定义 + 文末『改动清单』"为准**；头部的「依赖 / 摘要」行只是**索引**，不作为实现依据 | 契约本体是正文与清单；头部是索引。写死优先级，实现者才不会在两处表述间自行取舍（2026-09-18 实测：`tools.md` 头部多列了未使用的 `AuditSink`，`model.md` 头部少列了必需的 `ToolCallRequest`） |

> **落地方式**：本目录定字段，`src/agent_sec_perf/contracts/` 是它的**唯一实现**。
> 契约变更 = 先改本目录 → 再改 `contracts/` → 同步单测（顺序不可颠倒）。

---

## 3. 文件索引

| 文件 | 覆盖的契约模块 | 对应 ADR-0015 §5.4.1 |
| --- | --- | --- |
| [`model.md`](model.md) | `contracts/model.py` | `model/`（L2 能力层，本地/云端统一抽象） |
| [`tools.md`](tools.md) | `contracts/tools.py` | `tools/`（L2 能力层，信任边界执行侧） |
| [`policy.md`](policy.md) | `contracts/policy.py` | `security/`（横切 SEC 层） |
| [`audit.md`](audit.md) | `contracts/audit.py` | `observability/`（横切 OBS 层） |
| [`sandbox.md`](sandbox.md) | `contracts/sandbox.py` | `security/sandbox/`（逐维度探针，ADR-0006/0007） |
| [`harness.md`](harness.md) | `contracts/harness.py` | `harness/`（L3 编排层，8 件：会话事件流、会话生命周期、内部接缝、领域包 schema） |

---

## 4. 本轮澄清的问题 → 结论落点

本轮要回答实现者提出的 9 条问题，逐条对应关系如下（**不扩写、只回答这 9 条**）：

| # | 问题 | 结论 | 落点 |
| --- | --- | --- | --- |
| Q1 | 12 个类型的字段/成员 | 全部给定（含 2 个新增枚举支撑字段）；**另按 `REQ-PERF-06` 新增 `HardwareTier`**（见 [`model.md`](model.md) §2.3.2） | 各模块文件 §「类型」 |
| Q2 | `ModelClient.chat(...)` 省略的参数；`tools` 是否可选 | 「否」——5 个参数见下；`tools` **可选，默认 `None`（不暴露任何工具）** | [`model.md`](model.md) §4 |
| Q3 | `PolicyEngine` / `ToolRegistry` 是否也在 `contracts/` 定义 Protocol | **是**——两者都在 | [`policy.md`](policy.md) §2.5、[`tools.md`](tools.md) §2.6 |
| Q4 | `ModelUnavailableError` / `ModelProtocolError` 归属 | **`foundation/errors.py`**（单一异常层次） | [`model.md`](model.md) §3 |
| Q5 | `PolicyDecision` 是否需要拒绝理由 / 审计关联字段 | **需要**，新增 `risk_level` / `reason` / `audit_id` | [`policy.md`](policy.md) §2.4 |
| Q6 | `AuditEvent` 字段 | 12 个字段 + 2 个枚举 | [`audit.md`](audit.md) §2 |
| Q7 | `sandbox.*` 三类型字段 | 给定；并补齐 ADR-0007 要求的**逐维度**结构 | [`sandbox.md`](sandbox.md) §2 |
| Q8 | `ToolSpec` / `ExecutionContext` 字段 | 给定；二者由 **Protocol 占位改为 `frozen` dataclass** | [`tools.md`](tools.md) §2 |
| Q9 | `Capability` / `RiskLevel` 成员 | 给定；`CapabilityTier` 成员**【待定】**并说明卡点 | [`policy.md`](policy.md) §2.1/§2.2、[`model.md`](model.md) §2.3 |

---

## 5. 对 `src/agent_sec_perf/contracts/` 的落地动作

> 这些是**实现侧**动作（`src/` 不是架构师的文件域）。此处只给清单，
> 供实现工程师 / 验证工程师核对契约与代码是否一致。

| 文件 | 动作 |
| --- | --- |
| `contracts/model.py` | 新增 `Role` / `FinishReason` / `TokenUsage` / **`HardwareTier`（`S`/`M`/`L`，见 §2.3.2）**；`ChatMessage` / `ModelResponse` 补字段；`CapabilityTier` 补占位成员；`chat` 补 5 个参数 |
| `contracts/tools.py` | `ToolSpec` / `ExecutionContext` 由 Protocol 改为 `frozen` dataclass 并补字段；新增 `ToolRegistry` Protocol |
| `contracts/policy.py` | `Capability` / `RiskLevel` 补成员；`PolicyRequest` 补字段；`PolicyDecision` 补 `risk_level` / `reason` / `audit_id`；新增 `PolicyEngine` Protocol |
| `contracts/audit.py` | 新增 `AuditEventKind` / `AuditOutcome`；`AuditEvent` 补字段 |
| `contracts/sandbox.py` | 新增 `IsolationDimension` / `IsolationMechanism` / `IsolationDimensionStatus` / `SandboxTier`；三类型补字段 |

**不变量**：`contracts/` 仍是**零行为**（只有类型、Protocol、常量）。上述改动**不得**
引入任何函数体逻辑（`Protocol` 方法体固定为 `...`）。

---

## 6. 未决项（本目录已明确标注、不在此臆断）

| # | 未决项 | 卡在哪 | 解除方式 |
| --- | --- | --- | --- |
| U1 | `CapabilityTier`（**模型能力档位**）的档数与成员名 | **经查证：无任何文档定义其成员** —— `SRS Q-3` 仍是**开放问题**（"建议…档位暂定 3 档"只是建议）；`REQ-MODEL-06` 只要求"估计档位"、不给集合。**注意 `S/M/L` 是"硬件档位"（`ADR-0011 §5.1` / `SRS §6.3` / `SRS §14`），属另一条轴，不可用于填充**（见 [`model.md`](model.md) §2.3.1 自查结论） | `REQ-MODEL-06` 落地时定，变更走 ADR |
| U2 | `ToolSpec.description_digest` 的**规范化口径**（`REQ-TOOL-03` 防 rug-pull） | 摘要应覆盖哪些字段、如何规范化尚未定 | 与 MCP 接入（`REQ-TOOL-02`，Should）同期定；本目录给**可用的初始口径** |
| U3 | **"本次 / 总是 / 拒绝"的持久授权**表示（`REQ-UX-02`） | 不在本轮 9 条问题内；属 `security/` 的审批门 + 配置存储 | 审批门设计时定义（**本目录不扩写**） |
| U4 | 审计事件的 `detail` **脱敏规则** | 依赖 `REQ-SEC-07` 的脱敏处理器（`observability/` + structlog 管线，D4） | `observability/` 设计时定；契约只强制"必须已脱敏" |
| U5 | ~~硬件档位 `S/M/L` 在 `contracts/` 中无对应类型~~ ⇒ **已解决（2026-09-18 领导批准）** | 契约缺该类型时，实现者**无法表达一个已批准的需求**（`REQ-PERF-06`）；**授权判据**：`ADR-0011 §5.1` 已定义 `S/M/L` ⇒ 这是"实现**已有**决策、不是新决策" | **已落**：定义见 [`model.md`](model.md) §2.3.2（与 §2.3.1 的两轴对照表），落地动作见 §5 表；`ADR-0015` 修订记录已登记；实现者补 `contracts/model.py` + 单测（"集合恰好相等"断言） |
| U6 | ~~`capability`（单值字段）与 `PolicyRequest.requested`（集合字段）的语义缺口~~ ⇒ **已裁决（2026-09-19，`e7d9298` + 本笔）** | 修订前的不变式要求 `POLICY_DECISION` 的 `capability` **无条件非 `None`**，与"返回前必须 `emit`"在**空集**下互斥；**多元素时取哪一个**未定义（该半由架构侧独立查出）；**非 `Capability` 成员**（类型违规）未被覆盖（实现侧第三轮报出） | **已落**：[`policy.md`](policy.md) §2.5「补充规定」给出**三种情形**的规定行为（空集 ⇒ 硬拒绝且**仍须 `emit`**；多元素 ⇒ **合法**、取确定性代表、任一缺失即整体拒绝；非法成员 ⇒ **整体拒绝**，判据必须是**类型检查**而非名字检查）与**确定性代表规则**；不变式改写并扩为 [`audit.md`](audit.md) §2.3 的 **I1~I4**；被否决方案（占位成员 `NONE` / 只靠上游构造前强制 / `capability` 改集合类型 / 非法成员走 `False/True` / 忽略非法成员）理由同在 §2.5。**实现侧待改两处**：求值失败路径补 `detail["requested"]`、新增非法成员硬拒绝分支 |
| U7 | ~~`audit.directory`（项目级 `.lowspec.toml`）可决定审计落点~~ ⇒ **已裁决（2026-09-19，`a83b938`）** | 项目级配置**跟着仓库走** ⇒ 按 `SECURITY.md` 口径属**不可信输入**；只校验"绝对路径 + 无 NUL"不满足"路径必须做规范化与白名单校验"，且构成**任意路径追加写**原语（污染用户文件 / 把审计写到取证看不到处 / DoS）。实现侧正确地拒绝自行发明 `roots` | **已落**：[`audit.md`](audit.md) §2.5 的 `P1`~`P7`（根集合为**常量** `ALLOWED_AUDIT_ROOTS`、配置期 `ConfigError` / 装配期 `PathNotAllowedError` 两层校验、**先校验后 `mkdir`**、禁止回退默认或静默关审计、校验在构造期）与判据 `W1`~`W8`；威胁模型 `T-02` 已联动登记（**状态与计数不变**）；两项**策略待确认项**见 [`audit.md`](audit.md) §5（`A1` 额外根 / `A2` 是否移除该键） |
| U8 | [`audit.md`](audit.md) §2.2 的 kind→outcome 表**缺** `TOOL_CALL` 的"未执行"取值 | 已核实的两处契约合成一个不可满足的组合：[`tools.md`](tools.md) §2.6 要求"未知工具 ⇒ **默认拒绝 + 审计**"，而 §2.2 只允许 `kind=TOOL_CALL` 的 `outcome ∈ {OK, ERROR}` ⇒ **"被拒绝、没有执行"无法被忠实表达**；用 `ERROR` 会让"工具执行失败"与"根本没有执行"在 `REQ-OBS-01` 的按结果检索里**同形**（`architecture.md` §5.3 硬规定 2 正是"拒绝不等于失败"） | **已裁决（2026-09-19，`1a7c044`）**：规范写在 [`harness.md`](harness.md) §2.7——`TOOL_CALL` 的 `outcome` 扩为 `{OK, ERROR, DENY}`；`DENY` 时 `detail["denied_reason"]` **必填**且限 `{"unknown_tool","not_exposed","invalid_arguments","policy_denied","approval_denied"}`；同一 `call_id` 可能出现多条事件 ⇒ 判据是**"存在且可回放"**，不是"恰好一条"。**已落（2026-09-19，`be63b31`）**：[`audit.md`](audit.md) §2.2 的 kind→outcome 约束表已把 `TOOL_CALL` 的允许集放宽为 `{OK, ERROR, DENY}`（**放宽允许集，未新增 `AuditOutcome` 成员**——`DENY` 本就存在）并补 `D1`~`D4` 与「不得外推」边界；§4 修订记录已登记。**下游联动已核实**：`contracts/audit.py` 与 `observability/audit.py` **均无需改动**（读取侧只校验枚举取值，不校验 kind×outcome 组合）；**0 处测试会因此翻红**（全仓无 kind→outcome 允许集断言） |
| U9 | [`harness.md`](harness.md) 引入的未决项（**不阻塞开工**，但**不得默认处置**/**不得按已实现表述**） | ① **参数校验器选型**（pydantic 严格模式 vs 手写）：`D2` 的第一处用途当前无载体（`architecture.md` §11 的 `G-2`）⇒ 选定后**停下上报**（选 pydantic ⇒ ADR-0015 修订记录登记；选手写 ⇒ **新增 ADR**）；② **"总是允许"的持久授权**（= `U3`）：本轮 `ALLOW_ALWAYS` **等价于** `ALLOW_ONCE`，但必须在事件与审计里**如实记录** `allow_always`；③ **检查点持久化落点**（`REQ-HARNESS-05`）：`checkpoint.py` 本轮只做纯函数快照/还原，落盘路径与权限未决——任何落盘**必须**沿用 [`audit.md`](audit.md) §2.5 的口径（根不得来自不可信来源、先校验后创建）；④ **领域包根**：`load_pack(roots=...)` **必填无默认**，装配点给哪些根未决；⑤ ~~实现侧差异 `R-1`~`R-4` 待裁决~~ ⇒ **已裁决（2026-09-19，`c3d5610`）**：`R-1` 以契约为准、**改实现**（轴用 `CapabilityTier`，**不新增 ADR**）；`R-2`/`R-3` **采纳实现**、契约已收紧（`prompts` 四 builder + `data_context` 角色守卫；`ErrorDisposition{RETRY, FEEDBACK, ABORT}` 留 `harness/errors.py`，新增 `error_kind`）；`R-4` **保留契约**（`not_exposed` / `unknown_tool` 必须可分） | 见 [`harness.md`](harness.md) §3.4 / §2.5.5 `R6` / §3.1（`checkpoint.py`）/ §4.1 `P4` / §7.1（裁决落地清单）/ §8 的 `R-1`~`R-4`；逐项解除时走本目录 §7 的变更流程（触及决策则另开 ADR） |

---

## 7. 变更流程

1. 契约变更（字段/成员/语义）→ **先改本目录**，说明动机与影响面；
2. 若变更触及**决策**（分层、依赖方向、选型）→ 按 ADR 规则**新增 ADR**（ADR 只增不改）；
3. 同步 `src/agent_sec_perf/contracts/` 与相关单测；
4. `make check` 全绿（V1 会拦住任何引入第三方依赖的契约改动）。
