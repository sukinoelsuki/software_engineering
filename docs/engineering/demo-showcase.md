# 成果展示运行手册（CLI + 端侧 agent 案例）

- **面向**：项目所有者（演示者）；也供任何接手的人照着重跑。
- **案例设计（为什么这样设计、判据从哪来）**：[`../design/showcase-incident-triage.md`](../design/showcase-incident-triage.md)。
- **产品手册（全部命令与排错）**：[`product-handbook.md`](product-handbook.md)。
- **口径**：本文件里每条命令都在 2026-10-26 的云环境实跑过；**未实测的命令不写**。

---

## 0. 一页纸：今晚怎么讲

> **一句话**：端侧 4B 模型在"只读、只授权一个能力、只允许两个目录"的受限环境里，
> 自己读现场日志、自己对照配置，给出**每个值都能从原文复核**的结论；
> 它读了几次、被放行了什么、结果是什么，全部可从审计文件回放。

| 时间 | 动作 | 目的 |
| --- | --- | --- |
| 0:00–0:30 | `agent-sec-perf`（无参数） | 展示引导：产品知道自己该被怎么用 |
| 0:30–1:40 | `agent-sec-perf doctor` | 展示体检 + **隔离矩阵**（L1 的负向证据） |
| 1:40–2:10 | `agent-sec-perf demo` | 秒级准备隔离工作区，展示允许根与领域包 |
| 2:10–10:30 | `agent-sec-perf demo --run` | **主舞台**：真跑 + 8 条机检（本次实测 **489 s**） |
| 10:30–11:30 | 读机检表 + 审计回放 | 证明"跑对了"而不是"跑完了" |
| 11:30–12:30 | 默认拒绝对照（可选） | 证明"没有授权就真的做不了" |

**总时长约 12 分钟**（真跑 **489 s**，2026-10-26 本机实测；机器负载不同会有浮动）。
若只有 5 分钟：**提前跑一次并用 `--keep` 保留工作区**，现场只讲
`doctor → 机检表 → 审计回放`，把真跑说成"已跑过的证据"，不要现场等 8 分钟。

---

## 1. 演示前 10 分钟：三条检查

```bash
cd /workspace
agent-sec-perf doctor            # 必须没有 fail；warn 允许（隔离 L1）
agent-sec-perf demo              # 秒级准备；确认列出 4 个文件
```

第三条（可选，彩排一次真跑）：

```bash
agent-sec-perf demo --run --workspace /tmp/lowspec-showcase-xxx/workspace
```

⚠️ **不要**在演示前重建镜像或改 `.ide/Dockerfile`：镜像重建约 20 分钟，且与演示无关。

---

## 2. 六幕脚本（每幕：命令 → 看什么 → 口播）

### 第 1 幕：首屏引导（40 秒）

```bash
agent-sec-perf
```

**看什么**：四步路径 `doctor → demo → demo --run → manual`。

**口播**：
> 我们没有做前端，因为今晚要证明的是"受限环境里的 agent 能不能干活"，不是"界面好不好看"。
> 但 CLI 不能是一堆参数——所以第一屏给的是**下一步做什么**，不是选项清单。

### 第 2 幕：体检与隔离矩阵（80 秒）

```bash
agent-sec-perf doctor
```

**看什么**：8 项检查；最后一项「隔离能力」是 `L1`，并且写着"3/8 个维度有负向证据"。

需要展开证据时：

```bash
agent-sec-perf doctor --json | python3 -c "
import json,sys
d=json.load(sys.stdin)
for k,v in d['isolation']['dimensions'].items():
    print(f\"{k:15} enforced={v['enforced']!s:5} {v['mechanism']:10} {v['evidence']}\")
"
```

**口播**（这一段是安全主线的核心，**不要**说"我们已经隔离了文件系统和网络"）：
> 这里报告的隔离是**逐维度、有负向证据**的：`user` 维度是子进程回读 uid=65534；
> `address_space` 是"分配 3 GiB 被拒绝"；`file_size` 是"写 65 MiB 被拒绝"。
> 文件系统和网络这两个维度**没有内核级隔离**——我们试过 bwrap，内核禁用 user namespace；
> 也试过 firejail，它是 fail-open（退出码 0 但网络仍通），所以我们**不采用、也不宣称**。
> 这两项在 L1 下靠的是"路径白名单 + 默认拒绝 + 审计"，而且这个降级事实会写进审计。

**为什么这段话值钱**：它把"没做到"说清楚，同时证明"我们知道怎么判断做到没做到"。

### 第 3 幕：准备隔离工作区（40 秒）

```bash
agent-sec-perf demo
```

**看什么**：工作区路径、4 个文件（`.lowspec.toml` / `README.md` / `app.log` / `service.toml`）、
下一步命令。

**口播**：
> 演示现场在临时目录里，允许根只有"这个工作区"和"案例目录"。
> 授权集合是写死的 `read_file`，不读用户级配置——**演示的权限不会比标准配置更大**。

### 第 4 幕：主舞台 · 真跑（约 8 分钟；本次实测 489 s）

用第 3 幕打印的命令：

```bash
agent-sec-perf demo --run --workspace <第 3 幕打印的工作区>
```

**看什么**（逐行出现）：

```text
→ 调用 read_file          ← 第一次取证：app.log
✓ 结果 read_file
→ 调用 read_file          ← 第二次取证：service.toml
✓ 结果 read_file
· 模型：CODE=E42 ... CONFIG_TIMEOUT_MS=8000 ...
· 终态：completed
```

**口播**（趁模型在跑）：
> 注意这不是"一次问答"：模型先决定读日志，拿到结果后再决定读配置，
> 两次调用都经过参数校验、策略求值和审计。我们用的是 4B 模型、CPU 推理、无网络。

### 第 5 幕：机检表（60 秒）

**看什么**：8 条全绿，重点讲三条：

| 判据 | 讲什么 |
| --- | --- |
| 工具调用 ≥ 2 次 | "它真的读了两个文件，不是编的" |
| 结论事实命中 `E42/3000/8000` | "每个值都能回原文核对" |
| 审计回放 2/2 | "从磁盘证据还原每一次调用" |

**口播**（讲第一版失败最有说服力）：
> 第一版任务里，模型只读了一个文件就编了一个配置值 `5000`。人眼看格式完全正确。
> 是机检把它拦下来的——所以"可验证"不是加分项，是**唯一能区分'会说话'和'读过文件'**的东西。

### 第 6 幕（可选）：默认拒绝对照（60 秒，**秒级**，不起模型）

```bash
W=$(mktemp -d); cd "$W"; printf 'DEMO\n' > hello.txt
cd /workspace && agent-sec-perf run "读取 hello.txt" \
  --pack examples/showcase/incident-01/pack \
  --allowed-root "$W" --allowed-root /workspace/examples \
  --working-dir "$W" --model-path /opt/models/Qwen3-4B-Q4_K_M.gguf
```

**看什么**：`拒绝：read_file（风险 critical）— 未授权操作...`，以及审计里的 `policy_decision deny`。
（该目录没有 `.lowspec.toml` ⇒ 默认不授予任何能力。）

**口播**：
> 同一个工具、同一个模型，只因为**人没有在配置里授予能力**，它就不能执行。
> 拒绝不是"模型失败"，也不是"执行失败"——它是策略结论，并且有审计。

⚠️ 这一幕会**启动模型**（等一次往返，约 1~2 分钟）。时间不够就只讲第 5 幕的审计。

---

## 3. 隔离叙事：能说什么、不能说什么

| ✅ 可以说 | ❌ 不可以说 |
| --- | --- |
| 非特权 uid + 资源上限 + 最小环境**已生效**（有负向探针） | "我们做了完整的沙箱隔离" |
| 文件系统/网络在 L1 下是**应用级拦截**（白名单 + 默认拒绝 + 审计） | "文件系统和网络已经被内核隔离" |
| 命名空间后端在本环境**不可用**，firejail **fail-open**，因此停在 L1 | "装了 bwrap 就等于有沙箱" |
| 降级事实会写进审计（`execution_degradation`） | "降级是静默的/无所谓的" |

---

## 4. 故障处置（按出现顺序）

| 现象 | 最可能原因 | 当场处置 |
| --- | --- | --- |
| `doctor` 报「推理后端 fail」 | PATH 无 llama-server | 用 `--model-binary /opt/llama.cpp/build/bin/llama-server`，或 `export PATH=/opt/llama.cpp/build/bin:$PATH` |
| `doctor` 报「端侧模型 fail」 | 模型不在 `/opt/models` | `--model-dir` 指向实际目录 |
| `demo` 报「演示无法开始：…没有可用的 .gguf」 | 同上 | 同上；或 `--model <路径>` |
| 真跑超过 6 分钟仍无输出 | 机器负载高 / 模型更大 | 等；`--max-completion-tokens` 已由案例固定 1536；**不要**中途 Ctrl-C（会丢审计） |
| 机检「工具调用 fail」 | 模型只读了一个文件 | 这是**真实失败**，照实讲"机检拦住了跳步"，再重跑一次 |
| 机检「结论事实 fail」 | 模型幻觉 | 同上；这正是机检存在的理由 |
| 退出码 `3` | 装配期失败（路径/配置/模型） | 看 stderr 的 `error_type`；先跑 `doctor` |
| 审计回放 fail | 审计目录不可写 | 看 `doctor` 的「审计落点」；`ls -l ~/.local/state/lowspec/audit` |

---

## 5. 备用素材（网络/模型出问题时）

1. **不需要模型**：`doctor`、`demo`、`manual` 三幕都能跑，仍能讲清"引导 + 体检 + 隔离矩阵"。
2. **已有实测证据**：`docs/devlog/0031-*.md` 记录了一次 8/8 通过的事件流与审计行数。
3. **默认拒绝**：第 6 幕即使模型没起来，`policy_decision deny` 仍会在装配后立刻出现（不需要等补全）。

---

## 6. 实测记录

| 时点 | 命令 | 结果 |
| --- | --- | --- |
| 2026-10-26 | `agent-sec-perf doctor` | 8 项：7 ok / 1 warn（隔离 L1）；负向证据维度 `user` / `address_space` / `file_size` |
| 2026-10-26 | `agent-sec-perf demo` | 秒级；工作区 4 个文件 |
| 2026-10-26 | `agent-sec-perf demo --run`（第一版任务） | **失败**：1 次工具调用 + 幻觉值 `5000`；机检 3 条 fail |
| 2026-10-26 | `agent-sec-perf demo --run`（加强任务后） | **8/8 通过**：10 条事件、`seq` 0..9、2 次 `read_file` 均 ok、结论命中 `E42/3000/8000`、审计 4 行、2/2 可回放、降级已留痕 |
| 2026-10-26 | `agent-sec-perf demo --run --keep`（第三次，门禁全绿后） | **8/8 通过**；墙钟 **489 s**；工作区保留供核对 |
| 2026-10-26 | `make check` | **退出码 0**：1003 passed / 1 skipped；bandit / 密钥扫描 / pip-audit 全过 |

⚠️ 墙钟是**观测值**，不构成性能结论（`REQ-PERF-*` 仍未测）。
