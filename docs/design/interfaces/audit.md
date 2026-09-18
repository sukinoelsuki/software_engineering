# 契约：`contracts/audit.py`

- 对应模块：`src/agent_sec_perf/observability/`（横切 OBS 层：结构化日志 / 审计落盘与查询）
- 上游决策：ADR-0015 §5.1.2（`HARNESS/CAPABILITY ↔ OBS` 行）、
  ADR-0006 §5.2 规则 S-2（**禁止静默降级**，降级必须结构化记录）、
  SRS `REQ-SEC-06/07/08`、`REQ-OBS-01`、`REQ-UX-02`
- 依赖：`contracts/policy.py`（`Capability` / `RiskLevel`）——契约层内部引用，允许（R3）

本文件回答 Q1（`AuditEvent` 字段）。

---

## 1. 设计要点（先读这一节）

1. **审计是"可回放"的载体**（`REQ-SEC-06`）：一条事件必须能独立回答
   "谁、在哪个会话、对哪个工具、请求了什么能力、结果如何"。因此**关联键必须显式**
   （`event_id` / `session_id` / `call_id`），不靠时间戳猜。
2. **失败必须冒泡**：`emit()` / `flush()` 的异常**不得**被吞——静默丢事件等于 `REQ-SEC-06`
   验收失败（ADR-0015 §5.1.2）。这一条与 `PolicyEngine` 的"求值失败收敛为拒绝"**处置相反**，
   见 [`policy.md` §2.5](policy.md#25-policyengineq3)。
3. **`detail` 必须先脱敏再入事件**：`REQ-SEC-07` 要求"敏感信息不入日志/出站"。
   契约只能**要求**脱敏，具体规则属 `observability/` 设计（U4）。
4. **只追加、不可变**：事件一旦 `emit` 即成为**证据**，不得原地修改（`frozen=True` 是契约级保证）。

---

## 2. 类型定义

### 2.1 `AuditEventKind`（**新增**）

```python
class AuditEventKind(StrEnum):
    POLICY_DECISION = "policy_decision"  # 一次策略求值（REQ-SEC-06）
    APPROVAL = "approval"  # 一次人工确认选择（REQ-UX-02：本次/总是/拒绝均被审计）
    TOOL_CALL = "tool_call"  # 一次工具调用的发起与结果（REQ-SEC-06 / REQ-OBS-01）
    EXECUTION_DEGRADATION = "execution_degradation"  # 沙箱或模型路由降级（ADR-0006 §5.2 规则 S-2）
    REFUSAL = "refusal"  # 能力边界拒答（REQ-SEC-08）
```

**为什么是这 5 个**：每一个都对应一条**明确要求**（见上表注释），没有"为了完整而列"的成员。
`REQ-OBS-01` 要求"可按时间/工具/结果检索"——这由 `timestamp` / `tool_name` / `outcome`
三个字段支撑，不需要额外的 kind。

### 2.2 `AuditOutcome`（**新增**）

```python
class AuditOutcome(StrEnum):
    ALLOW = "allow"  # 放行（策略 / 审批）
    DENY = "deny"  # 拒绝（策略 / 拒答）
    CONFIRM = "confirm"  # 待人工确认（尚未执行）
    OK = "ok"  # 执行成功
    ERROR = "error"  # 执行失败
    DEGRADED = "degraded"  # 发生了降级
```

**kind → outcome 的取值约束**（实现与测试按此校验）：

| `kind` | 允许的 `outcome` |
| --- | --- |
| `POLICY_DECISION` | `ALLOW` / `DENY` / `CONFIRM` |
| `APPROVAL` | `ALLOW` / `DENY` |
| `TOOL_CALL` | `OK` / `ERROR` |
| `EXECUTION_DEGRADATION` | `DEGRADED` |
| `REFUSAL` | `DENY` |

> 用枚举报 `outcome` 而不是裸 `str`：`REQ-OBS-01` 要"按结果检索"，裸字符串会因拼写差异
> （`"deny"` / `"denied"` / `"rejected"`）让查询静默漏项。

### 2.3 `AuditEvent`（Q1）

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `event_id` | `str` | 事件标识（`uuid4().hex`）；与 `PolicyDecision.audit_id` / `ToolResult.audit_id` 同源 |
| `kind` | `AuditEventKind` | 事件类别（见 §2.1） |
| `timestamp` | `str` | **ISO-8601 UTC**，必须带时区（如 `2026-09-18T08:06:08.123456+00:00`） |
| `session_id` | `str` | 会话标识 |
| `outcome` | `AuditOutcome` | 结果（见 §2.2） |
| `call_id` | `str \| None` | 本次操作标识（`TOOL_CALL` / `POLICY_DECISION` 必填） |
| `tool_name` | `str \| None` | 相关工具名（`REQ-OBS-01` 按工具检索） |
| `capability` | `Capability \| None` | 相关能力的**单一代表**（多能力时为字典序最小者；空能力请求时为 `None`）；**完整集合在 `detail["requested"]`**（不变式 I2/I3；取值规则见 [`policy.md`](policy.md) §2.5「补充规定」） |
| `risk_level` | `RiskLevel \| None` | 风险等级（策略决策与审批时给出） |
| `detail` | `Mapping[str, object]` | 结构化附加信息；**必须已脱敏**（`REQ-SEC-07`） |

```python
@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    kind: AuditEventKind
    timestamp: str
    session_id: str
    outcome: AuditOutcome
    call_id: str | None = None
    tool_name: str | None = None
    capability: Capability | None = None
    risk_level: RiskLevel | None = None
    detail: Mapping[str, object] = field(default_factory=dict)
```

**关于 `timestamp` 用 `str` 而不是 `datetime`**：

- 审计是**要落盘、要被查询**的数据；ISO-8601 文本是它的**存储形态**，直接写入可避免
  "序列化时区处理不一致"这一经典缺陷；
- 强制"**必须带时区**"（`DTZ` 规则与 naive datetime 的教训）：naive 时间跨时区比较会静默出错；
- 实现侧仍可用 `datetime.now(tz=UTC).isoformat()` 生成，二者不冲突。

**谁产生哪类事件（生产者归属，`REQ-SEC-06` 的"每次决策/调用"靠它闭合）**：

| `kind` | 生产者 | 关键内容 |
| --- | --- | --- |
| `POLICY_DECISION` | `security/policy.py`（`PolicyEngine` 实现） | `capability` / `risk_level` / `outcome`；`event_id` 即 `audit_id`；`detail` **必含** `requested`（I2），求值未失败时另有 `missing` |
| `APPROVAL` | `cli/approval.py` | 用户选择（本次/总是/拒绝）；`tool_name` + `outcome` |
| `TOOL_CALL` | `harness/`（调用前后各一条，或用 `detail` 合并成败） | `call_id` / `tool_name` / `outcome` |
| `EXECUTION_DEGRADATION` | `security/sandbox/` 或 `model/router.py` | 降级原因与未满足维度；`detail` 携带 `IsolationMatrix` 摘要 |
| `REFUSAL` | `security/refusal.py` | 拒答理由（`REQ-SEC-08`） |

> **为什么 `capability` 不再是"无条件非 `None`"**：`capability` 是**单值**字段，而
> `PolicyRequest.requested` 是**集合**（[`policy.md`](policy.md) §2.3）——空集时没有能力可填，
> 多元素时"填哪一个"未定义。原写法在**空集**下与 [`policy.md`](policy.md) §2.5 的
> "返回前必须 `emit` 一条 `POLICY_DECISION`"**不可同时满足**（2026-09-19 由实现侧报出、
> 架构侧复核确认）。修订后：**完整集合进 `detail["requested"]`，单值字段是它的确定性代表**，
> 两者由 I3 锁定一致；空集与多元素的规定行为见 [`policy.md`](policy.md) §2.5「补充规定」。

**不变式**（`POLICY_DECISION` 部分于 2026-09-19 修订为 I1~I3，缘由见 §4）：

- **I1（关联键，无条件）**：`kind == POLICY_DECISION` ⇒ `call_id` / `risk_level` 均非 `None`；
- **I2（能力集合必须可回放）**：`kind == POLICY_DECISION` ⇒ `detail["requested"]` **存在**，
  为 `list[str]`——元素是 `Capability` 的**值**、**升序**、无重复；请求不可解析时记 `[]`
  （此时 `detail["error"]` 存在，见 [`policy.md`](policy.md) §2.5）；
- **I3（单值字段与集合字段必须一致）**：`capability is None` **⇔** `detail["requested"] == []`；
  且 `capability is not None` ⇒ `capability.value ∈ detail["requested"]`；
- `kind == TOOL_CALL` ⇒ `call_id` / `tool_name` 非 `None`；
- `detail` 中**不得**出现密钥/令牌/凭据/个人数据（`REQ-SEC-07`）；脱敏由 `observability/`
  的处理器保证（U4），契约层只强制"已脱敏"这一前置条件。

### 2.4 `AuditSink`（已有，**不变**）

```python
class AuditSink(Protocol):
    def emit(self, event: AuditEvent) -> None: ...

    def flush(self) -> None: ...
```

| 项 | 规定 |
| --- | --- |
| 并发 | 无状态；**实现内部串行化写入**（可被多线程调用） |
| 资源 | 进程退出前**必须** `flush()`（否则缓冲区里的事件丢失 = `REQ-SEC-06` 验收失败） |
| 错误语义 | `emit` / `flush` 失败**必须冒泡**；**禁止**吞异常或降级为告警 |
| 幂等性 | `flush` 应可重复调用（退出路径可能多次触发） |

### 2.5 落点来源与路径白名单（**新增**，2026-09-19）

**问题**：`AuditSink` 的落点来自 `foundation/config.py` 的 `[audit] directory`。而**项目级
`.lowspec.toml` 跟着仓库走** ⇒ 按 `SECURITY.md` 的口径它**是不可信输入**（克隆来的仓库、
或被别的进程 / 代理写入）⇒ `audit.directory` 是**攻击者可能影响的值**。

**为什么必须堵**（不是"顺手加的校验"）：

1. `SECURITY.md` 硬性要求"文件路径必须做**规范化与白名单校验**（防目录穿越）；
   禁止用字符串拼接构造路径"——只校验"绝对路径 + 无 NUL"**不满足**这一条；
2. 不加约束时它是一个**任意路径追加写**原语：`emit` 以 `"a"` 打开目标文件，
   可被用来**覆盖/污染**用户文件（写进 `authorized_keys`、日志、dotfile 一类"后续会被别的
   程序读"的位置）、把审计写入**取证看不到**的地方（`REQ-SEC-06` 可回放性被架空），
   或指向不可写路径制造 **DoS**（`emit()` 冒泡 ⇒ 会话失败）；
3. 审计落点是"**每次拒绝均有可回放记录**"（`REQ-SEC-06`）的**唯一载体**——让不可信内容
   决定"审计写到哪"，等于让攻击者决定证据存放在哪里。

**裁决（规范性原文）**：

| # | 规定 |
| --- | --- |
| **P1** | **允许的根集合是常量，且不来自配置**：`foundation.config.ALLOWED_AUDIT_ROOTS`（元组；当前**只含** `default_audit_directory()` 一个元素）。配置**不得**影响它——`roots` 只允许两种来源：**该常量**，或**装配代码显式给出的值**。 |
| **P2** | **配置期校验（早失败）**：`load_config()` 校验 `audit.directory` 时**必须**经 `foundation.paths.resolve_within(candidate, ALLOWED_AUDIT_ROOTS, what="audit.directory")`，并把**已 `resolve` 的结果**（不是原始字符串）写入 `AuditConfig.directory`。`..` 上跳与符号链接逃逸由 `expanduser().resolve()` 展开后再判定，因此**天然被拒**；`~` 展开后落在根外同样被拒（这是预期的收紧）。越界 ⇒ **`ConfigError`**，**不得回退默认目录**。 |
| **P3** | **装配期再校验一次（纵深防御）**：`JsonlAuditSink.__init__(directory, *, roots=ALLOWED_AUDIT_ROOTS, filename=...)` **必须自己**再跑一次 `resolve_within(directory, roots, what="审计目录")`，越界 ⇒ **`PathNotAllowedError`**。理由：配置只是**一条**调用路径——测试、未来的 CLI 参数、其它装配点会**绕过配置**直接构造 sink；契约**不接受**"调用方一定校验过"这一假设。`roots` **有安全默认**（常量）且**必须可被显式覆盖**（测试要用 `tmp_path`）；默认值取向遵循约定 `C6`（最保守的一方）。 |
| **P4** | **必须先校验、后 `mkdir`**：任何越界判定都发生在**创建目录之前**。否则"拒绝"的语义会被破坏——`mkdir(parents=True, exist_ok=True)` 本身就已经按不可信路径**写了一次**（创建目录即越界写）。 |
| **P5** | **禁止静默降级**：不得"越界就改用默认目录"、不得"越界就关闭审计"、不得捕获 `PathNotAllowedError` 后继续（`ADR-0006 §5.2` 规则 `S-2`）。落点无法确定时唯一正确的处置是**拒绝启动**。 |
| **P6** | **校验时点在构造期，不在每次 `emit`**：路径在构造后已固定为 `self._path`，每次 `emit` 重校验没有增量安全，只是把检查塞进热路径。构造期覆盖不了的运行期替换（TOCTOU）是**既有残余**（[`../threat-model/execution-and-isolation.md`](../threat-model/execution-and-isolation.md) 的 `T-02` 残余风险 2），不靠"每写一次查一次"解决——`open` 与判定之间仍有窗口。 |
| **P7** | **`emit()` / `flush()` 的冒泡规则不变**（§2.4）：构造期拒绝是**另一条**失败路径，**不改变**"写入失败必须冒泡"。 |

**为什么根取"默认审计目录"这一个点，而不是更大的集合**：

| 候选根 | 结论 | 理由 |
| --- | --- | --- |
| `default_audit_directory()`（**推荐**） | 采纳 | **最小充分**：审计只需要"写审计"。根收紧到审计子树后，未来 `user_state_path` 下出现会话/索引文件时，"审计"也不会自动获得覆盖它们的权限 |
| `user_state_path`（整个应用状态目录） | 否决 | 把审计模块的写权限放大到"应用**全部**状态"——权限面大于需求（最小权限） |
| 由**配置文件**给出 | **禁止** | 是不可信来源 ⇒ 等于把白名单交给攻击者 |
| 由调用方给任意根、**无安全默认** | 否决 | "忘了传"与"传一个宽根（如 `/`）"两种退化都没有护栏；`resolve_within` 只保证"不越出给定根"，**它不校验根本身**（`T-02` 残余风险 3 已登记同一失效模式） |

**配置键的取舍**：**保留 `audit.directory`**，语义**收窄**为"必须是 `ALLOWED_AUDIT_ROOTS` 内的路径"。
更彻底的替代是"删掉 `directory`、只保留 `audit.filename`（落点完全钉死）"——**面更小**，
但会改动**已定案**的配置 schema（`foundation/config.py` 与其单测），且失去"在审计子树内按会话 /
日期分子目录"的能力 ⇒ 列为**待确认项**（见文末「待确认项」），由所有者拍板。两者共同点是**根被钉死**，
差别只在"是否允许在根内选子目录"。

**两种输入来源 → 两类异常（与 `ADR-0015` §5.1.2 对齐）**：

| 输入来源 | 校验点 | 越界时抛 | 为什么是这个类型 |
| --- | --- | --- | --- |
| 配置文件里的 `audit.directory` | `foundation/config.py` 加载期 | `ConfigError` | 语义是"**配置非法**"（用户可修配置）；`ADR-0015` §5.1.2 已规定"只有 `ConfigError` 会冒泡到 CLI" |
| 代码传入的 `directory=` | `JsonlAuditSink` 构造期 | `PathNotAllowedError` | 语义是"**该路径未被允许**"（调用方越界，属程序错误）；与 `paths.resolve_within` 的既有语义一致（`errors.py`） |

> 两层**不是重复**：同一份非法配置通常在第一层就被拦下（`ConfigError`）；第二层覆盖的是
> **绕过配置的调用方**。**不得**只保留其中一层——只留配置层 = 信任调用方；只留装配层 =
> 配置错误会以"路径越界"的形态出现在更晚的位置，且错误信息不再指向配置文件。
>
> **与 `ADR-0015` §5.1.2 不冲突**：该行的"只有 `ConfigError` 会冒泡到 CLI"约束的是
> **`Session.run` 之内**的业务失败（用 `SessionEvent(kind="error")` 表达）。而 sink 的构造发生在
> **会话建立之前**（装配阶段）⇒ 抛 `PathNotAllowedError` 不属"业务失败"范畴。
> **待办（`cli/` 设计时落地）**：装配点应把包括 `PathNotAllowedError` 在内的 `BenchError`
> 转为"中文错误提示 + 非零退出码"，**不得**转为"回退默认落点"。

**验证方式**（**可执行**判据；`tests/security/` 归验证工程师、`tests/unit/` 归实现工程师）：

| # | 输入 | 期望 |
| --- | --- | --- |
| W1 | 项目级 `.lowspec.toml`：`[audit] directory = "<根外目录>"` | `load_config()` 抛 `ConfigError`；**且该目录未被创建**（防"先建后拒"的实现） |
| W2 | `directory` 形如 `<允许根>/../../.ssh`（上跳） | `ConfigError`（`..` 被 `resolve()` 展开后落在根外） |
| W3 | 允许根内建一个**指向根外**的符号链接，`directory` 指向该链接 | `ConfigError`（`resolve()` 展开符号链接后判定） |
| W4 | `directory` 恰为允许根本身，或位于根内的子目录 | 加载成功；`AuditConfig.directory` 是**已 resolve 的绝对路径**（不是原始字符串） |
| W5 | **绕过配置**直接 `JsonlAuditSink("<根外目录>")`（用默认 `roots`） | 抛 `PathNotAllowedError`；**且目录未被创建**；`sink.path` 不可得 |
| W6 | 越界之后 | 断言**没有**回退到 `default_audit_directory()`（该目录下无新文件），也**没有**静默关闭审计 |
| W7 | **变异探针**：把 `resolve_within` 调用换成 `pathlib.Path(directory)` | W1~W5 至少 3 条**失败** ⇒ 证明断言依赖真实白名单、非恒过 |
| W8 | **两层各删一层**（只留配置期 / 只留装配期） | 对应层的用例失败 ⇒ 证明两层**都在起作用**，而非"其中一层恒真" |

> **单测注意（隐蔽失效模式）**：`ALLOWED_AUDIT_ROOTS` 在 `observability/audit.py` 里应通过
> **模块属性**读取（`config.ALLOWED_AUDIT_ROOTS`），**不要**写成
> `from ... import ALLOWED_AUDIT_ROOTS` 的直接绑定——后者会让测试替换根时**静默失效**
> （被替换的是源模块属性，而 sink 用的是导入时的旧值）。

**落地动作（实现侧）**：

| 文件 | 动作 |
| --- | --- |
| `foundation/config.py` | 新增常量 `ALLOWED_AUDIT_ROOTS`；`_as_absolute_directory` 收窄为"形状校验 + `resolve_within`"（把 `PathNotAllowedError` 转成 `ConfigError`，并保存**已 resolve** 的路径） |
| `observability/audit.py` | `__init__` 增加 `roots` 形参（默认常量），**先** `resolve_within` **再** `mkdir`；`self._directory` 存 resolve 结果；用模块属性读取常量 |
| `contracts/audit.py` | **无需改动**（`AuditSink` 只声明 `emit` / `flush`，构造签名不属契约形状；本节的约束落在"装配约定"上） |
| `tests/unit/` | 既有用到 `audit.directory` 的单测需改为在允许根内取值，或显式覆盖 `roots` |

---

## 3. 对 `contracts/audit.py` 的改动清单

1. 新增 `AuditEventKind` / `AuditOutcome` 两个 `(StrEnum)`；
2. `AuditEvent` 补 10 个字段（其中后 6 个有默认值）；
3. import 补 `field`（`dataclasses`）、`Capability` / `RiskLevel`（`contracts/policy.py`）；
4. `AuditSink` **保持不变**；
5. 模块 docstring 删除"字段未规定 ⇒ 占位"，改为指向本文件并重申"失败必须冒泡"。
6. `AuditEvent` docstring 里的不变式一句按 §2.3 修订后的 I1~I3 改写（**仅注释**）；
7. **字段与类型不变**——本次是语义澄清，不改 schema，**不得**为 I1~I3 在契约层加运行期校验
   （`contracts/` 的零行为不变量，见 [`README.md`](README.md) §5）。

---

## 4. 修订记录

| 日期 | 修订 | 依据 |
| --- | --- | --- |
| 2026-09-19 | §2.3 的不变式第 1 条改写为 I1~I3：`capability` 的"非 `None`"改为**以 `requested` 非空为前置**，并把**完整能力集合**规定为 `detail["requested"]`；同步 §2.3 字段表、生产者表与 §3 清单 | 实现侧报出的契约缺口（空集下"必须 `emit`"与"`capability` 非 `None`"互斥）；裁决、规则与验证判据见 [`policy.md`](policy.md) §2.5「补充规定」 |
| 2026-09-19 | **新增 §2.5「落点来源与路径白名单」**：`audit.directory` 来自项目级 `.lowspec.toml`（不可信输入）⇒ 现只校验"绝对路径 + 无 NUL"**不满足** `SECURITY.md` 的路径白名单硬性要求。规定 P1~P7（根集合为常量 `ALLOWED_AUDIT_ROOTS`、配置期 `ConfigError` / 装配期 `PathNotAllowedError` 两层校验、先校验后 `mkdir`、禁止静默降级回退、校验时点在构造期、冒泡规则不变），并给出 W1~W8 判据 | 实现侧报出的接口决策缺口（`resolve_within` 的 `roots` 需调用方提供，"允许哪些根"属接口决策）；复核成立：不加约束时构成**任意路径追加写**原语；威胁模型侧同步见 `T-02`（**不升降状态、不改计数**） |

---

## 5. 待确认项（**需所有者拍板**，架构侧不代为决定）

> 这两项都属**策略**而非**机制**：机制（P1~P7）已定死，下面两项只影响"允许的根集合有多宽"与
> "配置面有多大"。在拍板之前，实现按**当前最保守的取值**进行（单一根 + 保留 `directory` 键）。

| # | 待确认项 | 候选 | 架构侧倾向与理由 | 不拍板的后果 |
| --- | --- | --- | --- | --- |
| **A1** | **是否允许 `ALLOWED_AUDIT_ROOTS` 含"额外根"**（典型诉求：CI 把审计放到挂载卷 / 演示时放到仓库内以便归档） | ① 只保留 `default_audit_directory()`（当前）② 允许**编译期固定**的第二个根 ③ 允许装配代码**运行时**传入 | 倾向 ①；若确有 CI 归档需求，选 ②（固定在常量里 ⇒ 仍需过 ADR 与 review），**不要**选 ③——运行时传入等于把"根集合"变成运行期可变状态，审计落点将随调用点漂移，`REQ-SEC-06` 的可回放性要靠人去追 | 实现按 ① 落地；后续若出现 CI 归档需求，须走 ADR 扩常量（含影响面评估） |
| **A2** | **是否移除 `audit.directory` 键**（改为只保留 `audit.filename`，落点完全钉死） | ① 保留（语义收窄为"根内路径"）② 移除（面最小） | 倾向 ①：本缺口是"**校验缺失**"而非"**配置项本身有害**"，收窄校验即可闭合；② 面更小但属**配置 schema 的破坏性变更**（`foundation/config.py` 与其单测已入库） | 实现按 ① 落地（保留键 + 收窄校验）。**注意**：无论哪一项，P1~P7 与 W1~W8 都**不**随之变化——差别只在"配置能否在根内选子目录" |
