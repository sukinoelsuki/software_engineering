# 契约：`contracts/model.py`

- 对应模块：`src/agent_sec_perf/model/`（L2 能力层，本地 `llama-server` 与云端 OpenAI 兼容端点统一抽象）
- 上游决策：ADR-0015 §5.1.2（`HARNESS ↔ CAPABILITY` 行）、§5.3 自研模块 1、ADR-0011（档位）
- 依赖：`contracts/tools.py`（`ToolSpec`）——契约层内部引用，允许（R3）

本文件回答 Q1（`ChatMessage` / `ModelResponse` / `CapabilityTier`）与 Q2（`chat` 参数）。

---

## 1. 设计要点（先读这一节）

1. **本地与云端共用同一形状**（`REQ-MODEL-03`）：路由（`model/router.py`）对上层透明，
   上层代码无差别调用两类后端。
2. **消息是"数据"，不是"指令"**：`ChatMessage` **不带**信任标记；信任由**装配层按 `role` 判定**
   （见 §2.1 的信任规则）。这是 `REQ-SEC-03`（指令-数据分离）在契约层的落点。
3. **工具暴露是显式授权**：`chat(tools=None)` 表示**不暴露任何工具**，属最保守默认（C6）。
4. **两个失败模式各有一类异常**，且**不合并**（见 §3）。

---

## 2. 类型定义

### 2.1 `Role` / `ChatMessage`（**新增枚举 `Role`**）

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `role` | `Role` | 消息来源；决定信任与装配位置 |
| `content` | `str \| None` | 文本内容；纯工具调用的助手消息为 `None` |
| `tool_calls` | `tuple[ToolCallRequest, ...]` | **仅** `role == ASSISTANT` 可非空；模型请求的工具调用 |
| `tool_call_id` | `str \| None` | **仅** `role == TOOL` 必填；回指它所响应的 `ToolCallRequest.call_id` |

```python
class Role(StrEnum):
    SYSTEM = "system"  # 我方生成的系统指令（可信）
    USER = "user"  # 用户输入（不可信）
    ASSISTANT = "assistant"  # 模型输出（不可信）
    TOOL = "tool"  # 工具结果回喂（不可信）


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str | None = None
    tool_calls: tuple[ToolCallRequest, ...] = ()
    tool_call_id: str | None = None
```

**不变式（实现必须断言 / 测试必须覆盖）**：

- `role in {SYSTEM, USER}` ⇒ `tool_calls == ()` 且 `tool_call_id is None`；
- `role == TOOL` ⇒ `tool_call_id` 非 `None`，否则回指断裂、审计无法回放；
- `role == ASSISTANT` ⇒ `content` 与 `tool_calls` **至少一个非空**。

**信任规则（`REQ-SEC-03`，必须写进装配层）**：

| 来源 | 信任 | 装配要求 |
| --- | --- | --- |
| `SYSTEM` | **可信**（仅由我方模板生成） | 不得由模型/用户/外部内容产生 |
| `USER` / `TOOL` / `ASSISTANT` | **一律不可信** | 只能作为**数据**进入上下文；不得被解释为指令，不得影响工具选择、权限判定与控制流 |

> **生产者 / 消费者**：`ChatMessage` 由 HARNESS 的上下文引擎装配（`harness/`），
> 消费方是 `ModelClient` 实现。**信任判定发生在装配处**，不在本类型上——因此本类型刻意
> **不设** `trusted: bool` 字段（避免与 `role` 语义重复、产生两处真相）。

### 2.2 `TokenUsage`（**新增**）

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `prompt_tokens` | `int` | 输入侧 token 数 |
| `completion_tokens` | `int` | 生成侧 token 数 |
| `total_tokens` | `int` | 合计 |

```python
@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
```

- **为什么存在**：`REQ-PERF-02` 要求"同等成功率下 token 消耗下降 ≥ 30%"，上下文引擎需要
  **服务端 `usage` 回执**作为唯一可信口径（ADR-0015 §5.3 模块 4）。
- **谁产生**：`ModelClient`（本地 `llama-server` 与云端同协议，均返回 `usage`）。
- **谁消费**：`harness/context/`（token 预算与压缩决策）、审计事件（`detail`）。

### 2.3 `CapabilityTier`（**成员【待定】**，Q1）

```python
class CapabilityTier(StrEnum):
    """**模型能力**档位。与**硬件档位** S/M/L 是两条正交的轴（ADR-0010 §5.2），
    二者不可互相推导、**不得混用**。

    【待定】档数与成员名尚未决策 —— 权威来源 SRS `Q-3`、验收见 `REQ-MODEL-06`。
    卡点：① 档数（3 还是 4）；② 能力判定口径（探针任务集与阈值）。
    以下成员为**占位**，供实现者写代码；名称可在 `REQ-MODEL-06` 落地时经 ADR 变更。
    """

    BASIC = "basic"
    STANDARD = "standard"
    ADVANCED = "advanced"
```

- **谁产生**：`model/probe.py`（能力探测，`REQ-MODEL-06`）。
- **谁消费**：`harness/trimming.py`（工具裁剪，`REQ-HARNESS-03`）、`harness/prompts.py`（提示分级，`REQ-HARNESS-04`）。
- **【待定】U1**：成员与档数见 [README §6](README.md#6-未决项本目录已明确标注不在此臆断)。
  **在决策前，任何代码不得把 `CapabilityTier` 与 `S/M/L` 硬件档位相互转换。**

### 2.4 `FinishReason`（**新增**）

```python
class FinishReason(StrEnum):
    STOP = "stop"  # 正常结束
    LENGTH = "length"  # 触达 max_tokens / 上下文上限（**截断**）
    TOOL_CALLS = "tool_calls"  # 请求调用工具
    CONTENT_FILTER = "content_filter"  # 内容被上游过滤
    UNKNOWN = "unknown"  # 缺失或无法识别
```

- **为什么存在**：`REQ-HARNESS-02/06` 的错误分级需要区分"截断"（`LENGTH` ⇒ 增预算 / 回喂）
  与"正常结束"（`STOP`）。
- **fail-safe 取向**：无法识别时落 `UNKNOWN`，**不得**默认成 `STOP`（否则截断会被当成成功）。

### 2.5 `ModelResponse`（Q1）

| 字段 | 类型 | 语义 |
| --- | --- | --- |
| `content` | `str \| None` | 文本回复；纯工具调用时为 `None` |
| `tool_calls` | `tuple[ToolCallRequest, ...]` | 模型请求的工具调用（**参数仍是原始 JSON 文本，未解析**） |
| `finish_reason` | `FinishReason` | 生成终止原因 |
| `usage` | `TokenUsage` | 本次调用的 token 计量 |
| `model_id` | `str` | **实际应答**的后端标识（本地 GGUF 路径或云端模型名） |

```python
@dataclass(frozen=True)
class ModelResponse:
    content: str | None
    tool_calls: tuple[ToolCallRequest, ...]
    finish_reason: FinishReason
    usage: TokenUsage
    model_id: str
```

**不变式**：

- `content` 与 `tool_calls` **至少一个非空**，否则视为响应不符合协议 ⇒ 抛 `ModelProtocolError`；
- `finish_reason == TOOL_CALLS` ⇒ `tool_calls` 非空；
- `tool_calls` 里的 `arguments_json` **是原始 JSON 文本、未经解析**（与 `ToolCallRequest` 一致）；
  解析与校验**只能**发生在 HARNESS 侧的信任边界（ADR-0015 §5.1.2 的关键约定）。

- **`model_id` 为什么需要**：`REQ-MODEL-05`（路由与降级）要求记录"这次用的是哪个后端"，
  供审计与"降级是否真的发生"的验证。

---

## 3. 异常归属（Q4）

| 异常 | 位置 | 触发条件 | 调用方处置（ADR-0015 §5.1.2） |
| --- | --- | --- | --- |
| `ModelUnavailableError` | **`foundation/errors.py`** | 后端不可达 / 未就绪 / 已达重试上限 | **路由降级**到另一后端（`REQ-MODEL-05`） |
| `ModelProtocolError` | **`foundation/errors.py`** | 响应不符合本契约（缺 `choices`、`content` 与 `tool_calls` 皆空等） | **重试一次**，仍失败则回喂给模型（`REQ-HARNESS-02`） |

**决定：放在 `foundation/errors.py`，不放在 `contracts/`。** 理由：

1. **单一异常层次**：`foundation/errors.py` 的 docstring 已声明它是"全项目共享的异常层次"，
   基类 `BenchError`（名称沿用历史，ADR-0015 §5.4.1 已登记）。异常若分裂成两套基类，
   `except BenchError` 会漏接，错误处理会出现"该兜的没兜住"。
2. **`contracts/` 不得依赖本项目其它模块**（R3）：若异常放 `contracts/`，它们无法继承
   `BenchError`（那需要 import `foundation`），只能裸继承 `Exception` ⇒ 正好造成上述分裂。
3. **契约不需要引用它们**：`Protocol` 的方法签名不声明 `raise`，因此异常放 `foundation/`
   不影响契约层的零依赖（R3/V1）。

**两条必须写明的边界**：

- 两者**都是** `BenchError` 的直接子类，**不**继承已有的 `ProtocolError`
  （后者语义是"基准参数/协议不可比"，处置是**中止**；模型协议错误处置是**重试 + 回喂**，
  合并会让两种不同处置被同一个 `except` 捕获，属错误处理缺陷）。
- **不新增** `CapabilityDeniedError`：策略拒绝经 `PolicyDecision` **返回值**表达，
  不抛异常（fail-secure 的可判定路径，见 [`policy.md`](policy.md)）。

---

## 4. `ModelClient`（Q2）

```python
class ModelClient(Protocol):
    def chat(
        self,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSpec] | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        timeout_s: float = 60.0,
    ) -> ModelResponse: ...

    def close(self) -> None: ...
```

ADR-0015 §5.1.2 写作 `chat(messages, tools, ...)`，其中 `...` 的**其余参数**与 `tools` 的
可选性即上表。逐项理由：

| 参数 | 类型 | 默认 | 为什么 |
| --- | --- | --- | --- |
| `messages` | `Sequence[ChatMessage]` | 必填（位置） | 唯一必填项 |
| `tools` | `Sequence[ToolSpec] \| None` | **`None`** | **可选**。`None` = **不暴露任何工具**（纯对话 / 能力探测 `REQ-MODEL-06`）。默认不暴露 = default-deny（C6） |
| `temperature` | `float` | `0.0` | 与 `bench/runner.py` 的固定温度一致；保证可复现（`REQ-PERF-08` 的测量纪律） |
| `max_tokens` | `int \| None` | `None` | `None` = 用服务端默认；HARNESS 的 token 预算显式传入 |
| `timeout_s` | `float` | `60.0` | 安全基线要求**出站请求必须设超时**；实现**不得**把 `None` 当作"无限等待"。默认值可经 `foundation/config.py` 覆盖 |

**并发假设**：**非线程安全**；一个会话一个实例（`llama-server` 默认 `-np 1`）——沿用 ADR-0015 §5.1.2。
**资源生命周期**：`close()` **幂等**；进程型后端由 `ExitStack` 托管（本地 `llama-server` 经 `foundation.proc.spawn`）。
**错误语义**：见 §3；`chat` 内**不得**吞掉异常后返回空响应。

---

## 5. 对 `contracts/model.py` 的改动清单

1. 新增 `Role` / `FinishReason` / `TokenUsage` 三个 `(StrEnum)` / `frozen dataclass`；
2. `ChatMessage` 补 `role` / `content` / `tool_calls` / `tool_call_id`；
3. `ModelResponse` 补 5 个字段；
4. `CapabilityTier` 补 3 个**占位**成员并注明【待定】（U1）；
5. `chat` 补 `tools`（可选）/ `temperature` / `max_tokens` / `timeout_s` 四个 keyword-only 参数
   （`messages` 保持位置参数）；
6. `close` 不变。
