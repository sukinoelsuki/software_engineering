# 0002. 项目方向与开源组件全景（第二轮调研）

- 状态：**已被 [ADR-0003](../adr/0003-select-base-project.md) 采纳（2026-09-14）**——
  推荐的"方向 1：低资源安全 Agent Harness"被采纳为项目主线，
  并由 [提案 0003](0003-lowspec-coding-agent.md) 细化为最终产品定义（见 ADR-0003 §5.1）。
  本文为论证过程记录。（状态同步：2026-09-18）
- 日期：2026-09-14
- 关联：Issue #3、[提案 0001](0001-base-project-selection.md)、[调研笔记：CNB 环境与额度](../research/2026-09-14-cnb-quota-and-hardware.md)

---

## 1. 定位修正（先对齐认知）

提案 0001 的方向假设有偏差，本节先修正。

| 项 | 提案 0001 的（错误）假设 | 修正后的定位 |
| --- | --- | --- |
| 工作方式 | fork 一个前沿项目，重写/重构其内核 | **组装**：挑选多个成熟开源组件，集成为自己的软件系统 |
| 自身工作量占比 | 大量自研内核代码 | **约 30% 是自研增量**，70% 是集成与工程化 |
| 项目形态 | "改造 AIOS 内核" | "用开源积木搭出**自己的完整软件**，并对其中 2~3 个模块做有意义的改进" |
| 首要目标 | 研究价值优先 | **完成交付优先**，研究价值与工程成长在可完成的前提下争取 |

**核心约束（新增，且优先级最高）**：

> 项目所有者是本科大四学生，**同时有其他目标**，可用时间约 2~3 个月。
> 因此所有方案必须满足：**在 2~3 个月内可以完整交付**，而不是"有可能做出好东西"。
> 任何需要从零自研核心系统、或依赖不稳定上游、或需要付费算力的方案，**直接排除**。

---

## 2. 约束与目标

### 硬约束

| 编号 | 约束 | 说明 |
| --- | --- | --- |
| C-1 | **必须能完成** | 首要目标。宁可小而完整，不要大而残缺 |
| C-2 | 周期 2~3 个月，单人 | 且存在其他事务占用时间 |
| C-3 | **不产生费用** | CNB 的 GPU 无免费额度（0.5 元/核时），因此**不使用 GPU 依赖的方案** |
| C-4 | 无 KVM 依赖 | CNB 为容器环境；本地 VMware 嵌套虚拟化能力不确定 |
| C-5 | 以集成开源组件为主 | 自研部分控制在可独立验证的 2~3 个模块 |
| C-6 | 需体现软件工程全过程 | 需求 → 设计 → 实现 → 测试 → 发布，有文档与证据链 |

### 目标

| 编号 | 目标 | 权重 |
| --- | --- | --- |
| G-1 | 交付一个**能跑、能演示、有文档**的完整软件 | 最高 |
| G-2 | 在**安全**方向做出可验证的增量 | 高 |
| G-3 | 在**效率/性能**方向做出可验证的增量 | 高 |
| G-4 | 学习前沿 AI 生态，提升工程能力 | 高 |
| G-5 | 产出一定的"研究性"证据（调研、对比、基准） | 中 |

---

## 3. 关键范式发现：Agent Harness

调研中最有价值的发现：**2026 年 AI 工程的核心范式已经从"堆模型"转向"造 Harness"**。

> **Agent = Model + Harness**
> "如果你不是模型，你就是 harness。" —— LangChain, Vivek Trivedy

这与项目所有者的直觉完全一致（"模型日新月异，但总需要系统，需要 Harness 来发挥最大功效"）。
**这为项目提供了现成的、前沿的、且不需要自研核心系统的定位。**

### 3.1 支撑证据

| 证据 | 内容 |
| --- | --- |
| LangChain 实验 | 不改模型、只改 Harness 架构，TerminalBench 2.0 排名从 30 名开外升至第 5 |
| ARC-AGI-3 | 仅通过 harness 优化，得分从 13.3% 提升到 38.3% |
| Anthropic 实践 | 用 `claude.md` + `.claude/` 做记忆；把权限执行与模型推理**彻底解耦** |
| 效率研究（ACON） | 优先保留推理痕迹而非原始工具输出，可在保持 95%+ 准确率的同时**减少 26%~54% token 消耗** |
| 工具裁剪（Vercel） | 砍掉 80% 工具后效果**变好**；Claude Code 用懒加载实现 **95% 上下文缩减** |
| 错误放大效应 | 10 步流程每步 99% 成功率，端到端只有约 **90.4%** |

> 来源：《万字讲透 Agent Harness 的十二大模块》（2026-04-19，53AI 转载 Akshay Pachaar）
> <https://www.53ai.com/news/langchain/2026041962051.html>（访问日期 2026-09-14）

### 3.2 Harness 的十二大模块（项目边界参考）

| # | 模块 | 与本项目的关系 |
| --- | --- | --- |
| 1 | 编排循环（Orchestration Loop） | 自研（核心，但结构简单——"笨循环"） |
| 2 | 工具（Tools） | **复用** MCP 生态 |
| 3 | 记忆（Memory） | 复用向量库 + 自研轻量策略 |
| 4 | **上下文管理（Context Management）** | ⭐ **自研增量点（效率主线）** |
| 5 | 提示词组装（Prompt Assembly） | 自研（轻量） |
| 6 | 工具调用与结构化输出 | 复用（依赖模型原生 tool calling + Pydantic） |
| 7 | 状态与检查点（Checkpointing） | 复用（SQLite / 简单实现） |
| 8 | 错误处理（Error Handling） | 自研（轻量，但很重要） |
| 9 | **护栏（Guardrails）** | ⭐ **自研增量点（安全主线）** |
| 10 | 验证与反馈（Verification） | 自研（评估集） |
| 11 | 子 Agent 编排 | **明确不做**（范围外） |
| 12 | 初始化与环境搭建 | 自研（轻量） |

**结论**：Harness 的十二个模块中，真正需要自研的是 ①②⑤⑦⑧⑩⑫（且都不复杂），
而 ⭐ **④ 上下文管理** 与 ⭐ **⑨ 护栏** 正好落在项目的两条主线上——
这是一个**天然的、可完成的**项目边界。

---

## 4. 开源组件全景图（全部已联网核实存在）

> 核验方式：对每个仓库执行 `git ls-remote`，确认可访问。核验日期 2026-09-14，共 54 个仓库全部通过。

### 4.1 推理运行时（本地、CPU 友好）

| 项目 | 定位 | 本项目用途 |
| --- | --- | --- |
| [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp) | CPU/GPU 推理引擎，GGUF 格式，自带 OpenAI 兼容 server | **主推理后端**（CPU 可跑，无 GPU 依赖） |
| [ollama/ollama](https://github.com/ollama/ollama) | 模型管理与服务封装，最易用 | 备选/开发期便利 |
| [mudler/LocalAI](https://github.com/mudler/LocalAI) | 多后端统一 OpenAI API 网关 | 备选（统一 API 层） |
| [Mozilla-Ocho/llamafile](https://github.com/Mozilla-Ocho/llamafile) | 单文件可执行分发 | 演示打包可选 |

### 4.2 目标模型（4B 级，8 GB 内可跑）

| 项目 | 说明 |
| --- | --- |
| [QwenLM/Qwen3](https://github.com/QwenLM/Qwen3) | Qwen3-4B：中文强、原生支持工具调用，**首选** |
| [OpenBMB/AgentCPM](https://github.com/OpenBMB/AgentCPM) | AgentCPM-Explore 4B：清华 NLLP + 人大 + 面壁 + OpenBMB，**端侧智能体**专用，在 GAIA/HLE/BrowseComp 等 8 个长程任务上以 4B 全量参数登榜 |
| microsoft/Phi-4-mini | 小模型推理能力强（主要托管在 Hugging Face） |
| google/gemma-3-4b-it | 多模态小模型（主要托管在 Hugging Face / Kaggle） |

### 4.3 Harness / Agent 编排（可参考或复用）

| 项目 | 定位 | 参考价值 |
| --- | --- | --- |
| [deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness) | DeepSeek 2026-08-13 开源的 Agent Harness，**MIT**，"一切皆插件"，TypeScript/Node + pnpm，Web UI on :3080 | ⭐ **架构参考首选**；处于 Developer Preview 且**明确声明会有破坏性变更**，不适合作为依赖基座 |
| [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) | 显式状态图编排（Python） | 成熟稳定，可用于状态/检查点；但抽象较重 |
| [huggingface/smolagents](https://github.com/huggingface/smolagents) | 极简 Python agent 框架 | ⭐ **轻量参考首选**，代码量小易读 |
| [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai) | 类型安全的 agent 框架 | 结构化输出与类型校验参考 |
| [OpenHands/OpenHands](https://github.com/OpenHands/OpenHands) | 全功能软件工程 agent 平台 | 架构与安全/沙箱设计参考 |
| [cline/cline](https://github.com/cline/cline) · [Aider-AI/aider](https://github.com/Aider-AI/aider) · [continuedev/continue](https://github.com/continuedev/continue) | 编码 agent / IDE 助手 | 工具设计与权限确认流程参考 |

### 4.4 工具协议（MCP）

| 项目 | 用途 |
| --- | --- |
| [modelcontextprotocol/python-sdk](https://github.com/modelcontextprotocol/python-sdk) | ⭐ MCP 客户端/服务端 SDK（工具接入标准） |
| [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) | 官方参考 server 集合（文件、Git、抓取等） |
| [modelcontextprotocol/inspector](https://github.com/modelcontextprotocol/inspector) | MCP 调试与检查工具 |

### 4.5 沙箱与隔离（**不依赖 KVM** 的优先）

| 项目 | 隔离机制 | 无 KVM 环境可用性 |
| --- | --- | --- |
| [google/nsjail](https://github.com/google/nsjail) | Linux namespaces + seccomp-bpf | ⭐ **首选**，纯用户态 |
| [containers/bubblewrap](https://github.com/containers/bubblewrap) | 非特权用户命名空间（Flatpak 同款） | ⭐ 首选，无需 root |
| [google/gvisor](https://github.com/google/gvisor) | 用户态内核（应用内核） | 可用，但需容器内具备运行条件【待验证】 |
| [superradcompany/microsandbox](https://github.com/superradcompany/microsandbox) | microVM（需 KVM） | ❌ 本项目不采用 |
| [e2b-dev/E2B](https://github.com/e2b-dev/E2B) | 云端沙箱（Firecracker） | ❌ 依赖云服务/付费 |

### 4.6 安全与护栏

| 项目 | 用途 |
| --- | --- |
| [protectai/llm-guard](https://github.com/protectai/llm-guard) | ⭐ 输入/输出扫描器集合（注入、PII、越狱、毒性等） |
| [NVIDIA/NeMo-Guardrails](https://github.com/NVIDIA/NeMo-Guardrails) | 可编程护栏（Colang 语言） |
| [guardrails-ai/guardrails](https://github.com/guardrails-ai/guardrails) | 输出结构校验与修复 |
| [meta-llama/PurpleLlama](https://github.com/meta-llama/PurpleLlama) | Llama Guard 等模型级防护 |
| [NVIDIA/garak](https://github.com/NVIDIA/garak) | ⭐ LLM 漏洞扫描器（红队自动化） |
| [Azure/PyRIT](https://github.com/Azure/PyRIT) | 微软红队框架 |
| [promptfoo/promptfoo](https://github.com/promptfoo/promptfoo) | ⭐ 红队 + 评估 + 回归测试 |
| [UKGovernmentBEIS/inspect_ai](https://github.com/UKGovernmentBEIS/inspect_ai) | 英国 AISI 的评估框架 |

### 4.7 策略引擎（权限模型实现）

| 项目 | 用途 |
| --- | --- |
| [open-policy-agent/opa](https://github.com/open-policy-agent/opa) | 通用策略引擎（Rego 语言），工业级 |
| [casbin/casbin](https://github.com/casbin/casbin) | 轻量访问控制库（RBAC/ABAC） |

### 4.8 可观测与审计

| 项目 | 用途 |
| --- | --- |
| [langfuse/langfuse](https://github.com/langfuse/langfuse) | ⭐ LLM 可观测平台（自托管，追踪每次调用） |
| [Arize-ai/phoenix](https://github.com/Arize-ai/phoenix) | 追踪与评估 |
| [traceloop/openllmetry](https://github.com/traceloop/openllmetry) | 基于 OpenTelemetry 的 LLM 埋点 |

### 4.9 记忆与检索

| 项目 | 用途 |
| --- | --- |
| [lancedb/lancedb](https://github.com/lancedb/lancedb) | ⭐ 嵌入式向量库（无需独立服务，最适合本项目） |
| [chroma-core/chroma](https://github.com/chroma-core/chroma) | 轻量向量库 |
| [qdrant/fastembed](https://github.com/qdrant/fastembed) | 轻量 embedding（CPU 友好） |
| [MemTensor/MemOS](https://github.com/MemTensor/MemOS) · [letta-ai/letta](https://github.com/letta-ai/letta) | 记忆系统设计参考（不直接依赖） |

### 4.10 前端与产品壳

| 项目 | 用途 |
| --- | --- |
| [open-webui/open-webui](https://github.com/open-webui/open-webui) | ⭐ 自托管 AI 界面，可快速获得可用前端 |
| [danny-avila/LibreChat](https://github.com/danny-avila/LibreChat) | 多模型对话平台（含 MCP、Agents） |
| [lobehub/lobe-chat](https://github.com/lobehub/lobe-chat) | 现代 AI 对话应用框架 |

### 4.11 供应链安全（可作为加分项）

| 项目 | 用途 |
| --- | --- |
| [anchore/syft](https://github.com/anchore/syft) | SBOM 生成 |
| [sigstore/cosign](https://github.com/sigstore/cosign) | 制品签名与验证 |

---

## 5. 候选方向

> 所有方向都遵循同一原则：**70% 复用 + 30% 自研**，且自研部分必须可独立验证。

### 方向 1：低资源安全 Agent Harness ⭐

**一句话**：为 **4B 级小模型**构建一个完整的 Agent Harness，让弱模型在**低资源**（纯 CPU 或 8 GB 显存）
下可靠完成工具调用任务，并把**安全护栏**做成 Harness 的一等公民。

**组装方案**

| 层 | 选型 |
| --- | --- |
| 推理后端 | llama.cpp（OpenAI 兼容 server） |
| 目标模型 | Qwen3-4B（首选）/ AgentCPM-Explore-4B |
| Harness 内核 | **自研轻量循环**（参考 smolagents 的简洁 + dsh 的插件思想） |
| 工具层 | MCP Python SDK + 文件/Shell/HTTP 内置工具 |
| 沙箱 | nsjail 或 bubblewrap（无 KVM） |
| 护栏 | LLM Guard + 自研声明式权限策略（default-deny） |
| 记忆 | LanceDB + fastembed |
| 可观测 | Langfuse（自托管）+ 结构化审计日志 |
| 评估 | promptfoo + garak |
| 前端 | Open WebUI（复用）或 CLI 优先 |

**自研增量（30%，即"自己的有意义的工作"）**

| # | 模块 | 主线 | 可验证指标 |
| --- | --- | --- | --- |
| 1 | **工具权限与策略层**：声明式策略 + default-deny + 审批 + 全链路审计 | 安全 | 预定义越权场景集拦截率 100%，且拒绝均有审计记录 |
| 2 | **上下文效率层**：上下文压缩、观察屏蔽、工具懒加载 | 效率 | 同等任务成功率下，token 消耗下降 ≥ 30%；P95 延迟下降 ≥ 25% |
| 3 | **小模型工具调用评估集**：任务集 + 对抗语料，接入 promptfoo/garak | 工程/研究 | 可复现的基线数据与回归门禁 |

**交付物**：可运行系统（CLI + 可选 Web）、需求/设计/威胁模型文档、测试与基准报告、安全评估报告、演示。

**可行性**：**高**。全部组件 CPU 可跑，CNB 免费 CPU 额度足够（构建 160 核时/月、开发 1600 核时/月）。

**风险**：小模型工具调用能力弱，需要用工程手段补偿（**这恰恰是本项目的价值所在**，
但也是工作量最不确定的部分）；需防止滑向"调 prompt"。

---

### 方向 2：Agent/MCP 安全网关

**一句话**：在 Agent 与模型/工具之间插入一个独立的安全中间件，拦截提示注入、工具投毒、
越权调用与数据外泄。

**组装方案**：MCP 代理（自研）+ LLM Guard + 策略引擎（OPA/Casbin）+ 审计 + Langfuse
+ promptfoo/garak 做验证。

**自研增量**：策略引擎与工具描述完整性校验（防 rug-pull）、污点追踪、审计与可回放。

**可行性**：**最高**。与上游解耦，纯用户态，交付物形态清晰（一个网关）。

**风险**：**性能主线很弱**（只有"自身开销不能成为瓶颈"这类弱指标）；生态已有多个小型竞品，
差异化空间需仔细找。

---

### 方向 3：本地优先 AI 工作台（增强型）

**一句话**：以 Open WebUI / LibreChat 为前端，后端接 llama.cpp + RAG + MCP + 护栏，
组装成一套完整的本地 AI 工作台，并在安全与效率上做增强。

**可行性**：高，且"完整产品"形态最明显。

**风险**：**研究含量最低**，容易沦为"配置与部署工作"；自研增量不易界定。

---

### 方向 4：端侧小模型能力增强 Harness（研究型）

**一句话**：以 AgentCPM-Explore-4B 为基座，自研 Harness 提升其在长程任务
（GAIA / BrowseComp 类）上的完成率。

**可行性**：中低。需要浏览器/检索类环境，评估成本高，**2~3 个月很可能不够**。

**风险**：接近论文级工作量；且评估结果受环境影响大。**与"必须能完成"这一首要目标冲突。**

---

## 6. 对比矩阵

权重按"首要目标是完成交付"重新分配：

| 评估维度（权重） | 方向 1 小模型安全 Harness | 方向 2 安全网关 | 方向 3 本地工作台 | 方向 4 能力增强 |
| --- | --- | --- | --- | --- |
| **可完成性（30%）** | 4.5 | 5 | 5 | 2.5 |
| 学习与工程成长（20%） | 5 | 4 | 3.5 | 4.5 |
| 安全主线契合（20%） | 5 | 5 | 3 | 3 |
| 效率主线契合（15%） | 4.5 | 2.5 | 3.5 | 4 |
| 研究前沿性（15%） | 5 | 4 | 2.5 | 5 |
| **加权总分** | **4.78** | **4.28** | **3.70** | **3.60** |
| **排名** | **1** | **2** | 3 | 4 |

---

## 7. 推荐

### 首选：方向 1 —— 低资源安全 Agent Harness

**理由**：

1. **可完成性可控**：所有组件都在 CPU 上可跑，无 GPU、无 KVM、无付费依赖；
   自研范围被严格限定为 3 个可独立验证的模块。
2. **安全与效率同时有真问题可做**：这在"组装型"项目里很少见——大多数组装型项目
   性能维度只能做弱指标，而"小模型 + 上下文管理 + 工具裁剪"是**真实且可量化**的效率问题。
3. **学习价值最高**：会完整接触推理后端、工具协议（MCP）、沙箱、护栏、可观测、评估
   这整条技术栈，并亲手实现 Harness 内核——这正是 2026 年 AI 工程的核心能力。
4. **研究站位好**："小模型 + 强 Harness"是 2026 年公认的真问题（证据见第 3 节），
   而"面向小模型的安全护栏"是其中被讨论较少的一角。

### 明确的范围控制（**Must / Should / Won't**）

| 级别 | 内容 |
| --- | --- |
| **Must（必须完成）** | ① llama.cpp + Qwen3-4B 跑通<br>② 自研 ReAct 循环 + MCP 工具接入（≥3 个工具）<br>③ 工具权限策略层（default-deny + 审计）<br>④ CLI 可交互 + 完整测试<br>⑤ 文档：需求、设计、威胁模型、使用说明 |
| **Should（尽量完成）** | ⑥ 上下文效率层 + 基准数据<br>⑦ 对抗测试集 + garak/promptfoo 集成<br>⑧ Langfuse 可观测<br>⑨ nsjail 沙箱执行<br>⑩ Open WebUI 前端集成 |
| **Won't（明确不做）** | ✗ 模型训练/微调<br>✗ 自研推理引擎<br>✗ 多智能体复杂编排<br>✗ KVM/microVM 沙箱<br>✗ 分布式部署<br>✗ 权限系统做成企业级（RBAC 全功能） |

### 备选：方向 2（Agent/MCP 安全网关）

若在实施方向 1 的过程中发现小模型工具调用能力**严重不足**（例如任务成功率长期低于 30%），
导致效率主线无法建立有效基线，则**切换到方向 2**：
把已经写好的"权限策略层 + 护栏"独立出来，做成网关产品。
**注意**：方向 1 的 ③ 号自研模块与方向 2 高度重叠，**切换成本低**，因此该备选是真实的退路。

---

## 8. 迭代计划（8 周，可按实际压缩到 6 周）

| 周 | 目标 | 出口判据 |
| --- | --- | --- |
| W1 | 打通最小闭环 | llama.cpp + Qwen3-4B 可调用；一个工具可执行；CLI 可对话 |
| W2 | Harness 内核成型 | ReAct 循环、错误处理、状态记录完成；≥3 个工具（含 MCP） |
| W3–4 | **安全主线** | 权限策略层 + default-deny + 审计日志；越权场景测试集跑通 |
| W5 | **沙箱** | nsjail/bubblewrap 中执行工具；逃逸与资源耗尽用例通过 |
| W6 | **效率主线 + 基准** | 上下文压缩/观察屏蔽/工具懒加载；产出前后对比基准数据 |
| W7 | 加固与对抗测试 | garak/promptfoo 集成；威胁模型逐条验证；残余风险记录 |
| W8 | 交付 | 文档齐备、`make check` 全绿、发布 v1.0.0、演示材料 |

> 每周为一个迭代，周五收尾并回顾（见 [`docs/engineering/sdlc.md`](../engineering/sdlc.md)）。
> **W3/W6/W7 是关键节点**——若 W4 结束时安全主线未成型，应缩减 Should 项。

---

## 9. 待确认信息

| 编号 | 待确认 | 影响 |
| --- | --- | --- |
| Q-1 | 方向 1 / 2 / 3 选哪个？ | 决定后续全部工作 |
| Q-2 | 是否接受**纯 CLI 优先**、Web 界面作为 Should 项？ | 影响 W1–W4 的形态选择 |
| Q-3 | 目标模型倾向：Qwen3-4B（通用强）还是 AgentCPM-Explore-4B（agent 专用）？ | 影响工具调用成功率与工作量 |
| Q-4 | 是否需要向上游提 PR？ | 影响是否要把组件改动回馈（本项目以集成为主，通常不需要） |

---

## 10. 下一步

1. 确认第 9 节 Q-1 ~ Q-4；
2. 若选方向 1，先做一个 **2 小时的技术冲刺验证**（Timebox）：
   - 在 CNB 云原生开发环境里跑通 llama.cpp + Qwen3-4B（Q4 量化）+ 一次工具调用；
   - 结论写入 `docs/research/`，**无论成功失败都要归档**；
3. 验证通过后冻结决策，写入 `docs/adr/0003-select-base-project.md`（改写为组装型定位）；
4. 进入 Phase 1：需求规格（SRS）、架构设计、威胁模型、模块接口定义。
