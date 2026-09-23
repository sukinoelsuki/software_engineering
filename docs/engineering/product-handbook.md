# 产品使用与演示手册（含现状报告）

- **面向**：项目所有者（人类维护者），以及任何接手本仓库的人。
- **对应版本**：`0.1.0`（已发布：标签 `v0.1.0` = 合并提交 `14000b3`）；**`M2` 出口判据已达成**（2026-09-20，
  判据 [`sdlc.md`](sdlc.md) §3.3）；台账下一级 `0.2.0` **待所有者决定是否落定**（见 §7）。
- **证据口径**：本手册里的**每条命令都由团队领导于 2026-09-20 在本机实测跑过**，输出原样摘录；
  凡引用他人产出，一律注明出处。**没有实测过的命令不写进来。**
- **独立复核（他证）**：2026-09-21 由**验证角色**逐字复跑全部 5 个舞台（只读本手册、自建临时目录），
  **结论：逐条可跑通、结果一致**（置信度 高）。证据
  [`../research/2026-09-21-handbook-reproducibility-review.md`](../research/2026-09-21-handbook-reproducibility-review.md)；
  该复核指出的**三处呈现差异**已于同日按"更正而非抹掉"修正（见 §2.3、§3 舞台 3 / 舞台 4 的注）。
- **权威源（不要在本文件找"结论真源"）**：里程碑判据 → [`sdlc.md`](sdlc.md) §3；
  接口契约 → [`docs/design/interfaces/`](../design/interfaces/README.md)；
  威胁状态 → [`docs/design/threat-model/README.md`](../design/threat-model/README.md) §4.1；
  测试策略 → [`testing-strategy.md`](testing-strategy.md)；活待办 → `docs/devlog/` 最新一篇 §7。
- **放这里的原因**：`docs/engineering/` 已有同类非规范文档（如 [`post-build-checklist.md`](post-build-checklist.md)）。
  按项目规则"新目录必须先有 ADR"，本手册**不新建目录**；若日后要独立成 `docs/guides/`，需先开 ADR 并迁移。

---

## 0. 这份文档怎么用（三条路径）

| 你想做什么 | 直接跳 |
| --- | --- |
| 先掌握**现在到底有什么、强在哪、弱在哪** | §1 现状报告 |
| 亲手跑一遍、看着它工作 | §2 五分钟上手 → §3 演示脚本（5 个舞台） |
| 它出错了，我要定位 | §4 调试手册（退出码 → 症状 → 根因 → 处置） |
| 想**按阶段亲手跑一遍并理解每步的设计取舍与代价** | [`product-walkthrough.md`](product-walkthrough.md)（执行顺序 + 每步看什么 + 为什么这么设计；**命令仍以本手册为准**） |
| 我要判断下一步做什么 | §6 已知边界、§7 下一步候选 |

---

## 1. 现状报告

### 1.1 里程碑（判据一律见 [`sdlc.md`](sdlc.md) §3）

| 里程碑 | 状态 | 判据出处 |
| --- | --- | --- |
| `M0` 框架就绪（Harness 可开工） | ✅ 2026-09-19（`G1`~`G9` 全绿） | `sdlc.md` §3.1 |
| `M1` 需求 + 架构 + 威胁模型成稿 | ⚠️ **未完成**：五项中四项按"窄口径"达成，**`M1-1`（SRS 成稿）仍缺** | `sdlc.md` §3.2 |
| `M2` 端到端主流程可运行（Alpha） | ✅ 2026-09-20（`M2-1`~`M2-4` 全满足） | `sdlc.md` §3.3 |

版本：`0.1.0`「首个可用版本」已发布（端到端可跑 + 调用工具 + 审计可回放）；
`0.2.0` 的台账判据（指向 `sdlc.md` §3.3）**已满足**，但**落定是独立动作**，须按 [`git-workflow.md`](git-workflow.md) §5.3
与 [`ADR-0019`](../adr/0019-release-and-version-policy.md) §5.7 由所有者决定（代理不自行决定发布）。

### 1.2 现在有什么代码（逐模块）

| 层 | 模块 | 状态 |
| --- | --- | --- |
| 契约（零行为） | `contracts/`（6 模块：audit / harness / model / policy / tools / 基类） | ✅ |
| 基础 | `foundation/`（config / logging / paths / errors / proc） | ✅ |
| 安全 | `security/capabilities.py` · `security/policy.py`（default-deny + `PolicyEngine.decide()`） | ✅ |
| 观测 | `observability/audit.py`（JSONL 落盘、`query_by_id` 可回放） | ✅ |
| 模型 | `model/client.py`（`LocalLlamaClient`：起 `llama-server`，**仅本地回环**） | ✅ |
| 工具 | `tools/registry.py` · `tools/files.py`（读/写/列目录）· `tools/shell.py`（执行命令） | ✅ |
| 编排 | `harness/`（9 件：session / loop / context / trimming / prompts / domain_pack / errors / arguments / budget） | ✅ |
| 表现 | `cli/`（3 件：`app` / `render` / `approval`，入口 `agent-sec-perf run`） | ✅ |
| 数据 | `examples/packs/`（两份只读示例领域包） | ✅ |
| **未开工** | `security/sandbox/` · `security/refusal.py` · `model/{router,probe,assets}.py` · `tools/search.py` · `observability/tracing.py` | ❌ |

> 逐项实现状态与依据（文件 / 提交）以 [`architecture.md`](../design/architecture.md) §4 与
> [`ADR-0015`](../adr/0015-layering-and-reuse-boundary.md) §9 为准；本表只是索引。

### 1.3 产品现在**能**做的事（每条都有可复现证据）

1. **非交互跑完一次真实会话**：真实 `llama-server` + 本地 GGUF，模型自己决定调用工具，
   工具真的读到磁盘内容，退出码表达结果（§2.2 有逐字命令与实测输出）。
2. **多步会话**：同一会话内多次模型往返 + 多次工具调用（本次实测：`list_dir` → `read_file` 两步，
   §3 舞台 2）。这是 `M2-1` 的形态。
3. **每一步都留可回放的审计**：磁盘上的 JSONL，每条工具调用有唯一 `event_id`，
   可用 `query_by_id` 从**落盘文件**还原（§3 舞台 3）。
4. **能力默认拒绝（default-deny）**：没有显式授予就跑不动工具；拒绝是**可观察**的
   （事件 + 审计都有记录，§3 舞台 4）。
5. **领域包（声明式 TOML，无代码）**：声明暴露哪些工具、需要哪些能力、风险等级、提示片段、输出格式；
   包内出现 `.py`/`.pyc`/`__pycache__` 会被拒绝加载（`R5`）。
6. **工具裁剪**：按模型能力档位（`--capability-tier basic|standard|advanced`）与领域包白名单**只收窄**，
   BASIC 档只读。
7. **质量门禁可一键复跑**：`make check`（本次实测 **955 passed** + bandit + 密钥扫描 + 依赖审计，
   8.44 s）；`make test-security`（**96 passed**，1.26 s）。
8. **真模型集成用例可复跑**（默认不跑，两道锁）：`AGENT_SEC_PERF_E2E=1 ... -m integration` ⇒ **10 passed**，
   466.55 s（证据见 §1.5）。

### 1.4 产品现在**不能**做的事（硬边界，**不得放大**）

- **不是"安全已到位"**：威胁模型 13 条里 **已缓解并验证 1 / 部分缓解 10 / 未缓解 2**（`T-04`、`T-10`）；
  §5「缺行为层验证」**6 条**。真源：[`threat-model/README.md`](../design/threat-model/README.md) §4.1。
- **没有沙箱**：`security/sandbox/` 未开工 ⇒ 命令执行**没有隔离层**（对应威胁 `T-01`/`T-13`，
  落位以 [`architecture.md`](../design/architecture.md) §7.2 为准）。
- **提示注入与上下文污染（`T-04`）仍未缓解**：但它的落点是 `harness/{context,prompts}` 与
  **缺失的注入语料集**，**与沙箱无关** —— 📌 2026-09-21 更正：本段此前把两者写进同一条（**归因错误**），
  现拆开并改正（发现经过见 [`docs/devlog/0020`](../devlog/0020-2026-09-21-M1出口推进与手册独立复核.md) §3.8）。
- **没有出站**：`network_allowed` 恒为 `False`，云端模型客户端未实现（`T-10` 仍未缓解）。
- **没有拒绝层 / 模型路由 / 能力探测 / 成本资产 / 检索工具 / 追踪**：见 §1.2 的"未开工"行。
- **未测性能**：目前只有**单次采样**的墙钟（如 196 s、507 s），**不构成**任何性能判据
  （要 P50/P95/P99 + 重复 ≥3 次 + 极差，走 `bench/` 与 `make bench-round`）。
- **模型相关取值不通用**：token 预算与超时是**本机 + 本模型**实测值，**换模型必须重测**。
- **`examples/` 不随 wheel 分发**（`ADR-0021` §7 已实测：wheel 内 `examples/` 条目 0）。

### 1.5 证据链在哪（想自己核就从这几条走）

| 想核什么 | 去处 |
| --- | --- |
| 里程碑结论与逐条判据 | [`sdlc.md`](sdlc.md) §3.1 / §3.2 / §3.3（每条都有"证据"列） |
| 端到端实跑取证（含产品 CLI 多步会话、审计回放、集成用例复跑） | [`docs/research/2026-09-20-m2-exit-criteria-evidence.md`](../research/2026-09-20-m2-exit-criteria-evidence.md) |
| `0.1.0` 发布时的单步端到端取证 | [`docs/research/2026-09-20-v0.1.0-e2e-evidence.md`](../research/2026-09-20-v0.1.0-e2e-evidence.md) |
| **本手册自身能否逐字复跑**（他证，非作者自证） | [`docs/research/2026-09-21-handbook-reproducibility-review.md`](../research/2026-09-21-handbook-reproducibility-review.md) |

---

## 2. 五分钟上手

### 2.1 环境自检（不需要模型，秒级）

**本机实测输出**（2026-09-20）：

```text
$ uv --version          → uv 0.12.16 (x86_64-unknown-linux-gnu)
$ uv run python --version → Python 3.12.14
$ nproc                 → 8
$ free -h | head -2     → Mem: 16Gi total, 13Gi available
$ ls -l /opt/models/*.gguf
  AgentCPM-Explore.Q4_K_M.gguf   2716068064
  MiniCPM5-2B-Q4_K_M.gguf        1561318368
  Qwen3-4B-Q4_K_M.gguf           2497280256   ← 本手册全部实跑用这个
  Qwen3-8B-Q4_K_M.gguf           5027783488
$ /opt/llama.cpp/build/bin/llama-server --version
  version: 0.4.1-dev (build 1, commit 69eb250) / built with GNU 12.2.0 for Linux x86_64
```

```bash
cd /workspace
make check          # 质量门禁：本次实测 955 passed in 8.44s + bandit / detect-private-key / pip-audit 全过
make test-security  # 安全用例：本次实测 96 passed, 869 deselected in 1.26s
```

> 若 `make check` 挂在 `hooks-check`：那是本地 git 钩子没装，跑一次 `make setup` 即可
> （该目标是 fail-secure 的：钩子缺失时**立刻**失败，而不是等 100+ 测试跑完）。

### 2.2 一次最小真实会话（约 3 分钟）

**关键前提（三条，缺一条就会以"看起来像模型不行"的方式失败）**：

1. **在哪个目录运行很重要**：`cwd` 决定两件事——去哪找 `.lowspec.toml`（能力授予），
   以及 `--allowed-root` 省略时的**默认允许根 = 工作目录**。
2. **能力必须显式授予**：产品默认**什么都不授予**（default-deny）。领域包只能在授予集合上**收窄**，
   **不能替你授予**。
3. **模型的 token 预算与超时属"模型相关参数"**：本机 + Qwen3-4B 实测需要显式给定（见下表）。

```bash
# ① 准备工作目录（demo 放在 /tmp，不污染仓库）
WORK=$(mktemp -d); cd "$WORK"
printf 'DEMO-SENTINEL-A-7f21\n' > hello.txt
printf 'DEMO-SENTINEL-B-3c94\n' > notes.txt

# ② 显式授予"读文件"这一项能力（cwd 下的项目级配置）
printf '[policy]\ngranted_capabilities = ["read_file"]\n\n[logging]\nlevel = "INFO"\n' > .lowspec.toml

# ③ 跑一次多步会话（真实 llama-server + 真实 GGUF）
uv run --project /workspace agent-sec-perf run \
  "工作目录下有若干文件。请严格按顺序完成两步，每一步都必须真的调用工具：第一步，调用 list_dir 列出工作目录的条目（path 取值为 .）；第二步，从列出的条目里找到 hello.txt，调用 read_file 读取它（path 取值为 hello.txt）。必须先做第一步，等结果返回后再做第二步；最后把 hello.txt 的内容原样作为最终回答。" \
  --pack /workspace/examples/packs/coding-readonly \
  --allowed-root "$WORK" --allowed-root /workspace/examples \
  --working-dir "$WORK" \
  --output-format json \
  --model-path /opt/models/Qwen3-4B-Q4_K_M.gguf \
  --max-completion-tokens 1536 --model-request-timeout-s 600 \
  > stream.jsonl 2> stderr.log
echo "EXIT=$?"     # 本次实测：EXIT=0，墙钟 196 s，stdout 10 行 JSONL
```

| 开关 | 为什么不能省（**本机实测**的后果） |
| --- | --- |
| `--allowed-root`（≥1 个） | 默认根 = 工作目录。领域包也必须落在允许根内，否则装配期直接失败（见 §3 舞台 1） |
| `.lowspec.toml` 的 `granted_capabilities` | 不写 ⇒ 默认拒绝 ⇒ 模型每次调用都被拒（§3 舞台 4 的实测形态） |
| `--max-completion-tokens` | **给少了任务直接失败**：Qwen3-4B 是思考模型，预算不足时 token 全耗在推理上、**一个工具调用都不发** ⇒ 客户端按契约抛 `ModelProtocolError` ⇒ `FAILED`。`384` 在更窄上下文里够用、在更丰富上下文里**不够**（同一取值在不同上下文里结论不同，**不要外推**） |
| `--model-request-timeout-s` | 弱硬件生成速度实测约 **3.4 tok/s**：预算放大后单次补全可达 450 s+，会超过协议默认的 `60 s` ⇒ 请求超时 ⇒ 重试耗尽 ⇒ `FAILED` |
| `--pack` | 不配领域包时**所有**工具取保守默认 `DEFAULT_TOOL_RISK = HIGH` ⇒ 每次调用都需人工确认；非交互没有确认通路 ⇒ **一个工具都执行不了**（这是**可用性缺口**，不是安全缺陷，已登记在 `devlog 0018` §3.11(c)） |

### 2.3 真实会话的实测输出长什么样（本次 196 s 那一次）

事件流（`--output-format json` 的 10 行，逐行 `json.loads` 成功）：

```text
EVENTS 10
KINDS ['model_response', 'tool_call', 'policy_decision', 'tool_result', 'model_response',
       'tool_call', 'policy_decision', 'tool_result', 'model_response', 'task_finished']
SEQ   [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]              ← 从 0 连续、无空洞
FINAL_STATUS ['completed']                          ← 恰一条 TASK_FINISHED 且在末尾
SID 49419deab35e4ea48360e71cceb277b5

TOOL_CALL  list_dir   call_id= NSFEGBR9ceIuJyiDghqmIPfwOAZkOQLD
TOOL_CALL  read_file  call_id= VEko9pfMPQ3qybse15EgrFQAJJ1RrlUZ
TOOL_RESULT list_dir  ok=True  audit_id= 22f3683c85c5449c8e038bf8708ce4d6
TOOL_RESULT read_file ok=True  audit_id= e6112516443a4caeb5fea2e64ce828d8
SENTINEL_IN_TOOL_RESULT True        ← 工具真的读到了磁盘上的哨兵串（不是编造）
SENTINEL_IN_FINAL_ANSWER True       ← 最终回答里也确实引用了读到内容
```

**怎么读这段**：一次模型往返 = 一条 `model_response`；模型要工具 → `tool_call`；
策略求值 → `policy_decision`；执行结果 → `tool_result`。两次 `tool_call` 说明是**两步**（不是一次批量）。

> 📌 **以上是"派生扁平化视图"，不是 JSONL 的顶层键**（2026-09-21 独立复核指出，已更正呈现口径）：
> `ok` / `audit_id` 在 `tool_result` 事件的 **`result` 对象内部**（`result.ok` / `result.audit_id`）；
> 最终回答**不在** `task_finished`（它只有 `status` 与状态文案），而在**末条 `model_response` 的
> `response.content`**。`KINDS` / `SEQ` / `FINAL_STATUS` / `SID` / `SENTINEL_*` 同理都是**派生的核对输出**。
> 直接 `json.loads` 后取顶层 `ok` 会**取不到** —— 想按原文核字段请以
> [复核报告](../research/2026-09-21-handbook-reproducibility-review.md) §4.4 的真实结构为准。

---

## 3. 演示脚本（建议按顺序做，5 个舞台）

### 舞台 0：质量门禁（秒级，不需要模型）

```bash
cd /workspace && make check && make test-security
```

**你在看什么**：项目的"底线护栏"。`make check` 里 `hooks-check` 排最前是为了**早失败**；
`make test-security` 在**零用例**时会主动失败（fail-secure）。

### 舞台 1：装配期拒绝（秒级，**不起模型**）

```bash
WORK=$(mktemp -d); cd "$WORK"
printf '[policy]\ngranted_capabilities=["read_file"]\n' > .lowspec.toml
printf 'DEMO-SENTINEL-1\n' > hello.txt
uv run --project /workspace agent-sec-perf run "读取 hello.txt" \
  --pack /workspace/examples/packs/coding-readonly \
  --model-path /opt/models/Qwen3-4B-Q4_K_M.gguf --output-format json
echo "EXIT_CODE=$?"
```

**本次实测**：stderr 一条 JSON 诊断 ⇒ `{"error_type": "PathNotAllowedError", "event": "装配失败", ...}`，
**退出码 `3`**，**秒级返回**（模型都没启动）。

**为什么值得演示**：领域包在**允许根之外** ⇒ 直接拒绝启动，而不是"先跑起来再说"。
这就是"失败即拒绝（fail-secure）+ 配置错误与任务失败**可区分**"的最小例子。

### 舞台 2：真实多步会话（约 3 分钟）← **主舞台**

照 §2.2 逐字执行。**本次实测**：`EXIT=0`，196 s，10 条事件，审计 4 行，
两条工具调用都成功且 `tool_name` 不同 ⇒ 满足 `M2-1` 判据。

### 舞台 3：审计回放（秒级）

```bash
cd /workspace && uv run python -c "
import json, pathlib
from agent_sec_perf.observability.audit import JsonlAuditSink
from agent_sec_perf.foundation.config import default_audit_directory
ap = default_audit_directory(); print('AUDIT_DIR', ap)
mine = [json.loads(l) for l in ap.joinpath('audit.jsonl').read_text().splitlines()
        if l.strip() and json.loads(l)['session_id'] == '把舞台 2 的 SID 粘这里']
print('AUDIT_LINES_THIS_SESSION', len(mine))
sink = JsonlAuditSink(ap)
for e in mine:
    print(e['kind'], e['outcome'], e.get('tool_name'), e['event_id'])
for e in [x for x in mine if x['kind'] == 'tool_call']:
    r = sink.query_by_id(e['event_id'])
    print('REPLAY', r.kind.value, r.call_id, r.outcome.value)
"
```

**本次实测输出**：

```text
AUDIT_DIR /root/.local/state/lowspec/audit
AUDIT_LINES_THIS_SESSION 4
AUDIT policy_decision allow list_dir   b3d0ec3a604042b18305442be544dfef
AUDIT tool_call       ok    list_dir   22f3683c85c5449c8e038bf8708ce4d6
AUDIT policy_decision allow read_file  cc4ff36a83974c719d78e41039327321
AUDIT tool_call       ok    read_file  e6112516443a4caeb5fea2e64ce828d8
REPLAY 22f3683c85c5449c8e038bf8708ce4d6 => ('tool_call', 'NSFEGBR9ceIuJyiDghqmIPfwOAZkOQLD', 'ok')
REPLAY e6112516443a4caeb5fea2e64ce828d8 => ('tool_call', 'VEko9pfMPQ3qybse15EgrFQAJJ1RrlUZ', 'ok')
```

**你在看什么**：这条链是 `REQ-SEC-06`"不丢证据"的可操作形态 ——
**从磁盘文件**（不是内存里那个对象）按 `event_id` 还原出"谁、什么工具、什么结果"，
且能与事件流逐条对齐（`call_id` 一致）。

> 📌 **一处更正（2026-09-21 独立复核发现）**：本块输出里的 `AUDIT_LINES_THIS_SESSION 4`
> 在原命令中**并未打印**（属文档笔误）⇒ 命令已补上该行 `print`，使命令与输出对得上。
> 该补法由复核角色实测过（[报告](../research/2026-09-21-handbook-reproducibility-review.md) §4.5）。

### 舞台 4：默认拒绝 / fail-secure（**约 8 分钟**，最能说明安全取向）

```bash
WORK2=$(mktemp -d); cd "$WORK2"
printf 'DEMO-SENTINEL-C-5a13\n' > hello.txt
# 注意：**故意不写** .lowspec.toml ⇒ 默认什么都不授予
uv run --project /workspace agent-sec-perf run \
  "工作目录下有 hello.txt，请调用 read_file 工具读取它，然后把内容原样作为最终回答" \
  --pack /workspace/examples/packs/coding-readonly \
  --allowed-root "$WORK2" --allowed-root /workspace/examples --working-dir "$WORK2" \
  --output-format json --model-path /opt/models/Qwen3-4B-Q4_K_M.gguf \
  --max-completion-tokens 1536 --model-request-timeout-s 600 > stream.jsonl 2> stderr.log
echo "EXIT=$?"    # 本次实测：EXIT=0（会话自己走完），墙钟 507 s
```

**本次实测输出**：

```text
TOOL_CALL read_file            ← 模型请求了两次
TOOL_CALL read_file
TOOL_RESULT result_is_None= True  text= 策略拒绝，本次调用未执行
TOOL_RESULT result_is_None= True  text= 策略拒绝，本次调用未执行
AUDIT policy_decision deny read_file {'missing': ['read_file'], 'reason': '未授权操作：缺少所需能力，能力只能由显式配置授予，单次确认不改变授权集合（REQ-SEC-01）', 'requested': ['read_file']}
AUDIT tool_call       deny read_file {'denied_reason': 'policy_denied'}
```

> 📌 上表两行 `AUDIT …` 同样是**派生视图**（2026-09-21 独立复核指出）：真实 JSONL 里
> `missing` / `requested` / `reason` 在 **`policy_decision.detail`** 内、`denied_reason` 在
> **`tool_call.detail`** 内，不在顶层。内容与字段完全一致，仅呈现层级不同。

**四个值得讲的点**：
1. **不是"执行失败"**：`tool_result` 的 `result` 是 **`None`**（未执行），与我方中文说明一起回喂模型；
   项目刻意让"拒绝"与"执行失败"在事件与审计里**不同形**（`harness.md` 口径）。
2. **拒绝有审计**：`policy_decision deny` 写了缺哪个能力、为什么；`tool_call deny` 写了 `denied_reason`。
3. **模型不能靠"多试几次"绕过去**：被拒是**策略结论**，不随重试改变；如果连续被拒到上限
   （`--max-consecutive-failures`，默认 3），会话会以 `STALLED` → `FAILED`（退出码 `1`）收尾。
4. **这解释了 §2.2 那个前提**：授予能力是**人**在配置里做的显式决定，不是模型能争取到的。

---

## 4. 调试手册

### 4.1 退出码 → 症状 → 根因 → 处置（真源：`src/agent_sec_perf/cli/app.py` 与契约 §5.2）

| 退出码 | 含义 | 典型原因 | 先看什么 |
| --- | --- | --- | --- |
| `0` | `TASK_FINISHED(completed)` | — | 审计里 `tool_call ok` 有几条？是不是真执行过 |
| `1` | `TASK_FINISHED(failed)` | 模型请求超时（`ModelUnavailableError`）／`ModelProtocolError`（预算不足、输出不合契约）／连续失败达上限 `STALLED` | stderr 的 `error_type`；`llama-server.log` 的 `n_gen` / `truncated` |
| `2` | `LIMIT_REACHED`：模型往返步数用尽 | 任务太复杂或模型打转 | 加大 `--max-steps`；或把任务写得更"一步一事" |
| `3` | 装配/配置失败（`BenchError` 家族） | 领域包不在允许根内（`PathNotAllowedError`）／配置非法／工具注册失败／**模型路径没给** | stderr 那条 JSON 的 `error_type`（**不起模型**，秒级） |
| `4` | 审计写入失败 | 落点不可写 / 越出 `ALLOWED_AUDIT_ROOTS` | 审计目录权限与常量 |
| `5` | 其它未预期异常 / 事件流缺 `TASK_FINISHED` | 真缺陷 ⇒ **请当 issue 处理** | stderr 的 `error_type` + 复现步骤 |

> ⚠️ **别把 `2` 当超时**：超时走的是 `1`。这一点在本轮之前被写错过（见
> [`docs/research/2026-09-20-m2-exit-criteria-evidence.md`](../research/2026-09-20-m2-exit-criteria-evidence.md) §A.3）。

### 4.2 三个"看起来像模型不行、其实是配置"的坑

| 症状 | 真因 | 处置 |
| --- | --- | --- |
| 模型一直"在推理"但从不调用工具、最后 `FAILED` | `--max-completion-tokens` 太小（思考模型的推理吃掉了全部预算） | 加大预算（本机 Qwen3-4B 用 `1536` 跑通；换模型重测） |
| 每次调用都被拒、结果 `None`、审计 `deny` | **没有授予能力**（或授予集合与包声明求交后为空） | 在 `.lowspec.toml` 里显式授予；领域包只能**收窄**不能授予 |
| 报 `PATH_NOT_ALLOWED` / 读文件失败 | 目标不在 `--allowed-root` 内（模型给的相对路径是相对 `--working-dir` 解析的） | 显式给足允许根；不要靠"拼接路径"绕过 |

### 4.3 日志与证据在哪

| 产物 | 位置 | 说明 |
| --- | --- | --- |
| 事件流 | 你的 stdout（`--output-format json` 时是纯 JSONL） | 诊断永远走 stderr，二者不混 |
| 诊断日志 | stderr（结构化 JSON，含 `error_type`） | 不含凭据、不回显不可信内容原文 |
| 模型服务日志 | `--model-log`，默认 `<working-dir>/llama-server.log` | 生成速度、`n_gen`、`truncated` 都在这里 |
| **审计** | 默认 `~/.local/state/lowspec/audit/audit.jsonl`（本机实测：`/root/.local/state/lowspec/audit`） | 一行一事件、只追加、可 `query_by_id` 回放 |

### 4.4 想加一个自己的领域包？

抄 [`examples/packs/coding-readonly/pack.toml`](../../examples/packs/coding-readonly/pack.toml)（36 行）即可：
它的键就是全部合法键；**包目录内不得有 `.py`/`.pyc`/`__pycache__`**（有则拒绝加载）。
合法的键与形状见 [`harness.md`](../design/interfaces/harness.md) §4.2，
面向用户的说明见 [`examples/README.md`](../../examples/README.md)。

> ⚠️ **不要把 `run_command`/`write_file` 声明成 `low` 来"让它能跑"**：那是**下调安全默认**，
> 必须先走 ADR。示例包刻意只声明只读工具，就是为了不给你一个坏榜样。

---

## 5. 安全侧怎么"看得见"（本产品目前的安全面）

| 机制 | 在哪里看得见 | 现状 |
| --- | --- | --- |
| **默认拒绝**（能力必须显式授予） | 舞台 4：`policy_decision deny` + `missing` 字段 | ✅ 已实现 |
| **能力包（领域包）声明风险** | 包里的 `[security]` / `[security.risk_overrides]`；审计的 `risk_level` | ✅ 已实现 |
| **档位裁剪**（少暴露 = 小攻击面） | `--capability-tier basic` ⇒ 只读；被裁掉的工具对模型等同"未知工具"并**默认拒绝 + 审计** | ✅ 已实现 |
| **人工确认通路** | `--interactive`（需 TTY）；非交互**显式不提供**通路 ⇒ 需确认即拒绝 | ✅ 已实现 |
| **审计与回放** | 舞台 3 | ✅ 已实现 |
| **子进程最小环境 / 不继承父环境** | `foundation/proc.py`；对抗性用例 `tests/security/test_t08_independent_spawn_env.py` 等 | ✅ 已实现 |
| 沙箱隔离、拒绝层、未授权出站拦截、模型/资产完整性 | — | ❌ **未实现**（`T-04`/`T-10` 未缓解） |

> **一句话口径**：现在能"看得见"的是**授权与证据**（谁能做什么、做了什么、凭什么）；
> **还没有**的是**隔离与强制执行**（真要做坏事时，除了拒绝授权，目前没有第二道墙）。

---

## 6. 已知边界（不得放大）

1. **威胁模型不是"通过验收"**：分布 `1 / 10 / 2`，口径是"一份**待办清单**式的威胁模型"。
2. **未测性能**：本手册里的 196 s / 507 s 都是**单次采样**，不得当阈值判据。
3. **模型相关取值**：`1536` / `600` / `384` 等只对本机 + 指定模型成立，换模型**必须重测**。
4. **单专家、单机、无出站**：`model/router.py`（多模型路由）与云端客户端均未实现。
5. **`examples/` 不随 wheel 分发**：只装 wheel 的用户看不到示例包（`ADR-0021` §7 已实测）。
6. **`M1-1`（SRS 成稿）仍未满足**：`M2` 达成**不**使它自动满足。

---

## 7. 下一步候选（交所有者决定，附我的推荐）

| # | 候选 | 为什么现在做 | 代价/前提 |
| --- | --- | --- | --- |
| 1 | **落定 `0.2.0`**（Alpha） | 台账判据（= `M2` 出口）**已满足**；发布一次能让"版本 ↔ 里程碑"这条线立住 | `make release VERSION=0.2.0` → 代理开 PR → **所有者**合并 → 代理打 tag + 回合（`ADR-0019` §5.7） |
| 2 | **`M1-1` SRS v0.2 收敛** | `M1` 唯一未满足项；`M1` 未完成会一直挂在报告里 | 需**所有者**确认 `devlog 0005` 的 `S-1`~`S-10` / `Q-1`~`Q-8`（这是人的决策，代理不替你做） |
| 3 | **沙箱 + 拒绝层**（`security/sandbox/`、`security/refusal.py`） | `T-04` 是仅剩的两条"未缓解"之一，也是"综合治理"主线最实的一块 | 属 Phase 2/3 主线；需先有接口契约（架构师）+ ADR |
| 4 | **性能基准**（`bench/` 已有子系统与 `make bench-round`） | Phase 3 的门；目前**零**性能数据 | 要跑多轮（时间成本）；口径见 [`benchmark-automation.md`](benchmark-automation.md) |
| 5 | **可用性缺口收口**（非交互 + 无 pack ⇒ 工具全不可执行） | 这是产品"好不好用"的第一道坎 | 三个候选方案已登记（`devlog 0018` §3.11(c)），其中"只读工具风险分级另议"**属改安全默认 ⇒ 必须 ADR** |

**我的推荐顺序**：1（落定 `0.2.0`，把 Alpha 定格）→ 2（清 `M1-1`，把需求线收口）→ 3（沙箱，动真正的安全主线）
→ 4/5 穿插。理由：1、2 都是"把已有的东西钉死"，代价小、收益立刻体现在报告与验收上；
3 才有真正的技术增量，但需要先有契约与 ADR，不宜与"收口"混在同一步。

---

## 8. 本手册的实测记录（可复核）

| 时点 | 命令/动作 | 结果 |
| --- | --- | --- |
| 2026-09-20 | `make check` | 退出码 0，`955 passed in 8.44s`；bandit / detect-private-key / pip-audit 全过 |
| 2026-09-20 | `make test-security` | 退出码 0，`96 passed, 869 deselected in 1.26s` |
| 2026-09-20 | 舞台 1（pack 在允许根外） | 退出码 **3**，stderr `PathNotAllowedError`，秒级（未起模型） |
| 2026-09-20 | 舞台 2（多步真实会话） | 退出码 **0**，**196 s**，10 条事件，`seq` 0..9 连续，2 次工具调用均 `ok`，哨兵串出现在工具结果与最终回答 |
| 2026-09-20 | 舞台 3（审计回放） | 本会话审计 **4 行**；2 条 `TOOL_CALL` 均可 `query_by_id` 从落盘文件还原并与事件流对齐 |
| 2026-09-20 | 舞台 4（默认拒绝） | 退出码 **0**（会话自行收尾），**507 s**，2 次调用均 `result=None` + "策略拒绝"；审计 `policy_decision deny` / `tool_call deny` |
| 2026-09-20 | 环境指纹 | uv 0.12.16 / Python 3.12.14 / 8 核 / 16GiB / llama-server 0.4.1-dev (69eb250) / Qwen3-4B-Q4_K_M (2,497,280,256 B) |
| 2026-09-21 | **独立复核**（验证角色，**非作者自证**）：5 个舞台 + 环境自检 + 审计回放**逐字复跑** | 逐条通过；`make check` 955 passed（8.29 s）、`make test-security` 96 passed（1.25 s）；舞台 2 = 194 s、舞台 4 = 502 s（与手册 196 s / 507 s 属同量级**观察值**）；据实指出三处**呈现**差异，已按"更正而非抹掉"修正（见 §2.3 与 §3 舞台 3/4 的注）。证据：[复核报告](../research/2026-09-21-handbook-reproducibility-review.md) |
| 2026-09-21 | **未复核项（如实登记，不得读成"已全部覆盖"）** | ① §1.3 第 8 条的真模型集成用例（`AGENT_SEC_PERF_E2E=1 … -m integration`，约 466 s）本轮**未重跑**（其独立证据见 [`m2` 取证报告](../research/2026-09-20-m2-exit-criteria-evidence.md)）；② **跨机 / 换模型**复跑**未做** ⇒ §6 第 3 条的"模型相关取值"声明成立，但**未被推翻也未被他证** |
| 2026-09-23 | **按 [`product-walkthrough.md`](product-walkthrough.md) 逐阶段复跑（阶段 1~6；代理执行、所有者当次授权）** | 阶段 1 `make check` **957 passed / 1 skipped**（10.23 s）+ `make test-security` **96 passed**；首跑按设计挂在 `hooks-check`（钩子未装）后 `make setup` 复绿；阶段 2 退出码 **3** / `PathNotAllowedError` / **0 s**；阶段 3 退出码 **0** / **315 s** / 10 事件 / `seq` 0..9 / 2 次工具调用均 `ok` / 哨兵串见于工具结果与最终回答；阶段 4 本会话审计 **4 行**、2 条 `REPLAY` 与事件流 `call_id` **逐字相等**；阶段 5 退出码 **0** / **842 s** / 2 次调用 `result is None` + 审计 `deny`；阶段 6 `git status` **空**、未合入 **1** 处（长驻 `bench/data`，按 `ADR-0014` §2.1 属设计如此）。⚠️ 墙钟均为**单次采样**，且**慢于本表 09-20/09-21 记录**（315 s vs 196/194 s、842 s vs 507/502 s）⇒ **不构成性能结论**。详见 [`docs/devlog/0021`](../devlog/0021-2026-09-23-阶段式跑测取证.md) |

> 复现提示：本手册所有 demo 的临时目录都在 `/tmp`（`mktemp -d`），**不会**污染仓库；
> 审计会写到用户状态目录（只追加）。想清理 demo 目录：`rm -rf /tmp/tmp.*`（**先确认没别的东西**）。
