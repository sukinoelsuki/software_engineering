# 能力包内核：新增 `pack/` 产品层，并把"调教过程"变成可校验的一步

- **状态**：已接受（2026-10-27，所有者本会话指令：**"先把这个内核做出来，这个内核就是我们当前的产品"**）
- **编号说明**：接续现有最大编号 `0033`；不复用任何历史编号。
- **关联**：提案 [`0005`](../proposals/0005-capability-pack-compiler.md)、`devlog 0031` §4.5、
  Issue #11、[`ADR-0015`](0015-layering-and-reuse-boundary.md)（`R1`~`R5`）

## 1. 背景

过去六周的实做里，真正耗时的不是写 harness，而是**反复调教同一个端侧 4B 模型**：
`devlog 0031` §4.5 记录了八轮迭代，每一轮都是"模型没做到 → 改一处 → 复跑 → 记录"。
这些修改有稳定模式，但只存在于人的经验与一篇日志里；产品里没有承载它们的载体。

所有者 2026-10-27 明确三点约束：

1. **只有一个拟合较好的成功案例**（`incident-01`），没有资源做多案例验证；
2. 本期**只做内核**，冒烟能跑即可；插件事后再说；
3. 大模型在框架里的角色是**总控**：由它监测并驱动内核跑完整流程，
   **不是**只让它"生成一段 prompt"。

## 2. 驱动因素

| # | 因素 | 说明 |
| --- | --- | --- |
| 1 | 方法资产会随人流失 | 八类失效模式与对应修法目前只写在文档里，无法被执行、无法被机器检查 |
| 2 | "能跑"与"跑对"分不开 | `0031` §4.2 的第一版演示格式完美、结论是编的；只有机检抓住 ⇒ 判据必须先于演示 |
| 3 | 判据本身会骗人 | `notes/evaluation-pitfalls.md` 第 1 条：没有"正例必过、反例必败"的判据不得用于评估 |
| 4 | 总控需要一个**窄接口** | 大模型不能直接改代码，也不能自称"通过了"；状态与判定必须由内核计算 |
| 5 | 包会成为不可信输入 | 未来包可能来自他人 ⇒ 摘要、schema、路径白名单、拒绝代码必须一开始就在 |

## 3. 候选方案

### 3.1 代码落点

| 方案 | 内容 | 结论 |
| --- | --- | --- |
| A | 并入 `cli/`（单模块 `cli/pack.py`） | ❌ 编译器/判据/状态机属产品逻辑，塞进 L4 会让"表现层"长出业务；且未来要被插件复用 |
| B | 并入 `harness/` | ❌ harness 是**执行**编排（会话循环），编译是**生产**流程；混层后 `R1` 的依赖方向会被"顺手"打破 |
| **C（采纳）** | 新增 `src/agent_sec_perf/pack/`，作为**产品层**（与 `cli/` 同层、位于 `harness/` 之上） | ✅ 判定逻辑可单测、不依赖 Typer；不 import `cli`（执行通过调用方注入） |
| D | 独立仓库/独立 wheel | ❌ 本期资源不足，且会切断与既有 harness/判据的复用 |

### 3.2 大模型的参与方式

| 方案 | 内容 | 结论 |
| --- | --- | --- |
| A | 大模型直接生成 `pack.toml` 并跑，内核只执行 | ❌ 产出无法被拒绝，退化成"生成 prompt 的对话框" |
| B | 大模型直接改 `src/` 代码 | ❌ 违反 `R5` 的同类风险；也不可回滚 |
| **C（采纳）** | 大模型只读 `status/plan` 的 JSON，只改**声明式文件**，每次由 `pack build/verify` 重新判定 | ✅ 职责清晰：模型提议、内核判定、证据留痕 |

### 3.3 判据自证的范围

| 方案 | 内容 | 结论 |
| --- | --- | --- |
| A | 声称"全部判据已自证" | ❌ 事件流判据需要真实执行，静态样例证明不了 |
| **B（采纳）** | 只自证**内容判据**（4 项），manifest 如实写 `covered` / `uncovered` | ✅ 宁可少报，不可虚报 |

## 4. 决策

1. 新增产品层 **`src/agent_sec_perf/pack/`**；允许依赖 `contracts` / `foundation` / `harness`，
   **禁止** import `cli`（由 `tests/unit/test_architecture_layers.py` 的 `ALLOWED_DEPENDENCIES` 强制）。
2. **案例契约上移**：`CaseSpec` / `load_case` 从 `cli/demo.py` 迁到 `pack/case.py`；
   `cli.demo` 保留 `DemoCase` **别名**与 `load_case` 薄包装（补默认仓库路径），既有调用方零改动。
3. **bundle 是唯一分发单位**：`manifest.json`（每文件 sha256 + 判据覆盖）+ `case/` + `README.md`；
   `evidence/` 不参与完整性校验。同输入重复构建**字节一致**（默认不写时间戳）。
4. **判据必须自证**：`case.toml` 增加 `[selftest] positive/negative`；
   正例不全过或反例全过 ⇒ **拒绝发布**。未声明 `[selftest]` ⇒ 拒绝构建。
5. **总控接口只读/窄**：`pack status --json` 给出 `stage` / `blockers` / `next_actions`；
   `pack plan --json` 给出 `F1`~`F8` 与**允许的**结构性修复（含明确禁止项）。
6. **内核不执行大模型写的代码**，也不生成 prompt；它只校验声明式产物、执行、留证据。
7. **退出码语义**沿用 CLI 表：`0` 通过；`1` 校验未通过 / 运行未通过；`3` 装配期故障
   （案例非法、清单不可读、目录不可用）。

### 4.1 对外契约（字段级，供后续插件复用）

| 命令 | 输入 | 输出（`--json`） | 副作用 |
| --- | --- | --- | --- |
| `pack build` | 案例目录、`--out` | `{ok, bundle, name, version, files, selftest{ok,positive_ok,negative_ok,covered,uncovered}}` | 写 bundle |
| `pack verify` | bundle 目录 | `{ok, problems[], files, covered[], uncovered[], selftest}` | 无（只读） |
| `pack status` | bundle 目录 | `{ok, stage, blockers[], evidence[], next_actions[]}` | 无（只读） |
| `pack plan` | 可选 bundle 目录 | `{catalog:[{code,failure,signal,action,forbidden}]}` | 无 |
| `pack run` | bundle 目录、`--model` | `{ok, evidence, checks[], workspace, isolation_tier}` | 写 `evidence/*.json` |

`manifest.json` 必备字段：`schema`（`capability-pack/v1`）、`name`、`version`、`case_id`、
`model`、`criteria.covered/uncovered`、`files[{path,sha256,size}]`；可选 `stamped`。

## 5. 后果（含负面）

**正面**

- 方法资产从"文档"变成"可执行判定"：`F1`~`F8` 有了机器可读形态，判据自证是发布闸门。
- 包可分发、可被篡改检测；"同一个包"有确定含义（摘要一致）。
- 总控（大模型）有了窄接口：读状态、选动作、再读状态；它**不能**自称通过。

**负面 / 已知局限（必须如实写）**

1. **新增一层**：架构图与依赖白名单多一条边；`cli` 现在依赖 `pack`。
2. **判据自证只覆盖内容维度**（4 项）；事件流/审计判据（7 项）仍需要一次真实执行，
   manifest 会如实标 `uncovered`，**不得**被读成"全部已验证"。
3. **仍只有单案例**（`incident-01`）：方法论的可迁移性**未验证**；本 ADR 不宣称通用。
4. **`pack run` 仍是分钟级**（4B 模型、CPU），本期内核的价值在"判定与证据"，不在速度。
5. **`F1`~`F8` 目录是人工整理的经验**，没有统计意义上的覆盖率；新增失效模式只能**追加**编号。

## 6. 验证方式

| 判据 | 方式 | 结果 |
| --- | --- | --- |
| 构建/校验/状态链路可跑 | `pack build → verify → status`（无需模型） | ✅ 2026-10-27 实测 |
| 篡改可见 | 改动包内文件后 `verify` 退出码 `1` 并指出文件 | ✅ 实测 |
| 判据自证是闸门 | 反例被人为改成"也通过" ⇒ `build` 拒绝 | ✅ 单测覆盖 |
| 分层不被破坏 | `tests/unit/test_architecture_layers.py` | ✅ |
| 冒烟测试 | `tests/unit/test_{pack_kernel,pack_bundle,cli_pack}.py` | ✅ 17 条 |

复跑命令：

```bash
uv run pytest tests/unit/test_pack_kernel.py tests/unit/test_pack_bundle.py tests/unit/test_cli_pack.py -q
uv run python -m agent_sec_perf.cli.app pack build examples/showcase/incident-01 --out /tmp/ip.bundle --json
uv run python -m agent_sec_perf.cli.app pack verify /tmp/ip.bundle --json
uv run python -m agent_sec_perf.cli.app pack status /tmp/ip.bundle --json
```

## 7. 追加（2026-10-27，实测驱动；不改上文结论）

用 Qwen3-0.6B / 1.7B / MiniCPM5-2B 跑同一案例后，发现失败模式目录有一处歧义：

- **同一观测信号**（反复调用同一工具直到预算耗尽）对应**两种原因**：
  ① 目标工具没暴露（`F1`）；② 目标工具暴露但模型不选（新增 `F9`）。
- 修复动作完全不同：①调预算/白名单；②拆阶段/收窄工具形状。
  ⇒ 按"编号只增不复用"追加 **`F9`**，并把 `F1` 的判据收窄为"工具**不在**暴露面内"。

对应实现：`src/agent_sec_perf/pack/plan.py`；实测记录见 `docs/devlog/0032` §4.2。
⚠️ 本 ADR 的其余结论（层定位、bundle 格式、自证范围、负面后果）**不变**。
