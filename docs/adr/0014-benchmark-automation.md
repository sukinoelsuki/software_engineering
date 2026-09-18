# ADR-0014：基准自动化——夜间数据流水线、协议版本化与数据分支

- 状态：已接受（2026-09-16）
- 决策者：项目所有者（批准方案）+ 实现者（细化并落地）
- 关联：[ADR-0010 动态硬件适配](0010-dynamic-hardware-adaptation.md)、
  [ADR-0011 档位构成修订](0011-tier-composition-revision.md)、
  [ADR-0013 分支模型](0013-branch-model-for-solo-dev.md)、
  [`notes/evaluation-pitfalls.md`](../notes/evaluation-pitfalls.md)、
  [`engineering/benchmark-automation.md`](../engineering/benchmark-automation.md)、
  [`devlog/0012`](../devlog/0012-2026-09-16-基准自动化数据流水线.md)

---

## 1. 背景与问题

到 2026-09-16 为止，本项目的性能与能力数据是**偶发测量**的产物：09-15 一轮三档对比、
09-16 一轮重建后复跑。两个数据点无法回答三个关键问题：

1. **环境是否漂移**（同一份协议，今天与上周的数字是否可比）；
2. **判据是否可靠**（10% 阈值 vs 实测 8.4% 的单次采样极差——阈值与噪声同量级）；
3. **改动是否真的更好**（"优化前/优化后"如果不在同一套条件下测，差异无法归因）。

同时，测量脚手架只存在于开发容器的 `/root/w1`（仓库之外），**环境一重建就丢失**，
每次都要从 `docs/research/reports/*/HARNESS.md` 手工还原（0011 §7 登记为待办）。

## 2. 决策

### 2.1 建立两条长驻分支，各司其职

| 分支 | 内容 | 谁写 | 触发 |
| --- | --- | --- | --- |
| `bench/nightly` | 基准代码（协议、任务集、夹具、编排） | 人（正常提交） | `push` / `crontab` / `web_trigger_bench` |
| `bench/data` | 机器产出（JSON、报告、日志、模型产物） | **只有 CI** | 不被任何流水线触发 |

这是对 ADR-0013"短期分支、当次会话合回"的**显式例外**，理由有三：

- `crontab` 只支持**单一明确分支名**（CNB 文档），必须选一条；
- 定时任务取**该分支 HEAD 的代码** ⇒ 测试代码与日常开发分支分离，协议才不会被
  "顺手改一下"破坏（协议一变，序列即断）；
- 数据分支不被任何事件触发 ⇒ 不存在"CI 推数据 → 又触发一轮测试"的死循环。

约束（防止例外蔓延）：`bench/data` 只接收 CI 的快进推送，禁止 force；
`bench/nightly` **不回写任何代码**。

### 2.2 三种触发 + 串行锁

| 触发 | 规模 | 用途 |
| --- | --- | --- |
| `push`（本分支） | 三档中的 S/M，R=3 | 改了测试代码立刻验证能跑通 |
| `crontab: 0 4 * * 2-6,0` | 三档，R=10 | 夜轮：建立可比时间序列 |
| `crontab: 0 4 * * 1` | 三档 R=20 + 线程 4 轮 R=5 | 深跑：更小置信区间 + 核数维度取数 |
| `web_trigger_bench` | 三档，R=5 | 手动补跑 |

**`lock` 串行化是数据有效性要求，不是省钱**：两个基准轮次并发会互抢 CPU，
吞吐数字失去可比性。锁键统一为 `bench-cpu`。

### 2.3 预算（核时）

`runner.cpus: 8` ⇒ 内存 8 × 2 GiB = 16 GiB。**不能降到 4 核**：L 档常驻 8.70 GiB，
4 核只有 8 GiB 会 OOM。

| 项目 | 频次 | 单次耗时 | 核时/月 |
| --- | --- | --- | --- |
| 夜轮（R=10） | 每周 6 次 | ≈50 min | ≈210 |
| 深跑（R=20 + 线程 4） | 每周 1 次 | ≈2 h | ≈70 |
| 推送即时轮 | ≤每周 5 次 | ≈20 min | ≈30 |
| **合计** | | | **≈310（约占配额 1.8%）** |

余量充足，但**不再靠加重复来"用掉"预算**：噪声只随 √n 下降，边际收益递减。
按约定把余量投向**扩任务集**（L0 子集）与**参数维度**（线程数对照）。

### 2.4 协议版本化与"比较签名"

- `PROTOCOL_VERSION`（当前 `bench-v2`）是可比性的锚点：启动参数、提示词、判定方式、
  任务集任一变化都必须提版本；
- 报告只在**同签名**内比较，签名为
  `协议版本 | ctx | threads | max_tokens | tiers`；
- 跨版本数据保留但不比较（`bench-v1` 的 09-15 数据即为历史序列）。

### 2.5 测量纪律（本次试跑换来的一条，必须固化）

1. **每次重复重启服务进程**。同一进程内重复发送同一提示词时，服务端会复用该槽位的
   KV 前缀，日志给出 `prompt eval time = 34.89 ms / 1 tokens`——那不是 prefill 测量。
   首次试跑由此得到一条**极差 326%** 的假序列。
2. **prefill 行必须通过 token 数闸门**（`MIN_PREFILL_TOKENS = 32`）：第二道防线，
   防止缓存行为变化时悄悄污染统计。
3. **计时行数必须严格匹配**（每次重复 3 条）：口径变化要暴露，不要硬凑。
4. **重复次数 ≥ 3 且报极差**（schema 层强制）：单次采样不得用于阈值判断。
5. 加载耗时**只作同会话比较**（页面缓存/存储状态不可规范化，见 0011 §4）。

### 2.6 夹具与脚本入库，归档附录冻结

`docs/research/reports/*/HARNESS.md` 是**已发生实验的冻结证据**，不得修改；
仓库内的 `src/agent_sec_perf/bench/tasks/*.txt` 与 `bench` 包是**可维护的最新版**。
两者关系用协议版本号区分：附录属于 `bench-v1` 口径，仓库实现是 `bench-v2`。
不使用"以谁为准"的模糊表述——**比的是签名，不是文件**。

夹具用 `.txt` 后缀保存：它们**故意**含缺陷且无类型标注，若以 `.py` 保存，
`ruff` / `mypy` 会试图"修好"被测对象。

### 2.7 数据格式与保留策略

```
bench/index.json                  # 轮次索引（机器可读，含签名、中位数、核时）
bench/latest.md                   # 最新一轮报告（一屏摘要，便于跨分支读取）
bench/daily/<日期>/
  env.json                        # 环境指纹（协议、参数、llama 版本、模型摘要、触发方式）
  perf.json                       # 性能（中位数/极差/逐次原始值）
  capability.json                 # 能力（逐任务通过率 + 逐次判定明细）
  report.md                       # 人读报告（含超阈告警）
  logs/<档>_r<NN>.server.log      # 服务端日志（性能数字的原件）
  artifacts/<档>_r<NN>_<任务>.md  # 模型产物原文
```

保留期：JSON 与报告长期保留；`logs/`、`artifacts/`、`work/` 仅保留 30 天
（由发布前的清理逻辑执行）。判定中间目录 `work/` **不入库**——它们是模型生成的
`.py`，进版本库会被代码格式化钩子改写，等于篡改证据。

### 2.8 安全边界

| 项 | 决定 |
| --- | --- |
| 不可信边界 | **模型产物**（含 LLM 生成的测试代码）。执行它们 = 执行不可信代码 |
| 隔离方式 | 非特权 uid（nobody）+ **清空环境变量** + setrlimit（CPU/AS/FSIZE/NOFILE）+ 一次性工作目录 |
| 为什么不用容器 | 评测需要 `mypy`/`pytest`，嵌套容器内无法装配；且本平台命名空间不可用（ADR-0006/0007） |
| 凭据 | 只用运行期 `CNB_TOKEN`（构建结束销毁），**不新增任何密钥**；通过 credential helper 传入，不写入 remote URL / git config |
| 推送范围 | 固定 `HEAD:bench/data`，**禁止 force**；来源分支白名单（`bench/nightly`） |
| 入库闸门 | schema 校验不通过即拒绝发布（坏数据比没有数据更难发现） |
| 残余风险 | 若非特权子进程逃逸，理论上仍可读到宿主进程的 `CNB_TOKEN`（短时、限本仓库）。缓解：无凭据传递 + 只读源码 + rlimit。**进一步硬化方向**：把"执行"与"发布"拆成两条流水线（执行侧 `sandbox: true` 无令牌）——代价是编排复杂，留待需要时评估 |

### 2.9 豁免登记（SECURITY.md 要求"确需豁免必须登记"）

| 豁免 | 位置 | 理由 | 影响面 |
| --- | --- | --- | --- |
| `import subprocess`（**bandit B404**；ruff S404 处于 preview、**当前未启用**，见 §2.9.1） | `src/agent_sec_perf/foundation/proc.py`（**2026-09-18 更正**；原 `bench/proc.py`，见文末「修订记录」） | 承载项目的子进程需求 | 仅该模块 |
| `subprocess.run/Popen`（ruff **S603** / bandit **B603**，二者均生效） | 同上，共 3 处 | 不经 shell、参数以列表传入、执行前施加隔离与资源上限 | 3 个调用点 |
| 数据提交不运行代码钩子（`core.hooksPath` 指向空目录） | `scripts/bench/publish.sh` | 提交内容是**机器产出**，`ruff` 会格式化其中的代码块从而改写证据；代码侧门禁已在 `bench/nightly` 推送时执行 | 仅数据分支的提交 |
| 签名不可用时降级为未签名提交 | 同上 | 平台签名助手依赖会话上下文，在流水线中可能不可用；降级会**显式打进日志**，不静默 | 数据分支的提交 |

另有一条**机器检查**把约定钉住（`tests/unit/test_bench_encapsulation.py`）：
除 `proc.py` 外任何模块使用 `subprocess`、任何地方出现 `shell=True`、
`src/` 中出现 `print(...)`，测试即失败。

#### 2.9.1 规则号更正与**逐点**豁免理由（供实现者照抄）

**（a）规则号更正：`S404` 是 preview、未启用。**

上表原将 `import subprocess` 标为"ruff `S404` / bandit `B404`"。2026-09-18 实测（命令与输出见下）：
`ruff` 的 `S404`（`suspicious-subprocess-import`）**处于 preview**，
当前配置（未开 `--preview`）下**不生效** ⇒ 该行真正生效的只有 **bandit `B404`**。
原写法会让读者以为有两条检查在管它，属**登记准确性问题**，故更正为只保留生效的规则号。

```text
$ uv run ruff rule S404
This rule is in preview and is not stable. The `--preview` flag is required for use.
$ uv run ruff check --select S404 src/agent_sec_perf/foundation/proc.py
warning: Selection `S404` has no effect because preview is not enabled.
All checks passed!
```

> **将来若启用** `S404`（例如升级后转正、或显式开 `--preview`），**必须重新登记**本表——
> 届时该 import 行将同时受 ruff `S404` 与 bandit `B404` 约束。
> 对照：`S603` **不在** preview（`uv run ruff rule S603` 无 preview 提示），当前生效。

**（b）逐点豁免理由。**

安全基线要求"确需豁免时必须在**该行**写明理由"。实测现状：理由此前只写成
`foundation/proc.py` L142~L144 的一段注释块，**仅覆盖 1 个调用点**；其余 3 个豁免点
（`import` 行与另外 2 个调用点）**该行没有任何理由文本** ⇒ 属"**豁免理由与豁免点未逐点对应**"的
符合性缺口（**该缺口先于 D9 的文件移动就存在**，非移动引入）。

下表给出**每一豁免点**的理由措辞（**最终稿**，照抄为**独立注释行**，放置规则见下）。措辞
**由架构师定义、实现者照抄**，以避免"登记与实现分叉"。

| 豁免点（实测行号） | 生效规则（实测） | 理由措辞（照抄为**独立注释行**） |
| --- | --- | --- |
| `import subprocess`（L23） | bandit **B404** | `本模块是全项目唯一子进程封装层，导入 subprocess 即其职责。` + `豁免登记：docs/adr/0014-benchmark-automation.md §2.9` |
| `subprocess.run`（L145，`run`） | ruff **S603** + bandit **B603** | `不经 shell、参数为列表；执行前施加非特权 uid + rlimit + 最小环境。` + `豁免登记：docs/adr/0014-benchmark-automation.md §2.9` |
| `subprocess.run`（L187，`run_inherit_env`） | ruff **S603** + bandit **B603** | `仅执行自有工具链静态检查、不执行模型产物；不经 shell、参数为列表。` + `豁免登记：docs/adr/0014-benchmark-automation.md §2.9` |
| `subprocess.Popen`（L223，`spawn`） | ruff **S603** + bandit **B603** | `启动常驻 llama-server（自有二进制、绝对路径）；不经 shell、参数为列表。` + `豁免登记：docs/adr/0014-benchmark-automation.md §2.9` |

**放置规则（**已更正**：理由**不得**写进 `# nosec` / `# noqa` 注释内部）**：

> ⚠️ **更正原因（实测，安全相关；见文末「修订记录」）**：把理由文本追加到豁免标记之后**不安全**。
> bandit 会把 `# nosec` 之后的 **ASCII 词元当成规则号候选**，且**合法规则号会被静默接受**：
>
> ```text
> # 现状（实现者按旧措辞照抄后，导入行变成）：
> import subprocess  # nosec B404 —— 本模块是全项目唯一子进程封装层，导入 subprocess 即其职责（登记：ADR-0014 §2.9）
>
> $ uv run bandit -r src
> [manager] WARNING Test in comment: subprocess is not a test name or id, ignoring
> [manager] WARNING Test in comment: ADR is not a test name or id, ignoring
> [manager] WARNING Test in comment: 0014 is not a test name or id, ignoring
> ```
>
> 上例只是**告警**（词元非法，未被接受）。真正危险的是**静默过度豁免**——探针实测：
>
> ```text
> $ uv run bandit -q /tmp/probe_r.py
> # 文件内容：assert True  # nosec NOPE —— 说明中提到 B101 与 subprocess
> [manager] WARNING Test in comment: NOPE is not a test name or id, ignoring
> [manager] WARNING Test in comment: subprocess is not a test name or id, ignoring
> bandit_exit=0
> # ↑ B101（assert_used）因"理由文本里恰好写了 B101"被顺手豁免，且无任何报错、无任何告警
> ```
>
> ⇒ **理由文本可以不知不觉地多豁免一条规则**。这与 `SECURITY.md`「禁止静默削弱安全检查」
> 直接冲突，故否决"行尾追加"写法。

**正确做法（现行默认，记作 `F1`）**：**标记注释只写规则号**，理由另起**紧随其上的独立注释行**
——即仓库既有写法（`proc.py` L142~L144 已在覆盖 L145 调用点）：

```text
# 本模块是全项目唯一子进程封装层，导入 subprocess 即其职责。
# 豁免登记：docs/adr/0014-benchmark-automation.md §2.9
import subprocess  # nosec B404

        # 不经 shell、参数为列表；执行前施加非特权 uid + rlimit + 最小环境。
        # 豁免登记：docs/adr/0014-benchmark-automation.md §2.9
        completed = subprocess.run(  # noqa: S603
            argv,
            ...
        )  # nosec B603
```

**逐点措辞**：上表即为**最终稿**（每点两行：一句理由 + 一句登记），实现者**照抄**。
四点措辞为**单一来源**，实现者不得自造措辞（避免"登记与实现分叉"）。

**已实测的等价备选（`F2`，不推荐）**：`...  # <理由>  # nosec B404`（理由在前、标记放在最后）——
实测无告警、抑制正常；但自由文本仍留在标记所在的**同一行**，顺序一写反即回到上面的 hazard，故不推荐。

**验证方式（必须可执行；无验证视为未实现）**：

| # | 检查 | 命令 | 通过判据 |
| --- | --- | --- | --- |
| E1 | 无"把 prose 当规则号"的告警 | `uv run bandit -r src` | `WARNING ... Test in comment` **计数 = 0** |
| E2 | 豁免**精确**、不多不少 | `uv run bandit -r src` | `Total potential issues skipped due to ... #nosec` **计数 = 4**（1×`B404` + 3×`B603`） |
| E3 | 反向验证（无过度豁免） | `uv run bandit -r src --ignore-nosec` | 恰好 4 条：L23 `B404` + L145/L187/L223 `B603` |
| E4 | 标记注释内无"额外"规则号 | 对 `# nosec` 行做静态检查 | `# nosec` 后的 id 集合**恰好等于**预期集合（建议落成单测，防"理由里写了 `B6xx` 就顺手豁免"） |

> E4 是**防过度豁免**的机器检查；安全断言不得由实现者自证（`CODEBUDDY.md` §10.2 规则 4），
> 建议由验证工程师落成 `tests/security/` 用例。

**豁免点与承载关系（实测，一一对应、无多无少）**：

```text
$ uv run ruff check --select S603 --ignore-noqa src/agent_sec_perf/foundation/proc.py
S603 ... proc.py:145:21 / 187:… / 223:…
$ uv run bandit -q --ignore-nosec -r src/agent_sec_perf/foundation/proc.py
B404 @ L23 ; B603 @ L145 / L187 / L223
```

⇒ = **3 个调用点**（L145/L187/L223，每个同时受 ruff `S603` 与 bandit `B603` 管）
\+ **1 个 import**（L23，bandit `B404`）。

## 3. 备选方案与取舍

| 方案 | 为何不选 |
| --- | --- |
| 数据直接提交到 `develop` | 机器产出会污染主线历史，且每日几十个文件让 `git log` 不可读 |
| 只用制品库（不做数据分支） | 跨分支读取不方便（本项目的所有者需要"在别的分支随时看到最新数据"）；且制品会随保留策略消失 |
| 单分支（代码+数据同一分支） | CI 推数据会再次触发 `push` 流水线 ⇒ 死循环；靠 `ifModify` 过滤很脆 |
| 不做自动化，继续偶发手测 | 无法回答 §1 的三个问题；且脚手架每重建一次就丢一次 |
| 另建精简评测镜像 | 与开发镜像的 llama.cpp/资产会分叉；数据可比性优先于拉取时间。若拉取成为瓶颈再评估 |

## 4. 后果

**正面**：性能与能力有了可比的时间序列；测量脚手架随仓库走，重建环境不再丢；
判据（重复次数、极差、协议版本）由 schema 与测试强制，而不是靠记性。

**代价与已知限制**：

1. 温度 0 ⇒ 同一任务多次重复的产出几乎逐字相同，**重复的价值在性能而非能力方差**；
   能力的精度只能靠**扩任务集**提升（L0 子集，列入下一步）；
2. 夜轮数据的可比性依赖"协议冻结"纪律：改协议必须提版本，否则序列断裂；
3. `bench/data` 会持续增长（约 15 MB/月量级，含 30 天滚动日志），需在月度回顾时确认；
4. 定时任务的**执行身份是最新修改该配置者**（CNB 文档），若该账号被移出仓库，
   任务会失败——记入 runbook 的检查项。

## 5. 验证方式

- `make bench-round`（本地 S × 3）端到端跑通，产出 schema 合规的数据与报告；
- 缓存陷阱的回归测试：`tests/unit/test_bench_runner.py`；
- 发布链路以 `DRY_RUN=1 BENCH_ALLOW_LOCAL=1` 演练（校验 → 提交，不推送）；
- 首夜的真实性验证清单见
  [`engineering/benchmark-automation.md`](../engineering/benchmark-automation.md) §6。

---

## 修订记录

> 本 ADR 接受后**正文结论与决策不再原地修改**（ADR 只增不改）。
> 按 [`README.md`](README.md) 的约定，**只有状态栏与"指针"一类内容**
> （登记位置、文件路径等）可被后续更正，且必须在此逐条留痕
> （**照录原句 + 更正依据 + 日期**）。本节**不承载任何决策变更**。

- **2026-09-18**：**更正 §2.9 豁免登记表的登记位置——属"指针"类更正，不是结论变更。**
  原句（照录）：

  > `import subprocess`（ruff S404 / bandit B404） | `src/agent_sec_perf/bench/proc.py` | 承载项目的子进程需求 | 仅该模块

  更正为 `src/agent_sec_perf/foundation/proc.py`；第二行"同上，共 3 处"随第一行同步指向新路径。

  **依据**：`bench/{proc,paths,errors}.py` 已按
  [ADR-0015](0015-layering-and-reuse-boundary.md) §5.4.1 / D9 经 `git mv` **提升**为
  `foundation/`（提交 `617f564`，2026-09-18）。豁免注释**随文件一起移动**、内容未变：
  `# nosec B404`（import 行）、`# noqa: S603` + `# nosec B603`（3 个调用点：
  `run` / `run_inherit_env` / `spawn`）。安全基线要求"豁免实际位置"与"登记位置"一致，
  故此处必须同步——**豁免的范围、理由与影响面一律不变**（3 个调用点、仅该模块）。
  验证方式：`make check` 全绿（豁免在该位置被 ruff 与 bandit 实际接受）。

  **同日反向搜索（确认无残留活引用）**：以 `bench/proc.py` / `bench/paths.py` /
  `bench/errors.py` / `bench.proc` / `bench.paths` / `bench.errors` 在全仓搜索，命中仅 3 类，
  均**不是**豁免登记：
  ① `docs/engineering/doc-consistency-report.md`（记录员域的**当时快照**报告）；
  ② `docs/devlog/0013-…md`（开发日志，**按时间演进、禁止回溯修改**）；
  ③ `src/agent_sec_perf/foundation/errors.py` 的 docstring（"由 `bench/errors.py` 提升而来"
  ——**准确的来源标注**，非失效引用）。
  ①②的正确处置是"在**新一篇**里说明变更"，由各自域负责，本文不改。

  **同日的相关事实（仅登记，不改 §2.9 末段原文）**：§2.9 末段提到的机器检查
  `tests/unit/test_bench_encapsulation.py` 仍然有效；此外已新增
  `tests/unit/test_architecture_layers.py`（ADR-0015 §7.1 的 V1~V6），
  并把封装层判定由"文件名 `proc.py`"升级为"位于 `foundation/` 下的 `proc.py`"。

- **2026-09-18（追加，同日第二轮）**：**豁免登记的两项准确性更正**（均由实测驱动，非采信报告）。
  1. **规则号更正**：原表将 `import subprocess` 标为"ruff S404 / bandit B404"。实测
     `ruff rule S404` 报 "in preview … requires `--preview`"、`ruff check --select S404` 报
     "has no effect because preview is not enabled" ⇒ **`S404` 未启用，真正生效的只有 bandit B404**。
     原句（照录）：

     > `import subprocess`（ruff S404 / bandit B404）

     已更正为只保留生效规则（见 §2.9），并在 §2.9.1(a) 注明"若将来启用 `S404` 需重新登记"。
     对照：`S603` **不在** preview、当前生效，故第二行"ruff S603 / bandit B603"**不动**。
  2. **新增 §2.9.1(b)：逐点豁免理由**。安全基线要求"在该行写明理由"；实测理由是
     `foundation/proc.py` L142~L144 的**一段注释块、仅覆盖 1 个调用点**，其余 3 个豁免点
     （L23 与另外 2 个调用点）**该行无理由文本** ⇒ 属符合性缺口（**先于 D9 的文件移动就存在**，
     非移动引入）。§2.9.1(b) 为 4 个豁免点逐点给出**可直接抄成行尾注释**的措辞与放置规则，
     附解析安全性实测（追加中文文本后 ruff/bandit 仍正确抑制）。
     **豁免范围、理由实质与影响面不变**，仅把"理由"落到每一个点。
  3. **证据（实测命令与输出摘要见 §2.9.1）**：`ruff check --select S603 --ignore-noqa` 报
     L145/L187/L223；`bandit --ignore-nosec` 报同 3 行 `B603` + L23 的 `B404`
     ⇒ **3 个调用点 + 1 个 import**，与豁免一一对应、无多无少。

- **2026-09-18（当日第三次；**更正上一条的放置规则**）**：上一条给出的"理由**追加在行尾**"写法
  **经实测不安全，已作废**。原句（照录）：

  > **实测依据（2026-09-18，探针文件）**：豁免标记后**追加中文文本不会破坏解析**——
  > `# noqa: S603 —— …` 仍抑制 `S603` 且**不触发** `RUF100`；`# nosec B404 —— …` 仍被计为 1 条 skip。

  该结论**只对纯中文理由成立**：一旦理由里含有 ASCII 词元（如 `subprocess`、`ADR-0014 §2.9`），
  bandit 会把它们当作**规则号候选**并告警；**若其中恰好是合法规则号（如 `B101`），则被静默接受、
  使该行多豁免一条规则**——探针 `assert True  # nosec NOPE —— 说明中提到 B101 与 subprocess`
  下 bandit 退出码 `0`（`assert_used` 被豁免），即**理由文本造成了静默削弱安全检查**。

  故 §2.9.1 的放置规则已更正为 **`F1`：标记注释只写规则号，理由另起紧随其上的独立注释行**，
  并补充 **E1~E4** 四项**可执行**验证（含"`# nosec` 后的 id 集合恰好等于预期集合"的防过度豁免检查）。
  **豁免范围、理由实质与影响面均不变**；仅"理由放在哪里"这一条被更正。
