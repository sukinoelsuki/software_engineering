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
class Capability(StrEnum):
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
class RiskLevel(StrEnum):
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
| `requested` | `frozenset[Capability]` | 本次操作**需要**的能力集合；**必须非空**（前置条件，见下方"谁产生"）——空集属**上游构造缺陷**，仍由引擎硬拒绝（§2.5「补充规定」） |
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

- **谁产生**：HARNESS（在信任边界**完成参数校验之后**；ADR-0015 §5.1.2）。构造时**必须**满足
  前置条件 `requested` 非空：工具未声明任何能力属**声明缺陷**，应在构造**之前**即拒绝，
  **不得**构造出空集请求。⚠️ 该前置条件**只是调用方约定**——`frozenset[Capability]` 在类型层面
  表达不了"非空"（`mypy --strict` 拦不住），`contracts/` 又是**零行为**层
  （[`README.md`](README.md) §5）⇒ 契约层无法校验，按威胁模型口径记为**部分缓解**；
  兜底由 `PolicyEngine` 的防御分支负责（§2.5）。
- **前置条件二：每个元素必须是 `Capability` 成员**（**类型**要求）。值恰好等于合法能力名的
  **裸 `str` 同样不合规**——`StrEnum` 与 `str` 的 `==` / `hash` 相等，故"只做名字或取值校验"
  会**静默通过**（2026-09-19 实测：`frozenset({"read_file"})` 被当成 `READ_FILE` **放行**）。
  声明式来源的能力名**必须**先经 `security/capabilities.py` 的 `parse_capabilities()`
  （未知名即拒绝，**不跳过**）再进入 `requested`；**不得**把原始声明字符串直接塞进来。
  同上，契约层无法校验 ⇒ 兜底仍由 `PolicyEngine` 的防御分支负责（§2.5）。
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
| `False` | `False` | **硬拒绝**（`CRITICAL`：连人工确认也不接受）；来源：能力未授予、`requested` 为空集、`requested` 含非 `Capability` 成员（后两者均属**上游构造缺陷**） |

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
| 审计 | 返回前**必须**先 `emit` 一条 `kind=POLICY_DECISION` 的事件，并让 `audit_id` 等于其 `event_id`；此规则**无条件**成立——**含空集拒绝与求值失败两条路径**（**禁止**用"不审计"回避任何冲突，见「补充规定」） |
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

**补充规定（2026-09-19 裁决）——空集与多能力的规定行为**：

背景：`AuditEvent.capability`（[`audit.md`](audit.md) §2.3）是**单值**字段，而 §2.3 的 `requested`
是**集合**。修订前的不变式要求 `POLICY_DECISION` 事件的 `capability` **无条件非 `None`**，
与本节"返回前必须 `emit`"在**空集**下不可同时满足；多元素时"取哪一个"也未定义。
此缺口由实现侧于 2026-09-19 报出，架构侧复核成立，裁决如下：

| 输入形态 | 是否合法 | 规定行为 |
| --- | --- | --- |
| **空集** `frozenset()` | **否**——属**上游构造缺陷** | `PolicyEngine` **硬拒绝**：`allow=False` / `requires_confirmation=False` / `risk_level=CRITICAL`；`reason` 说明"未声明所需能力"；`capability=None`；**仍必须 `emit`** 一条 `POLICY_DECISION`（`outcome=DENY`） |
| **单元素** | 是 | 常规求值；`capability` = 该成员 |
| **多元素** | **是**——"先读后写"这类操作本就需要多个能力，**不得**拒绝 | 常规求值；**任一缺失即整体拒绝**（`CapabilitySet.missing` 语义，不做"部分满足"降级）；`capability` 取**确定性代表**（见下）；完整集合进 `detail["requested"]` |
| **含非 `Capability` 成员**（类型违规） | **否**——属**上游构造缺陷**（与空集同族） | `PolicyEngine` **硬拒绝**：`allow=False` / `requires_confirmation=False` / `risk_level=CRITICAL`；`capability=None`；`detail["requested"]=[]`；非法项进 `detail["invalid"]`；**仍必须 `emit`**（`outcome=DENY`）。**整体拒绝**，**禁止**忽略非法成员后用合法子集继续求值——详见下方「非法成员的规定行为」 |

**`capability` 的取值规则（确定性，不得依赖枚举声明顺序）**：

1. `missing` 非空 ⇒ 取 `missing` 中**能力名字典序最小**者（"缺什么"比"要什么"更该被看到）；
2. 否则 ⇒ 取 `requested` 中**能力名字典序最小**者。

排序键用 `min(..., key=str)`（`StrEnum` 的 `str()` 返回**值**本身，见 [`README.md`](README.md) C3）。
两条规则都保证 `capability ∈ requested`（因为 `missing ⊆ requested`），从而满足
[`audit.md`](audit.md) §2.3 的不变式 I3。**不得**改用枚举声明顺序或集合迭代顺序：那会让同一
输入在不同版本给出不同审计内容，历史事件无法比对——"确定性"本身就是"可回放"的前提。

**非法成员的规定行为（2026-09-19 第二次裁决）**：`requested` 里出现**非 `Capability` 成员**
（类型违规）时的处置。

**实测证据（架构侧 2026-09-19 独立复现，非采信转述）**：

| 输入 | 现行为 | 破的约束 |
| --- | --- | --- |
| `frozenset({"bogus"})` | **拒绝**（`False/False`），但事件里 `capability` 是**裸 `str` `"bogus"`**；`detail["requested"] == ["bogus"]` | 字段类型 `Capability \| None`；[`audit.md`](audit.md) §2.3 的 I2（元素须是 `Capability` 的值）与 I3（`capability.value` 直接 `AttributeError`） |
| `frozenset({"read_file"})`（**裸 `str`、取值恰好合法**） | **`allow=True`——放行！**，且 `capability` 仍是裸 `str` | 同上，且**在 ALLOW 路径上**也破 I2/I3 |

第二条是"**必须做类型检查、不得做名字检查**"的依据：`StrEnum` 与 `str` 的 `==` / `hash` 相等
⇒ `frozenset({"read_file"}) - frozenset({Capability.READ_FILE})` 是**空集** ⇒ 缺失判定为"无缺失"
⇒ 走常规求值路径，"名字巧合合法"就被**静默当作**该能力。**任何"解析名字再比较"的兜底都不可靠。**

**裁决：(a) 视为与空集同族的构造缺陷 ⇒ 硬拒绝**。逐项规定：

| 项 | 规定 |
| --- | --- |
| 判据 | **类型检查**：只要存在 `not isinstance(member, Capability)` 的成员即命中（**不看取值是否合法**） |
| 是否整体拒绝 | **整体拒绝**。**禁止**"忽略非法成员、用剩下合法的继续求值"——静默丢弃不可解析成员等于**悄悄缩小请求面**：我们**无法知道**丢弃后剩下的能力是否足以覆盖本次操作（例：工具其实需要 `WRITE_FILE`，声明被写错成 `writes_files` 而被丢弃）。"默认**缩小**请求"与"默认**放宽**请求"是同一件事（`REQ-SEC-01` 的 100% 拦截率随之失效） |
| 决策取值 | `allow=False` / `requires_confirmation=False`（**硬拒绝**）/ `risk_level=CRITICAL`——与空集同族（理由见下"为什么空集取硬拒绝"）；且与实现侧已钉住的不变式 `not allow and not requires_confirmation ⇒ risk_level is CRITICAL` 一致 |
| `capability` | `None`（**没有任何合法的"单一代表"可选**——这正是不变量 I4 要求"必须是 `Capability` 实例或 `None`"的由来） |
| `detail["requested"]` | `[]`（沿用 `[]` = "**没有可用的能力信息**"；**不得**只记合法子集——那会让读者以为"合法部分被考虑了/被授予了"，与"整体拒绝"的处置不符） |
| `detail["invalid"]` | **新增**：非法成员的安全标识列表（规范见下） |
| `outcome` | `DENY`（与 [`audit.md`](audit.md) §2.2 的 kind→outcome 约束一致） |
| `reason` | 中文，说明"请求含非法能力成员，整体拒绝"；**不回显**具体取值（`reason` 会进确认界面 / CLI；取值细节属诊断信息，放 `detail`） |

**`detail["invalid"]` 的规范（内容不可信，只作数据）**：

1. **升序、去重**（确定性：同一输入同一输出，与 `capability` 代表规则的取向一致）；
2. `str` 取值 ⇒ **先脱敏 + 截断**（`foundation.logging.sanitize_for_display`，上限 **32** 字符——
   与 `parse_capabilities()` 回显未知能力名的既有口径一致），**不得**整段回显；
3. **非 `str` 取值 ⇒ 只记类型名**（形如 `<int>`，**不回显内容**）：非字符串对象没有"名字"可谈，
   回显 `repr` 会把任意对象带进长期留存的证据；
4. **永远只是数据**：查看器 / 调用方**不得**据此做权限判定或名称解析；**禁止**把它写进
   `capability` 或 `requested`（那正是本次缺口）；**禁止**因它出现在 `invalid` 里就把它当作
   "已识别的能力"。

**实现顺序（§2.5 第 1 步的细化，顺序本身是契约的一部分）**：

```text
1. 求值
   1a. requested 为空集          ⇒ 硬拒绝（理由：未声明所需能力）
   1b. 存在非 Capability 成员    ⇒ 硬拒绝（理由：含非法能力成员）
   1c. missing / risk_level      ⇒ 常规求值（§2.4 的四格）
```

> 1b **必须**早于 1c：非法成员会让 `missing` 的集合运算与代表选取作用于**混合类型**
> （实测 `min({"bogus", Capability.READ_FILE}, key=str)` 返回裸 `str`），
> 审计的 `requested` / `missing` / `capability` 随之**全部不可靠**。
> 1a 与 1b 的先后无实质差别（空集没有成员可判非法）；此处固定为"先空集、后类型"只为**确定性**。

**与 `parse_capabilities()` 的分工（避免两处语义分叉）**：

| 检查点 | 输入 | 判据 | 失败处置 | 位置 |
| --- | --- | --- | --- | --- |
| **正规入口**（解析） | **声明式来源的字符串**：配置文件、领域包、未来 MCP 工具描述里的工具能力声明 | 名字能否成为 `Capability` 成员（`Capability(name)`） | `UnknownCapabilityError`（**拒绝，不跳过**） | `security/capabilities.py::parse_capabilities()`（**M0 在途**） |
| **引擎兜底**（类型） | 已被构造进 `PolicyRequest.requested` 的**对象** | `isinstance(member, Capability)` | 本节 1b 的**硬拒绝** | `security/policy.py::PolicyEngine` |

- **两者判据不同、方向相同**：入口管"**名字能不能变成成员**"，引擎管"**成员类型对不对**"。
- **引擎不得做名字解析**：那会把"名字→成员"的职责复制两份 ⇒ 正是分叉源；且如上实测，
  **名字检查单独不可靠**（裸 `str` 与枚举取值相等）。反过来**入口也不得只做类型检查**
  （字符串永远"是字符串"）。
- **不得只保留一处**：删入口 ⇒ 非法值以更晚、更隐蔽的形态出现（本缺口即是实例）；
  删兜底 ⇒ 引擎的判定依赖"调用方守规矩"（与"前置条件只是调用方约定"同一依据）。
- **上游要求（`HARNESS` / `tools/`）**：`ToolSpec.capabilities` 的类型标注挡不住来自 MCP /
  领域包的字符串 ⇒ **工具注册 / 加载时**必须经 `parse_capabilities()` 解析并拒绝非法声明；
  `PolicyRequest.requested` 只能由**已解析的** `Capability` 构成。

**为什么空集取硬拒绝（`False/False`）而不是可升级拒绝（`False/True`）**：`False/True` 的语义是
"可经人工确认后继续"（§2.4），而"所需能力未知"意味着审批门**没有任何东西可以对照**——一旦
允许人工放行，人工确认就从"风险确认"退化为**绕过 default-deny 的通道**（`REQ-SEC-01` 的
"未授权操作拦截率 100%"随之失效）。故取四格中更保守的一格。

**为什么空集也必须留下审计事件**：本节"返回前必须先 `emit`"是**无条件**的（`REQ-SEC-06`：
每次拒绝均有可回放记录）。**禁止**用"空集时不审计"来回避上述冲突——那等于把一次契约冲突
改写成一次**静默的证据丢失**，是比原问题更严重的缺陷。

**为什么不在契约层校验"非空"**：见 §2.3 的"谁产生"。即：上游强制只能落在 HARNESS 的构造点
（调用方约定），**不能**因此取消引擎的防御分支——`PolicyRequest` 可被任意调用方（测试、
`cli/`、未来第二个调用点）构造，而引擎是安全决策点，其行为**不得**依赖"调用方守规矩"。

**事件内容要求（`POLICY_DECISION`）**：`detail["requested"]` **必存在**且为**升序能力名列表**
（元素是 `Capability` 的**值**字符串）；**求值未失败时**另需 `detail["missing"]`（同规范）。
求值失败时 `missing` **可省略，且不得以 `[]` 冒充**——`[]` 的语义是"无缺失 ⇒ 已授权"，
用它表示"求值失败"会把故障伪装成授权充足。`detail["requested"]` 记 `[]` 有**三种来源**，
**必须用判别键区分**（否则"没有能力信息"与"请求非法"在审计里同形）：

| `[]` 的来源 | 判别键 | 语义 |
| --- | --- | --- |
| **确为空集** | 既无 `error` 也无 `invalid` | 请求没声明任何能力（构造缺陷） |
| **请求不可解析** | `detail["error"]`（异常类型名） | 求值阶段故障 |
| **含非 `Capability` 成员** | `detail["invalid"]`（安全标识列表） | 类型违规输入（规范见上） |

`detail` 里的 `reason` / `domain_pack` 等其余键**不禁止**。

**实现侧影响（历次裁决累计，共两处）**：

1. **求值失败路径**（"实现顺序"第 1 步的收敛分支）当前只写 `detail["error"]`，**未写 `requested`**
   ⇒ 需按"事件内容要求"补齐（不可解析时记 `[]`），否则违反 [`audit.md`](audit.md) §2.3 的 I2/I3；
2. **新增 1b 分支**（存在非 `Capability` 成员 ⇒ 硬拒绝，见上）：判据用 `isinstance(member, Capability)`，
   **不得**用名字解析；`requested` 记 `[]`、非法项记 `detail["invalid"]`、`capability=None`。

其余行为与现行实现一致（**空集与多元素的处置无需改动**）。

**验证方式**（每条都是可断言判据；`tests/security/` 归验证工程师、`tests/unit/` 归实现工程师）：

| # | 输入 | 期望 |
| --- | --- | --- |
| V1 | `requested=frozenset()`，`granted=list(Capability)`（**全授予**） | `(allow, requires_confirmation) == (False, False)`（**即使全授予也拒绝**，防"没有要求就没有限制"）；`risk_level is CRITICAL`；`outcome is DENY`；恰好 1 条事件；`event.capability is None`；`detail["requested"] == []` |
| V2 | `requested={read_file, execute_command}`，仅授予 `read_file` | `(False, False)`；`event.capability is Capability.EXECUTE_COMMAND`（**缺的那个**）；`detail["missing"] == ["execute_command"]`；`detail["requested"]` 升序 |
| V3 | `requested={read_file, write_file}`，全授予且 `risk_level=LOW` | 放行；`event.capability is Capability.READ_FILE`（`read_file` < `write_file`，**确定性代表**）；重复调用结果一致 |
| V4 | 求值失败（能力模型抛错）、`requested={write_file}` | 收敛为拒绝；`event.capability is Capability.WRITE_FILE`；`detail["requested"] == ["write_file"]`；`detail` **无** `missing` 键 |
| V5 | 求值失败、`requested=None`（不可解析） | 收敛为拒绝；`event.capability is None`；`detail["requested"] == []` **且** `detail["error"]` 存在 |
| V6 | 对 V1~V10 产生的**全部**事件套用 [`audit.md`](audit.md) §2.3 的 **I1~I4** | 全部成立（建议抽成**一个**不变式断言函数，复用到全部策略用例） |
| V7 | `requested=frozenset({"bogus"})`，且 `granted=list(Capability)`（**全授予**） | `(allow, requires_confirmation) == (False, False)`；`risk_level is CRITICAL`；`outcome is DENY`；恰好 1 条事件；`capability is None`；`detail["requested"] == []`；`detail["invalid"]` 含 `"bogus"`；`detail` **无** `error` 键 |
| V8 | `requested=frozenset({"read_file"})`（**裸 `str`、取值合法**），`granted={READ_FILE}`，`risk_level=LOW` | **仍然拒绝** `(False, False)`——**不得放行**；`capability is None`；`detail["invalid"] == ["read_file"]` ⇒ 直接钉住"必须类型检查、不得名字检查" |
| V9 | `requested=frozenset({Capability.READ_FILE, "bogus"})`，`granted={READ_FILE}` | **整体拒绝** `(False, False)`（**不得**因 `read_file` 已授予而放行，也**不得**只报 `read_file`）；`detail["requested"] == []`；`detail["invalid"] == ["bogus"]` |
| V10 | `requested=frozenset({123})`（非 `str` 成员） | 拒绝；`detail["invalid"] == ["<int>"]`（**只类型名、不回显内容**）；审计里**不出现**该对象的 `repr` |

**被否决的方案（记录理由，防止重复讨论）**：

| 候选 | 内容 | 结论与理由 |
| --- | --- | --- |
| **R1**（**采纳**） | 修订不变式：`capability` 非 `None` **以 `requested` 非空为前置**；空集与多元素行为显式规定 | 唯一能同时满足"必须 `emit`"与"单值字段语义"的方案；**不改 schema、不扩权限模型面积** ⇒ 无需 ADR |
| R2 | 给 `Capability` 增加占位成员（如 `NONE`） | **否决**。① **不能消除空集**：`frozenset()` 仍可构造，引擎的防御分支照样要有 ⇒ 付出 ADR 成本却没解决根因；② 同一语义会有**两种表示**（`frozenset()` 与 `frozenset({NONE})`），审计出现两种等价记录而无法判断哪种是规范形；③ 哨兵值污染授权语义（`CapabilitySet.allows(NONE)` 该返回什么？`parse_capabilities(["none"])` 会被静默接受）；④ 扩成员改变**权限模型面积**，按 §2.1 与 `contracts/policy.py` 的 docstring 需开 ADR——为一个只用于**表达"无"**的哨兵值开 ADR，代价远大于收益 |
| R3 | 规定 `requested` 必须非空，由 HARNESS 在**构造前**强制 | **单独采用不足以解决**（已作为 R1 的**附加**前置条件采纳）。① 类型层面表达不了非空（`frozenset`），静态检查拦不住；② 契约层零行为 ⇒ 不能落在 `PolicyRequest` 上 ⇒ 只能靠**调用方自觉**；③ 引擎仍会收到空集（测试、`cli/`、未来调用点）⇒ 防御分支**仍然必须**有定义。即 R3 管**上游卫生**、R1 管**引擎兜底**，两者不是替代关系 |
| R4 | 把 `AuditEvent.capability` 改成 `frozenset[Capability]` | **否决**。① 改的是**已定案**的审计 schema（`approval` / `refusal` 生产者与按工具/结果的查询口径都要跟着改），影响面远超本缺口；② `REQ-OBS-01` 的检索维度是标量，集合字段削弱可查询性；③ 审计事件本就用 `detail` 承载结构化附加信息，"集合进 `detail`"是既有约定，不必改 schema |
| R5 | **非法成员走"求值失败"路径**（`False/True` 收敛） | **否决**。① **不可对照**：与空集同族——人工确认无法解除一个"所需能力不可解析"的请求 ⇒ 放行即绕过 default-deny；② 会把**实现层故障**与**请求侧缺陷**混进同一条路径，审计读者无法区分"引擎坏了"与"调用方传错了"（这正是本节反复强调的"表象相同、性质不同"）；③ `False/True` 分支的判别键是 `detail["error"]`（**异常类型名**），把非异常情形塞进去会让该键**语义漂移** ⇒ 本次为非法成员单列 `detail["invalid"]`，**两类情形在审计里必须可区分** |
| R6 | **忽略非法成员、用合法子集继续求值** | **否决**：静默缩小请求面 = fail-open（"默认缩小请求"与"默认放宽请求"同一件事）；且丢弃哪些成员由**输入**决定，等于让不可信输入影响权限判定的**输入面**（`REQ-SEC-03`） |

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
   契约层只放 Protocol；
7. `PolicyRequest` docstring 补**两条**前置条件（`requested` **必须非空**；**每个元素必须是
   `Capability` 成员**——裸 `str` 即使取值合法也不合规；两条均属上游构造缺陷，见 §2.3）
   ——**仅注释**；
8. `PolicyEngine` Protocol docstring 补一句空集处置（硬拒绝 + **仍须 `emit`**；`capability=None`
   由 `detail["requested"] == []` 表达，见 §2.5「补充规定」）——**仅注释**；
9. **禁止**为"非空"加 `__post_init__` 之类的运行期校验：`contracts/` 的零行为不变量
   （[`README.md`](README.md) §5）优先。

---

## 5. 修订记录

| 日期 | 修订 | 依据 |
| --- | --- | --- |
| 2026-09-19 | §2.3 给 `requested` 加**非空**前置条件（并说明契约层不可强制）；§2.4 的 `False/False` 一格补来源；§2.5 新增「补充规定」——空集/多元素的裁决、`capability` 确定性代表规则、事件内容要求、验证判据 V1~V6、被否决方案 R2/R3/R4；§4 补 3 条实现动作 | 实现侧报出的契约缺口；裁决方向 **R1** |
| 2026-09-19 | §2.3 补**前置条件二**（元素必须是 `Capability` 成员，裸 `str` 不合规）；§2.4 的 `False/False` 一格再补来源；§2.5 新增「**非法成员的规定行为**」——实测证据（含"裸 `str` 取值合法 ⇒ 被放行"）、裁决 (a) 与逐项规定、`detail["invalid"]` 规范、实现顺序 1a/1b/1c、与 `parse_capabilities()` 的分工表、被否决方案 R5/R6；验证判据扩为 **V1~V10**（V6 同步为 I1~I4）；"`[]` 的来源"由两种更正为**三种**并给出判别键；§4 第 7 条同步 | 实现侧第二/第三轮报出的同族缺口（类型违规输入）；架构侧独立复现并**扩大**（ALLOW 路径同样被破） |
