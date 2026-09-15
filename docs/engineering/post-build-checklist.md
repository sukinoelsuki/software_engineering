# 开发环境构建后待办清单

> **用途**：CNB 开发环境镜像构建完成后，按本清单顺序执行并**如实记录结果**。
> 清单约需 3~4 小时（其中 W1 风险验证占 3 小时时间盒）。
>
> **执行前提**：`.ide/Dockerfile` 已构建、开发环境已能进入。
> **记录位置**：结果写入 `docs/devlog/` 的新一篇，并回填
> [0006 的待验证表](../devlog/0006-2026-09-15-开发环境搭建与技术栈定案.md#5-问题与卡点)。
>
> **当前进度（2026-09-15）**：第 0、1、2、3 步 **已完成**，
> 结果见 [`docs/devlog/0007`](../devlog/0007-2026-09-15-开发环境验证与沙箱方案修正.md)；
> **剩余第 4 步（W1 风险验证）未执行**，第 5 步部分完成。
> 本清单已按实测结果修正了两处判定标准（第 1 步的扩展核对、第 2 步的沙箱判定）。

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

> **关于扩展自检（V-7 已结案，2026-09-15）**：镜像构建时扩展安装是**失败不阻断**的，
> 因此清单里的每一项都要在环境内用 `code-server --list-extensions` 实际核对一次。
> **核对方法必须是"集合比对"，不是"有没有报错"**——实测的核对结果：
>
> | 项 | 结论 |
> | --- | --- |
> | `tencent-cloud.coding-copilot`（CodeBuddy 扩展） | ✅ 已装（缺失说明 Open VSX 当时不可达，重跑构建即可） |
> | `ms-python.vscode-pylance` | ❌ **确认缺失**（Open VSX 返回 404，包不在其上架列表中）→ 已改用 `ms-pyright.pyright` |
> | 请求 17 个 vs 实装 18 个 | 差额来自两个**被依赖自动带入**的扩展（`ms-python.vscode-python-envs`、`ms-azuretools.vscode-containers`），已核对无误 |
>
> 证据与完整清单见 [`docs/devlog/0007`](../devlog/0007-2026-09-15-开发环境验证与沙箱方案修正.md) §4.3。

> **关于 settings.json（人工检查点）**：`.ide/settings.json` 是 JSONC，已从 `check-json`
> 钩子中排除（决策见 `docs/devlog/0006` §2.4.4）。这意味着它的格式合法性**不再有静态保障**——
> 一旦写错，表现为"设置静默不生效"而不是报错，很容易被忽略。
> 因此每次改动该文件后，都必须销毁并重启环境，确认主题与关键设置确实生效
> （相关待验证项：V-4）。

---

## 第 2 步：沙箱可用性（决定方案是否需要降级）

> **本步已于 2026-09-15 执行完毕，结论：namespace 型沙箱全线不可用，
> 方案已改为容器边界（[ADR-0006](../adr/0006-sandbox-isolation-strategy.md)）。**
> 下方命令保留，供将来更换运行平台时重新评估；**但判定标准已修正**（见文末）。

```bash
# 原主方案：bubblewrap
bwrap --bind / / --tmpfs /tmp --proc /proc --dev /dev /bin/echo sandbox-ok

# 原备选方案：firejail
firejail --quiet --noprofile /bin/echo sandbox-ok
```

⚠️ **不要用退出码判断沙箱是否生效**。实测：`firejail` 输出 `sandbox-ok`、退出码 0，
但去掉 `--quiet` 后它会打印：

```text
Warning: an existing sandbox was detected. /bin/sh will run without any additional sandboxing features
```

即它在检测到"已在沙箱中"后**放弃全部加固并直接执行**——这是一个**假阳性**。

**正确的验证方法：证明隔离"确实发生"**（任选其一，或都做）：

```bash
# 方法 1：对比 namespace inode（宿主 vs 沙箱内），完全一致即"没有隔离"
for n in user mnt pid net ipc uts; do
  printf '%-4s host=%s sandbox=%s\n' "$n" \
    "$(readlink /proc/self/ns/$n)" \
    "$(firejail --quiet --noprofile /bin/sh -c "readlink /proc/self/ns/$n")"
done

# 方法 2：直接探测内核是否允许创建命名空间（本平台返回 EPERM）
python3 -c "import ctypes,os; libc=ctypes.CDLL('libc.so.6',use_errno=True); \
r=libc.unshare(0x10000000); print('unshare(CLONE_NEWUSER) ->', r, os.strerror(ctypes.get_errno()) if r else 'OK')"

# 方法 3（当前采用的方案）：以容器为边界，验证加固参数逐项生效
docker run --rm --network none --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=64m \
  --cap-drop=ALL --security-opt=no-new-privileges \
  --pids-limit=64 --memory=256m -u 65534:65534 alpine:latest \
  sh -c 'id; touch /x || echo "根只读 ✓"; wget -T3 -q -O- http://example.com || echo "无网络 ✓"'
```

**判定**：

- ✅ 沙箱内 namespace inode 与宿主**不同**，或"方法 3"的四项加固全部生效 → 隔离成立，继续。
- ⚠️ 有输出但 inode **完全相同** → **视为不可用**（假阳性），按失败处理。
- ❌ 不可用（本平台即如此：容器禁用了嵌套命名空间且无 `CAP_SYS_ADMIN`，
  Landlock 又因内核 5.4 不可用）→ **改用容器边界**；
  若连 Docker 也不可得，才降级为「子进程 + 资源限制 + 路径白名单 + 人工确认」，
  并**新增 ADR 记录**该降级决策。

> **降级绝不能静默发生**：隔离不可用时的默认行为必须是**拒绝执行**（fail-secure），
> 而不是"悄悄换成无隔离执行"。

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

| 记录项 | 值 |
| --- | --- |
| 使用的模型仓库与文件名 | |
| 实际文件体积 | GB |
| 下载耗时 | |

> ⚠️ 具体命令（如 `llama-server -hf <repo>` 或 `huggingface-cli download`）**请在现场确认可用形式**，
> 不要凭猜测执行；确认后请把命令补回本文件。

### 4.2 启动服务并测量基础性能

```bash
# 启动后测量（具体启动参数按 llama.cpp 版本确认）
```

| 指标 | 记录 |
| --- | --- |
| 模型**加载耗时** | s |
| 首次 **prefill 耗时**（约 512 token 上下文） | s |
| **生成速度** | tok/s |
| **常驻内存峰值** | GB |
| CPU 核数 | |

> 这些数据是"切换成本"估算的基础（见
> [0003 §6：切换成本 = 切换次数 × 上下文长度](../devlog/0003-2026-09-15-单专家调度与上下文迁移.md)）。

### 4.3 三个真实任务（能力边界探测）

| # | 任务 | 内容 | 成功 | 耗时 | 备注 |
| --- | --- | --- | --- | --- | --- |
| T1 | 加类型标注 | 给一个 30~50 行的函数加完整类型标注 | ☐ | | |
| T2 | 修复简单 bug | 给出含明显 bug 的代码与失败测试，要求修复 | ☐ | | |
| T3 | 生成单元测试 | 为一个纯函数生成单元测试 | ☐ | | |

**每轮记录**：是否一次通过、重试次数、输出是否需要人工修改、失败时的具体表现（幻觉 / 格式错误 / 跑题）。

### 4.4 判定与结论

| 结果 | 处理 |
| --- | --- |
| 三个任务成功率可接受（可复现、可演示） | R-1 **未触发**，继续既定方案 |
| 明显不足（如成功率长期 < 30%） | R-1 **触发** → 收敛为"代码/文档问答 + 安全审查助手"，并新增 ADR |

**无论结果好坏，结论都必须归档到 `docs/research/`**（记录环境、命令、原始数据、置信度）。

---

## 第 5 步：回填验证结果

- [x] 新建 `docs/devlog/0007-*.md` 记录本轮执行过程与结果
- [x] 回填 `docs/devlog/0006` 第 5 节的 V-1 ~ V-8 待验证结论（并新增 V-9：firejail 假阳性）
- [x] 沙箱方案变更 → **新增 [ADR-0006](../adr/0006-sandbox-isolation-strategy.md)**（状态：提议中，待所有者确认）
- [ ] 更新 `docs/requirements/srs.md` 中受影响的需求条目（含 REQ-SEC-05 的验收标准）
- [ ] ADR-0006 确认后，更新 `docs/design/threat-model/` 中"命令执行"相关威胁条目

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

**本平台已命中该分支（2026-09-15）**，处置如下：

- 记录到 devlog（[0007](../devlog/0007-2026-09-15-开发环境验证与沙箱方案修正.md) §4.6 / §4.7），
  并**新增 ADR**（[ADR-0006](../adr/0006-sandbox-isolation-strategy.md)）；
- 第一顺位方案改为**容器边界**（Docker 一次性容器 + 加固参数）；
- 只有在容器也不可得时，才降级为「子进程 + 资源限制（CPU/内存/时间）+ 路径白名单 + 危险操作人工确认」；
- **降级必须显式**：默认行为是**拒绝执行**，禁止静默以无隔离方式执行；
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
