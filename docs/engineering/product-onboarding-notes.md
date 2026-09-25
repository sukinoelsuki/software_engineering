# 产品带读笔记（会话交接材料）

> ⚠️ **本文件的定位**：它是**一次"带读产品"会话的过程记录与交接材料**，不是规范、也不是第二份真源。
> 它存在的原因只有一个：**云环境每次重启即清空上下文**，已讲过的内容与"从哪继续"必须固化在仓库里。

- **面向**：项目所有者（人类维护者），以及任何需要从中断处续上的会话。
- **产生时间**：2026-09-25 ~ 2026-09-26（跨一次容器强制关闭，见 §6）。
- **权威源指针（本文件不重复任何决策）**：

  | 内容 | 真源 |
  | --- | --- |
  | 命令与实测输出 | [`product-handbook.md`](product-handbook.md) |
  | 执行顺序 · 每步看什么 · 设计取舍与代价 | [`product-walkthrough.md`](product-walkthrough.md) |
  | 分层 / 依赖方向 `R1`~`R5` / 实现状态表 | [`../design/architecture.md`](../design/architecture.md) |
  | 字段级契约 | [`../design/interfaces/README.md`](../design/interfaces/README.md) |
  | 威胁状态 | [`../design/threat-model/README.md`](../design/threat-model/README.md) §4.1 |
  | 代码现状 | `src/agent_sec_perf/`（**唯一事实来源**） |

- ⚠️ **行号会漂移**：本文的 `文件:行号` 只是定位辅助，**以符号名（函数 / 类 / 常量）与路径为准**
  （与 `architecture.md` §4「依据列」同一取向：行号不进表，符号名与路径会）。
- **放这里的原因**：`docs/engineering/` 已放同类的非规范文档（`product-handbook.md`、
  `product-walkthrough.md`、`post-build-checklist.md`）。按"**新目录必须先有 ADR**" ⇒ **不新建目录**。
- **本文件不重抄命令**：命令真源是 `product-handbook.md`。这与 `product-walkthrough.md` §0 的
  分工是同一条理由——本项目的重复失效模式是"**同一事实两处表述 ⇒ 必然漂移**"。
  本文只写"**指向哪一节 + 看什么 + 为什么**"。

---

## 1. 一句话与两条主线

> **一个"能力受限的本地模型 + 一套安全治理运行时"的 CLI**：模型（Qwen3-4B，CPU-only）自己决定调工具，
> 但**每一次工具调用都要过一道 default-deny 的能力闸门，并且全程留下可回放的审计**。

产品名 `agent-sec-perf`（配置目录前缀 `lowspec`），入口只有一条命令：`agent-sec-perf run`。

**它不是"一个更强的 agent 框架"**。两条主线：

| 主线 | 回答什么 | 落点 |
| --- | --- | --- |
| **能力探索** | 4B/8B 模型在受限环境里**能做什么** | `harness/`（工具裁剪 / 提示分级 / 上下文预算）+ `model/probe`（**未开工**） |
| **综合治理** | 怎么用系统手段把它**收在授权边界内** | `security/`（default-deny + 风险分级）+ `observability/audit` + `foundation/proc`（最小环境子进程） |

### 1.1 三条读图要点（决定了后面所有代码为什么长这样）

分层（`architecture.md` §1.1）：

```text
  L4 cli/           app · render · approval
  L3 harness/       session · loop · prompts · trimming · checkpoint
                    errors · context/ · domain_pack · arguments
  L2 model/ client(仅本地回环)      tools/ registry · files · shell
  L1 foundation/    errors · paths · proc · config · logging

  横切：security/{capabilities,policy} · observability/audit
  零行为：contracts/（类型 + Protocol，谁都能依赖、它不依赖任何人）
  独立：bench/（评测子系统，唯一接缝 = foundation/）
```

1. **`contracts/` 是零行为层**：只有 `dataclass` / `StrEnum` / `Protocol`，**没有一行 `__post_init__`**。
   ⇒ 所有"校验 / fail-secure / 不变式"都落在**实现层**（例如 `harness/session.py::_check_config`）。
2. **`security/` 与 `observability/` 是横切的**，不是流水线上的一段。它们必须在**每个特权操作点**被调用
   ⇒ `loop.py` / `policy.py` / `tools/files.py` 里到处是 `sink.emit(...)`（这是 `REQ-SEC-01/06` 的载体）。
3. **`foundation/proc.py` 是全项目唯一的子进程入口，`foundation/paths.py` 是唯一的路径校验入口**（`R4`），
   由 `tests/unit/test_architecture_layers.py` 机器检查钉住。

### 1.2 不得放大的三条

- **"已实现" ≠ "已跑通"**，**"能跑" ≠ "安全已到位"**：威胁模型 13 条当前 **已缓解并验证 1 / 部分缓解 10 / 未缓解 2**。
- **未开工清单**（`architecture.md` §0）：`security/sandbox/` · `security/refusal.py` ·
  `model/{router,probe,assets}.py` · `tools/search.py` · `observability/tracing.py`
  ⇒ 现在的产品**只有"授权与证据"，没有"隔离与强制执行"**。
- **单次墙钟不是性能数据**（196 s / 507 s 都是单次采样，不得作判据）。

---

## 2. 一次运行的三段生命周期

### 2.1 装配期（起模型之前就把非法配置拦住）

入口 `cli/app.py::execute()`；装配顺序由 `_assemble()` 承载，模块 docstring 写死：

```text
config → sink → tools实例 → pack → 能力收窄(∩) → policy → registry → model → Session
```

两个**必须记住**的点：

- **(a) 能力收窄必须早于 `PolicyEngine` 构造**：引擎是 `frozen dataclass`，构造后 `granted` 是只读视图，
  之后再收窄**不会生效** ⇒ `narrow_granted(...)` 必须写在 `PolicyEngine(granted=..., ...)` 之前。
- **(b) `load_pack` 需要 `known_tools`，而工具名只能从工具实例取** ⇒ 代码里先建 4 个工具实例
  （**不是** `ToolRegistry`），registry 本身仍排在 policy 之后。这是**如实登记的**实现说明，不是偷偷偏离。

装配期失败 ⇒ `logger.error(...)` + **返回退出码 3**，`BenchError` 家族（`foundation/errors.py`）。
领域包越界时是**秒级返回**，模型进程根本没起。

### 2.2 任务循环（`harness/loop.py` 是全产品的心脏）

`Session.run(task)` 只是**透传**，循环在 `TaskLoop.run()`：

```text
while True:
  ├─ steps >= max_steps         → TASK_FINISHED(LIMIT_REACHED)   退出码 2
  ├─ _model_step()（瞬时故障原地重试；不可重试 → ERROR + FAILED）  退出码 1
  ├─ 产出 MODEL_RESPONSE，assistant 消息压进 history
  ├─ 无 tool_calls              → TASK_FINISHED(COMPLETED)       退出码 0
  ├─ 对每个 tool_call 串行走【决策序列】（§2.3）
  │    └─ 连续失败达上限 → ERROR(STALLED) + FAILED               退出码 1
  └─ 一轮结束才快照 history（避免中途留下悬空 tool_calls）
```

三个易错点：

- **`max_steps` 计的是"模型往返"；瞬时故障的重试不新开一步** ⇒ 模型调用总数 ≤ `max_steps × (1 + MAX_TRANSIENT_RETRIES)`。
- **history 只在"整条响应处理完"之后写回**，中途终止时让历史停在自洽状态。
- **`_event()` 是 `seq` / `timestamp` 的唯一来源**；所有产出点写成 `yield self._event(...)`，
  使"装配"与"产出"之间不留可抛异常的间隙（否则流里会出现**序号空洞**，`I9`）。

### 2.3 工具调用决策序列（**最该背下来的六步**）

`loop.py::_handle_tool_call()`，契约 §3.3 写死，`S1` 的唯一验收对象：

| 步 | 动作 | 失败/拒绝时 |
| --- | --- | --- |
| 0 | **无条件**产出 `TOOL_CALL` | —— "模型请求过"本身是事实，必须可见 |
| 1 | 解析域判定（`spec is None`?） | `not_exposed` / `unknown_tool` → 拒绝 |
| 2 | 严格校验 `arguments_json` | `invalid_arguments` → 拒绝；校验器抛非契约异常 → **终止** |
| 3 | `policy.decide()` 策略求值 | 产出 `POLICY_DECISION` 事件 + 审计 |
| 4 | 审批通路（`requires_confirmation`?） | gate 为 `None` ⇒ **一律拒绝且不产 `APPROVAL_RESULT`** |
| 5 | `tool.invoke(args, ctx)` | 唯一构造 `ExecutionContext` 的地方 |
| 6a | 已执行 → `TOOL_RESULT(ok=...)` | 失败是 `result.ok=False` |
| 6b | 未执行 → `TOOL_RESULT(result=None)` + 中文 text | **拒绝**，刻意**不构造** `ToolResult` |

**步 1 为什么 `not_exposed` 与 `unknown_tool` 必须分开**：

- 名字**在注册表里、不在本会话暴露集合** ⇒ `not_exposed`（**我们把它裁掉了**）
- **两边都没有** ⇒ `unknown_tool`（**模型幻觉**）

前提：`ToolRegistry.specs()` **必须返回全量注册集（未裁剪）**——这是 `T6` 裁决（2026-09-19），
`loop.py` 模块 docstring 写明"不得被静默改动"，否则 `not_exposed` 这一档永远不可达。

**步 4 的 fail-secure**：非交互模式**显式传 `None`**（`app.py::_build_approval`）⇒ 需确认的调用一律拒绝，
且**不产 `APPROVAL_RESULT` 事件**——因为"没有人被问过"，伪造一条"用户拒绝"会让审计撒谎（`I2`）。

---

## 3. 七把"钥匙"

### 钥匙 1：default-deny 是两条结构性约束，不是口号

`security/capabilities.py::CapabilitySet`：

1. **默认构造是空集**（`granted: frozenset[Capability] = field(default_factory=frozenset)`）⇒ 什么都没授予；
2. **`parse_capabilities()` 遇未知名字即拒绝，不跳过**——"跳过"会把 `write_files` 这类拼写错误
   变成一次**静默降权**（系统照常启动、你以为授予了、其实没有）。

### 钥匙 2：领域包只能收窄，绝不扩权

`narrow_granted(granted, allowlist)` = `granted & allowlist`（**交集**，不重建、不并集）。
⇒ **只在 `pack.toml` 声明能力、不去 `.lowspec.toml` 授予 ⇒ 该能力仍不可用**。这不是配置错误，
正是"包不能扩权"的体现。

### 钥匙 3：`decide()` 的四格 + 分支顺序（顺序本身是契约）

分支顺序 `1a` 空集 → `1b` 类型检查 → `1c` 常规求值：

| 情形 | `allow` / `requires_confirmation` | 语义 |
| --- | --- | --- |
| 已授权 + `LOW` | `True` / `False` | 自动放行 |
| 已授权 + `MEDIUM`/`HIGH` | `True` / `True` | 须人工确认 |
| 求值阶段抛异常 | `False` / `True` | 可升级拒绝 |
| `CRITICAL` / 未授予 / 空集 / 非法成员 | `False` / `False` | **硬拒绝** |

⚠️ **`1b` 是全项目最反直觉、也最重要的一段**：判据**必须是类型检查、不能是名字检查**。
因为 `StrEnum` 成员与其 `str` 值 `==`/`hash` 相等 ⇒
`frozenset({"read_file"}) - frozenset({Capability.READ_FILE})` 是**空集** ⇒
按名字判"缺失"会得出"无缺失 ⇒ 已授权" ⇒ **`allow=True`**。
**这是 2026-09-19 实测过的 default-deny 绕过**（`interfaces/policy.md` §2.5 的 `I4`）。

### 钥匙 4：两处**处置相反**的规定（最易实现错的地方）

- **策略求值失败 ⇒ 收敛为拒绝**（`_evaluate_safely` 捕获 `Exception`，**不**冒泡）；
- **审计写入失败 ⇒ 原样冒泡**（`decide()` 第 5 步的 `sink.emit` **不得**被 `try` 包住）。

两者表象都是"没执行"，但一个是**正常的默认拒绝**、一个是**系统坏了**。
混为一谈 = 把一次审计基础设施故障伪装成一次普通拒绝。

### 钥匙 5：不可信内容只能以"数据"形态出现，位置写死

`contracts/harness.py::SessionEvent` 的 docstring：不可信内容**只允许四处**
（`response.content` / `result.content` / `result.error` / `tool_name`）；
原始 `arguments_json` **不得**进事件、也不得进 `text`。

| 位置 | 能否承载不可信内容 |
| --- | --- |
| 事件的**独立字段** | ❌ |
| `text` / 审计 `detail` / 日志 | ❌ |
| `response` **内部** | ✅（它整体就是不可信数据） |
| 面向终端的**渲染** | ❌（不显示参数值） |

**代价（真实且要记住）**：`MODEL_RESPONSE` / `ERROR` / `TASK_FINISHED` **刻意不审计**
⇒ **无法从审计还原"模型说了什么"**，只能看 stdout 事件流里的 `response.tool_calls[].arguments_json`。

### 钥匙 6：审计是"证据面"，不是"日志"

`observability/audit.py::JsonlAuditSink`：

1. **只追加、行即事件**：崩溃最多影响正在写的那一行；
2. **`flush()` = `fsync`**（强持久化点），**幂等**；
3. **落点是白名单内的常量** `ALLOWED_AUDIT_ROOTS`，**配置不能改根**——`.lowspec.toml` 跟着仓库走
   ⇒ 按 `SECURITY.md` 口径是**不可信输入**；不加约束时 `audit.directory` 就是"任意路径追加写"原语；
4. **读侧不跳过坏行**（`read_all` 抛 `SchemaError`）——静默跳过 = 证据链出现无声缺口。

写入失败抛 **`AuditWriteError`**（不是裸 `OSError`）：后者与"调用方自己的 I/O 异常"在类型上不可分，
下游只能用 `except Exception` 一把抓 ⇒ 会把"证据面坏了"收敛成"任务失败"（威胁模型 §8.2 的 `P-3`）。

### 钥匙 7：`foundation/paths.py` 是唯一的路径校验入口

`resolve_within(candidate, roots, *, what)`：先 `expanduser().resolve()`（展开 `..` 与符号链接），
**再**判是否落在某个根内 ⇒ 目录穿越与 symlink 逃逸天然被拒。

工具侧是**两道 + 纵深防御**（`tools/files.py::ReadFileTool.invoke`）：
`resolve_tool_path`（白名单）→ `is_file()` 判定 → 且 `invoke` 里**又做一次**参数逐项校验
（即使 HARNESS 已校验过）。

---

## 4. 带读路线图（R1 已完成）

| 轮 | 主题 | 要读的代码 | 读完应能回答 |
| --- | --- | --- | --- |
| **R1** ✅ | 产品全景 + 主流程 + 七把钥匙（= 本文 §1~§3） | 已覆盖 | 一次运行怎么走、拒绝与失败怎么分 |
| **R2** | 装配期与配置 | `foundation/config.py` · `cli/app.py::execute/_assemble` · `_AuditRecorder` | 为什么配错是 `3`、审计失败是 `4` |
| **R3** | Harness 内在机制 | `harness/context/__init__.py` · `trimming.py` · `prompts.py` · `checkpoint.py` · `arguments.py` | 模型"看不到"什么、`--capability-tier` 裁了什么 |
| **R4** | 工具层与子进程 | `tools/registry.py` · `tools/shell.py` · `foundation/proc.py` | 命令执行怎么做到"不继承父环境" |
| **R5** | 模型层与端到端 | `model/client.py` · `bench/` | 为什么换模型必须重测超时与预算 |

所有者已明确：**R2 与 R3 都要学**（即使主线是 Harness，配套的技术基础不能缺）。

---

## 5. 跑测的两个阶段（交接时的执行要点）

纪律取自 `product-walkthrough.md` §0.2：**一次一个阶段 · 命令由所有者亲自敲 · 不符就停下 · 跑测不产仓库改动**。
（"一次一个阶段"针对的是 `3/4/5` 这三个长耗时段；`阶段 1` 与 `阶段 2` 均**秒级、不起模型**，故可合批。）

### 5.1 阶段 1：环境与门禁 —— 命令见 `product-handbook.md` §2.1 / §3 舞台 0

`make check` 的六步顺序**不是随手排的**（`Makefile` 的 `check` 目标）：

```text
hooks-check → format-check → lint → typecheck → test → security
```

- **`hooks-check` 排最前是刻意的**（该目标上方的注释）：本地防线缺失时该**立刻**失败，
  而不是等 100+ 用例跑完。首跑挂在这里 = 本地 git 钩子没装 ⇒ `make setup`。
- **两个"绿灯不等于全覆盖"**：
  - `test` 口径是 `-m "not benchmark and not slow"` ⇒ **通过数 ≠ 全仓用例数**；
  - bandit **只扫 `src/`** ⇒ 绿灯**不覆盖** `tests/` / `docs/` / `.cnb.yml`（这是 `T-06` 覆盖面不全的一部分）。

### 5.2 阶段 2：装配期拒绝 —— 命令见 `product-handbook.md` §3 舞台 1

预期：**退出码 `3`** · stderr 一条 JSON（`error_type` = `PathNotAllowedError`、`event` = `装配失败`）·
stdout **空**（装配期故障不进事件流）· **秒级**（模型没起）· 无 `llama-server`。

**逐跳调用链**（按这个顺序读源码）：

```text
① cli/app.py          run(): raise typer.Exit(code=execute(request, ...))
② cli/app.py          execute(): try: assembly = _assemble(...)
③ cli/app.py          _assemble(): roots = (working_dir,)   ← 省略 --allowed-root 时的最保守非空集合
④ cli/app.py          load_pack(Path(request.pack_directory), roots=roots, ...)
⑤ harness/domain_pack.py   load_pack(): resolve_within(directory, roots, what="领域包目录")
⑥ foundation/paths.py      resolve_within(): resolved = Path(...).expanduser().resolve()
⑦ foundation/paths.py      for root in roots:  ← roots 只有工作目录
                                root_resolved in resolved.parents?  ✗
⑧ foundation/paths.py      raise PathNotAllowedError
⑨ cli/app.py          execute(): except BenchError → logger.error("装配失败", error_type=...)
                                → return EXIT_ASSEMBLY (= 3)
```

三个值得停下来想的点：

- **`roots` 为什么是"工作目录"**：`app.py` 里 `if not roots: roots = (working_dir,)`
  = **最保守的非空集合**；而 `session.py::_check_config` 又规定"空允许根 ⇒ 拒绝启动"
  ⇒ 两处配合才是"没有默认值可退化，但命令仍跑得起来"。
- **`load_pack` 的 `roots` 是必填 keyword-only、无默认值**（契约 §4.1 的 `P4`）：
  默认值会让"忘了传"退化为"任意路径"（与审计落点白名单同一取向）。
- **⚠️ `3` 不等于"领域包有问题"**：`ModelUnavailableError`（模型起不来）**也走 3**
  （同一个 `BenchError` 家族）⇒ **必须看 `error_type`，不要只看退出码**。

### 5.3 阶段 3 起（未执行）——需要额外给的两个参数

阶段 3 是**真起模型**的长跑（本机实测 196 s ~ 850 s，**单次采样，非性能数据**）。
它比阶段 2 多两个必须**显式**给的参数，理由是弱硬件上的可用性（本机实测生成速度约 2.0 ~ 3.4 tok/s）：

| 参数 | 不给的后果 |
| --- | --- |
| `--max-completion-tokens 1536` | Qwen3-4B 是**思考模型**，预算不足时 token 全耗在推理上、**一个工具调用都不发** ⇒ 客户端按契约抛 `ModelProtocolError` ⇒ `FAILED`。本机实测 `384` 失败、`1536` 通过（**换模型必须重测**） |
| `--model-request-timeout-s 600` | 单次补全可达 450 s+，超过协议默认 `60 s` ⇒ 请求超时 ⇒ 重试耗尽 ⇒ `FAILED` |

另需 `--allowed-root /workspace/examples`（让领域包进允许根——这正是阶段 2 失败的正解）。

---

## 6. 恢复点（**下次会话从这里开始**）

> 读到这里就够了；接 `docs/devlog/` 最新一篇的 §7，并执行"重拾语境"四步
> （`docs/devlog/README.md` §重拾语境）。

**截止落盘时的进度**：

| 项 | 状态 |
| --- | --- |
| 带读 **R1**（本文 §1~§3） | ✅ **已讲完并落盘** |
| 带读路线图 R2~R5（§4） | ✅ 已排定；所有者确认 **R2 与 R3 都要学** |
| **阶段 1（门禁）** | ⬜ **命令已给出，未执行** |
| **阶段 2（装配期拒绝）** | ⬜ **命令已给出，未执行** |
| 阶段 3（真模型多步会话） | ⬜ 未开始 |
| 阶段 4（审计回放）/ 阶段 5（默认拒绝）/ 阶段 6（收尾核对） | ⬜ 未开始 |

**续上的第一句话**（可直接复制）：

```text
按 docs/engineering/product-onboarding-notes.md §6 续上。
先跑【阶段 1 + 阶段 2】（命令在 product-handbook.md §2.1 / §3 舞台 1），
我把原始输出（含退出码与 stderr）贴回；你不要替我跑、不要替我猜输出。
跑完对照 product-onboarding-notes.md §5 的要点讲，然后进入阶段 3。
```

⚠️ **为什么这份材料必须落盘**：云环境**每次从 `develop` 拉起、重启即清空上下文**，
且平台会在 4~6 点强制关闭超过 8 小时的容器 ⇒ **未落盘的内容下次会话等于不存在**
（2026-09-16 已因此丢过一批内容，机制性理由见 `ADR-0013`）。
