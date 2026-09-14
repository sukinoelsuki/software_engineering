# 0003. 项目定案提案：低资源本地编码代理（LowSpec Coding Agent）

- 状态：**待决策**（本提案给出完整的项目定义与范围边界）
- 日期：2026-09-14
- 关联：Issue #3、[提案 0002](0002-project-direction-landscape.md)、
  [调研笔记：CNB 环境与额度](../research/2026-09-14-cnb-quota-and-hardware.md)

---

## 1. 为什么要再次修正方向

提案 0002 推荐了"低资源安全 Agent Harness"（方向 1）。项目所有者提出了一个关键质疑：

> "单纯安全方向的 AI Harness 看上去有点像一个简单的插件。我能不能做成一个
> **小设备上直接能 vibe coding 的、功能更丰富的 Ollama**，这样就像产品了？"

**这个质疑成立，本提案采纳它并给出更精确的定义。**

原方向的问题不在于技术，而在于**产品形态**：

| 问题 | 说明 |
| --- | --- |
| 没有用户可见的产品形态 | "安全护栏"是属性，不是产品；无法演示"用户用它做了什么" |
| 安全是"附加"而非"必需" | 自己造一个 Harness 再加护栏，护栏看起来是可选装饰 |
| 软件工程过程难以充分体现 | 缺少安装、配置、版本、用户交互、发布等环节 |

---

## 2. 关键判断：换成编码代理后，两条主线从"附加"变成"内在需求"

这是本提案最重要的论证。**不是换个场景，而是换了一种问题结构。**

### 2.1 安全：从"可以加"变成"不加就不敢用"

本地编码代理的核心能力是**在这台机器上执行 AI 生成的代码与命令**。而这恰好构成了
Simon Willison 提出的 **致命三要素（Lethal Trifecta）**：

```text
        ① 访问私有数据（你的代码仓库、密钥文件、环境变量）
                    +
        ② 处理不可信内容（AI 读进来的文件、依赖、README、issue 内容）
                    +
        ③ 具备对外通信能力（网络请求、包安装、git push）
                    ↓
              = 数据外泄 / 任意代码执行
```

**具体攻击场景（真实存在，不是假想）**：

| 场景 | 攻击路径 |
| --- | --- |
| 间接提示注入 | 仓库里一个依赖的 README 或源码注释中藏有指令，代理读到后把 `.env` 发送到外部地址 |
| 命令注入 | 模型生成的 `rm -rf` / `curl \| sh` / 覆盖 `.git` 等危险命令被执行 |
| 供应链 | MCP 工具描述被投毒，代理调用时泄露上下文 |
| 越权写盘 | 代理修改了项目目录之外的文件（如 `~/.ssh/config`） |

因此**这一层的安全不是"插件"，而是产品的准入条件**——用户不会用一个"AI 能随便在你机器上跑命令"的工具。

### 2.2 效率：从"优化指标"变成"能不能用"

| 约束 | 后果 |
| --- | --- |
| 4B 级模型 + 8 GB 内存/显存 | 无法把整个仓库塞进上下文 |
| 小模型的工具调用能力弱 | 工具一多就乱 → 必须**动态裁剪工具** |
| 长会话上下文膨胀 | 不压缩会直接 OOM 或严重掉点 |
| CPU 推理速度有限 | 每一轮 token 都要省，否则用户等不起 |

**结论：在低资源场景下，"上下文与工具的效率工程"不是加分项，而是产品能否可用的分水岭。**
这正是提案 0002 中 ⭐ 上下文管理 与 ⭐ 工具裁剪 两个模块的价值所在——只是现在它们有了
具体的产品载体。

---

## 3. 定位澄清：不是"功能更丰富的 Ollama"

这个类比需要修正，否则会低估工作量、也会混淆产品边界：

| 类比 | 它实际负责什么 | 与本项目的关系 |
| --- | --- | --- |
| Ollama / llama.cpp | **模型运行时**：下载、量化、加载、推理、API | 本项目**复用**它，不重造 |
| Claude Code / Cursor / Aider | **编码代理（Harness）**：读代码、规划、改文件、跑命令 | 本项目**要做的核心** |
| nsjail / bubblewrap / LLM Guard | **执行隔离与内容防护** | 本项目**集成并编排**它们 |

**更准确的定位**：

> 一个**面向低资源设备（8 GB 级）**、**以安全执行为前提**、**可审计**的
> **本地终端编码代理**。它自带模型运行时（所以开箱可用），但不以模型运行时为卖点。

---

## 4. 生态空白点（本项目的差异化依据）

现有关键项目对照（仓库均已联网核实存在）：

| 项目 | 定位 | 面向低资源设备 | 执行安全设计 | 形态 |
| --- | --- | --- | --- | --- |
| [ollama/ollama](https://github.com/ollama/ollama) · [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp) | 模型运行时 | ✅ | ❌（不管 agent 行为） | CLI / 服务 |
| [Aider-AI/aider](https://github.com/Aider-AI/aider) | CLI 编码代理 | ⚠️ 部分 | ⚠️ 弱（有确认机制） | CLI |
| [cline/cline](https://github.com/cline/cline) · [RooVetGit/Roo-Cline](https://github.com/RooVetGit/Roo-Cline) | IDE 编码代理 | ❌（假设强模型） | ⚠️ 中（人工确认） | VS Code 插件 |
| [continuedev/continue](https://github.com/continuedev/continue) | IDE 助手 | ⚠️ 部分 | ⚠️ 弱 | IDE 插件 |
| [block/goose](https://github.com/block/goose) | 终端 agent | ⚠️ 部分 | ⚠️ 中 | CLI |
| [OpenHands/OpenHands](https://github.com/OpenHands/OpenHands) | 全功能 agent 平台 | ❌ | ✅ 中（容器沙箱） | Web / CLI |
| [sst/opencode](https://github.com/sst/opencode) · [charmbracelet/crush](https://github.com/charmbracelet/crush) | 终端 agent | ❌ | ⚠️ 中 | TUI |
| [open-webui/open-webui](https://github.com/open-webui/open-webui) | 聊天界面 | ✅ | ❌ | Web |

**空白点**：**「低资源可用」+「执行安全优先」+「可审计」+「端到端本地」** 四者同时满足的
终端编码代理，目前没有成熟项目。

- 强模型路线的项目（Cline/OpenHands/OpenCode）**不针对 8 GB 设备设计**，
  它们默认上下文窗口与推理速度充足；
- 低资源路线的项目（Ollama/llama.cpp）**只解决推理，不解决执行安全**。

> ⚠️ 该空白点结论基于公开资料的横向对比，**属于【待验证】**：
> 实施前应再对上述 2~3 个项目做一次源码级确认（它们是否已有类似能力）。

---

## 5. 项目定义

**名称（暂定）**：`lowspec-agent`（项目代号 TBD）

**一句话**：让 8 GB 级设备也能跑一个**安全、可控、可审计**的本地 AI 编码代理。

### 5.1 四层架构

```text
┌─────────────────────────────────────────────────────────────┐
│ L4 交互层    CLI / TUI · 非交互模式 · 审计查看器              │  ← 复用 + 自研
├─────────────────────────────────────────────────────────────┤
│ L3 安全执行层 权限策略 · 沙箱 · 注入防护 · 审计               │  ← 自研（亮点）
├─────────────────────────────────────────────────────────────┤
│ L2 代理核心    ReAct 循环 · 上下文引擎 · 工具 · 会话/检查点    │  ← 自研（核心）
├─────────────────────────────────────────────────────────────┤
│ L1 模型运行时  模型管理 · 量化选择 · llama.cpp 服务 · API     │  ← 复用 + 薄封装
└─────────────────────────────────────────────────────────────┘
```

### 5.2 复用清单（约 70%）

| 层 | 复用项目 | 用途 |
| --- | --- | --- |
| 推理 | [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp) | GGUF 推理 + OpenAI 兼容 server |
| 模型分发 | [huggingface/huggingface_hub](https://github.com/huggingface/huggingface_hub) | 模型下载与缓存 |
| 代码解析 | [tree-sitter/tree-sitter](https://github.com/tree-sitter/tree-sitter) | 仓库索引 / repo map |
| 工具协议 | [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) · [servers](https://github.com/modelcontextprotocol/servers) | MCP 工具接入 |
| 沙箱 | [google/nsjail](https://github.com/google/nsjail) · [containers/bubblewrap](https://github.com/containers/bubblewrap) | 命令执行隔离（**无需 KVM**） |
| 内容防护 | [protectai/llm-guard](https://github.com/protectai/llm-guard) | 注入/PII/敏感内容扫描 |
| 策略 | [casbin/casbin](https://github.com/casbin/casbin) 或自研声明式策略 | 权限模型 |
| CLI/TUI | [tiangolo/typer](https://github.com/tiangolo/typer) · [Textualize/textual](https://github.com/Textualize/textual) · [prompt-toolkit/python-prompt-toolkit](https://github.com/prompt-toolkit/python-prompt-toolkit) | 命令行与终端界面 |
| 可观测 | [langfuse/langfuse](https://github.com/langfuse/langfuse)（可选自托管） | 调用追踪 |
| 红队验证 | [NVIDIA/garak](https://github.com/NVIDIA/garak) · [promptfoo/promptfoo](https://github.com/promptfoo/promptfoo) | 对抗测试与回归 |
| 打包 | [astral-sh/uv](https://github.com/astral-sh/uv) · [pypa/pipx](https://github.com/pypa/pipx) | 依赖与分发 |

### 5.3 自研增量（约 30%，即"自己的有意义的工作"）

| # | 模块 | 主线 | 可验证指标 |
| --- | --- | --- | --- |
| 1 | **低配友好的模型管理器**：GGUF 发现/下载、按内存预算推荐量化档、模型热切换与释放 | 体验/效率 | 在 8 GB 设备上按预算自动选择可加载模型，选错率 0 |
| 2 | **上下文引擎**：tree-sitter 仓库索引 + 就地检索 + 压缩/观察屏蔽 + 工具动态裁剪 | 效率 | 同等任务成功率下 token 消耗 ↓ ≥ 30%，P95 延迟 ↓ ≥ 25% |
| 3 | **安全执行层**：能力/权限模型（default-deny）、命令风险分级、沙箱执行、注入防护、**审计与回放** | 安全 | 预定义危险场景集拦截率 100%，且每次拒绝均产生可回放审计记录 |
| 4 | **低配评估集**：小模型工具调用基准 + 对抗语料 + 回归门禁 | 工程 | 可复现的基线数据，纳入 CI |

### 5.4 参考项目（**读源码学架构，不作为依赖**）

| 项目 | 值得借鉴的点 |
| --- | --- |
| [Aider-AI/aider](https://github.com/Aider-AI/aider) | **repo map 设计**、diff 格式（小模型友好）、提交规范自动化 —— 最值得精读 |
| [cline/cline](https://github.com/cline/cline) · [RooVetGit/Roo-Cline](https://github.com/RooVetGit/Roo-Cline) | 权限确认交互（allow/deny/always）、工具设计 |
| [block/goose](https://github.com/block/goose) | 终端 agent + MCP 集成方式 |
| [OpenHands/OpenHands](https://github.com/OpenHands/OpenHands) | 沙箱执行架构、agent 平台分层 |
| [sst/opencode](https://github.com/sst/opencode) · [charmbracelet/crush](https://github.com/charmbracelet/crush) | 终端 agent 的 TUI 形态 |
| [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) | 插件化 Harness 架构（MIT）；**处于 dev preview，仅参考** |

---

## 6. 产品化要素（"完整感"的具体来源）

要让评审者一眼看出"这是一套完整软件"，以下要素必须齐备：

| 类别 | 具体内容 |
| --- | --- |
| **安装** | `pipx install lowspec-agent` 或 `uv tool install`，一条命令可用 |
| **模型管理** | `lowspec model list / pull / rm / info`，显示内存占用估算 |
| **核心命令** | `lowspec chat`（交互）、`lowspec do "<任务>"`（一次性）、`lowspec review`（代码审查） |
| **非交互模式** | 可脚本化、可进 CI（`--yes` / `--output-format json`） |
| **配置系统** | `~/.lowspec/config.toml` + 项目级 `.lowspec.toml`；**权限策略文件可读可审** |
| **权限交互** | 危险命令触发确认：`allow once / allow always / deny`，并说明风险理由 |
| **审计** | `lowspec audit` 查看历史操作、被拒绝的操作、注入拦截记录 |
| **会话** | 持久化 + `--resume`；上下文用量可视化 |
| **基准** | `lowspec bench` 跑内置评估集，输出结构化报告 |
| **文档** | README + `docs/`（需求/设计/威胁模型/使用/开发），与本仓库现有结构一致 |
| **发布** | SemVer 版本、CHANGELOG、带注释标签、可复现构建 |

> **这就是"软件工程"的体现**：不是把功能堆出来，而是把**安装—配置—使用—审计—评估—发布**
> 整条产品链路做完整。

---

## 7. 范围边界（Must / Should / Won't）

| 级别 | 内容 |
| --- | --- |
| **Must** | ① llama.cpp + 4B 模型跑通，模型管理器可用<br>② CLI 交互：读文件、改文件、跑命令、多轮对话<br>③ 上下文引擎 v1：仓库索引 + 相关性检索<br>④ 权限模型 + 危险命令确认 + 审计日志<br>⑤ 沙箱内执行命令（nsjail/bubblewrap）<br>⑥ 单元/集成/安全测试；`make check` 全绿<br>⑦ 文档：需求、设计、威胁模型、使用说明 |
| **Should** | ⑧ 上下文压缩与观察屏蔽（带基准数据）<br>⑨ 工具动态裁剪<br>⑩ MCP 工具接入<br>⑪ TUI（Textual）<br>⑫ `lowspec bench` + 评估集<br>⑬ garak/promptfoo 对抗回归<br>⑭ Langfuse 追踪接入 |
| **Won't** | ✗ IDE 插件（VS Code / JetBrains）<br>✗ 自研代码编辑器或 GUI 富客户端<br>✗ 模型训练 / 微调<br>✗ 多智能体协作<br>✗ KVM / microVM 沙箱<br>✗ 云端服务与账号体系<br>✗ 多人协作 / 团队功能 |

**Won't 列表是硬边界。** 任何"顺手加一下"的想法一律进入 backlog 并显式记录，不在本期内实现。

---

## 8. 风险（诚实评估）

| 编号 | 风险 | 影响 | 概率 | 应对 |
| --- | --- | --- | --- | --- |
| R-1 | **4B 模型在编码任务上能力不足** | 高 | **中高** | ① 限定任务域：单文件修改、小函数、测试生成、文档，而非"从零写功能"；② 用工程手段补偿（精准上下文 + 小模型友好 diff 格式）；③ 若效果仍不可接受，把定位收敛为"**代码库问答 + 安全审查助手**" |
| R-2 | 范围膨胀 | 高 | 高 | 严格执行第 7 节 Won't 列表；单周迭代 + 3 天卡点规则 |
| R-3 | CNB 容器内 nsjail/bubblewrap 不可用 | 中 | 中 | 提前验证（V-4）；退化为"子进程 + 资源限制 + 路径白名单 + 人工确认" |
| R-4 | 小模型工具调用不稳定，评估集难建立基线 | 中 | 中 | 评估集设计为**分级任务**（L1 单步 → L3 多步），先建立 L1 基线 |
| R-5 | 时间不足（存在其他事务） | 高 | 中 | Must 项优先；Should 项可裁；W8 必须冻结功能进入交付 |

> **R-1 是本项目最大的不确定性**，必须在最早的迭代中验证，而不是最后。

---

## 9. 迭代计划（8 周 + 2 周缓冲）

| 周 | 目标 | 出口判据 |
| --- | --- | --- |
| **W1** | 打通最小闭环 | llama.cpp + Qwen3-4B 可调用；`lowspec chat` 能多轮对话 |
| **W1** | ⚠️ **风险验证（提前）** | 用 3 个真实小任务测 4B 模型改代码的可行边界，结论归档 → 决定是否触发 R-1 应对 |
| W2 | 模型管理器 | `model list/pull/rm` + 内存预算推荐 |
| W3 | 代理核心 | ReAct 循环、文件读写工具、检查点、错误处理 |
| W4 | **上下文引擎 v1** | tree-sitter 索引 + 相关性检索；在真实仓库上定位准确 |
| W5 | **安全执行层** | 权限模型、危险命令分级、确认交互、审计日志 |
| W6 | 沙箱 + 注入防护 | nsjail 执行；提示注入用例拦截 |
| W7 | 效率主线 + 基准 | 压缩/观察屏蔽/工具裁剪；产出前后对比数据 |
| W8 | 对抗测试与加固 | garak/promptfoo 回归；威胁模型逐条验证 |
| W9–10 | **交付（缓冲）** | 文档齐备、`make check` 全绿、v1.0.0 发布、演示材料 |

---

## 10. 待确认

| 编号 | 待确认 | 影响 |
| --- | --- | --- |
| Q-1 | 是否采纳本提案（替代提案 0002 的方向 1） | 决定后续全部工作 |
| Q-2 | 目标模型：Qwen3-4B（通用）还是其他 | 影响 R-1 的实际严重程度 |
| Q-3 | 任务域是否接受"收敛为单文件/小范围修改 + 代码问答" | 直接影响可完成性 |
| Q-4 | 是否接受 CLI 优先、TUI/Web 作为 Should | 影响交付形态 |

---

## 11. 下一步

1. 确认第 10 节；
2. **立即执行 W1 的风险验证（时间盒 3 小时）**：
   - 在 CNB 云原生开发环境安装 llama.cpp，拉取 Qwen3-4B（Q4_K_M）；
   - 测三个真实任务：① 给一个函数加类型标注 ② 修复一个简单 bug ③ 生成一个单元测试；
   - 记录：成功率、耗时、显存/内存占用、生成质量的主观评价；
   - **结论写入 `docs/research/`（无论好坏）**。
   这一步直接决定 R-1 是否触发，**必须先做**。
3. 依据验证结论冻结决策 → 改写 `docs/adr/0003-select-base-project.md`；
4. 进入 Phase 1：需求规格（SRS）、架构设计、威胁模型、模块接口定义。
