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

---

## 4. 本轮澄清的问题 → 结论落点

本轮要回答实现者提出的 9 条问题，逐条对应关系如下（**不扩写、只回答这 9 条**）：

| # | 问题 | 结论 | 落点 |
| --- | --- | --- | --- |
| Q1 | 12 个类型的字段/成员 | 全部给定（含 2 个新增枚举支撑字段） | 各模块文件 §「类型」 |
| Q2 | `ModelClient.chat(...)` 省略的参数；`tools` 是否可选 | 「否」——5 个参数见下；`tools` **可选，默认 `None`（不暴露任何工具）** | [`model.md`](model.md) §2.1 |
| Q3 | `PolicyEngine` / `ToolRegistry` 是否也在 `contracts/` 定义 Protocol | **是**——两者都在 | [`policy.md`](policy.md) §2.6、[`tools.md`](tools.md) §2.6 |
| Q4 | `ModelUnavailableError` / `ModelProtocolError` 归属 | **`foundation/errors.py`**（单一异常层次） | [`model.md`](model.md) §3 |
| Q5 | `PolicyDecision` 是否需要拒绝理由 / 审计关联字段 | **需要**，新增 `risk_level` / `reason` / `audit_id` | [`policy.md`](policy.md) §2.5 |
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
| `contracts/model.py` | 新增 `Role` / `FinishReason` / `TokenUsage`；`ChatMessage` / `ModelResponse` 补字段；`CapabilityTier` 补占位成员；`chat` 补 5 个参数 |
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
| U5 | **硬件档位 `S/M/L` 在 `contracts/` 中无对应类型** | `REQ-PERF-06` 要求"映射到固定 3 档（`S/M/L`）预设配置"，但 `ADR-0015 §5.4.1` 的 `contracts/model.py` 只列了 `CapabilityTier`（模型能力档位）——**两条轴各需一个类型**（见 [`model.md`](model.md) §2.3.1 第 (4) 条） | 建议新增 `HardwareTier(StrEnum)` = `S/M/L`；属**新增契约类型**（决策级），需领导确认后落 ADR；本目录**不擅自新增** |

---

## 7. 变更流程

1. 契约变更（字段/成员/语义）→ **先改本目录**，说明动机与影响面；
2. 若变更触及**决策**（分层、依赖方向、选型）→ 按 ADR 规则**新增 ADR**（ADR 只增不改）；
3. 同步 `src/agent_sec_perf/contracts/` 与相关单测；
4. `make check` 全绿（V1 会拦住任何引入第三方依赖的契约改动）。
