# 测试策略

> 本文定义测试分层、覆盖要求、运行方式与证据归档规则。
> Agent 侧摘要见 [`.codebuddy/rules/testing/`](../../.codebuddy/rules/testing/)。

---

## 1. 测试金字塔与本项目的特殊性

```text
        ╱╲          benchmark    性能基准（少而精，必须可复现）
       ╱  ╲
      ╱────╲        security     安全/对抗性测试（本项目重点，不属于常规金字塔层）
     ╱      ╲
    ╱────────╲      integration  集成测试
   ╱          ╲
  ╱────────────╲    unit         单元测试（最多，最快）
 ╱______________╲
```

本项目在标准金字塔之外**单列安全测试层**，原因：安全断言无法用"正常功能测试通过"来证明，
必须由**攻击者视角的对抗性用例**独立验证。这是本项目区别于普通工程项目的关键。

---

## 2. 分层定义

| 层 | 目录 | 依赖 | 速度 | 目标 |
| --- | --- | --- | --- | --- |
| 单元 | `tests/unit/` | 无外部依赖，使用 fake/stub | 毫秒级 | 逻辑正确性、边界与异常路径 |
| 集成 | `tests/integration/` | 真实组件或高保真替身 | 秒级 | 模块间契约、时序、错误传播 |
| 安全 | `tests/security/` | 对抗性输入、攻击场景 | 秒级 | 拒绝行为与审计记录同时成立 |
| 基准 | `bench/`（`tests/benchmark/` **不新增**，见下方状态说明） | 真实资源 | 分钟级 | 延迟、吞吐、内存 |

> ⚠️ **当前状态（2026-09-20 复核更新；2026-09-18 的旧数字已由本轮实测取代）**：
> 上述分层是**目标态**；实际现状如下。
> **每个数字都附可复算的命令**——这些数字会随用例增删**持续漂移**，
> 「写死数字而不写口径」正是本项目反复复现的失效模式（数字一旦过期就无人能判断它错在哪）。
>
> - **已建立**：
>   - `tests/unit/`：`test_*.py` **42** 个（**不含** `conftest.py` 等非 `test_*.py` 文件；
>     目录下共 43 个 `.py`）。口径与命令：**模块数 = `ls tests/unit/test_*.py | wc -l` = `42`**；
>     **用例数 = `uv run pytest -m unit --collect-only -q` 末行**（`854/960 tests collected (106 deselected)`）。
>   - `tests/security/`：`test_*.py` **18** 个（另有 `__init__.py` / `conftest.py` /
>     `_harness_fakes.py` / `corpus/`，**均不计入模块数**）。口径与命令同单元：
>     **模块数 = `ls tests/security/test_*.py | wc -l` = `18`**；
>     **用例数 = `uv run pytest -m security --collect-only -q` 末行**（`96/960 tests collected`）。
>     ⚠️ `corpus/` 目前**只有路径穿越语料** `traversal_payloads.txt`，**无注入语料**。
>   - `tests/integration/`：**1** 条真模型端到端 `test_end_to_end.py`（标 `slow`、默认不跑）。
>     命令：`ls tests/integration/test_*.py | wc -l` = `1`。
> - **不新增**：`tests/benchmark/` —— 性能基准由 **`bench/` 的轮次与 `bench/data` 分支**承担
>   （[`ADR-0015`](../adr/0015-layering-and-reuse-boundary.md) §5.4.2；`architecture.md` §4.4）。
>   因此 `uv run pytest -m benchmark` **当前零用例**
>   （2026-09-20 实测：`no tests collected (960 deselected)`；末行的 `deselected` 数即**总收集数**）。
>   ⚠️ 这不是缺口、也不是"基准没做"：**基准不进 `pytest` 的默认/标记集**是分工（§6）。
> - `make test-security` 现有 **96** 个用例匹配 ⇒ 正常运行并通过（同上 `-m security` 命令）。
>   **零用例时的行为已由"打印提示并返回 0"改为 fail-secure（`exit 1`）**——依据提交 `a39ad48`
>   （理由：安全测试层是基线的一部分，**零用例 / 标记丢失必须显式暴露**，不得静默通过）。
> 依据：2026-09-20 复核——上述 `ls … | wc -l` 与 `uv run pytest -m … --collect-only -q`
> **原样复跑**；原 `doc-consistency-report.md` 的 A-9 记录见
> [`doc-consistency-report.md`](doc-consistency-report.md)。

标记：`@pytest.mark.unit` / `integration` / `security` / `benchmark` / `slow`。

---

## 3. 单元测试要求

- **命名描述行为**：`test_policy_denies_tool_when_capability_missing`，
  而非 `test_check_1`。
- **一个用例一个行为**；断言必须具体（禁止只断言"不抛异常"）。
- 必测清单：
  - 正常路径
  - 边界值（空、单元素、上限、下限、超大输入）
  - 非法输入（类型错误、格式错误、超长、编码异常）
  - 失败路径（依赖报错、超时、部分失败）
  - 并发/时序敏感场景（如适用）
- **确定性**：注入时钟与随机源；禁止依赖网络与执行顺序。
- **禁止 sleep 等异步**，使用事件/条件同步。

---

## 4. 集成测试要求

- 覆盖模块间的**契约**：类型、错误语义、并发假设、资源生命周期。
- 关键路径必须有端到端用例（从入口到可观测结果）。
- 优先使用真实依赖；无法使用真实依赖时，替身必须**行为等价**（含错误行为），
  并在测试中注明"此处使用了替身及原因"。

---

## 5. 安全测试要求（重点）

### 必测攻击面

| 类别 | 示例用例方向 |
| --- | --- |
| 输入注入 | 提示注入、命令注入、路径穿越、模板注入、反序列化 |
| 越权 | 缺失能力/权限时必须被拒绝；提权路径应不可达 |
| 隔离逃逸 | 沙箱内进程越界访问；文件系统/网络越权 |
| 资源耗尽 | 超长输入、深层嵌套、递归、无限循环、内存放大 |
| 指令/数据混淆 | 不可信内容试图改变控制流或权限决策 |
| 供应链 | 恶意/被篡改的工具描述、插件、依赖元数据 |
| 信息泄漏 | 错误信息、日志、时序中泄漏敏感数据 |

### 用例要求

- 命名体现安全意图，断言必须验证**两件事**：
  1. 攻击被**拒绝**（或无害化）；
  2. 该拒绝被**正确记录**（审计日志存在且内容正确）。
- 对抗性语料集（如提示注入语料）需可复用、可扩充、纳入版本管理，
  放在 `tests/security/corpus/` 下。
- 每个威胁模型条目都必须有对应用例；**没有用例的缓解措施视为未实现**。

---

## 6. 性能基准要求

- **可复现**：固定环境描述、输入规模、随机种子、预热轮次、重复次数。
- **指标**：P50 / P95 / P99、吞吐、内存峰值；报告方差。
- **对照组**：必须包含改动前的基线（可用 `git stash` 或上一提交测得）。
- **归档**：机器产出走 **`bench/data` 分支**（见 [ADR-0014](../adr/0014-benchmark-automation.md)，
  由 `make bench-publish` 发布）；人工整理的研究归档进 `docs/research/reports/<date>-<topic>/`。
  **不使用 `reports/bench/`**——该路径经 2026-09-18 一致性核查确认**不存在**，
  且与流水线的实际落点不同（依据：[`doc-consistency-report.md`](doc-consistency-report.md) 的 A-5）。
- 基准**不进入**默认快速回归（`make test` 排除 `benchmark` 标记）。

---

## 7. 覆盖率

- 目标是**关键路径全覆盖**，不是刷数字。
- 要求：
  - 安全与策略相关代码：接近 100%；
  - 新增代码：必须自带测试，覆盖率不得下降；
  - 禁止用无意义断言刷覆盖率。
- 报告：`make test-cov` 生成终端摘要与 `coverage.xml`。

---

## 8. 运行方式

```bash
make test              # 快速回归（排除 benchmark 与 slow）
make test-cov          # 含覆盖率（同一排除集）
make test-security     # 仅安全测试（现有 96 个用例；零用例会 fail-secure，见 §2 的状态说明）
make check             # 完整自检（提交 PR 前必须执行）
uv run pytest -m benchmark    # 当前零用例（见 §2）：基准由 bench/ 轮次承担，不走 pytest 标记集
AGENT_SEC_PERF_E2E=1 uv run pytest -m integration   # 真模型端到端（默认不跑，见下）
```

> **`slow` 的排除口径（2026-09-19 起）**：`make test` / `make test-cov` 用
> `-m "not benchmark and not slow"`；CI（`.cnb.yml`）经 `make check` **继承同一口径**，
> 不需要另写一份（"同一事实两处表述必然漂移"）。
> **为什么必须排除**：`slow` 类用例会起**真实 `llama-server` + 真实 GGUF**（实测一轮 ≈ 6 分钟），
> 放进默认回归会让门禁变慢且不稳定；它们靠 `make test` 与**用例自带的环境变量门槛**
> **两道锁**保证默认不跑（后者防"显式 `-m integration` 时误跑"，见 `tests/integration/` 的 docstring）。

---

## 9. 分工：人 vs 机器 vs AI

| 角色 | 负责 |
| --- | --- |
| 机器（CI） | 可判定的事实：格式、类型、测试通过与否、覆盖率、已知漏洞签名 |
| AI 代理 | 生成用例草稿、扩充对抗语料、识别遗漏的边界与信任边界 |
| 人类 | 判断**测试是否真正验证了意图**；安全测试是否覆盖真实攻击面；残余风险是否可接受 |

> AI 可以快速产出大量用例，但"用例是否测到了点子"必须由人判断。
> **测试数量不等于测试质量。**
