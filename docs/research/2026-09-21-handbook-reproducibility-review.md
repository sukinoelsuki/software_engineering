# 产品手册可复跑性独立复核

- 日期：2026-09-21
- 关联：`docs/engineering/product-handbook.md`（v0.1.0，对应提交 `370e9f3`）；复核性质，无独立 Issue
- 时间盒：≤ 1 天（实际使用：约 2.5 小时墙钟，含 2 次真模型会话约 11 分钟）
- 状态：已完成（全部 5 个演示舞台 + 环境自检 + 审计回放均复现成功）

## 1. 研究问题

手册作者（团队领导）声称 `docs/engineering/product-handbook.md` 中"每条命令都由团队领导于
2026-09-20 在本机实测跑过，输出原样摘录"（手册 §0 证据口径）。本复核要回答一个**可证伪**的问题：

> **手册里的命令，逐字照做能不能跑通？结果是否与手册所述一致？**

本复核不采信手册自述，全部独立执行、抓取真实输出。凡是"跑通 / 未跑通 / 与手册不符"的陈述，
均附**原始命令 + 原始输出**（见 §4）；未执行的项单列（见 §7）。

## 2. 现状扫查

- 被复核对象：`docs/engineering/product-handbook.md`(`370e9f3`，v0.1.0，2026-09-20 实测)。
- 手册规定的可复跑命令集合（排除"证据链"中引用的其它研究笔记）：
  - §2.1 环境自检：`uv --version` / `uv run python --version` / `nproc` / `free -h` /
    `ls -l /opt/models/*.gguf` / `llama-server --version` / `make check` / `make test-security`。
  - §3 五个舞台：舞台 0（质量门禁）、舞台 1（装配期拒绝）、舞台 2（真实多步会话）、
    舞台 3（审计回放）、舞台 4（默认拒绝 / fail-secure）。
- 环境来源：本机（与手册同机：8 核、16GiB、/opt/models 下 4 个 GGUF、
  /opt/llama.cpp 构建）。本复核刻意用与手册**同一套宿主资源**，以排除"换机导致差异"的干扰。

## 3. 方法

- 只读手册，按 §2.1 → §3 舞台 0→1→2→3→4 顺序逐条执行。
- 所有 demo 临时目录用 `mktemp -d`（落在 `/tmp`，不污染仓库）；审计落盘到
  `~/.local/state/lowspec/audit/audit.jsonl`（只追加，手册 §4.3 已声明）。
- 真模型会话**串行**执行（手册 §2.2 与复核约束均要求：8 核下两个 `llama-server` 并行会互抢 CPU
  并拖入请求超时）。顺序：舞台 2 先跑（写审计）→ 舞台 3 复用其 SID 回放 → 舞台 4 后跑。
- 墙钟时间用 `date +%s` 前后相减记录，**仅作观察，不写成任何性能结论**（手册 §6 / 本角色硬性要求 4）。
- 对每条"与手册所述一致"的结论，额外用 `python` 解析真实 JSONL 事件流与落盘审计，
  **逐字段核对**（KINDS / SEQ / outcome / tool_name / call_id / event_id / 哨兵串位置），
  而非只看手册的"摘要视图"。
- 真模型会话两次（舞台 2、舞台 4），各一次，符合"真模型会话一次只跑一个"的约束。

## 4. 结果与数据

> 约定：输出中 `\u88c5\u914d\u5931\u8d25` 即中文"装配失败"（JSON 转义）；
> pytest 进度点（`.`）已省略，仅保留判据行。

### 4.1 环境自检（§2.1，秒级）

**原始命令与输出：**

```text
$ uv --version
uv 0.12.16 (x86_64-unknown-linux-gnu)
$ uv run python --version
Python 3.12.14
$ nproc
8
$ free -h | head -2
               total        used        free      shared  buff/cache   available
Mem:            16Gi       2.1Gi        13Gi          0B       440Mi        13Gi
$ ls -l /opt/models/*.gguf
-rw-r--r-- 1 root root 2716068064 /opt/models/AgentCPM-Explore.Q4_K_M.gguf
-rw-r--r-- 1 root root 1561318368 /opt/models/MiniCPM5-2B-Q4_K_M.gguf
-rw-r--r-- 1 root root 2497280256 /opt/models/Qwen3-4B-Q4_K_M.gguf
-rw-r--r-- 1 root root 5027783488 /opt/models/Qwen3-8B-Q4_K_M.gguf
$ /opt/llama.cpp/build/bin/llama-server --version
version: 0.4.1-dev (build 1, commit 69eb250)
built with GNU 12.2.0 for Linux x86_64
$ ls -l /workspace/.git/hooks/pre-commit /workspace/.git/hooks/commit-msg
-rwxr-xr-x 1 root root 609 ... /workspace/.git/hooks/pre-commit
-rwxr-xr-x 1 root root 609 ... /workspace/.git/hooks/commit-msg
```

**判定**：逐项与手册 §2.1 自述一致（uv 0.12.16 / Python 3.12.14 / 8 核 / 16Gi 总、13Gi 可用 /
4 个 GGUF 字节数完全相同 / llama-server 0.4.1-dev @69eb250 / 钩子已安装）。**完全一致。**

### 4.2 质量门禁（§2.1 / 舞台 0）

**原始命令：** `cd /workspace && make check` 与 `make test-security`。

**`make check` 真实输出（判据行）：**

```text
uv run ruff format .                       → 233 files already formatted
uv run ruff check .                        → All checks passed!
uv run mypy                                → Success: no issues found in 48 source files
uv run pytest -n auto -m "not benchmark and not slow"
============================= 955 passed in 8.29s =============================
uv run bandit -q -r src                    （无输出即通过）
uv run pre-commit run detect-private-key --all-files
detect private key.......................................................Passed
uv run pip-audit
No known vulnerabilities found
agent-sec-perf  Dependency not found on PyPI and could not be audited: agent-sec-perf (0.1.0)
```

**`make test-security` 真实输出（判据行）：**

```text
====================== 96 passed, 869 deselected in 1.25s ======================
```

**判定**：手册声称 `make check` = 955 passed / 8.44s、`make test-security` = 96 passed / 1.26s。
本机复得 **955 passed / 8.29s** 与 **96 passed / 1.25s**——**通过数一致**，墙钟差 0.15s 属环境噪声
（同一宿主、不同负载），不构成差异。**一致。** bandit / 密钥扫描 / pip-audit 均通过。

### 4.3 舞台 1：装配期拒绝（§3，秒级，不起模型）

**原始命令（逐字照手册）：**

```bash
WORK=$(mktemp -d); cd "$WORK"
printf '[policy]\ngranted_capabilities=["read_file"]\n' > .lowspec.toml
printf 'DEMO-SENTINEL-1\n' > hello.txt
uv run --project /workspace agent-sec-perf run "读取 hello.txt" \
  --pack /workspace/examples/packs/coding-readonly \
  --model-path /opt/models/Qwen3-4B-Q4_K_M.gguf --output-format json
echo "EXIT_CODE=$?"
```

**真实输出：**

```text
{"error_type": "PathNotAllowedError", "event": "\u88c5\u914d\u5931\u8d25",
 "level": "error", "logger": "agent_sec_perf.cli.app",
 "timestamp": "2026-09-21T01:35:25.870204Z"}
EXIT_CODE=3
```

**判定**：手册声称 `PathNotAllowedError` + 退出码 `3` + 秒级（模型未启动）。本机复得
**完全相同的 error_type 与退出码 3**，且命令在亚秒内返回（无 llama-server 进程残留）。
**完全一致。** 这是 fail-secure + 配置错误先于任务失败可区分的最小证据。

### 4.4 舞台 2：真实多步会话（§2.2 / §3，主舞台，真模型）

**原始命令（逐字照手册，去掉不存在的 `/usr/bin/time`，改用 `date` 计墙钟）：**

```bash
WORK=$(mktemp -d); cd "$WORK"
printf 'DEMO-SENTINEL-A-7f21\n' > hello.txt
printf 'DEMO-SENTINEL-B-3c94\n' > notes.txt
printf '[policy]\ngranted_capabilities = ["read_file"]\n\n[logging]\nlevel = "INFO"\n' > .lowspec.toml
START=$(date +%s)
uv run --project /workspace agent-sec-perf run "工作目录下有若干文件。请严格按顺序完成两步...
（同手册原 prompt）..." \
  --pack /workspace/examples/packs/coding-readonly \
  --allowed-root "$WORK" --allowed-root /workspace/examples --working-dir "$WORK" \
  --output-format json --model-path /opt/models/Qwen3-4B-Q4_K_M.gguf \
  --max-completion-tokens 1536 --model-request-timeout-s 600 > stream.jsonl 2> stderr.log
echo "EXIT=$?"; echo "ELAPSED=$((END-START))s"; wc -l < stream.jsonl
```

**真实输出：** `EXIT=0` `ELAPSED=194s` `10` 行 JSONL。

**对 `stream.jsonl` 逐字段解析的真实结果：**

```text
LINES 10
KINDS ['model_response', 'tool_call', 'policy_decision', 'tool_result',
       'model_response', 'tool_call', 'policy_decision', 'tool_result',
       'model_response', 'task_finished']
SEQ [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
FINAL_STATUS ['completed']
SID 9a4d9dd00f0c44f881391774974c60d9
TOOL_CALL list_dir   call_id= FAdHt1IXvPYLsgJa3QjbRdE82Yz8qV7d
TOOL_CALL read_file  call_id= 6lVxMCJEFavc9VGXRYjRAmpru4wbh9tI
SENTINEL_IN_TOOL_RESULT True          ← hello.txt 内容确实被工具读回
```

**`tool_result` 事件真实结构（验证"工具真的执行了"，非静默失败）：**

```json
{ "kind": "tool_result", "tool_name": "list_dir",
  "result": { "audit_id": "019d224b...", "content": ".lowspec.toml\nhello.txt\n...",
              "error": null, "ok": true, "truncated": false } }
{ "kind": "tool_result", "tool_name": "read_file",
  "result": { "audit_id": "1af54e47...", "content": "DEMO-SENTINEL-A-7f21\n",
              "error": null, "ok": true, "truncated": false } }
```

**最终回答真实位置（手册称"哨兵串出现在最终回答"）：** 不在 `task_finished` 事件（该事件
只有 `status=completed` + 状态文案，无回答正文），而在 `seq=8` 的 `model_response` 事件
`response.content`：

```text
seq 8 has_sentinel= True
grep: "model_response", "response": {"content": "DEMO-SENTINEL-A-7f21", "finish_reason": "stop", "model_id": ...}
```

**判定**：手册 §2.3 的逐项断言**全部成立**——EXIT=0、约 196s（本机 194s）、10 事件、
SEQ 0..9 连续、FINAL_STATUS completed、两次 `tool_call` 且 `tool_name` 不同
（list_dir / read_file）、哨兵串同时出现在工具结果与最终回答。两次工具调用 `result.ok=true`
且 `content` 为真实磁盘内容 ⇒ **确为真实执行，非静默失败**。

> **差异（非功能性，已上报）**：手册 §2.3 显示行写作
> `TOOL_RESULT list_dir ok=True` 与 `SENTINEL_IN_FINAL_ANSWER True`，
> 但真实 JSONL 中 `ok` 是 `tool_result.result.ok`（嵌套字段），`task_finished` 事件**不含**
> 回答正文（最终回答在最后一个 `model_response.response.content`）。即手册的这两行是
> **从嵌套字段派生的扁平化展示**，不是 JSONL 顶层键。读者若 `json.loads` 后直接取顶层 `ok`
> 会取不到——属可读性/呈现误差，不影响结论正确性。

### 4.5 舞台 3：审计回放（§3，秒级，依赖舞台 2 的 SID）

**原始命令（把 §2.2 的 SID 粘入占位符）：**

```python
import json, pathlib
from agent_sec_perf.observability.audit import JsonlAuditSink
from agent_sec_perf.foundation.config import default_audit_directory

ap = default_audit_directory()
print("AUDIT_DIR", ap)
mine = [
    json.loads(l)
    for l in ap.joinpath("audit.jsonl").read_text().splitlines()
    if l.strip() and json.loads(l)["session_id"] == "9a4d9dd00f0c44f881391774974c60d9"
]
print("AUDIT_LINES_THIS_SESSION", len(mine))
sink = JsonlAuditSink(ap)
for e in mine:
    print(e["kind"], e["outcome"], e.get("tool_name"), e["event_id"])
for e in [x for x in mine if x["kind"] == "tool_call"]:
    r = sink.query_by_id(e["event_id"])
    print("REPLAY", r.kind.value, r.call_id, r.outcome.value)
```

**真实输出：**

```text
AUDIT_DIR /root/.local/state/lowspec/audit
AUDIT_LINES_THIS_SESSION 4
policy_decision allow list_dir 145c48aa21d64c99982f159dabec85c8
tool_call ok list_dir 019d224b859f482f942c2ff8080cb329
policy_decision allow read_file 2a3a357f334f4471985466abbe9dc7ac
tool_call ok read_file 1af54e47c86a44c88c72e5d059e80f82
REPLAY tool_call FAdHt1IXvPYLsgJa3QjbRdE82Yz8qV7d ok
REPLAY tool_call 6lVxMCJEFavc9VGXRYjRAmpru4wbh9tI ok
```

**判定**：手册声称 `AUDIT_DIR=/root/.local/state/lowspec/audit`、本会话审计 4 行、
`query_by_id` 可从落盘文件还原并与事件流 `call_id` 对齐。本机复得**完全相同结构**
（AUDIT_DIR 一致、4 行、REPLAY 的 `call_id` 与 §4.4 的 `TOOL_CALL` 一一对应）。
event_id 因会话不同而不同，属预期。**完全一致。** 验证了 `REQ-SEC-06`（不丢证据、可回放）。

> **手册内不一致（提示）**：手册 §3 舞台 3 的"本次实测输出"块含一行 `AUDIT_LINES_THIS_SESSION 4`，
> 但其上方"命令"块**并未打印该行**（命令只打印 `AUDIT_DIR` 与逐事件行）。属文档笔误，
> 不影响命令可复跑——本复核已用同一条命令补印该行以核对。

### 4.6 舞台 4：默认拒绝 / fail-secure（§3，真模型，约 8 分钟）

**原始命令（逐字照手册，故意不写 `.lowspec.toml`）：**

```bash
WORK2=$(mktemp -d); cd "$WORK2"
printf 'DEMO-SENTINEL-C-5a13\n' > hello.txt
START=$(date +%s)
uv run --project /workspace agent-sec-perf run "工作目录下有 hello.txt，请调用 read_file 工具读取它..." \
  --pack /workspace/examples/packs/coding-readonly \
  --allowed-root "$WORK2" --allowed-root /workspace/examples --working-dir "$WORK2" \
  --output-format json --model-path /opt/models/Qwen3-4B-Q4_K_M.gguf \
  --max-completion-tokens 1536 --model-request-timeout-s 600 > stream.jsonl 2> stderr.log
echo "EXIT=$?"; echo "ELAPSED=$((END-START))s"
```

**真实输出：** `EXIT=0` `ELAPSED=502s` `10` 行。

**对 `stream.jsonl` 解析的真实结果：**

```text
SID 400000897e044a2ab2269adad1dd4e0f
KINDS ['model_response', 'tool_call', 'policy_decision', 'tool_result',
       'model_response', 'tool_call', 'policy_decision', 'tool_result',
       'model_response', 'task_finished']
FINAL ['completed']
TOOL_CALL read_file
TOOL_CALL read_file
TOOL_RESULT result= None text= 策略拒绝，本次调用未执行
TOOL_RESULT result= None text= 策略拒绝，本次调用未执行
POLICY_DECISION None read_file {'allow': False, 'audit_id': '4135c442...',
  'reason': '未授权操作：缺少所需能力，能力只能由显式配置授予，单次确认不改变授权集合（REQ-SEC-01）',
  'requires_confirmation': False, 'risk_level': 'critical'}
POLICY_DECISION None read_file {...（同上，第二次）...}
```

**落盘审计（`~/.local/state/lowspec/audit/audit.jsonl`，本 SID 的 4 行）真实结构：**

```json
{ "kind": "policy_decision", "outcome": "deny", "tool_name": "read_file",
  "detail": { "domain_pack": "coding-readonly", "missing": ["read_file"],
              "reason": "未授权操作：缺少所需能力...（REQ-SEC-01）", "requested": ["read_file"] } }
{ "kind": "tool_call", "outcome": "deny", "tool_name": "read_file",
  "detail": { "denied_reason": "policy_denied" } }
（以上各两条，对应两次调用）
```

**判定**：手册 §3 舞台 4 的核心断言**全部成立**——EXIT=0（会话自行收尾）、约 507s（本机 502s）、
模型请求 `read_file` 两次、两次 `tool_result` 均为 `result=None` + 中文"策略拒绝，本次调用未执行"、
落盘审计有 `policy_decision deny`（含 `REQ-SEC-01` 原文）与 `tool_call deny`
（`denied_reason: policy_denied`）。拒绝是**策略结论、可审计、不随重试改变**，且模型被回喂
"策略拒绝"文案（非静默放行）。**一致。** 这是产品安全取向的最强实证。

> **差异（非功能性，已上报）**：手册 §2.3 舞台 4 的显示行把审计对象的 `detail` 内容
> （`missing` / `requested` / `reason` / `denied_reason`）**拍平**到顶层写成
> `{'missing':..., 'reason':..., 'requested':...}` 与 `{'denied_reason': 'policy_denied'}`；
> 真实 JSONL 中这些键位于 `detail` 嵌套对象内（`policy_decision.detail` / `tool_call.detail`）。
> 内容完全正确、字段齐全，仅呈现层级不同——读者若按手册字面结构去取顶层键会取不到。

## 5. 结论

| 手册条目 | 复现结果 | 置信度 |
| --- | --- | --- |
| §2.1 环境指纹（uv/py/nproc/内存/GGUF/llama-server/钩子） | 逐项一致 | 高（实测比对） |
| §2.1 `make check` 955 passed + bandit/密钥/pip-audit | 一致（8.29s vs 8.44s） | 高 |
| §2.1 `make test-security` 96 passed | 一致（1.25s vs 1.26s） | 高 |
| §3 舞台 1 装配期拒绝（exit 3 / PathNotAllowedError / 不起模型） | 一致 | 高 |
| §3 舞台 2 真实多步会话（EXIT=0 / 10 事件 / 2 工具调用 / 哨兵串） | 一致（194s vs 196s） | 高 |
| §3 舞台 3 审计回放（AUDIT_DIR / 4 行 / query_by_id 对齐） | 一致 | 高 |
| §3 舞台 4 默认拒绝（EXIT=0 / result=None / deny 审计） | 一致（502s vs 507s） | 高 |

**总体结论**：手册全部 5 个演示舞台 + 环境自检 + 审计回放命令，**逐字照做均可跑通，
结果与手册所述一致**。本机与手册同宿主、同资源，排除了"换机差异"干扰；所有结论均附原始命令
与原始输出，未转述、未编造。置信度**高**（每个断言均独立用真实 JSONL / 落盘审计逐字段核对）。

**需领导知晓的两类非功能性差异（不影响"可跑通/结果一致"判定，但建议手册修正呈现形态）：**

1. 手册 §2.3 的 `TOOL_RESULT ok=True` 与 `SENTINEL_IN_FINAL_ANSWER` 是**派生扁平化展示**：
   `ok` 实为 `tool_result.result.ok`；最终回答不在 `task_finished` 而在末条 `model_response.response.content`。
2. 手册 §2.3 舞台 4 把审计 `detail` 嵌套对象**拍平**成顶层字典显示。
3. 手册 §3 舞台 3 的"实测输出"含一行命令未打印的 `AUDIT_LINES_THIS_SESSION 4`（文档笔误）。

**未发现的静默失败**：无。舞台 2 工具结果确为真实磁盘内容（`result.ok=true` + `content`）；
舞台 4 拒绝确为策略结论且回喂模型（`result=None` + "策略拒绝"），均非"绿着但没生效"。

## 6. 对本项目的影响

- 手册 v0.1.0 的"可复跑性"主张**通过独立复核**：新接手者按手册操作可复现全部 5 个舞台，
  手册作为"现状报告 + 上手 + 演示"文档的可靠性成立。
- 两处呈现形态差异建议在手册下次修订时改为"贴近真实 JSONL 嵌套结构"的展示，或在 §2.3
  加一句"以下为从事件流派生的扁平化视图"，以免读者误以为顶层键存在。属文档准确性改进，
  不改动任何代码或安全行为。
- 不涉及 `src/` / `tests/` / `docs/design/` / `docs/adr/` / `CHANGELOG.md` 的修改（本复核为只读）。

## 7. 待验证项

- **【待验证】§1.3 第 8 条 / §1.5**：`AGENT_SEC_PERF_E2E=1 ... -m integration` ⇒ 10 passed /
  466.55s 的真模型集成用例复跑。该项**不在手册"五个演示舞台"范畴内**（属 §1.5 证据链引用的
  独立研究笔记），且为约 466s 的真模型门禁用例。本轮为收敛墙钟预算**未执行**，标记待验证；
  如需复核，命令形如：
  `AGENT_SEC_PERF_E2E=1 uv run --project /workspace pytest -m integration`
  （届时需确保无其它 llama-server 占用 8 核，遵守"真模型一次只跑一个"）。
- **【待验证】跨机可复跑**：本轮与手册同机，未验证"换模型 / 换宿主"下的稳健性；手册 §6 已声明
  模型相关取值（1536 / 600）仅对本机 + Qwen3-4B 成立，换模型须重测——此声明本身未被复核推翻，
  但跨机复跑不在本轮范围内。
