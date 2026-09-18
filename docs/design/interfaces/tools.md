# 契约：`contracts/tools.py`

- 对应模块：`src/agent_sec_perf/tools/`（L2 能力层；工具是信任边界的**执行侧**）
- 上游决策：ADR-0015 §5.1.2（`HARNESS ↔ CAPABILITY` 行 + "关键约定"）、§5.3 自研模块 3、
  §5.4.1（实现落 `tools/registry.py`）
- 依赖：`contracts/policy.py`（`Capability`）——契约层内部引用，允许（R3）。
  **不依赖** `contracts/audit.py`：工具经 `ToolResult.audit_id`（`str`）回指审计事件，
  **不**直接持有 `AuditSink`（见 [README](README.md) §2 的 C10）

本文件回答 Q8（`ToolSpec` / `ExecutionContext` 字段）与 Q3（`ToolRegistry` 是否入契约）。

---

## 1. 设计要点（先读这一节）

1. **一次工具调用被拆成三个时刻**，各有明确归属（这是整个工具边界的安全骨架）：

   ```mermaid
   sequenceDiagram
       participant H as HARNESS
       participant P as PolicyEngine
       participant T as Tool (capability)
       H->>H: 1. 按 ToolSpec.parameters_schema 校验原始 JSON（信任边界）
       H->>P: 2. decide(PolicyRequest)
       P-->>H: PolicyDecision（allow / requires_confirmation）
       H->>T: 3. invoke(args=已校验参数, ctx=ExecutionContext)
       T-->>H: ToolResult
   ```

2. **`args` 必须是已校验的结构化参数**：`Tool.invoke` **不得**再解析原始 JSON 字符串——
   否则校验可被绕过，违反"不可信内容一律视为数据"（ADR-0015 §5.1.2 的关键约定，`REQ-SEC-03`）。
3. **`ToolResult.ok=False` 是"工具级失败"，不是异常**：它会被**回喂**给模型（`REQ-HARNESS-06`），
   因此必须是一条正常的数据通路，而不是控制流中断。
4. **路径与子进程各只有一条路子**：工具内每次文件访问经 `foundation.paths.resolve_within`，
   每次命令执行经 `foundation.proc`（R4）。故 `ExecutionContext` **不**提供 `subprocess` 或
   `Path.open` 之类的"后门"，只提供**白名单根**与**已校验的工作目录**。

---

## 2. 类型定义

### 2.1 `ToolCallRequest`（已有，**不变**）

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `call_id` | `str` | 本次调用标识（审计与回喂的关联键） |
| `name` | `str` | 工具名 |
| `arguments_json` | `str` | **原始 JSON 文本：不可信、尚未解析** |

**这条"尚未解析"是契约的一部分**，不是实现细节：它把信任边界**钉在 HARNESS 侧**，
使"在工具内部偷偷 `json.loads` 后直接用"变成一处**可见的违约**。

### 2.2 `ToolResult`（已有，**不变**）

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `ok` | `bool` | `False` = 工具级失败（可回喂，`REQ-HARNESS-06`） |
| `content` | `str` | 供回喂的观察内容（**不可信**，按数据装配） |
| `error` | `str \| None` | 失败时的可读说明（面向模型，非面向用户） |
| `truncated` | `bool` | `content` 是否被输出上限截断（安全基线要求限制输出体积） |
| `audit_id` | `str \| None` | 关联的 `AuditEvent.event_id`（供回放） |

### 2.3 `ToolSpec`（Q8；**由 Protocol 占位改为 `frozen` dataclass**）

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `name` | `str` | 工具名（**唯一**；注册表键） |
| `description` | `str` | 面向模型的自然语言描述（**将进入上下文**） |
| `parameters_schema` | `Mapping[str, object]` | 参数的 **JSON Schema**（`D2` 由 pydantic 模型生成，ADR-0015 §5.2.2） |
| `capabilities` | `frozenset[Capability]` | 该工具**声明的权限**（`REQ-TOOL-01`：每个工具含权限声明） |
| `source` | `str` | 来源标识：`"builtin"` 或 `"mcp:<server_id>"`（`REQ-TOOL-03` 防投毒） |
| `description_digest` | `str \| None` | 描述摘要；用于检测描述被篡改（rug-pull，`REQ-TOOL-03`） |

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters_schema: Mapping[str, object]
    capabilities: frozenset[Capability]
    source: str = "builtin"
    description_digest: str | None = None
```

**为什么是 `dataclass` 而不是 `Protocol`**：`ToolSpec` 是**数据**（描述一个工具），不是行为。
契约层的 Protocol 用于"可被替换的实现"；描述用不可变数据，才能安全地序列化给模型、
存进审计、做摘要比对。

**`description_digest` 的初始口径（U2；可用，但非终稿）**：

- 覆盖范围：`name` + `description` + `parameters_schema`；
- 规范化：`json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))`，
  UTF-8 编码后取 **`hashlib.sha256(...).hexdigest()`**；
- 用途：`ToolRegistry` 在**首次**见到某 `source` 的工具时记录该摘要，之后每次注册比对，
  不一致即**告警并默认拒绝使用**（fail-secure）；`builtin` 工具可为 `None`（代码即来源）。

> `builtin` 为何允许 `None`：内置工具的来源就是本仓库代码，摘要校验的收益低于维护成本。
> 摘要的价值在**外部来源**（MCP，`REQ-TOOL-02`，Should）。此为**初始口径**，MCP 接入时若发现
> 规范化不足以覆盖差异（如参数顺序、空白），在新的 ADR 中修订。

### 2.4 `ExecutionContext`（Q8；**由 Protocol 占位改为 `frozen` dataclass**）

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `session_id` | `str` | 会话标识（审计关联） |
| `call_id` | `str` | 本次工具调用标识（与 `ToolCallRequest.call_id` 一致） |
| `working_dir` | `Path` | **已校验**的一次性工作目录（可写；调用方负责创建与清理） |
| `allowed_roots` | `tuple[Path, ...]` | 文件访问的**白名单根**；工具内每次访问都必须经 `foundation.paths.resolve_within` |
| `timeout_s` | `float` | 本次调用的超时上限（命令执行与网络均须遵守） |
| `network_allowed` | `bool` | **默认 `False`**；出站仅在策略显式授予时为 `True`（`REQ-SEC-07`） |

```python
@dataclass(frozen=True)
class ExecutionContext:
    session_id: str
    call_id: str
    working_dir: Path
    allowed_roots: tuple[Path, ...]
    timeout_s: float
    network_allowed: bool = False
```

**不变式**：

- `working_dir` 必须**落在** `allowed_roots` 之内（由生产者保证；消费者可断言）；
- `network_allowed` 默认 `False`（C6 / default-deny）；
- **不含任何凭据**，且**不暴露** `os.environ`——命令执行经 `foundation.proc`，
  后者自建最小环境，`SECURITY.md` 要求凭据不进子进程。

- **谁产生**：HARNESS（在 `PolicyDecision` 允许之后构造）。
- **谁消费**：`Tool` 实现（`tools/files.py` / `shell.py` / `search.py`）。

### 2.5 `Tool`（已有，**不变**）

```python
class Tool(Protocol):
    @property
    def spec(self) -> ToolSpec: ...

    def invoke(self, args: Mapping[str, object], *, ctx: ExecutionContext) -> ToolResult: ...

    # Q3 决定：注册表接口也入契约，见 §2.6
```

### 2.6 `ToolRegistry`（Q3，**新增**）

```python
class ToolRegistry(Protocol):
    def specs(self) -> Sequence[ToolSpec]: ...

    def resolve(self, name: str) -> Tool | None: ...
```

**Q3 的结论：是，`ToolRegistry` 也放 `contracts/`。** 理由同 [`policy.md` §2.5](policy.md#25-policyengineq3)：
它是被 `harness/` 消费的跨层行为接口，与 `Tool` / `ModelClient` 同类；实现仍在
`tools/registry.py`（ADR-0015 §5.4.1 不变）。

**语义（两处与 ADR-0015 §5.1.2 的写法有**细化**，在此声明）**：

| 项 | 规定 |
| --- | --- |
| `specs()` | 返回当前**裁剪后**、可暴露给模型的工具描述（`REQ-HARNESS-03`）。返回值是 `ToolSpec` 而不是 `Tool`，因为上层只应看到"描述"，不应拿到可执行的句柄 |
| `resolve(name)` | ADR 原文写作 `-> Tool`；本契约细化为 **`-> Tool \| None`**：**未知工具返回 `None`**，由调用方**默认拒绝 + 审计**，**不抛异常** |
| 并发 | 只读视图；注册发生在启动阶段，会话期间不变（无锁假设） |
| 资源 | 无生命周期（无 `close`） |

> **为什么 `resolve` 返回 `Optional` 而不是抛 `KeyError`**：`REQ-SEC-01` 要求"未授权操作
> 拦截率 100%"。模型**幻觉出一个不存在的工具**是常态（弱模型尤甚），这属于**预期的不可信输入**，
> 用返回值表达可让"未知工具 ⇒ 拒绝 + 审计"成为一条**显式、可测试**的路径；
> 用异常表达则会被 `except` 笼统吞掉，且与"工具级失败用返回值"（`ToolResult.ok`）不一致。

---

## 3. 对 `contracts/tools.py` 的改动清单

1. `ToolSpec`：由空 Protocol 改为 `frozen dataclass`，补 6 个字段；
2. `ExecutionContext`：由空 Protocol 改为 `frozen dataclass`，补 6 个字段；
3. 新增 `ToolRegistry` Protocol（`specs` / `resolve`）；
4. import 补 `Path`（`pathlib`，标准库）、`Capability`（`contracts/policy.py`）；**不得** import `AuditSink`（未使用 ⇒ `ruff F401`）；
5. `ToolCallRequest` / `ToolResult` / `Tool` **保持不变**；
6. 模块 docstring 删除"形状待澄清"表述，改为指向本文件。
