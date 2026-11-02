# 能力包内核 · 现场运行手册（Demo Runbook）

> **用途**：在一台干净环境里，用约 **5 分钟**（一键）或 **10 分钟**（手工）复现
> "方法论装进内核"的完整链路。
> **不依赖 GPU**；CPU 8 核即可。模型只用 2B，单次真跑约 4 分钟。
> 完整安装、移植与逐屏讲解见 [`capability-pack-manual.md`](capability-pack-manual.md)；
> 本文只保留现场最短路径。
> **前提**：分支 `feat/11-capability-pack-kernel` 已拉取；`make setup` 已执行。

---

## 0. 一键演示（推荐；2026-11-02 实测）

> 起点是**同一个裸 GGUF 模型**（没有工具、没有提示工程产物）；能力包把「现场 + 动作面 +
> 任务 + 判据」编译成一次可执行的动作面，再由既有 agent 框架执行、由内核算状态。

现场按四步讲：

1. **裸模型**：`/opt/models/MiniCPM5-2B-Q4_K_M.gguf` 只是一个 GGUF，没有工具；
2. **声明**：`case.toml` 写清现场、动作面、两阶段任务与判据（不是靠提示词堆出来的）；
3. **编译**：`pack build` → `verify`（逐文件 sha256 + 判据自证）→ `status=verified`；
4. **执行**：`pack run` → 11 条机检 → `status=evaluated`（证据落 `evidence/`）；
5. **审查**：`pack review --all` → 能力维度、失效归因、下一轮动作（报告落 `reports/`）。

```bash
# 演示前彩排（约 3 秒，不跑模型）：环境 + 构建 + 校验 + 状态 + 失败模式目录
scripts/pack-demo.sh --prepare

# 正式演示（本环境七次实测：249~257 s，均 11/11；当前思考预算 512）
scripts/pack-demo.sh
```

> ⚠️ `--prepare` 与正式演示都会 `--force` 重建 `BUNDLE`（默认 `/tmp/ip.bundle`），
> 会清掉上一次的 `evidence/`。要把某次结果留作对照，先
> `cp -a /tmp/ip.bundle /tmp/ip.bundle.keep`。
>
> **现场只剩 2 分钟**：不要现场跑模型；展示已保留的 `evidence/*.json` 与工作区
> `report.md` / `reasoning-*.md`，说明这是本环境 7 次实测之一。**不要**在展示前跑
> `--prepare` 把这份证据覆盖掉。

它执行的就是 §1~§4 的命令，并在 `run` 后调用 `pack review --all`；额外把三件
**与模型无关**的前提做成 fail-fast 断言
（这三件是现场最常见的"看起来像模型不行"）：

| 断言 | 不做的后果 |
| --- | --- |
| 8080 空闲 | 残留 `llama-server` ⇒ 新进程起不来。镜像里可能**既没有 `ss` 也没有 `netstat`**，脚本用 `connect` 兜底（本环境实测两者都缺） |
| 旧 bundle 用 `--force` 重建 | 复用旧目录 ⇒ `status` 早已是 `evaluated`，证明的不是"这一次跑的" |
| 模型 / 推理后端 / 依赖存在 | 失败信息看起来像"模型能力不足"，而不是环境没配好 |

退出码：`0` 成功 · `1` 机检未通过 · `2` 前置条件不满足（**没有**开始跑模型）· `3` 装配期故障。
其余手工步骤（含 `--json` 与失败路径）在下面 §1~§5，仍可单独执行。

---

## 0.1 先确认在哪条分支（10 秒）

```bash
git branch --show-current     # 期望：feat/11-capability-pack-kernel
git log --oneline -3
```

⚠️ 本手册的所有命令都**只读/只写 `/tmp` 与 bundle 目录**，不修改仓库内容；
若你发现自己在 `develop` 或 `main` 上，先切回分支再继续。

---

## 1. 环境自检（约 30 秒）

```bash
make setup          # 首次：装依赖 + 装钩子
agent-sec-perf doctor
```

期望：8 项里没有 `fail`；隔离一项是 **`! l1`**（3/8 维度有负向证据）——
**这是如实报告，不是故障**，现场照实说"应用级拦截 + 逐维度留痕"。

若 `agent-sec-perf` 不在 PATH，用等价写法：

```bash
uv run python -m agent_sec_perf.cli.app doctor
```

---

## 2. 构建 → 校验 → 状态（约 2 秒）

```bash
agent-sec-perf pack build examples/showcase/incident-01 \
  --out /tmp/ip.bundle --json

agent-sec-perf pack verify /tmp/ip.bundle --json

agent-sec-perf pack status /tmp/ip.bundle --json
```

期望：

| 命令 | 关键字段 |
| --- | --- |
| `build` | `ok=true`、`files=9`、`selftest.ok=true`（covered 4 / uncovered 7） |
| `verify` | `ok=true`、`problems=[]` |
| `status` | `stage="verified"`、`next_actions` 含 `run` |

**这一步就是方法论的第一层**：包不是"一个目录"，是**逐文件 sha256 + 判据自证**。

```bash
# 篡改演示（10 秒，可选）：改一个字节，verify 必须拒绝
cp -r /tmp/ip.bundle /tmp/ip.bundle.tampered
printf '\n# tampered\n' >> /tmp/ip.bundle.tampered/case/case.toml
agent-sec-perf pack verify /tmp/ip.bundle.tampered --json; echo "exit=$?"
```

期望：`ok=false`、`problems=["大小不一致：case/case.toml"]`、`exit=1`。

---

## 3. 方法论可见化（1 秒）

```bash
agent-sec-perf pack plan --json
```

期望：`catalog` 里 **`F1`~`F9`** 九条，每条含 `signal` / `action` / **`forbidden`**。
现场要讲的一句：

> 同一类失效只允许同一类结构性修法；"再改改提示"不在允许的动作里。

---

## 4. 真跑（约 4 分钟，MiniCPM5-2B）

```bash
agent-sec-perf pack run /tmp/ip.bundle \
  --model /opt/models/MiniCPM5-2B-Q4_K_M.gguf \
  --model-dir /opt/models --json --keep
```

期望：`ok=true`、**11/11 机检通过**、`evidence=/tmp/ip.bundle/evidence/...json`。
随后：

```bash
agent-sec-perf pack status /tmp/ip.bundle --json   # 期望 stage="evaluated"
```

`--keep` 会保留工作区，可以打开 `report.md` 看终稿四行、看 `reasoning-*.md`（思考全文）。

> **模型不在 `/opt/models` 时**：成功演示需要 **≥2B 且支持工具调用**的 GGUF。
> 不要用 1.7B/0.6B 顶替——它们会在本案例上失败（见 §5），那是另一段演示。

---

## 5. 失败路径（可选，约 3.5 分钟）

> ⚠️ **本镜像没有 0.6B/1.7B**（2026-11-02 核对 `/opt/models`：只有 AgentCPM-Explore、
> MiniCPM5-2B、Qwen3-4B、Qwen3-8B）。失败路径需要**自备**小模型；**不要**为了演示当场下载
> ——那会把 4 分钟的演示变成不可控的下载与验证。没有小模型就**跳过**本节。

```bash
agent-sec-perf pack run /tmp/ip.bundle \
  --model /tmp/models/Qwen3-0.6B-Q8_0.gguf --json
```

期望：`ok=false`、`exit=1`。现场只需指出两条：

1. `工具拒绝生效` 通过——工具当场拒绝了编造的取值（`':1000'` 不在现场文件里）；
2. `落盘报告` 失败——模型没写出来。

**这一刻要讲的是**："内核能给出否定结论，而且拒绝不是失败，是治理生效。"
（若要复现 1.7B 的 `F9`，命令同上，把模型换掉即可。）

---

## 6. 现场口径（能说什么 / 不能说什么）

| ✅ 可以说 | ❌ 不能说 |
| --- | --- |
| "这个任务在 2B 上通过了 11 条机检" | "2B 具备 agent 能力" |
| "目标档是 4B~14B（16 GB 卡）；27B 在 16 GB 上装不下" | "我们验证了 27B" |
| "判据自证覆盖内容维度 4 项，事件流 7 项未自证" | "全部判据都已验证" |
| "隔离是 L1：应用级拦截 + 逐维度如实报告" | "我们有沙箱" |
| "失败可归因到 `F1`~`F9`，修复动作是白名单" | "内核可以自动修复" |

---

## 7. 故障处置

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| `model 路径不存在` / 启动失败 | 模型不在该镜像 | 检查 `/opt/models`；或自备 ≥2B 工具调用模型 |
| `Address already in use`（8080） | 残留 `llama-server` | `pgrep -a llama-server`，确认后结束残留进程再跑 |
| `pack run` 退出 `2`（`LIMIT_REACHED`） | 模型反复调工具、步数耗尽 | 换 ≥2B 模型；这是 `F9`，不是环境故障 |
| `pack run` 退出 `3` | 案例/bundle/模型装配期故障 | 看 stderr 的类型与消息；不要回退默认值重试 |
| `verify` 报"多余文件" | bundle 被手工改过 | 重新 `build --force` |
| 超时 | `case.toml` 的 `request_timeout_s=600` 不够 | 换更快模型/量化；不要靠改判据绕过 |

---

## 8. 环境重启后的恢复（30 秒）

`/tmp` 会被清空，**bundle 不是仓库内容**，重建即可：

```bash
agent-sec-perf pack build examples/showcase/incident-01 --out /tmp/ip.bundle --json
agent-sec-perf pack verify /tmp/ip.bundle --json
```

`/tmp/models` 下的下载模型也会丢；`/opt/models` 里的镜像自带模型不会。

---

## 9. 验收清单（演示结束前逐条核对）

- [ ] `scripts/pack-demo.sh --prepare` 退出码 `0`，且输出 `阶段 verified`
- [ ] `pack build` 输出 `files=9` 且 `selftest.ok=true`
- [ ] 篡改后 `verify` 退出码 `1` 且指出文件
- [ ] `pack plan` 列出 `F1`~`F9`
- [ ] `pack run`（2B）**11/11**，`status` 为 `evaluated`
- [ ] `pack review --all` 生成报告，含 8 个维度、`findings`、`next_actions`
- [ ] 失败路径（0.6B/1.7B）如实给出 `ok=false`（**模型不在 `/opt/models` 时跳过**，不要临时下载顶替）
- [ ] 现场口径表没有说错一句
