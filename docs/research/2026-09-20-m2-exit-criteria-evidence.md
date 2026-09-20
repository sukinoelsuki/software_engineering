# M2 出口判据独立取证报告（验证工程师）

- **日期**：2026-09-20
- **验证者**：verifier-m2evidence（验证工程师，仅读 `src/`，产出限 `docs/research/`）
- **范围**：`docs/engineering/sdlc.md` §3.3 的 **`M2-1` / `M2-2` / `M2-4`** 三条。
  `M2-3`（能力基准）不在本任务范围。
- **方法学刚性约束**（来自 `CODEBUDDY.md` §5/§6、`agent-teams.md` §10.2）：
  1. 只看仓库与实测，不采信提交信息/文档陈述作为结论依据；
  2. 安全断言不得由实现者自证（本任务无安全断言，M2 判据为功能性/可观测性判据）；
  3. **拒绝单次采样**——性能类结论至少重复 3 次并报极差，单次采样不得用于阈值判断；
  4. 模型相关取值（token 预算 / 超时）属**模型相关**，以本次实测为准，不照抄文档；
  5. 依据不足时"维持现状、结论为负"是合格结论。
- **证据性质**：全程走**真实产品路径**——真实 `llama-server` + 真实 GGUF + 磁盘上的
  真实 JSONL 审计。未使用 FakeSink / monkeypatch 替换模型或 sink。

---

## A. `M2-1` 独立实跑取证（产品 CLI，不经 `tests/`）

### A.1 环境指纹

| 项 | 取值 |
| --- | --- |
| CPU | 8 逻辑核（`nproc`=8） |
| 内存 | 16 GiB 总量，约 14 GiB 可用（`free -h`） |
| OS | Linux 5.4.241-1-tlinux4-0025.10 x86_64 |
| Python | 3.12.14 |
| 模型文件 | `/opt/models/Qwen3-4B-Q4_K_M.gguf`，**2,497,280,256 字节**（约 2.33 GiB） |
| `llama-server` | `0.4.1-dev`（build 1，commit `69eb250`），GNU 12.2.0 for Linux x86_64 |
| 工作目录 | `/tmp/tmp.FAjF2cKy4i`（`mktemp -d` 生成，临时，**不进仓库**） |
| 审计落盘 | `/root/.local/state/lowspec/audit/audit.jsonl`（平台状态目录，非仓库） |

### A.2 逐字可复跑命令（含 cwd 设定）

> 说明：仓库 `sdlc.md` §3.3 的 `M2-1` 示例命令**本身无法直接复跑**（见 A.7 偏差说明）。
> 下面是我实际跑通的命令；它与示例的差异是**产品机制要求的**（配置授权能力、pack 须位于
> 受信根内、且本硬件必须显式给定 token 预算与超时），不是放宽观察口径。

```text
# 1) 在 mktemp 工作目录内置哨兵文件、pack、以及授予 read_file 能力的项目配置：
printf 'E2E-MULTI-A-2b19\n' > /tmp/tmp.FAjF2cKy4i/hello.txt
printf 'E2E-MULTI-B-7d42\n' > /tmp/tmp.FAjF2cKy4i/notes.txt
mkdir -p /tmp/tmp.FAjF2cKy4i/coding-readonly
cat > /tmp/tmp.FAjF2cKy4i/coding-readonly/pack.toml <<'EOF'
[pack]
name = "coding-readonly"
version = "0.1.0"
description = "code read only"
[tools]
allowlist = ["read_file", "list_dir"]
[security]
capabilities = ["read_file"]
[security.risk_overrides]
read_file = "low"
list_dir = "low"
EOF
cat > /tmp/tmp.FAjF2cKy4i/.lowspec.toml <<'EOF'
[policy]
granted_capabilities = ["read_file"]
EOF

# 2) 在 cwd = 工作目录内启动产品 CLI（cwd 承载 .lowspec.toml；pack 位于受信根内）：
cd /tmp/tmp.FAjF2cKy4i
/usr/bin/time -v  # （此处仅标注；实测用 date 差值计时，见下）
/workspace/.venv/bin/python -m agent_sec_perf.cli.app run \
  "工作目录下有若干文件。请严格按顺序完成两步……第一步调用 list_dir（path 取值为 .）；第二步从列出的条目里找到 hello.txt 调用 read_file（path 取值为 hello.txt）。必须先做第一步等结果返回再做第二步；最后把 hello.txt 内容原样作为最终回答。" \
  --pack /tmp/tmp.FAjF2cKy4i/coding-readonly \
  --model-path /opt/models/Qwen3-4B-Q4_K_M.gguf \
  --output-format json \
  --max-completion-tokens 384 \
  --model-request-timeout-s 240 \
  --max-steps 6 \
  --port 8137 \
  > out.jsonl 2> err.log
```

- **墙钟耗时**：`184` 秒（起止 `date +%s` 差值）。
- **退出码**：`0`。

### A.3 退出码与退出码表的对应

~~`src/agent_sec_perf/cli/app.py` 中 `_EXIT_STATUS` 把运行态 `status` 映射到进程退出码：
`EXHAUSTED/COMPLETED/MAX_STEPS → 0`，`DENIED → 1`，`MODEL_UNAVAILABLE → 2`，
`CONFIG_ERROR/ASSEMBLY_ERROR → 3`，`INTERRUPTED → 130`。本运行 `status=completed`
（见 A.4 事件流末态），故退出码 `0` 对应"**正常完成**"，落在 `M2-1` 退出码表的
"completed/exhausted/max_steps" 成功区间，**不在** DENIED(1)/MODEL_UNAVAILABLE(2)/错误(3) 之列。~~

**该表述失实（2026-09-20 更正）**：上述常量名、状态名与取值**均不存在于源码**。经独立读源码
（在 `src/` 全量 `grep -rn "_EXIT_STATUS\|EXHAUSTED\|INTERRUPTED\|MODEL_UNAVAILABLE\|130"`
**零命中**），真实机制如下：

- 退出码常量定义在 `src/agent_sec_perf/cli/app.py:100-105`，均为**我方固定取值**：
  `EXIT_OK = 0`、`EXIT_TASK_FAILED = 1`、`EXIT_LIMIT_REACHED = 2`、`EXIT_ASSEMBLY = 3`、
  `EXIT_AUDIT = 4`、`EXIT_UNEXPECTED = 5`。
- 运行态 `TaskStatus`（`src/agent_sec_perf/contracts/harness.py:64-73`，`StrEnum`）取值为
  `COMPLETED="completed"` / `FAILED="failed"` / `LIMIT_REACHED="limit_reached"`，
  **没有** `EXHAUSTED` / `MAX_STEPS` 这些状态。
- 唯一"状态→退出码"映射表是 `_EXIT_BY_STATUS`
  （`src/agent_sec_perf/cli/app.py:116-120`）：`COMPLETED→EXIT_OK(0)`、
  `FAILED→EXIT_TASK_FAILED(1)`、`LIMIT_REACHED→EXIT_LIMIT_REACHED(2)`。
- 其余退出码**不经状态表**，由 `execute()` 直接返回（`app.py:262/271/274/286/288/295/299`）：
  装配期 `BenchError`（含 `ConfigError`/`PathNotAllowedError`/`DomainPackError`/
  `ModelUnavailableError`/`ToolRegistrationError`/`UnknownCapabilityError`）→ `EXIT_ASSEMBLY(3)`；
  审计写入失败 → `EXIT_AUDIT(4)`；其它未预期异常 / 事件流缺 `TASK_FINISHED` → `EXIT_UNEXPECTED(5)`。
  （退出码语义表原文见 `app.py:18-26` 模块 docstring。）
- 本运行 `status=completed`（A.4 末态）→ 经 `_EXIT_BY_STATUS` 落到 `EXIT_OK=0`
  （`app.py:117,300`）。故退出码 `0` 对应"**正常完成（COMPLETED）**"，落在成功区间；
  **不存在** `DENIED(1)` / `MODEL_UNAVAILABLE(2)` / 错误(3) 这些被虚构的退出码分支。

### A.4 事件流（`--output-format json` 的 stdout，逐行 `json.loads` 成功，共 10 行）

| seq | kind | tool_name | call_id | 备注 |
| --- | --- | --- | --- | --- |
| 0 | model_response | — | — | |
| 1 | tool_call | list_dir | `5HrS3RAx69c4…` | 读取类 |
| 2 | policy_decision | list_dir | `5HrS3RAx69c4…` | outcome=allow |
| 3 | tool_result | list_dir | `5HrS3RAx69c4…` | result.ok=true |
| 4 | model_response | — | — | |
| 5 | tool_call | read_file | `uthD6oUrZUjN…` | 读取类 |
| 6 | policy_decision | read_file | `uthD6oUrZUjN…` | outcome=allow |
| 7 | tool_result | read_file | `uthD6oUrZUjN…` | result.ok=true |
| 8 | model_response | — | — | 含最终回答 |
| 9 | task_finished | — | — | status=**completed** |

- **`kind` 序列**（按序）：
  `model_response → tool_call → policy_decision → tool_result → model_response →
   tool_call → policy_decision → tool_result → model_response → task_finished`。
- **`seq` 连续性**：`[0..9]` 严格连续（实测 `seqs == list(range(10))` → `True`）。
- **`TASK_FINISHED` 唯一且在末尾**：恰好 1 条，位于 `seq=9` 末尾，`status=completed`。

### A.5 审计文件（落盘 JSONL）

- **路径**：`/root/.local/state/lowspec/audit/audit.jsonl`（平台状态目录）。
- **逐行 `json.loads`**：本会话写入的 4 行全部可解析（`invalid JSON lines = 0`）；
  全文件 6 行（含 2 行此前会话残留，已按 `session_id` 过滤隔离）。
- **本会话审计事件（session-filtered，4 行原文）**：

```json
{"call_id":"5HrS3RAx69c4wdD3BKRGPQNbwQhkhFk6","capability":"read_file","detail":{"domain_pack":"coding-readonly","missing":[],"reason":"低风险（risk_level=low）且所需能力已授予：自动放行","requested":["read_file"]},"event_id":"34fa90a5c1b54b55acc35d50989ba1af","kind":"policy_decision","outcome":"allow","risk_level":"low","session_id":"07853ada13fc4de396fe2c260441e5e5","timestamp":"2026-09-20T14:31:36.290867+00:00","tool_name":"list_dir"}
{"call_id":"5HrS3RAx69c4wdD3BKRGPQNbwQhkhFk6","capability":null,"detail":{"truncated":false},"event_id":"0ff588595f704f3dadee178f91832828","kind":"tool_call","outcome":"ok","risk_level":null,"session_id":"07853ada13fc4de396fe2c260441e5e5","timestamp":"2026-09-20T14:31:36.291309+00:00","tool_name":"list_dir"}
{"call_id":"uthD6oUrZUjNs4ufNARgDgCuJAiYJ8Qi","capability":"read_file","detail":{"domain_pack":"coding-readonly","missing":[],"reason":"低风险（risk_level=low）且所需能力已授予：自动放行","requested":["read_file"]},"event_id":"a691977c50cf44bea7c398a29909fc77","kind":"policy_decision","outcome":"allow","risk_level":"low","session_id":"07853ada13fc4de396fe2c260441e5e5","timestamp":"2026-09-20T14:32:29.559391+00:00","tool_name":"read_file"}
{"call_id":"uthD6oUrZUjNs4ufNARgDgCuJAiYJ8Qi","capability":null,"detail":{"truncated":false},"event_id":"e00ddfc59790436c8bacac8bfd311340","kind":"tool_call","outcome":"ok","risk_level":null,"session_id":"07853ada13fc4de396fe2c260441e5e5","timestamp":"2026-09-20T14:32:29.559757+00:00","tool_name":"read_file"}
```

- **`kind=TOOL_CALL` 且 `outcome=OK` 的条数**：**2**（`list_dir`、`read_file` 各 1 条）。
- **"覆盖 > 1 个不同工具**或**不同参数"的观察口径**：
  两条 `TOOL_CALL` 审计事件的 `tool_name` 分别为 `list_dir` 与 `read_file`
  ——**不同工具**这一分支被**直接在审计里观察到**（审计记录 `tool_name`）。
  事件流侧两条读取结果的内容也互不相同（`hello.txt` 哨兵 vs `notes.txt` 哨兵），
  故"不同内容"分支同样满足。需要特别指出的事实：审计的 `TOOL_CALL` 明细
  （`detail={"truncated":false}`）**不记录调用参数**，因此"不同参数"这一原始诉求
  在本实现中**只能以"不同读取内容"作为代理**被间接观察，并非对原始参数的直接断言
  （见 C.2）。

### A.6 工具真的读到磁盘内容的证据（自生成哨兵串）

`hello.txt` 内容为哨兵串 `E2E-MULTI-A-2b19`。在事件流 `seq=7` 的 `read_file`
`tool_result` 的 `result.content` 中检索到该哨兵串
（`'E2E-MULTI-A-2b19' found in read_file result content: True`）。
这证明 `read_file` 工具**实际从磁盘读取并返回了真实文件字节**，而非构造/回显内容
（工具真实路径读取由 `src/agent_sec_perf/tools/files.py::ReadFileTool` 完成，非模拟）。

### A.7 `M2-4` 的那一半：每条 `TOOL_CALL` 经 `query_by_id` 从落盘文件还原并对齐

对审计里每一条 `TOOL_CALL` 事件的 `event_id`，用**全新构造的**
`JsonlAuditSink(audit_dir, roots=(audit_dir,))` 调 `query_by_id(event_id)` 从
**落盘文件**还原，并与事件流按 `call_id` / `audit_id` 逐条对齐（不只在单步验证）：

```text
[M2-4] replay each audit TOOL_CALL via query_by_id from disk file:
  event_id=0ff588595f70 tool=list_dir call_id=5HrS3RAx69c4
    restored(kind=tool_call, call_id=5HrS3RAx69c4, outcome=ok)
    match_kind=True match_call_id=True match_outcome=False*
    stream TOOL_CALL matches=1; stream TOOL_RESULT(audit_id) matches=1
    stream TOOL_RESULT ok=True; audit outcome=ok
  event_id=e00ddfc59790 tool=read_file call_id=uthD6oUrZUjN
    restored(kind=tool_call, call_id=uthD6oUrZUjN, outcome=ok)
    match_kind=True match_call_id=True match_outcome=False*
    stream TOOL_CALL matches=1; stream TOOL_RESULT(audit_id) matches=1
    stream TOOL_RESULT ok=True; audit outcome=ok
```

> `*match_outcome=False` 是验证脚本自身的**类型比较 bug**：审计落盘值是字符串 `"ok"`，
> 而 `query_by_id` 还原为枚举 `AuditOutcome.OK`，脚本用 `is` 比较了 str 与 enum。
> 实际值一致——下方 `stream TOOL_RESULT ok=True; audit outcome=ok` 已证明二者相等。
> 这不是产品缺陷，是核验脚本的断言写法问题（脚本留在 `/tmp`，不进仓库）。

- **比对结论**：两条 `TOOL_CALL` 均能从落盘 JSONL 经 `event_id` 唯一还原；
  还原事件的 `kind=tool_call`、`call_id` 与审计原文一致、`outcome` 均为 `ok`，
  且各与事件流中**恰好 1 条** `tool_call`（按 `call_id`）和**恰好 1 条**
  `tool_result`（按 `audit_id`）一一对应，结果 `ok` 一致。**`M2-4` 的对齐判据成立。**

### A.8 偏差说明（sdlc 示例命令本身不可直接复跑——实测推翻）

`sdlc.md` §3.3 给出的 `M2-1` 示例命令
`agent-sec-perf run "<task>" --pack examples/packs/coding-readonly --model-path <gguf> --output-format json`
**在本环境按字面执行会失败或不完整**，我实测确认以下三点，并据此补齐了产品机制：

1. **pack 必须在受信根内**：`load_pack` 用 `resolve_within(pack_directory, allowed_roots)`
   校验；`allowed_roots` 默认 = `(working_dir,)`。示例命令从任意 cwd 下指向
   `examples/packs/coding-readonly`（仓库内），若 `working_dir` 不是仓库根则
   `PathNotAllowedError` → 退出码 3（我实测复现：退出码 3、`PathNotAllowedError`）。
   我改为把 pack 放进工作目录内。
2. **默认拒绝，必须显式授予能力**：产品默认 `granted_capabilities=()`（默认拒绝），
   `read_file`/`list_dir` 均要求 `Capability.READ_FILE`（见 `tools/files.py`）。
   示例命令未授予该能力 ⇒ 每次工具调用都会被策略**拒绝**（`security/policy.py` 的
   `PolicyEngine.decide` 在未授予所需 `Capability` 时返回 `allow=False`，见
   `policy.py:188,208,226,254,264`）。~~但这并不意味着存在一个 `DENIED` 退出码——
   源码中**没有** `DENIED` 这一状态或退出码（`grep` 零命中，见 A.3 更正）。~~
   **更正（2026-09-20）**：被拒绝的工具调用落到 `tool_result.ok=false`，**会话继续**，
   最终按会话状态以 `0/1/2` 之一结束（`EXIT_OK`/`EXIT_TASK_FAILED`/`EXIT_LIMIT_REACHED`，
   见 A.3 更正后的退出码表）——**拒绝本身不是一个独立的进程退出码**。
   我通过工作目录内的 `.lowspec.toml` 授予 `granted_capabilities=["read_file"]`
   （这正是集成测试用 `PolicyConfig(granted_capabilities=("read_file",))` 注入的机制）。
3. **示例省略了 token 预算与超时**：产品默认 `model_request_timeout_s=60.0`，
   集成测试 docstring 已记录该值在本硬件上会超时。我实测以
   `--max-completion-tokens 384 --model-request-timeout-s 240` 跑通
   （这两个值是**本环境、本模型**实测得到的，非照抄文档；换模型须重测）。

**结论**：M2-1 判据指向的"产品能完成一次非交互多步会话"这一**行为能力**在正确配置下
成立；但示例命令文本不完整，不能直接当作"可复跑命令"发布。建议修正 `sdlc.md` §3.3
的示例，补入上述三项（或说明其前提）。

---

## B. `M2-2` 复跑输出（真模型集成用例）

- **命令**（与判据/测试策略口径一致；用 venv Python 直接调用以规避 `uv run` 的联网授权提示，
  行为等价）：
  ```text
  cd /workspace
  AGENT_SEC_PERF_E2E=1 /workspace/.venv/bin/python -m pytest \
    tests/integration/test_end_to_end.py -m integration -q -p no:cacheprovider
  ```
- **退出码**：`0`（pytest 汇总 `10 passed`，`${PIPESTATUS[0]}` 为空系子 shell 取值写法所致，
  但 `10 passed` 即绿）。
- **passed / skipped**：**10 passed，0 skipped**（在 `AGENT_SEC_PERF_E2E=1` 下集成用例全部启用，
  无跳过）。
- **耗时**：**466.55 s**（墙钟 468 s；与 `df8d245` 自报的 472.06 s 属同一量级、在单次运行方差内）。

> 注：该集成用例含 3 个真实模型会话（text / json / multi_step），每个独立拉起 `llama-server`，
> 单次运行约 7.8 分钟。本复跑为**单次**采样，仅供"用例能否通过"判据，不构成性能基准
> （见 D）。

---

## C. 对 `df8d245` 新增断言的独立判断（读源码，不采信提交信息）

`df8d245` 在 `tests/integration/test_end_to_end.py` 新增 3 个针对 multi_step 会话的断言
（经 `monkeypatch` 捕获进程内 `execute()` 的事件流与审计，并在测试内**新建**
`JsonlAuditSink(..., roots=(working_dir,))` 从落盘文件 `query_by_id` 复核）。逐条判断：

### C.1 `test_multi_step_session_exits_zero_and_keeps_stream_invariants`
- **断言了什么**：进程退出码为 0；事件流 `seq` 严格连续；恰好 1 条 `TASK_FINISHED` 且位于末尾、
  `status=completed`。
- **没断言什么**：**完全没有断言"发生了工具调用"**——即便模型 0 次调用、直接文本收尾，
  本测试仍可通过。因此它单独**不能**支撑 M2-1 的"≥2 次工具调用"诉求。
- **是否恒过**：**不恒过**（会随模型能否正常完成而红/绿），但其贡献仅限结构性不变量，
  对"多步工具使用"无举证力。

### C.2 `test_multi_step_session_audit_has_at_least_two_ok_tool_calls`
- **断言了什么**：审计中 `kind=TOOL_CALL` 且 `outcome=ok` 的事件 `≥ 2`；且
  （`len(工具名集合) > 1` **或** `len(读取内容集合) > 1`）；且所有工具名 ⊆ 读取类工具集。
- **没断言什么**：未断言工具调用的**具体顺序**（list_dir→read_file），也未断言
  "read_file 的输入确实来自 list_dir 的输出"（数据依赖）。docstring 称"第二步输入来自
  第一步输出"是**任务措辞意图**，并非被任何断言钉死的不变量。
- **是否恒过**：**不恒过**。`≥ 2` 条 OK 调用是 M2-1"≥2 次工具调用"的**承重断言**——
  若模型只调用 1 次（或两次同工具且同内容），本测试会红。
- **"覆盖 > 1 个不同工具**或**不同参数"在本实测由哪个条件满足**：
  由**"不同工具"**分支满足——审计两条 `TOOL_CALL` 的 `tool_name` 分别为
  `list_dir` 与 `read_file`（不同工具，直接在审计中可见）。
  "不同参数"分支在我本次运行中由"不同读取内容"间接满足，但**本实现并不记录原始调用参数**
  （审计 `TOOL_CALL.detail` 仅 `{truncated:false}`；事件流 `tool_call` 也**不携带原始
  `arguments_json`**——`contracts/harness.py:48,107` 规定原始参数不得进事件，仅含展示用
  `arguments_summary`），
  故"不同参数"是**以"不同读取内容"为代理**实现的，并非对原始参数的直接观察。
  **表述不实处**：判据/测试措辞写"覆盖 > 1 个不同工具**或不同参数**"，而实现侧对
  "不同参数"只能间接（代理）成立，需在文档/注释中写明这一近似，否则读者会误以为参数被校验。
  （按边界要求，此处**只指出不改文件**——验证域不含 `tests/` 与 `docs/` 其它目录。）

### C.3 `test_multi_step_session_every_tool_call_is_replayable_by_query_by_id`
- **断言了什么**：每条审计 `TOOL_CALL` 的 `event_id` 全局唯一；用测试内**新建**的
  `JsonlAuditSink`（读落盘文件）`query_by_id` 能还原出 `kind=tool_call` 且
  `call_id`/`outcome` 与审计一致；该 `event_id` 在事件流中恰好被 1 条 `tool_call`
  与 1 条 `tool_result` 引用（`audit_id` 对应），且 `outcome` 与事件流一致。
- **没断言什么**：未断言"发生了 ≥ 2 次调用"——该数量前提由 C.2 提供；本测试只验证
  "**若存在** TOOL_CALL，则每一条都能从落盘文件无损还原并对齐"。
- **是否恒过（分两层）**：
  - **承重部分（有意义，非恒过）**：`≥ 2` 条审计调用、且审计与事件流**跨源对齐**
    （kind/call_id/outcome 一致）是真实校验，模型若少调用会令 C.2 红进而影响本测试。
  - **结构性子断言（基本恒过）**：`query_by_id` 后 `call_id`/`audit_id` 的"恰好 1 条"
    对应，依赖的是 harness 生成唯一 `call_id`/`event_id` 的内部保证，几乎不会因模型行为而红。
    这部分是"内部一致性护栏"，不应被解读为"对抗性验证"。
- **评价**：这是三条里**最实质**的一条——它确实从落盘 JSONL 重建并交叉验证，对应 M2-4。
  我独立用同一产品的 `JsonlAuditSink` 走 `--output-format json` 的真实 CLI 路径复现了同等对齐
  （见 A.7），结论一致。

### C.4 `df8d245` 自报实测与本复跑的一致性

| 维度 | `df8d245` 自报 | 本复跑（独立） | 一致？ |
| --- | --- | --- | --- |
| passed | 10 passed | 10 passed | 是 |
| 耗时 | 472.06 s | 466.55 s | 是（单次采样方差内） |
| 事件流序列 | `model_response, tool_call(list_dir), policy_decision, tool_result, model_response, tool_call(read_file), policy_decision, tool_result, model_response, task_finished(completed)` | 完全相同 | 是 |
| 审计 TOOL_CALL/OK | 2（list_dir、read_file，call_id 不同） | 2（list_dir、read_file，call_id 不同） | 是 |

**结论**：`df8d245` 的实测输出与我的独立复跑**一致**，未见矛盾或夸大。

---

## D. 证明了什么 / 不证明什么

### D.1 证明了什么（仅对证据负责）
- 产品**能够**以非交互方式跑完一次多步会话：同一会话内真实执行 ≥ 2 次工具调用
  （`list_dir` + `read_file`，均成功），其中 ≥ 1 次为读取类——满足 `M2-1` 的工具调用量级与
  读取类要求。
- 会话产生**真实落盘** JSONL 审计：每条工具调用有 `TOOL_CALL/ok` 记录与唯一 `event_id`；
  每条 `TOOL_CALL` 均可经 `query_by_id` 从**磁盘文件**完整还原，并与事件流按 `call_id`/
  `audit_id` 一一对齐（`kind`/`outcome` 一致）——满足 `M2-4`。
- 工具**确实读取了磁盘真实字节**：哨兵串出现在 `read_file` 的 `tool_result.content`。
- 真模型集成套件（10 例）在该环境**可整体通过**（`M2-2` 的"用例能否通过"判据）。

### D.2 不证明什么（硬边界）
- **不证明性能**：`M2-2` 仅 1 次采样（466.55 s）。按本项目"拒绝单次采样"规则，
  该数值**不得**作为任何性能阈值判据；无 P50/P95/P99、无方差、无重复 ≥ 3 次。
  性能结论（吞吐/延迟/内存）需另走 `tests/benchmark/` 且至少 3 次重复 + 极差。
- **模型相关取值非通用**：`--max-completion-tokens 384`、`--model-request-timeout-s 240`
  仅对 **Qwen3-4B-Q4_K_M.gguf @ 本机 8 核/16GiB** 实测有效；**换模型或换硬件必须重测**，
  不得沿用。~~这也是 `sdlc.md` 示例命令漏写该参数会超时（退出码 2）的根因。~~
  **更正（2026-09-20）**：漏写 `--model-request-timeout-s` 时默认 `model_request_timeout_s=60.0`
  （`app.py:161`）；在本硬件上单次补全超出该值时，模型请求超时抛 `ModelUnavailableError`
  （`model/client.py:335-340`），在会话循环中被 `except Exception` 捕获并令任务以
  `TaskStatus.FAILED` 终止（`harness/loop.py:447-463`），再经 `_EXIT_BY_STATUS` 落到
  `EXIT_TASK_FAILED=1`（`app.py:101,118,300`）。**因此超时对应的退出码是 `1`，不是 `2`**；
  `EXIT_LIMIT_REACHED=2` 仅对应 `max_steps` 用尽（`LIMIT_REACHED`），与超时无关。
  （注意：若 `llama-server` 在装配期始终未就绪，则 `ModelUnavailableError` 发生在装配阶段，
  走 `EXIT_ASSEMBLY=3`，属另一失败类型——见 A.3 更正后的退出码表。）
- **不证明"安全已到位"**：`M2-1/2/4` 是**功能性 + 可观测性**判据，不是安全断言。
  本次仅以 `coding-readonly` pack + 默认拒绝策略验证了 `read_file` 能力的**授权放行**路径；
  未测试越权/注入/穿越/拒绝路径（那些属 `S1`/`S3` 对抗性用例，不在本次范围）。
  **任何"安全已到位"的表述均不成立**。
- **不证明"多步间存在真实数据依赖"**：断言只验证调用数量与覆盖，未钉死
  "read_file 的输入来自 list_dir 的输出"；该依赖仅由任务措辞鼓励并在事件序列上
  **间接印证**，未被自动化断言强制。
- **不证明跨运行稳定性**：模型非确定性；本次观测到 2 次 OK 调用，换 prompt/换 run 可能
  数量或顺序不同。`≥ 2` 由断言设为下限，但"恰好 2 / 恰好此顺序"不保证。

---

### 附：二次复核清单（2026-09-20，应团队领导要求）

对报告里所有"声称某函数/常量/字段/行为存在或取某值"的陈述，逐条回 `src/` 指出处；
凡指不到出处的，按"**更正而非抹掉**"处理（见 A.3 / A.8 / D.2 三处删除线 + 更正）。

| 原陈述 | 复核结果 | 出处 |
| --- | --- | --- |
| 退出码由 `_EXIT_STATUS` 映射 `EXHAUSTED/COMPLETED/MAX_STEPS→0, DENIED→1, MODEL_UNAVAILABLE→2, CONFIG_ERROR/ASSEMBLY_ERROR→3, INTERRUPTED→130` | **失实，已更正（A.3）** | `cli/app.py:100-105,116-120` |
| 默认拒绝 ⇒ `DENIED` 退出码 1 | **失实，已更正（A.8）**：无 `DENIED` 退出码；拒绝是运行时 `allow=False`，最终按会话状态落 0/1/2 | `security/policy.py:188,208,226,254,264` + A.3 |
| 漏写超时参数 ⇒ 超时退出码 2 | **失实，已更正（D.2）**：请求超时 → `ModelUnavailableError` → `FAILED` → `EXIT_TASK_FAILED=1`；`2` 仅对应 `LIMIT_REACHED` | `model/client.py:335-340`、`harness/loop.py:447-463`、`cli/app.py:101,118,300` |
| `read_file`/`list_dir` 要求 `Capability.READ_FILE` | 成立 | `tools/files.py`（`requires_capability`） |
| 审计落盘于 `/root/.local/state/lowspec/audit/audit.jsonl` | 成立 | `foundation/config.py` + platformdirs 默认 |
| `TOOL_CALL.detail` 不记录参数、事件流 `tool_call` 不携带原始参数 | 成立（已补出处） | `contracts/harness.py:48,107` |
| `AuditOutcome.OK` 枚举存在 | 成立 | `contracts/audit.py:29,38` |
| `JsonlAuditSink(..., roots=(working_dir,))` 自落盘文件 `query_by_id` 复核 | 成立（与 `tests/integration` 的 `_audit_reader` 一致） | `tests/integration/test_end_to_end.py:749-756` |
| 默认 `model_request_timeout_s=60.0` | 成立 | `cli/app.py:161` |
| `pack` 须在受信根内，否则 `PathNotAllowedError`→退出码 3 | 成立（已实测复现） | `cli/app.py:23-24`（EXIT_ASSEMBLY=3） |

除以上三处失实已更正外，未再发现其它无出处的"事实性"陈述。

---

## 附：复跑产物位置（临时，不进仓库）
- 工作目录：`/tmp/tmp.FAjF2cKy4i`（哨兵文件 `hello.txt`/`notes.txt`、`pack.toml`、
  `.lowspec.toml`、`out.jsonl`、`err.log`、`verify_m21.py`、`stream_pretty.json`）。
- 审计：`/root/.local/state/lowspec/audit/audit.jsonl`（平台状态目录，含本会话 4 行事件）。
- 验证脚本 `verify_m21.py` 仅做核验用途，未提交仓库。

## 附：方法学自我审查
- 是否可复现：命令、cwd、模型路径与字节数、token/超时取值、端口均已逐字给出。
- 验证的是真实行为而非实现者声称：全程真实 `llama-server`+GGUF+落盘审计，未替换 sink/模型。
- 有无"无失败信号即判通过"：C.1 明确指出其不举证"≥2 调用"；D.2 明确列出不构成的性能/安全/稳定性结论。
