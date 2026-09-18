# 契约：`contracts/policy.py`

- 对应模块：`src/agent_sec_perf/security/`（横切 SEC 层：能力模型 / 策略求值 / 审批门）
- 上游决策：ADR-0015 §5.1.2（`HARNESS/CAPABILITY ↔ SEC` 行）、§5.3 自研模块 3、
  SRS `REQ-SEC-01/02/06/08`
- 依赖：无（仅标准库）

本文件回答 Q1（`Capability` / `RiskLevel` / `PolicyRequest`）、Q3（`PolicyEngine` 是否入契约）、
Q5（`PolicyDecision` 字段）。

---

## 1. 设计要点（先读这一节）

1. **default-deny 是默认值取向，不是一句口号**：`PolicyEngine` 只回答"**这次**是否允许"，
   未显式授予的能力一律**不在**授予集合内（`REQ-SEC-01` 要求未授权操作拦截率 **100%**）。
2. **决策是返回值，不是异常**：拒绝经 `allow=False` 表达。异常只用于**审计失败**（必须冒泡）。
   这样"拒绝"是可判定、可断言、可回放的。
3. **求值失败必须收敛为拒绝，审计失败必须冒泡**——这两者在 ADR-0015 §5.1.2 里被写在同一行，
   但**处置相反**，必须分清（见 §2.5 的顺序约定）。这条是本节最容易实现错的地方。
4. **策略只能把 `arguments` 当数据**：它是**已校验**的结构化参数，但其中的字符串仍可能源自
   不可信内容（模型输出 / 外部内容）⇒ 策略**不得**把它们拼接进命令、路径、正则或表达式求值
   （`REQ-SEC-03`）。

---

## 2. 类型定义

### 2.1 `Capability`（Q1）

```python
class Capability(str, Enum):
    READ_FILE = "read_file"  # 读取文件 / 列目录
    WRITE_FILE = "write_file"  # 创建 / 修改 / 删除文件
    EXECUTE_COMMAND = "execute_command"  # 启动子进程（唯一经 foundation.proc）
    NETWORK_OUTBOUND = "network_outbound"  # 任何出站请求
```

**成员来源（每一条都能追溯到需求，不是凭感觉列的）**：

| 成员 | 来源 | 说明 |
| --- | --- | --- |
| `READ_FILE` / `WRITE_FILE` | `REQ-TOOL-01`（文件读写 / 列目录） | 路径访问**唯一**经 `foundation.paths.resolve_within` |
| `EXECUTE_COMMAND` | `REQ-TOOL-01`（命令执行）、`REQ-SEC-05` | 命令执行**唯一**经 `foundation.proc` |
| `NETWORK_OUTBOUND` | `REQ-SEC-07`（出站白名单）、SRS §8（"任何出站请求必须显式授权并记录"） | 默认拒绝，白名单放行 |

**刻意的粒度选择**：**保持粗粒度**（不拆 `CREATE` / `MODIFY` / `DELETE`）。
理由是"细粒度危险度"由 `RiskLevel` 表达，而不是把能力枚举组合爆炸；
能力回答"**这类操作是否被授权**"，风险回答"**这一次该不该让人确认**"。

> **扩展需 ADR**：新增成员改变权限模型的面积，属架构决策（ADR 规则：安全相关设计权衡）。

### 2.2 `RiskLevel`（Q1）

```python
class RiskLevel(str, Enum):
    LOW = "low"  # 低代价、可逆
    MEDIUM = "medium"  # 常规特权操作，建议单次确认
    HIGH = "high"  # 高代价 / 难以撤销，必须展示风险说明
    CRITICAL = "critical"  # 不可逆或高危领域，默认拒绝或强制转人工
```

**等级 → 处置的映射（对齐 SRS §4.2 的三行分类）**：

| 等级 | 对应 SRS §4.2 情形 | `PolicyDecision` 的默认取值 |
| --- | --- | --- |
| `LOW` | 低代价错误（可逆、影响面小） | `allow=True, requires_confirmation=False` |
| `MEDIUM` | 常规特权操作（如写工作目录内文件） | `allow=True, requires_confirmation=True` |
| `HIGH` | 高代价错误 / 难以撤销（覆盖、删除） | `allow=True, requires_confirmation=True`，且 `reason` **必须**给出风险说明（`REQ-SEC-02`） |
| `CRITICAL` | 不可逆操作 / 高危领域（对外发送数据、金融、医疗） | `allow=False`；**不得**静默执行（`REQ-SEC-08` 拒答或强制转人工） |

- **谁产生**：`PolicyEngine` **逐次求值**得出（依据 `tool_name` + `requested` + `arguments`
  + `domain_pack`），**不是**静态挂在工具上的属性——同一工具写 `/tmp` 与写 `/etc` 的风险不同。
- **谁消费**：`cli/approval.py`（确认界面必须展示风险说明与理由，`REQ-UX-02`）、
  审计（`AuditEvent.risk_level`）、拒答判定（`security/refusal.py`）。

### 2.3 `PolicyRequest`（Q1）

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `session_id` | `str` | 会话标识（审计关联） |
| `call_id` | `str` | 本次操作标识；同一工具调用的策略、审计、结果共用此 id |
| `tool_name` | `str` | 被请求的工具名 |
| `arguments` | `Mapping[str, object]` | **已校验**的结构化参数（信任边界之内；原 JSON 已解析并校验） |
| `requested` | `frozenset[Capability]` | 本次操作**需要**的能力集合 |
| `domain_pack` | `str \| None` | 当前激活的领域包标识；策略可按包差异求值（`REQ-HARNESS-08`） |

```python
@dataclass(frozen=True)
class PolicyRequest:
    session_id: str
    call_id: str
    tool_name: str
    arguments: Mapping[str, object]
    requested: frozenset[Capability]
    domain_pack: str | None = None
```

- **谁产生**：HARNESS（在信任边界**完成参数校验之后**；ADR-0015 §5.1.2）。
- **谁消费**：`PolicyEngine.decide()`。
- **安全语义**：`arguments` 已过校验，但仍是**数据**。策略若需要路径，必须走
  `foundation.paths.resolve_within` 再比较，**禁止**自行做字符串前缀判断。

### 2.4 `PolicyDecision`（Q1，Q5）

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `allow` | `bool` | 是否**无需人工**即可继续 |
| `requires_confirmation` | `bool` | 是否**可经人工确认**后继续 |
| `risk_level` | `RiskLevel` | 本次操作的风险等级（供展示与审计） |
| `reason` | `str` | **面向用户的中文理由**（`REQ-UX-04` 中文优先；`REQ-SEC-02` 必须展示） |
| `audit_id` | `str` | 与本次决策的 `AuditEvent.event_id` 相同（`REQ-SEC-06` 可回放的关联键） |

```python
@dataclass(frozen=True)
class PolicyDecision:
    allow: bool
    requires_confirmation: bool
    risk_level: RiskLevel
    reason: str
    audit_id: str
```

**Q5 的结论：需要，三个字段都要。** 理由逐条：

- `reason`：`REQ-SEC-02` 的验收标准就是"确认界面必须展示风险说明"；没有理由字段，
  这条需求在契约层无载体。
- `risk_level`：确认门（`REQ-UX-02`）与拒答（`REQ-SEC-08`）都要按等级分支；
  只在 `reason` 里写自然语言，机器无法分支。
- `audit_id`：`REQ-SEC-06` 要求"每次拒绝均有**可回放**记录"；返回值与审计事件之间必须有一个
  **显式关联键**，否则"回放"只能靠时间戳猜测。

**`allow` 与 `requires_confirmation` 的四种组合（必须全部被实现与测试覆盖）**：

| `allow` | `requires_confirmation` | 含义 |
| --- | --- | --- |
| `True` | `False` | 自动放行 |
| `True` | `True` | 自动路径不放行，**须人工确认**后执行 |
| `False` | `True` | 自动拒绝，但**可升级**为人工确认（`HIGH` 的保守取值、求值失败） |
| `False` | `False` | **硬拒绝**（`CRITICAL`：连人工确认也不接受） |

> 这四格是 `REQ-SEC-01`（"未授权操作拦截率 100%"）的**可断言形式**：
> 测试要对每一格给出期望行为，而不是只测 `allow` 一个布尔。

### 2.5 `PolicyEngine`（Q3）

```python
class PolicyEngine(Protocol):
    def decide(self, request: PolicyRequest) -> PolicyDecision: ...
```

**Q3 的结论：是，`PolicyEngine` 也放 `contracts/`。** 理由：

1. `ModelClient` / `Tool` / `AuditSink` 已在契约层，`PolicyEngine` 是**同一类**东西
   （被上层消费的行为接口）；只把它留在 `security/` 会破坏"契约层 = 全部跨层接口"的一致性。
2. 上层（`harness/`）因此只依赖 `contracts`，不必依赖 `security/` 的**具体实现**；
   单测可直接注入 fake 策略引擎，无需拉起策略实现。
3. **不违反 R1/R2**：`harness → security` 本就允许（ADR-0015 §5.1.1 依赖图），
   放进契约只是多给一条更松的依赖路径；`contracts` 本身仍零依赖（C1/V1）。

**语义与生命周期**：

| 项 | 规定 |
| --- | --- |
| 并发 | **无状态、纯函数式**；可多线程调用（ADR-0015 §5.1.2） |
| 资源 | 无（无 `close`/无句柄） |
| 失败收敛 | **求值阶段**任何异常 ⇒ 返回 `allow=False, requires_confirmation=True, risk_level=CRITICAL`，`reason` 说明"策略求值失败"。**禁止**异常逃逸为 `allow=True` |
| 审计 | 返回前**必须**先 `emit` 一条 `kind=POLICY_DECISION` 的事件，并让 `audit_id` 等于其 `event_id` |
| **审计失败** | `emit()` 的异常**必须原样冒泡**，**不得**被上面的"失败收敛"吞掉 |

**实现顺序（照此写即可，顺序本身是契约的一部分）**：

```text
1. 求值（规则匹配、能力检查）      ← 此段用 try/except 收敛为拒绝决策
2. 计算 risk_level
3. 生成 audit_id（uuid4().hex）
4. 构造 AuditEvent(kind=POLICY_DECISION, event_id=audit_id, outcome=..., ...)
5. sink.emit(event)                ← 此段**不得**包在 try/except 里；异常必须冒泡
6. 返回 PolicyDecision(..., audit_id=audit_id)
```

> **为什么 1 与 5 的处置相反**：`ADR-0015 §5.1.2` 指出"审计写入失败**必须**冒泡——
> 静默丢事件等于 `REQ-SEC-06` 验收失败"。若把 `emit` 也包进第 1 步的 `except`，
> 一次**审计基础设施故障**会被伪装成一次**普通的策略拒绝**，故障被静默吸收。
> 两者表象都是"没执行"，但一个是正常的默认拒绝、一个是系统坏了——**不可混为一谈**。

---

## 3. 本轮**不**定义（避免扩写，Q5 范围之外）

| 未决项 | 卡在哪 | 处置 |
| --- | --- | --- |
| "**本次 / 总是 / 拒绝**"的持久授权表示（`REQ-UX-02`） | 属审批门的实现 + 授权存储；涉及"总是"的持久化与撤销 | **后续交付物**（`security/` 设计时定义）；本目录不臆断字段（U3） |

> 说明：确认交互的**结果**（用户选了哪个）由 `AuditEvent(kind=APPROVAL)` 记录
> （见 [`audit.md`](audit.md)），因此审计侧已可回放；缺失的只是"把'总是'持久化为后续自动授予"
> 这条链路，它需要单独的存储与撤销设计。

---

## 4. 对 `contracts/policy.py` 的改动清单

1. `Capability` 补 4 个成员；
2. `RiskLevel` 补 4 个成员；
3. `PolicyRequest` 补 6 个字段（`domain_pack` 有默认值）；
4. `PolicyDecision` 补 `risk_level` / `reason` / `audit_id`（三个都**无默认值**，
   强制调用点显式给出——避免"忘了填理由"通过）；
5. 新增 `PolicyEngine` Protocol（`contracts/policy.py` 内，零行为）；
6. 模块 docstring 更新：`PolicyEngine.decide()` 的**实现**仍在 `security/policy.py`，
   契约层只放 Protocol。
