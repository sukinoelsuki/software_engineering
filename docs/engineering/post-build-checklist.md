# 开发环境构建后待办清单

> **用途**：CNB 开发环境镜像构建完成后，按本清单顺序执行并**如实记录结果**。
> 清单约需 3~4 小时（其中 W1 风险验证占 3 小时时间盒）。
>
> **执行前提**：`.ide/Dockerfile` 已构建、开发环境已能进入。
> **记录位置**：结果写入 `docs/devlog/` 的新一篇，并回填
> [0006 的待验证表](../devlog/0006-2026-09-15-开发环境搭建与技术栈定案.md#5-问题与卡点)。

---

## ✅ 执行状态（2026-09-15）

**本清单已执行完毕**，过程与结论见
[开发日志 0007](../devlog/0007-2026-09-15-开发环境验证与W1风险验证.md)。

| 步骤 | 结果 |
| --- | --- |
| 第 0 步 镜像生效 | ✅ 生效（`code-server 1.137.0` / `llama-server` / `codebuddy 2.151.0`，单容器模式） |
| 第 1 步 组件自检 | ✅ 全部通过；扩展仅 `pylance` 缺失（已改 `pyright`）；主题 ID 已修正 |
| 第 2 步 沙箱可用性 | ❌ **bwrap 与 firejail 均不可用**（后者静默失效）→ **触发降级，见 [ADR-0006](../adr/0006-sandbox-execution-degradation.md)** |
| 第 3 步 工程门禁 | ✅ `make setup && make check` 全绿 |
| 第 4 步 W1 风险验证 | ✅ 完成（约 1.2 小时，未用满 3 小时时间盒）→ 结论见[研究笔记](../research/2026-09-15-w1-4b-model-capability.md) |
| 第 5 步 回填 | ✅ 见下方勾选项 |

> 下文保留清单原文与现场确认后的**实际命令与记录**，供再次执行时对照。

---

## 第 0 步：确认自定义镜像是否生效（最高优先级）

```bash
code-server --version
command -v llama-server
command -v codebuddy
```

| 结果 | 处理 |
| --- | --- |
| ✅ 三个命令都成功 | 自定义镜像生效，**继续第 1 步** |
| ❌ `llama-server` 不存在 | 镜像未生效 → **跳到「故障处理 A」** |
| ❌ `codebuddy` 不存在 | 镜像的 CodeBuddy 部分未生效 → 云开发入口页会**静默隐藏 CodeBuddy Web 入口**，跳到「故障处理 A」 |

> 这一步决定了后面所有验证是否有意义。若镜像没生效，先修镜像，不要往下做。

---

## 第 1 步：关键组件自检（约 1 分钟）

```bash
python -V                    # 期望 Python 3.12.x
uv --version                 # 期望有输出
node -v                      # 期望 v20.x
pnpm --version               # 期望 9.x
command -v bwrap             # 期望 /usr/bin/bwrap
command -v git-lfs           # 期望有输出
cmake --version | head -n1   # 期望有输出
codebuddy --version          # 期望 >= 2.137.0
code-server --list-extensions   # 核对扩展是否真的装上了（见下方说明）
```

| 组件 | 期望 | 实际 | 通过 |
| --- | --- | --- | --- |
| Python | 3.12.x | | ☐ |
| uv | 有版本输出 | | ☐ |
| Node | v20.x | | ☐ |
| pnpm | 9.x | | ☐ |
| bwrap | 有路径 | | ☐ |
| git-lfs | 有路径 | | ☐ |
| cmake | 有版本 | | ☐ |
| codebuddy | ≥ 2.137.0 | | ☐ |

> **关于扩展自检（待验证项 V-7）**：镜像构建时扩展安装是**失败不阻断**的，
> 因此清单里的每一项都要在环境内用 `code-server --list-extensions` 实际核对一次。
> 重点确认两项：
>
> 1. `tencent-cloud.coding-copilot`（CodeBuddy 扩展）——若缺失，说明 Open VSX 当时不可达，
>    重跑构建即可；
> 2. `ms-python.vscode-pylance` ——该扩展**未上架 Open VSX**（查询返回 404），
>    极可能一直静默缺失。若确认缺失，改用 `ms-pyright.pyright`（MIT、已上架 Open VSX），
>    并把结论回填到 `docs/devlog/0006` 的 V-7。

> **关于 settings.json（人工检查点）**：`.ide/settings.json` 是 JSONC，已从 `check-json`
> 钩子中排除（决策见 `docs/devlog/0006` §2.4.4）。这意味着它的格式合法性**不再有静态保障**——
> 一旦写错，表现为"设置静默不生效"而不是报错，很容易被忽略。
> 因此每次改动该文件后，都必须销毁并重启环境，确认主题与关键设置确实生效
> （相关待验证项：V-4）。

---

## 第 2 步：沙箱可用性（决定方案是否需要降级）

```bash
# 主方案：bubblewrap
bwrap --bind / / --tmpfs /tmp --proc /proc --dev /dev /bin/echo sandbox-ok

# 备选方案：firejail
firejail --quiet --noprofile /bin/echo sandbox-ok
```

| 命令 | 结果 | 通过 |
| --- | --- | --- |
| bwrap | 输出 `sandbox-ok` / 报错信息 | ☐ |
| firejail | 输出 `sandbox-ok` / 报错信息 | ☐ |

**判定**：

- ✅ 任一可用 → 沙箱方案可行，继续。
- ❌ 两者都失败（常见于容器禁用 unprivileged user namespaces）→
  **沙箱需降级**为「子进程 + 资源限制 + 路径白名单 + 人工确认」，
  并**新增 ADR 记录**该降级决策。

> ⚠️ **2026-09-15 实测结论（重要，勿凭退出码判断）**：
> `bwrap` 报 `Creating new namespace failed: Operation not permitted`（硬失败）；
> `firejail` **退出码为 0、输出正常**，但在加了 `--net=none` 的情况下
> `curl https://example.com` 仍返回 **HTTP 200**。去掉 `--quiet` 才看到
> `Warning: an existing sandbox was detected. ... will run without any additional sandboxing features`
> —— 即 **`firejail` 完全没做隔离，且是静默失效（fail-open）**。
> `unshare --user / --net / --mount` 三者亦全部 `EPERM`（内核参数
> `max_user_namespaces` 很大，限制来自容器 seccomp 而非内核开关）。
>
> **因此"命令成功"绝不能作为隔离生效的判据**；必须用主动探针
> （尝试越界操作并确认被拒绝）判定。处置见 [ADR-0006](../adr/0006-sandbox-execution-degradation.md)。

---

## 第 3 步：工程门禁

```bash
make setup && make check
```

| 结果 | 处理 |
| --- | --- |
| ✅ 全绿 | 工程基线可用，继续第 4 步 |
| ❌ 失败 | 记下错误信息 → 跳到「故障处理 C」 |

---

## 第 4 步：W1 风险验证（时间盒 3 小时）

> **这是本清单最重要的一步。** 它直接决定 R-1（4B 模型能力不足）是否触发。
> **超过 3 小时仍未完成则停止，先归档已有结论**，不要无限期投入。

### 4.1 准备模型

目标：拉取一个 **4B 级 Q4_K_M 量化的 GGUF**（约 2.4 GB）。

```bash
# 建议候选（仓库与文件名需在现场用 Hugging Face 页面确认后再填）：
#   - Qwen3-4B（通用强、中文好、原生支持工具调用）
#   - AgentCPM-Explore-4B（端侧智能体专用）
```

| 记录项 | 值（2026-09-15 实测） |
| --- | --- |
| 使用的模型仓库与文件名 | `Qwen/Qwen3-4B-GGUF` → `Qwen3-4B-Q4_K_M.gguf` |
| 实际文件体积 | **2.50 GB** |
| 下载耗时 | **383 s**（≈6.7 MB/s） |

**现场确认的可用命令**（环境内**没有** `hf` / `huggingface-cli`，故用 `curl` 直接取文件；
`llama-server` 自带 `-hf` 下载能力，但先落盘便于把"下载"与"加载"分开计时）：

```bash
mkdir -p /root/models && cd /root/models
curl -sSL --retry 3 -o Qwen3-4B-Q4_K_M.gguf \
  "https://huggingface.co/Qwen/Qwen3-4B-GGUF/resolve/main/Qwen3-4B-Q4_K_M.gguf"
```

> ⚠️ 模型落在**仓库之外**（`/root/models`），避免 2.5 GB 大文件误入版本管理。
> 校验方式：文件头魔数应为 `GGUF`（实测 `b'GGUF'`、version 3）。

### 4.2 启动服务并测量基础性能

**现场确认的启动命令**（`--reasoning off` 关闭 Qwen3 思考链以模拟低延迟档位；
`--no-webui` 减噪；`--metrics` 便于后续采集）：

```bash
llama-server -m /root/models/Qwen3-4B-Q4_K_M.gguf -c 4096 -t 8 -tb 8 \
  --host 127.0.0.1 --port 8080 --reasoning off --no-webui --metrics
```

就绪判据：轮询 `GET /health` 返回 `{"status":"ok"}`。

| 指标 | 记录（2026-09-15 实测） |
| --- | --- |
| 模型**加载耗时** | **1.73 ~ 1.75 s**（⚠️ **热缓存**——容器 `/proc/sys` 只读，无法 `drop_caches` 做冷启动对照） |
| **prefill（提示处理）** | **66.4 ~ 67.6 tok/s**（14.8 ~ 15.1 ms/token） |
| **生成速度** | **16.1 ~ 16.8 tok/s**（59.6 ~ 62.2 ms/token） |
| **常驻内存峰值** | 加载后 ≈ **4.58 GiB**；生成后 ≈ **4.95 GiB**（`VmHWM`） |
| CPU 核数 | 8 |

> 精确计时取自 `llama-server` 日志的 `slot print_timing` 行
> （`prompt eval time` / `eval time`），比在客户端测的端到端耗时更准确。

> 这些数据是"切换成本"估算的基础（见
> [0003 §6：切换成本 = 切换次数 × 上下文长度](../devlog/0003-2026-09-15-单专家调度与上下文迁移.md)）。

### 4.3 三个真实任务（能力边界探测）

| # | 任务 | 内容 | 成功 | 耗时 | 备注（2026-09-15 实测） |
| --- | --- | --- | --- | --- | --- |
| T1 | 加类型标注 | 给一个 30~50 行的函数加完整类型标注 | ✅ | 32.8 s | 行为等价、签名标注完备；`mypy --strict` 残留 1 处局部变量注解（`buckets = {}`） |
| T2 | 修复简单 bug | 给出含明显 bug 的代码与失败测试，要求修复 | ✅ | 11.9 s | 5/5 测试通过，修复为最小改动 `range(0, len(items), size)` |
| T3 | 生成单元测试 | 为一个纯函数生成单元测试 | ⚠️ | 29.6 s | 断言完全正确（分支覆盖 **100%**、杀死变异体），但**漏写 import**，交付即 `NameError` |

> **判据已强化**：三项任务均以**自动化**方式判定（AST / `mypy` / `pytest` / 覆盖率 / 变异测试），
> 不依赖人工打分。详见[研究笔记 §3](../research/2026-09-15-w1-4b-model-capability.md)与
> [逐字脚本附录](../research/reports/2026-09-15-w1/HARNESS.md)。

**每轮记录**：是否一次通过、重试次数、输出是否需要人工修改、失败时的具体表现（幻觉 / 格式错误 / 跑题）。

### 4.4 判定与结论

| 结果 | 处理 |
| --- | --- |
| 三个任务成功率可接受（可复现、可演示） | R-1 **未触发**，继续既定方案 |
| 明显不足（如成功率长期 < 30%） | R-1 **触发** → 收敛为"代码/文档问答 + 安全审查助手"，并新增 ADR |

**2026-09-15 判定**：一次通过率按口径为 **1/3（严格）= 33%**、**2/3（任务语义）= 67%**、
**3/3（允许一行级自动修补）= 100%**。最严口径**略高于** 30% 阈值
⇒ **R-1 未触发**，置信度**中**（每任务仅单次采样、且为 `--reasoning off` 单一档位）。

倾向性结论：**裸模型不够用，但"校验 + 自动修复环"可补齐**——
两次失败（漏 import、缺局部注解）都被一次客观检查捕获且可自动修复，
这直接支撑把 `REQ-HARNESS-07`（可验证任务自动校验）提前实现。

**无论结果好坏，结论都必须归档到 `docs/research/`**（记录环境、命令、原始数据、置信度）
→ 已归档至[研究笔记](../research/2026-09-15-w1-4b-model-capability.md)。

---

## 第 5 步：回填验证结果

- [x] 新建 `docs/devlog/0007-*.md` 记录本轮执行过程与结果
- [x] 回填 `docs/devlog/0006` 第 5 节的 V-1 ~ V-7 七项待验证结论（另补 V-8）
- [x] 若沙箱降级或 R-1 触发，**新增对应 ADR** → 沙箱降级已触发，产出 [ADR-0006](../adr/0006-sandbox-execution-degradation.md)（R-1 **未**触发）
- [x] 更新 `docs/requirements/srs.md` 中受影响的需求条目（`REQ-SEC-05`、假设项 A-2、风险 R-4）

---

## 故障处理

### A. 自定义镜像没生效

按顺序检查：

1. `.ide/Dockerfile` 是否在**仓库根目录**（`.ide/` 下）；
2. `.cnb.yml` 的 `vscode` 事件中，`build.by` 是否声明了 `.ide/settings.json`（**漏了会构建失败**）；
3. **`vscode` 事件的分支键**：当前挂在 `"**"` 下。
   按 CNB 触发规则，`$` 仅匹配"未被任何 glob 命中的分支"，而 `"**"` 命中所有分支，
   因此挂在 `$` 下可能不生效。若当前无效，**把 `vscode:` 从 `"**"` 挪到 `$` 下**；

   ```yaml
   # 改法：把 vscode 事件整体移到 $: 下
   $:
     vscode:
       - ...（内容不变）
   ```

4. 修改后必须：**销毁当前开发环境 → 重新启动**才会重建镜像。

补充：若第 0 步只有 `codebuddy` 缺失（`llama-server` 正常），说明是新增的第 5 阶段
（CodeBuddy 安装）没有生效，而非整个镜像没生效 —— 按上面 1~4 步同样处理。

### B. bwrap / firejail 都不可用

- 记录到 devlog，并**新增 ADR** 说明降级方案；
- 降级方案：子进程 + 资源限制（CPU/内存/时间）+ 路径白名单 + 危险操作人工确认；
- 同步更新 SRS 中 REQ-SEC-05 的验收标准。

### C. `make check` 失败

- 记录完整错误信息；
- 常见原因：依赖未安装完整、Python 版本不符、pre-commit 未初始化；
- 若属工具链配置问题，修改后**新增 ADR**（工具链变更属于需要记录的决策）。

---

## 附：本清单相关的开放决策

执行过程中若得到结论，请一并记录：

| 编号 | 问题 | 出处 |
| --- | --- | --- |
| Q-1 | 主线用户（受限环境工作者 / 数字能力受限人群） | SRS §12 |
| Q-3 | 用户是否应知道"换了专家" | devlog 0003 §8 |
| Q-4 | 送给专家的上下文切片给多少 | devlog 0003 §8 |
| Q-5 | 允许几个专家同时驻留 | devlog 0003 §8 |
| Q-7 | 主 Agent 路由准确率的下限 | devlog 0003 §8 |

完整清单见 [`docs/devlog/0005-待确认设计建议与开放问题.md`](../devlog/0005-2026-09-15-待确认设计建议与开放问题.md)。
