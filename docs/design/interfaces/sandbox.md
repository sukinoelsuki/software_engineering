# 契约：`contracts/sandbox.py`

- 对应模块：`src/agent_sec_perf/security/sandbox/`（实现）；执行经 `foundation/proc.py`
- 上游决策：ADR-0006（分层沙箱 + 主动探针 + fail-secure 降级）、
  **ADR-0007（按"机制类别"划分隔离能力，**逐维度**探针）**、ADR-0015 §5.1.2/§5.4.1、
  SRS `REQ-SEC-05/09`
- 依赖：无（仅标准库）

本文件回答 Q7（`SandboxRequest` / `SandboxResult` / `IsolationMatrix` 字段）。

---

## 1. 设计要点（先读这一节）

1. **隔离能力是"逐维度"的，不是单一布尔**（ADR-0007 §4.2）：`IsolationMatrix`
   返回**每个维度**是否生效、由**哪种机制**提供、**证据**是什么。这样才能表达
   "文件系统隔离由容器提供、资源限制由 rlimit 提供"这种真实的**组合**。
2. **`enforced=True` 只能来自"负向探针确实被阻止"**（ADR-0006 §5.2 规则 S-1）：
   **禁止**以"命令退出码为 0"推断隔离生效——`firejail` 的 fail-open
   （退出码 0、网络未阻断）正是本规则要防的缺陷。
3. **降级必须显式记录**（ADR-0006 规则 S-2）：`degraded=True` ⇒ `reason` 必填，
   且要进审计（`AuditEvent(kind=EXECUTION_DEGRADATION)`）。
4. **`SandboxRequest` 只描述"要执行什么"**，不描述"用哪个档位"——档位由后端**按探针结果**
   选择（ADR-0007 §4.2），不由调用方指定。

---

## 2. 类型定义

### 2.1 `IsolationDimension` / `IsolationMechanism`（**新增**）

```python
class IsolationDimension(StrEnum):
    FILESYSTEM = "filesystem"
    NETWORK = "network"
    USER = "user"
    CAPABILITIES = "capabilities"
    CPU_TIME = "cpu_time"
    ADDRESS_SPACE = "address_space"
    FILE_SIZE = "file_size"
    PROCESS_COUNT = "process_count"


class IsolationMechanism(StrEnum):
    NONE = "none"  # 该维度未隔离
    SETRLIMIT = "setrlimit"  # 内核 per-process rlimit（ADR-0007 机制类别三）
    NAMESPACE = "namespace"  # 命名空间 / mount 类（ADR-0007 机制类别一）
    CGROUP = "cgroup"  # cgroup v2 配额（ADR-0007 机制类别二）
```

**维度取自 ADR-0007 §3.1 的探针矩阵**（不是新造的）：

| 维度 | 对应 ADR-0007 §3.1 的探针 | 当前环境的预期真相 |
| --- | --- | --- |
| `FILESYSTEM` | `--read-only` 后写文件 | L2-C 由容器提供；L1 由**路径白名单**兜底（非内核隔离） |
| `NETWORK` | `--network=none` 后出站 | L2-C 由容器提供；L1 = 默认拒绝 + 审计（非内核隔离） |
| `USER` | `--user 65534:65534` | 由容器或 `proc.run(isolation="user")` 提供 |
| `CAPABILITIES` | `--cap-drop=ALL` | 仅容器后端提供 |
| `CPU_TIME` | `RLIMIT_CPU` | **setrlimit**（可靠） |
| `ADDRESS_SPACE` | `RLIMIT_AS` | **setrlimit**（可靠） |
| `FILE_SIZE` | `RLIMIT_FSIZE` | **setrlimit**（可靠） |
| `PROCESS_COUNT` | `RLIMIT_NPROC` | **setrlimit**（可靠）；`--pids-limit` 静默失效 |

> `CGROUP` 保留在枚举里但**当前环境预期为"静默失效"**：ADR-0007 §3.1 实测
> `--memory` / `--pids-limit` 退出码正常却不生效。保留该成员是为了让探针能**如实记录**
> "尝试过 cgroup 但未生效"，而不是把失败从数据里抹掉。

### 2.2 `IsolationDimensionStatus`（**新增**）

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `dimension` | `IsolationDimension` | 维度 |
| `enforced` | `bool` | **探针确认**该维度确实被阻止（负向断言通过） |
| `mechanism` | `IsolationMechanism` | 由哪种机制提供 |
| `evidence` | `str` | 可复现的探针命令 / 输出摘要（ADR-0007："结论必须可用探针复现"） |

```python
@dataclass(frozen=True)
class IsolationDimensionStatus:
    dimension: IsolationDimension
    enforced: bool
    mechanism: IsolationMechanism
    evidence: str
```

**语义约束**：

- `enforced=True` ⇒ 必须存在一条**负向探针**（越界操作**被拒绝**）作为依据；
  单纯"正向命令成功"不构成证据（ADR-0006 §4.1 的关键教训）。
- `mechanism == NONE` ⇒ `enforced` 必须为 `False`（二者是同一事实的两个字段，不得矛盾）。
- `evidence` 为空字符串**视为未验证** ⇒ 该维度按 `enforced=False` 处理（fail-secure）。

### 2.3 `SandboxTier`（**新增**）

```python
class SandboxTier(StrEnum):
    L0 = "l0"  # 拒绝执行（所有探针未通过）
    L1 = "l1"  # RestrictedProcessBackend：始终可用（ADR-0006 §5.3）
    L2_NAMESPACE = "l2-namespace"  # NamespaceBackend：bwrap / firejail（ADR-0007 §4.2 的 L2-N）
    L2_CONTAINER = "l2-container"  # ContainerBackend：docker（ADR-0007 §4.2 的 L2-C）
```

> **档位名与 ADR-0007 §4.2 一一对应**，沿用该文已用的 `L2-N` / `L2-C` 语义
> （枚举值用连字符表示）。
> **产品不得依赖 `L2_CONTAINER`**：目标设备（手机 / Termux）无 docker，
> 产品必须能在只有 `L1` 时运行（ADR-0007 §4.3）。

### 2.4 `IsolationMatrix`（Q1）

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `tier` | `SandboxTier` | 本次执行实际达到的档位 |
| `dimensions` | `Mapping[IsolationDimension, IsolationDimensionStatus]` | **逐维度**状态 |
| `degraded` | `bool` | 是否发生过降级 |
| `reason` | `str \| None` | 降级原因；`degraded=True` 时**必填**（ADR-0006 规则 S-2） |

```python
@dataclass(frozen=True)
class IsolationMatrix:
    tier: SandboxTier
    dimensions: Mapping[IsolationDimension, IsolationDimensionStatus]
    degraded: bool = False
    reason: str | None = None
```

**不变式**：

- `dimensions` **未列出**的维度一律按 `enforced=False, mechanism=NONE` 处理
  （default-deny：没测过就不当作已隔离）；
- `tier == L0` ⇒ 调用方**必须拒绝执行**（不是"降级执行"）；
- `degraded == True` ⇒ `reason` 非 `None`；且**必须**产出一条
  `AuditEvent(kind=EXECUTION_DEGRADATION, detail={"reason": ..., "tier": ...})`
  （ADR-0006 禁止静默降级）；
- `tier in {L1, L2_NAMESPACE, L2_CONTAINER}` 时 `dimensions` **不得为空**
  （空矩阵无法支撑任何安全断言）。

- **谁产生**：沙箱后端的**能力探测**（启动时）+ 每次执行的实际达成情况。
- **谁消费**：`security/refusal.py`（据未满足维度决定是否拒答）、
  `cli/`（用户可见提示）、审计（降级事件）、`tests/security/`（对抗性断言）。

### 2.5 `SandboxRequest`（Q1）

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `argv` | `tuple[str, ...]` | 命令与参数；`argv[0]` **必须**是绝对路径（`foundation.proc.resolve_binary`） |
| `cwd` | `Path` | 工作目录；**必须落在** `allowed_roots` 内 |
| `allowed_roots` | `tuple[Path, ...]` | 路径白名单根（经 `foundation.paths` 校验） |
| `timeout_s` | `float` | 超时上限（超时即失败，不重试：重试会掩盖问题） |
| `env` | `Mapping[str, str]` | 显式环境变量；**默认空 = 用后端的最小环境**，**不得继承** `os.environ` |
| `network_allowed` | `bool` | **默认 `False`**；出站仅在策略显式授予时为 `True` |

```python
@dataclass(frozen=True)
class SandboxRequest:
    argv: tuple[str, ...]
    cwd: Path
    allowed_roots: tuple[Path, ...]
    timeout_s: float
    env: Mapping[str, str] = field(default_factory=dict)
    network_allowed: bool = False
```

**安全语义**：

- **不经 shell**：`argv` 是列表，后端**不得**拼接成 shell 字符串（`SECURITY.md` 硬性规则；
  全仓无 `shell=True` 由机器检查强制）。
- `env` 默认空并**禁止继承**父进程环境：父进程含 `CNB_TOKEN` 等敏感变量，
  执行模型产物时**绝不能**传入（`REQ-PERF-08`："执行模型产物时不得继承父进程凭据"；
  `foundation/proc.py` 的 `_isolated_env` 已是此语义）。
- **调用方负责**一次性工作目录的创建与清理（ADR-0015 §5.1.2 资源生命周期）。

### 2.6 `SandboxResult`（Q1）

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `returncode` | `int` | 退出码（**不**作为隔离是否生效的判据） |
| `stdout` / `stderr` | `str` | 输出（用于判定与回喂） |
| `timed_out` | `bool` | 是否超时 |
| `truncated` | `bool` | 输出是否被上限截断（安全基线要求限制输出体积） |
| `duration_s` | `float` | 耗时（性能留痕：`REQ-PERF-05/06` 的"探测值 → 决策 → 配置"记录可用） |
| `isolation` | `IsolationMatrix` | **本次执行实际达到**的隔离矩阵 |

```python
@dataclass(frozen=True)
class SandboxResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool
    duration_s: float
    isolation: IsolationMatrix
```

**为什么把 `IsolationMatrix` 放进每个结果（而不是只在启动时探一次）**：
ADR-0007 §4.2 明确"档位选择是**逐维度**的"，且不同后端 / 参数组合下达成维度不同。
把矩阵随结果返回，使**每一条执行记录都自带"当时到底隔离了什么"**——这正是
`REQ-SEC-05` 验收（"隔离档位可探测"）与 ADR-0006 规则 S-2（禁止静默降级）的共同载体。

---

## 3. 与 `foundation/proc.py` 的关系

`SandboxRequest` 是**抽象层**的类型；其落到 `foundation/proc` 的映射关系：

| `SandboxRequest` 字段 | `foundation.proc` 对应 |
| --- | --- |
| `argv` / `cwd` / `timeout_s` | `proc.run(argv, cwd=..., timeout_s=...)` 的同名参数 |
| `env` | 隔离执行时**忽略**并改用 `_isolated_env()`；仅 `isolation="root"` 才可能继承 |
| `network_allowed=False` | **无进程级机制**（L1）；由策略层拒绝 + 审计，故 `NETWORK` 维度在 L1 下 `enforced=False` |

> **必须写明的诚实结论**：在只有 `L1` 的环境里，`FILESYSTEM` / `NETWORK` 两个维度
> **不是内核级隔离**，而是"白名单拦截 + 审计 + 默认拒绝 + 人工确认"（ADR-0006 §6 的负面后果）。
> 因此 `IsolationMatrix` 对这两个维度在 L1 下的 `enforced` 取值必须**如实为 `False`**，
> 并在文档 / 输出中禁止写"已隔离"（ADR-0006 §6 风险与缓解）。
> **这一条不得为了"好看"而取 `True`**——那正是本项目已捕获过一次的 fail-open 缺陷。

---

## 4. 对 `contracts/sandbox.py` 的改动清单

1. 新增 `IsolationDimension` / `IsolationMechanism` / `IsolationDimensionStatus` / `SandboxTier`
   四个类型（前两个与 `SandboxTier` 为 `(StrEnum)`，`IsolationDimensionStatus` 为 `frozen dataclass`）；
2. `SandboxRequest` 补 6 个字段；
3. `SandboxResult` 补 7 个字段；
4. `IsolationMatrix` 补 4 个字段（**逐维度**结构）；
5. import 补 `Path`（`pathlib`）、`field`（`dataclasses`）、`Mapping`（`collections.abc`）；
6. 模块 docstring 删除"字段均未规定 ⇒ 占位"，改为指向本文件并重申
   "`enforced` 只能来自负向探针"与"禁止静默降级"。
