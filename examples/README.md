# 示例领域包（`examples/`）

领域包是本项目"**某个场景需要哪些工具、哪些能力、什么提示片段**"的声明式表达
（契约：`docs/design/interfaces/harness.md` §4；加载与校验的唯一实现：
`src/agent_sec_perf/harness/domain_pack.py`）。

## 为什么需要它

不配领域包时，`PolicyEngine` 对**所有**工具取 `DEFAULT_TOOL_RISK = HIGH`
（`src/agent_sec_perf/security/policy.py`）⇒ 每次调用都 `requires_confirmation`；
而非交互运行（默认）**没有确认通路**，按 fail-secure 一律拒绝（`harness.md` §2.5.5 的 `R1`）
⇒ **一个工具都执行不了**。

本目录里的示例包就是这一点的**正规解法**：把只读、低风险的工具**声明**出来，
让它们走完"参数校验 → 策略求值 → 执行 → 审计"的完整通路。

> ⚠️ **示例包不是安全默认的替代品**，它只是产品正规通路的一个示例。
> 风险仍由 `PolicyEngine` **逐次求值**、能力仍由 default-deny 控制、
> 拒绝与审计路径一字未改；请勿把示例包照抄进自己的配置就以为"已经安全"。

## 两个包

| 目录 | 面向场景 | `tools.allowlist`（暴露面） | `security.capabilities` | 风险声明 |
| --- | --- | --- | --- | --- |
| `packs/coding-readonly` | 代码阅读（只读）：在仓库里回答"某段逻辑在哪、怎么实现"，只读源码与目录 | `read_file`、`list_dir` | `read_file` | `read_file` / `list_dir` 均声明 `low` |
| `packs/tech-manual-qna` | 技术手册问答（只读）：针对手册 / 规范类文档做检索式问答，答案以原文为依据 | `read_file`、`list_dir` | `read_file` | `read_file` / `list_dir` 均声明 `low` |

两个包的能力声明都**只有** `read_file`（`list_dir` 同样走 `READ_FILE` 能力，见
`src/agent_sec_perf/tools/files.py`）。`write_file` / `execute_command` / `network_outbound`
一律不出现——它们是写、执行与出站能力，与"只读"场景无关。

工具名取自 `src/agent_sec_perf/tools/` 的**实际**工具（`read_file` / `write_file` /
`list_dir` / `run_command`）；包里的名字写错会**拒绝加载**，不会被静默跳过
（`harness.md` §4.3）。

## 怎么用

`--pack` 接的是**领域包目录**（不是 `pack.toml` 文件本身）：

```bash
agent-sec-perf run "读取 README.md，用三条要点总结它" \
  --model-path /opt/models/<your-model>.gguf \
  --pack examples/packs/coding-readonly \
  --allowed-root .
```

两个前提（都在**装配期**校验，失败即拒绝启动、退出码 `3`）：

1. **领域包目录必须落在允许的根之内**（`--allowed-root`，省略时取 `--working-dir`／cwd）：
   `load_pack` 先经 `foundation.paths.resolve_within` 校验，越界或符号链接逃逸即
   `PathNotAllowedError`；
2. **能力还要在配置里显式授予**：领域包**只能收窄**、不能扩权
   （生效授予 = 配置授予 ∩ `security.capabilities`），而默认一个能力都不授予。
   因此在项目根目录的 `.lowspec.toml`（或用户级 `config.toml`）里写：

   ```toml
   [policy]
   granted_capabilities = ["read_file"]
   ```

   只在包里声明能力、不去配置里授予，结果是**该能力仍不可用**——
   这正是"包不能扩权"的体现，而不是配置错误。

交互式确认（`--interactive`，需要 TTY）不是必须的：这两个包把只读工具声明为 `low`，
风险求值结果就是"自动放行"，无需确认通路。

## 自己写一个包

- 键名、类型、取值上限与**未知键 / 未知段一律拒绝**的口径见 `harness.md` §4.2 / §4.3；
- 包目录内**不得**出现 `.py` / `.pyc` / `__pycache__`（`R5`：只允许声明式配置），
  出现即拒绝加载；
- **只读工具才适合声明 `low`**。给 `run_command` / `write_file` 声明 `low` 等于把
  "需人工确认"变成"自动放行"，属**降级安全默认**，必须先走 ADR 并使所有者批准；
- `tests/unit/test_example_packs.py` 会**真实加载**本目录下的两个包并断言"风险声明不超出
  只读工具白名单"⇒ 若有人顺手把 `run_command` 降级进示例包，那条用例会**翻红**。
