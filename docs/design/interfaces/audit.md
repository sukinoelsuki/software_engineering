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
class AuditEventKind(str, Enum):
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
class AuditOutcome(str, Enum):
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
| `capability` | `Capability \| None` | 相关能力（策略决策与拒答时给出） |
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
| `POLICY_DECISION` | `security/policy.py`（`PolicyEngine` 实现） | `capability` / `risk_level` / `outcome`；`event_id` 即 `audit_id` |
| `APPROVAL` | `cli/approval.py` | 用户选择（本次/总是/拒绝）；`tool_name` + `outcome` |
| `TOOL_CALL` | `harness/`（调用前后各一条，或用 `detail` 合并成败） | `call_id` / `tool_name` / `outcome` |
| `EXECUTION_DEGRADATION` | `security/sandbox/` 或 `model/router.py` | 降级原因与未满足维度；`detail` 携带 `IsolationMatrix` 摘要 |
| `REFUSAL` | `security/refusal.py` | 拒答理由（`REQ-SEC-08`） |

**不变式**：

- `kind == POLICY_DECISION` ⇒ `call_id` / `capability` / `risk_level` 均非 `None`；
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

---

## 3. 对 `contracts/audit.py` 的改动清单

1. 新增 `AuditEventKind` / `AuditOutcome` 两个 `(str, Enum)`；
2. `AuditEvent` 补 10 个字段（其中后 6 个有默认值）；
3. import 补 `field`（`dataclasses`）、`Capability` / `RiskLevel`（`contracts/policy.py`）；
4. `AuditSink` **保持不变**；
5. 模块 docstring 删除"字段未规定 ⇒ 占位"，改为指向本文件并重申"失败必须冒泡"。
