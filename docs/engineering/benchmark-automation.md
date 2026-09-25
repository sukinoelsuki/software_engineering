# 基准自动化运行手册

> **定位**：本文件写"**怎么做**"；为什么这么做见
> [ADR-0014](../adr/0014-benchmark-automation.md)。
> 脚本入口统一为 `make bench-*`（CI 与本地跑**同一条命令**，CI 只改变量）。

---

## 1. 数据在哪、怎么读

| 位置 | 内容 | 读法 |
| --- | --- | --- |
| `bench/data` 分支 `bench/latest.md` | 最新一轮的一屏摘要 | `git fetch origin bench/data && git show FETCH_HEAD:bench/latest.md` |
| 同上 `bench/index.json` | 全部轮次的索引（签名、中位数、核时） | 机器读：用于画趋势或做对照 |
| 同上 `bench/daily/<轮次 id>/report.md` | 该轮的完整报告（含超阈告警） | `git show FETCH_HEAD:bench/daily/2026-09-17/report.md` |
| 同上 `bench/daily/<轮次 id>/{env,perf,capability}.json` | 环境指纹 / 性能 / 能力 | 逐字段可机读 |
| 同上 `.../logs/*.server.log` | 服务端日志（性能数字的**原件**） | 有争议时以它为准 |

**在别的分支上取数**：`git fetch origin bench/data` 即可，不必切分支，
也不需要改工作区——数据分支与开发分支互不干扰。

## 2. 一键跑测（`make bench`）

> **2026-09-24 起跑测不再由 CI 触发**：组织级「云原生构建」免费额度只有
> **160 核时/月**且按**顶级组织**共享 ⇒ 自动跑测全部下线
> （[ADR-0023](../adr/0023-ci-downgrade-to-manual-trigger.md)；实测账与口径分歧见
> [`../research/2026-09-24-ci-consumption-summary.md`](../research/2026-09-24-ci-consumption-summary.md)）。
> 跑测改为**在借来的云原生开发环境里人工一键触发**。
> ⚠️ **闸门没有跟着下线**：四道可信闸门从 CI 的 stage 结构**迁移进脚本**
> （`scripts/bench/run.sh` + `scripts/bench/gates.py`），**判据一条未改**。

```bash
# 一键跑测：跑一轮 → 四道可信闸门 → schema 校验 → 发布到数据分支
make bench

# 只跑 + 校验，不发布（演练改动时用这个）
BENCH_DRY_RUN=1 make bench

# 快速自检（S 档 3 次，约 3 分钟）：改完测量代码先跑这个
make bench BENCH_TIERS=S BENCH_REPEATS=3 BENCH_LABEL=local

# 校验预置资产摘要（复用构建期脚本，不引入第二个真源）
make bench-verify-assets
```

**四道闸门与它们的失败表现**（`make bench` 会逐道打印 `[gate ①..④][OK/FAIL]`）：

| 闸门 | 判据 | 不过时 |
| --- | --- | --- |
| ① 清 KV | 跑前清掉**同款** `llama-server` 残留；轮次内每次重复一个**全新**进程（`valid_prefill_repeats == repeats`） | 直接中止，不开跑 |
| ② token / 计时行 | 每份日志的 prefill 与 gen 各**恰好** 3 行，且每个 prefill 的被评估 token 数 ≥ 32 | 拒绝入库 |
| ③ 只暴露中位数 + 极差 | `index.json` 条目里**不得**出现单次采样（`values`） | 拒绝入库 |
| ④ schema 校验 | `rounds.validate_latest`（与发布脚本**同一套实现**） | 拒绝入库 |

> 记忆锚点：**「跑起来」≠「可信」**。少一道闸门，那一轮数据就不可比——
> 而且**不会有任何报错**，它只是静静地变成一条不能用的序列。

**环境要求**：跑测需要 **8 核 / 16 GiB**（L 档常驻 8.70 GiB，4 核只有 8 GiB 会 OOM）
与镜像内预置的模型 / `llama-server` ⇒ 它**只在云原生开发环境里**跑。
⚠️ **开发额度不是"用不完"的**：组织本月开发用量 **2512.7 核时**已超免费额度 1600
（实测见 [`../research/2026-09-25-quota-measurement-and-cost-model.md`](../research/2026-09-25-quota-measurement-and-cost-model.md)）
⇒ **跑测前必读 §3.1 的成本纪律**。运行分支建议用 `test/<slug>`
（见 [`.cnb.yml`](../../.cnb.yml) 头部与 [`CODEBUDDY.md`](../../CODEBUDDY.md) §2）。

可覆盖的变量：`BENCH_DATA_ROOT`、`BENCH_TIERS`、`BENCH_REPEATS`、`BENCH_THREADS`、
`BENCH_LABEL`、`BENCH_KEEP_DAYS`、`BENCH_MODEL_DIR`、`BENCH_ISOLATION`、`BENCH_DRY_RUN`。

> `make bench-round` / `make bench-publish` 仍在，但它们是**子步骤**：
> 单独跑会**绕过闸门**，只用于排障，不要用来产出可入库的数据。

## 3. 触发方式（**两个入口，都走云原生开发桶**）

> **2026-09-25 改写**（[ADR-0025](../adr/0025-benchmark-automation-moves-to-dev-bucket.md)）。
> 本节原写"不再有自动触发"，依据是"自动跑测 ⇒ 必烧构建桶"。**该前提已被实测推翻**：
> 区分构建/开发的判据是"**有没有声明 `services: [vscode]`**"，不是"它是怎么被触发的"
> （出处：https://docs.cnb.cool/zh/workspaces/workspace-vs-build.md；
> 探针 E1 实测：平台自打标签 `vscode=远程开发`，`ci` 增量仅 +110 s 而 `dev` 冻结量 +37800 s）
> ⇒ **自动化跑测可以走开发桶**（`total` = 17600 核时/月，余 ≈15000）。

| 入口 | 怎么触发 | 规模 | 何时用 |
| --- | --- | --- | --- |
| `api_trigger_bench`（**自动**） | `cnb build start-build --repo <slug> --branch test/amd64-8 --event api_trigger_bench` | 默认三档 × R=10 | 需要一轮可入库的数据，且**不需要人全程在场** |
| `make bench`（人工） | 在 `vscode` 环境里执行 | 同上 | 日常开发、顺手跑 |
| `make bench BENCH_TIERS=S BENCH_REPEATS=3` | 同上 | S × R=3 | 改完测量代码自检（约 3 分钟） |

> ⚠️ **两个入口都要求"先装 dev 依赖"**（`make setup`）。自动入口的 `prepare` 阶段跑它，
> 人工入口依赖 `vscode` 环境里已经跑过。**缺了它不会报错**：能力判定要跑 `mypy --strict`，
> 而 `mypy` 只在 `[project.optional-dependencies].dev` 里 ⇒ 每个任务的 `mypy_rc=1`
> ⇒ **能力通过率恒为 0%**，同时四道闸门全过、构建全绿、报告写着"无告警"
> （2026-09-25 实测：`sn=cnb-8g5-1k3avbsr4` 因此发出了一条 0% 的轮次）。
> 判据由 `tests/unit/test_cnb_config.py::test_bench_pipelines_install_dev_dependencies_before_running` 钉住。

**机器按分支名分派**：`.cnb.yml` 用**精确分支键**（`test/amd64-8`、`test/arm64-8`…）
声明各自的 `runner.tags` / `runner.cpus` ⇒ **分支上仍零提交**，配置集中在 `develop`。

> ⚠️ **触发前置（每次都要做，30 秒）**：`test/<slug>` **零提交** ⇒ 它的**引用不会自己前进**，
> 必须先把引用快进到被测提交：
>
> ```bash
> git push origin HEAD:refs/heads/test/amd64-8      # 只动引用、不新增提交（ADR-0027）
> git ls-remote origin refs/heads/test/amd64-8      # 核对：应与 git rev-parse HEAD 一致
> ```
>
> **不做这一步的后果是静默的**：环境从旧提交拉起 ⇒ **用旧代码测新修复**，
> 而日志、流水线状态、报告**全都看不出区别**（2026-09-25 实测踩到）。
> ⚠️ 两个写命令的坑：① `"$SHA:refs/..."` 在 zsh 下会被当成参数修饰符（`$SHA:r`）
> ⇒ 用 `HEAD:refs/...` 或 `${SHA}:refs/...`；② **不要把 `git push` 接进管道**
> （`| tail` 让退出码变成 `tail` 的 ⇒ 失败被吞掉，后续动作照常执行）。
> 详见 [ADR-0027](../adr/0027-test-branch-ref-must-track-the-commit-under-test.md)。

**不再采用的触发**（前两条由 `tests/unit/test_cnb_config.py` 拦下）：

- `crontab` 定时 —— 需求是"**按需**"而非"按点"（定时不等人、不看当天有没有别的事）。
  ⚠️ 理由已于 09-25 **变更**：不再是"烧构建桶"；
- `bench/nightly` 的 `push` 即时轮 —— 走构建桶；
- `web_trigger_bench` 页面手动补跑 —— 与 `api_trigger_bench` 重叠，且不可编程。

⚠️ **并发控制已恢复为机制**：跑测流水线带 `lock: {key: bench-cpu, wait: true}`
⇒ "同一时刻只有一轮"由平台保证（ADR-0023 §4.2 曾把它记为"退化为纪律"，**该条已被消除**）。
人工入口仍靠"一次只起一个环境"。两轮并发会互抢 CPU，吞吐数字立刻失去可比性——
这是**数据有效性**要求，不是省钱。

> **改 `.cnb.yml` 前必读两条硬规则**（`tests/unit/test_cnb_config.py` 会检查）：
>
> 1. **阶段脚本由镜像的 `/bin/sh` 执行**（Debian 12 上是 dash），
>    因此不要写 `set -euo pipefail` 这类 bash 专有语法——dash 会在第一行报
>    `Illegal option -o pipefail` 并以退出码 2 中止，**后续 stage 也不会执行**；
> 2. **镜像必须钉到发行版**（`python:3.12-bookworm`），不要用 `python:3.12` 这类浮动标签：
>    后者已从 Debian 12 漂到 Debian 13，导致同一份配置在不同时间行为不同，
>    并与开发镜像（bookworm）分叉。
>
> 这两条是 2026-09-16 那次 `bench-push` 失败的完整根因（§7 前两行）。
> 另注：门禁流水线必须**显式声明 `runner.cpus: 1`**——不声明就按平台默认 8 核计费。

## 3.1 人工拉起流程与成本纪律（[ADR-0024](../adr/0024-quota-discipline-and-cost-model.md)）

> **为什么这一节最重要**：开发环境按 `cpus × 存活时长` 计费。
> **8 核环境开机 1 小时 = 8 核时**，与在不在跑测无关。
> 一根夜轮 ≈6.1~7.3 核时，而**环境空转一天（8 h）= 64 核时 ≈ 9 根夜轮**
> ⇒ 成本的第一来源是**环境活着**，不是跑测次数。
>
> ✅ **2026-09-25 更正（E2/E3 + 当日 4 次实测）**：环境**会**在 stages 跑完后**自动释放**，
> 延迟 = `max(keepAliveTimeout, 一个 5 分钟检查周期)`（平台每 5 分钟做一次连接检查）
> ⇒ **成本 ≈ `cpus` × (跑测时长 + ≈5 min)**，**不是**"到人工关闭为止"。
> ⚠️ 本段此前写的是"环境不会跑完自毁、成本上界 = `cpus` × 到人工关闭的时长"
> ——那是 E1 阶段的**不完整观测**（据"到期不回收"判定），**已被推翻**；
> 流水线内 `cnb workspace workspace-stop` 仍因缺**账号级** `account-engage:rw` 被 403 拒绝，
> 但那只影响"**想立刻停**"这一场景（人工关闭 / 账号级令牌均属可选）。
> 详见 [ADR-0025 §2.3/§2.4](../adr/0025-benchmark-automation-moves-to-dev-bucket.md) 与
> [`../research/2026-09-25-cnb-platform-behavior-facts.md`](../research/2026-09-25-cnb-platform-behavior-facts.md) §3.2。
>
> 📌 **额度口径更正**（[ADR-0024 §1.2](../adr/0024-quota-discipline-and-cost-model.md) 的表述有误）：
> 该处写"开发桶 2512.7 / 1600 = **已超额 157%**"，是拿 `used` 比 `free`。
> 实测 `cnb charge get-quota`：`dev_in_sec.total` = **17600** 核时/月 ⇒ 余 ≈15000，
> **容量不是约束**；超出免费部分（≈912.7 核时）按 **¥0.125/核时**计**费**（≈¥114/月）。
> ⇒ 真正的**硬顶**是构建桶：`ci_in_sec.total == free == 160` 核时/月（已用 99.33）。
> **"要付费" ≠ "不够用"** —— 这一节管的是**钱**，不是额度。

**成本公式（背下来）**：

```text
开发桶成本 = runner.cpus × 环境存活小时数    （8 核 ⇒ 8 核时/小时）
构建桶成本 = runner.cpus × 流水线时长        （单核门禁 ⇒ 1 核时/小时）
```

**七步流程（谁在什么时候做什么）**：

| # | 步骤 | 判据 / 产物 |
| --- | --- | --- |
| ① | **决定**：需要一个新数据点吗？预计核时 = `cpus × 预计时长` | 写进最新一篇 devlog 的 §7（预算） |
| ② | **拉起**：在 `test/<slug>` 上借环境（**先把引用快进到 `develop` 的 tip**，见 §3 的触发前置） | ⚠️ 这一刻计费时钟开始 |
| ③ | **准备 + 跑测**：环境内 `make bench`，**一次跑完** | 四道闸门逐道 `[OK]` |
| ④ | **校验 + 发布**：schema 校验 → `bench/data` | 发布成功 = 数据落地 |
| ⑤ | **关闭**：发布成功**立即**关环境 | ⚠️ **"关闭"是流程的一步，不是收尾**；忘了关 = 直接损失 8 核时/小时 |
| ⑥ | **记账**：同一会话内写 devlog（原因 / 标签 / **实际核时** / 是否入库） | 实际核时取自 `index.json` 的 `core_hours` |
| ⑦ | **月度核对**：`make quota` → 把读数写进 devlog | 见下 |

**三条硬纪律**：

- `D-C1` **环境生命周期优先**：不做事就把环境关掉，**不允许"开着环境等"**。
- `D-C2` **核数与档位匹配**：只跑 S/M ⇒ 4 核；跑 L ⇒ 8 核；门禁 ⇒ 1 核。
- `D-C3` **预算与记账**：跑前报预计核时、跑后记实际核时、**月末核对组织额度**。

**月末核对（唯一动作）**：

```bash
make quota          # 组织额度 + 本月用量 + 按仓库拆分的 ci / dev
```

判据（[ADR-0024 §5](../adr/0024-quota-discipline-and-cost-model.md) 的 `Q1`，
**基数已按 2026-09-25 实测更正**）：

| 量 | 应见到的值 | 说明 |
| --- | --- | --- |
| `ci_in_sec.total` = `ci_in_sec.free` | **576000 s = 160 核时/月** | 构建桶是**硬顶**（`total == free`，无提升空间） |
| `get-volume` 的 `ci_in_sec` | 应**远小于** 160 | 跑测已迁出构建桶，只剩 1 核门禁 |
| `dev_in_sec`（`total`） | **63360000 s = 17600 核时/月** | 真正的容量池 |
| `get-volume` 的 `dev_in_sec` | 余量需 > 阈值 | 低于阈值 ⇒ [ADR-0025 §5](../adr/0025-benchmark-automation-moves-to-dev-bucket.md) **作废**，退回人工按需 |
| `*_gpu_in_sec.total` | **0** | GPU 节点无额度，机器矩阵里不可用 |

> ⚠️ **纪律的边界（不得表述为机制）**：⑦ 的核对与 ⑤ 的及时关闭都是**流程纪律**，
> **不可由 CI 强制**（CI 已不跑测，看不到这些动作）⇒ 按威胁模型口径记**部分缓解**。

---

## 4. 参数与预算

`runner.cpus: 8` ⇒ 内存 16 GiB（内存 = 核数 × 2 GiB）。**跑 L 档时不要降到 4 核**：
L 档常驻 8.70 GiB，4 核只有 8 GiB 会 OOM。
**但"一律 8 核"也是浪费**——只跑 S/M 时 4 核（8 GiB）就够
（实测峰值 S 2.67 / M 4.84 GiB）⇒ 核数与档位要匹配，见 §3.1（ADR-0024 的 `D-C2`）。
⚠️ "4 核跑 S/M"目前是**推论**：4 核下的耗时与峰值内存**没有实测**（待验证项 `W-2`）。

| 规模 | 实测耗时（2026-09-16，S 档 3 次 = 163 s）推算 |
| --- | --- |
| S × R=10 | ≈ 9 min |
| M × R=10 | ≈ 11 min |
| L × R=10 | ≈ 23 min |
| 三档 × R=10 + 判定 | ≈ 50 min（含每轮模型加载） |

**核时纪律（2026-09-25 再改写，原文见本段末尾的历史说明）**：一轮三档 × R=10
实测就是 **6.1 ~ 7.3 核时**（`bench/data` 的 `index.json`）。

- 走**开发桶**（现状）：容量 17600 核时/月、余 ≈15000 ⇒ **容量不是约束**；
  但超出免费额度部分按 **¥0.125/核时**计费 ⇒ **"每月跑几轮"算的是钱**；
- 走**构建桶**：硬顶 **160 核时/月**（`total == free`，已用 99.33，余 60.67），
  每日一轮（≈200 核时/月）会直接超掉整个组织的额度 ⇒ **跑测不得走构建桶**。

- 因此跑测改走开发桶、由 `api_trigger_bench` **显式**触发（ADR-0025）；
- 重复次数不要用来"用掉余量"：噪声只随 √n 下降，边际收益递减；
  需要扩的是**任务集**与**参数维度**（线程数对照），不是重复次数；
- ⚠️ 历史说明：本条原写"月预算约 310 核时（≈ 配额的 1.8%）"，其配额基数
  （≈17600）与官方免费额度表不是同一口径 ⇒ 按 160 计为 **194%**。
  口径分歧已登记在
  [`../research/2026-09-24-ci-consumption-summary.md`](../research/2026-09-24-ci-consumption-summary.md) §4.5；
  [ADR-0014](../adr/0014-benchmark-automation.md) §2.3 按"ADR 只增不改"**保留原文**。

## 4.1 机器矩阵（常量表，2026-09-25）

出处：[build-node.md](https://docs.cnb.cool/zh/build/build-node.md)、
`cnb charge get-quota`（额度），访问 / 实测日期 2026-09-25。
判据由 `tests/unit/test_cnb_config.py::test_runner_tags_are_limited_to_machines_that_actually_exist` 钉住。

| 架构 | `runner.tags` | 核数区间（默认） | 内存 | 最大时间 | 额度 | 可用性 |
| --- | --- | --- | --- | --- | --- | --- |
| amd64 | `cnb:arch:amd64` | 1 ~ 64（**8**） | cpus × 2 GiB | 18 h | 开发桶 17600 | ✅ 主力 |
| arm64/v8 | `cnb:arch:arm64:v8` | 1 ~ **16**（8） | cpus × 2 GiB | 18 h | 同上 | ✅ 端侧目标平台 |
| amd64 + GPU | `cnb:arch:amd64:gpu` / `:gpu:L40` | 固定 16 | 32 GiB / 48 GB 显存 | 18 h | **0** | ❌ **无额度** |
| riscv64 / loongarch64 | — | — | — | — | — | ❌ **无真机节点**，只能 qemu |
| 自托管 `namespace: group` | 自定义 | `cpus` 无效 | 宿主机 | — | 不计费 | ❌ **仅构建、不支持云原生开发**；本项目无自托管机 |

⚠️ 三条硬口径：

1. **第三方转载页把 arm64 写成 1~8 核，官方是 1~16 ⇒ 以官方为准。**
2. **qemu 模拟的 riscv64 / loongarch64 结果只允许进"正确性 / 可移植性"结论，
   ❌ 禁止出现在任何性能表或性能结论里** ——没有真机 ⇒ 数字不可比，而可比性是本项目的地基。
3. **跨架构不可比**：`bench/report.py::comparison_signature()` 已把 `cpu={cpu_model}`
   编进签名 ⇒ 换机器/换架构会**自动产生新签名**，不会与旧序列混比。
   ⇒ 机器矩阵产出的是"**按架构分组的各自序列**"，arm64 与 amd64 **不得**放进同一张性能表做差。

**平台注入的时长上限（实测值，比文档更硬）**：`CNB_PIPELINE_MAX_RUN_TIME` = 72000000 ms
（**20 h**，构建）、`CNB_VSCODE_MAX_RUN_TIME` = 64800000 ms（**18 h**，开发）。

## 4.2 分片、保活与关闭（**硬要求**，均来自 2026-09-25 实测）

| 项 | 值 / 做法 | 依据 |
| --- | --- | --- |
| **单片墙钟上限** | **≤ 60 分钟** | 远小于"不过夜"的 8 h 阈值，且落在 Job 默认超时 2 h 内 |
| 超过上限 | 该片**作废**，按外置进度**重跑**（幂等），不做"续着跑" | 避免半截状态 |
| **无输出超时** | 片内输出间隔 **< 10 分钟** | grammar §Job timeout；**与 `keepAliveTimeout` 是两个独立的 10 分钟** |
| 保活 | `services.vscode.options.keepAliveTimeout`（支持 `ms/s/m/h`） | **离线宽限期**（不是总时长）：失效即"离线"，下一个 5 分钟检查点释放；**设小值**（`5m`）⇒ 空转 ≈5 分钟（E2 1 核 / E3 8 核 / 2026-09-25 多次实测一致） |
| **产物外推点** | **每片最后一步，在 `stages` 内完成** | ⚠️ **不得**用 `endStages` 兜底：它是**销毁前**钩子，销毁时机不可控 |
| 进度外置 | `bench/data`（或制品库），重启后能续跑 | 环境随时可能消失 |
| **关闭** | **默认无需干预**：stages 跑完后 ≈5 分钟内由平台**自动释放**；"想立刻停"才人工关闭（账号级令牌自毁属 C 类、待裁决） | 实测：实际释放 = 基准 + `max(声明值, 一个 5 分钟检查周期)` ⇒ 成本 ≈ `cpus` × (跑测时长 + ≈5 min) |
| ⛔ 禁止 | 任何"定时重启保数据""模拟心跳保活" | 用脆弱手段对抗平台机制，属已知反模式 |

**环境回收的三条平台机制**（[workspace-recycling.md](https://docs.cnb.cool/zh/workspaces/workspace-recycling.md)）：

1. 心跳：10 分钟无 http/ssh 连接（**可用 `keepAliveTimeout` 声明**，默认 10 分钟）；
2. 最大保持 18 h；3. 不过夜：使用 > 8 h **且**处于凌晨 4–6 点 ⇒ 强制回收。
⚠️ **2026-09-25 05:35 实测到一次第 3 条**（环境存活 ≈8.0 h，页面开着），n=1。

## 5. 改协议 / 改任务集的正确姿势

任何会改变测量口径的改动（启动参数、提示词、判定方式、任务集）都**必须**：

1. 提升 `src/agent_sec_perf/bench/protocol.py` 的 `PROTOCOL_VERSION`；
2. 复核变异点：`t3_pure.py` 夹具里 `if start <= merged[-1][1]:` 必须仍能被替换命中，
   否则评测会**主动报错**（这是刻意的，避免把"夹具变了"误读成"模型变弱了"）；
3. 跑 `make bench-round BENCH_TIERS=S BENCH_REPEATS=3` 确认能跑通；
4. 在 devlog 里记一条：协议从哪个版本到哪个版本、为什么、旧数据如何处理。

> 记忆锚点：**跨版本的数据不得放在同一条序列上比较**。协议变了，之前的数字就只是历史。

## 6. 首次上线验证清单（**已失效，2026-09-24**）

> ⚠️ **本节与 §6.1 描述的是"定时任务上线"的验证，已随 ADR-0023 一并失效**
> ——定时任务已从 `.cnb.yml` 整体移除，不再有"第一夜"。
> **原文保留**（它是当时真实做过的验证过程，属"当时怎么想"的记录），
> 但**不要再按它执行**。当前对应的验证清单见 §2 与
> [`../research/2026-09-24-ci-consumption-summary.md`](../research/2026-09-24-ci-consumption-summary.md) §7。

- [ ] 定时任务确实触发了（构建历史里有 `bench-nightly`，触发方式为定时）
- [ ] `bench/data` 分支出现了当天的目录，且 `index.json` 多了一条
- [ ] 该条记录的 `env.trigger.event` 与预期一致（`crontab`）、`env.isolation` 为 `user`
- [ ] `logs/` 与 `artifacts/` **都在**（若缺失，先查 `.gitignore` 的 `!bench/**` 豁免）
- [ ] 报告里的 `prefill` 极差在个位数百分比量级（若出现几十上百，先查缓存陷阱，见 §7）
- [ ] 实测耗时与核时接近 §4 的估算（偏离过大说明规模参数需要复核）
- [ ] 记录实际生效的 `CNB_CPUS`/`CNB_MEMORY`（确认 8 核 / 16 GiB 的假设成立）

## 6.1 如何确认"定时任务真的注册了"（**没有查询接口**）

先说结论：**CNB 不提供查询定时任务列表的能力**——swagger 里与定时任务相关的只有一个
`POST /{repo}/-/build/crontab/sync/{branch}`（且只返回通用结果，不返回任务列表），
官方文档也是"配置即管理"，没有列出任务的界面说明。因此只能用**功能证据**，分三层：

| 层级 | 做法 | 证据强度 |
| --- | --- | --- |
| **① 一次性自检** | 临时加一条零副作用任务：`"crontab: */10 * * * *"`（每 10 分钟，避免受推送时间影响），用 `alpine` 只 `echo`、**不挂锁**；推送后等它触发 | **强**：证明"键格式被接受 + 注册生效 + 平台 cron 会触发"（与 04:00 两条同一机制）。**验证完必须删除该键**，否则会一直触发 |
| **② 例行证据（当前采用）** | 每天 04:00 之后查当天是否有 `event=crontab` 的构建；连续几天缺席即为故障信号 | 强（滞后一天，但零额外成本） |
| **③ 配置侧旁证** | 平台解析配置时会校验 crontab 表达式（文档：低于最小间隔的配置无法提交）⇒ 说明该键被读到过 | 弱：**不能**证明已注册 |

> **2026-09-16 的取舍**：首次上线时选择**跳过 ①、直接等 04:00 的 ②**——
> 理由是每推送一次 `bench/nightly` 都会额外触发一轮 ~20 分钟的基准测试，
> 为验证一件"最迟次日必然揭晓"的事付这个成本不划算。
> 若 04:00 没有触发，再按 ① 加自检键定位：**自检能触发但 04:00 不触发 ⇒ 问题在表达式本身**。

查询命令（只读）：

```bash
curl -s -H "Authorization: Bearer $CNB_TOKEN" -H 'accept: application/json' \
  "https://api.cnb.cool/<repo-slug>/-/build/logs?sourceRef=bench/nightly&event=crontab&page_size=5"
```

> **若已接入 `cnb-cli`**（[ADR-0016](../adr/0016-cnb-platform-integration-and-remote-write-authorization.md)）：
> 上述查询可用 `cnb` 命令替代 `curl`；但**不得**用它绕过
> [`git-workflow.md`](git-workflow.md) §4 的**授权分级**——平台写能力
> （建 PR / 合并 PR / 触发流水线）仍按 A~F 分级处置。

> ⚠️ **2026-09-17 实测更正：不能用这条命令确认"定时任务是否触发"。**
> 同一令牌下的实测：不带过滤只返回**最近的几条**构建——推送后为 `total=3`
> （当前 vscode 会话 + 刚推送的 `develop` / `bench/nightly` 两条 push）；
> 而 12 小时前 04:00 的那次 crontab 构建**不在返回中**；
> 加 `sourceRef=bench/nightly` 或 `event=crontab` 过滤均返回 `total=0`（参数不生效）；
> `swagger.json` 需要登录（`errcode 16`）。
> 因此 **不能把"查不到"当作"没触发"的证据**：它适合回答"我刚推的这轮跑了没有"，
> 不适合回答"昨夜 04:00 到底触发过没有"。
> 当前可靠的判定只有两条：① 网页上的构建历史（人看）；
> ② **发布结果**——数据分支上出现了当天的目录，才说明"触发 + 测量 + 发布"整条链路都通。

> 判定要点（网页侧）：出现 `event=crontab` 的构建 = 注册与触发都正常。
> 若自检能触发、而 04:00 的夜轮不触发，则问题不在注册机制，而在**该条表达式本身**
> （时区、星期字段、或"负责人被移出仓库"，见 §7 最后一行）。

## 7. 排障：症状 → 原因 → 处置

| 症状 | 最可能的原因 | 处置 |
| --- | --- | --- |
| **流水线是绿的，但 `bench/data` 没出现** | 发布阶段被 `\|\| echo` 吞掉；或推送 refspec 写法不对 | 查 `endStages` 的日志；推送必须用**全限定引用名**（见下一行）。已去掉吞错误的写法：发布失败会让构建变红 |
| `make: *** [Makefile:NNN: bench-publish] Error 141` | `… \| head` 在 `set -o pipefail` 下：head 读完若干行即退出，生产者后续写入收到 SIGPIPE（141），整条管道被判为非零 ⇒ 发布中止 | 不要用管道把 git 输出接到 `head`：**先落盘、再截断**（脚本已修，`tests/unit/test_cnb_config.py` 钉住）。**它只在"输出超过截断行数"时出现**：09-17 的 135 个文件必现、09-16 的 6 个文件不暴露 |
| 发布之后历史轮次（日志 / 产物 / 报告）消失 | 发布脚本**整体替换**了数据子目录（`rm -rf … bench` + `cp -a`），而 CI 的数据根目录只有本轮 | 发布必须是**合并**：只写本轮日期目录，索引经 `--merge-into` 按 `round_id` 并入（脚本已修，`tests/unit/test_cnb_config.py` 钉住） |
| 发布报 `error: The destination you provided is not a full refname` | 远端已存在**带斜杠**的分支（如 `bench/nightly`）时，`HEAD:bench/data` 这类短写法会被 git 拒绝 | 目标写成 `HEAD:refs/heads/bench/data`（脚本已修正）；此现象在本地假远端可复现 |
| 同一份协议在两个环境数字差 20%+ | 不同机器（CI runner 与开发容器实测差 25% 以上） | 比较签名已含 CPU 型号：跨环境数据**各成一条序列**，不要直接比 |
| 流水线第一行就报 `sh: 1: set: Illegal option -o pipefail`（退出码 2） | 阶段脚本由镜像的 `/bin/sh`（dash）执行，而脚本用了 bash 专有的 `pipefail` | 改为 `set -eu`；需要 pipefail 时显式切 bash。**注意**：非 `-e` 的 `set -uo pipefail` 会让整段脚本中止，看起来像"什么都没做" |
| 同一份 `.cnb.yml` 昨天能跑、今天同一处挂 | 镜像用了浮动标签（`python:3.12` 已从 Debian 12 漂到 13，dash 版本随之变化） | 镜像钉到发行版（`python:3.12-bookworm`），与开发镜像同源；两条硬规则见 §3 的提示框 |
| 只有某个分支/某条流水线挂，另一条正常 | 两条流水线用的镜像不同（本例：门禁用 `python:3.12`，基准用 `.ide/Dockerfile` 的 bookworm） | 先比镜像，再比脚本：**同一份配置在不同基础镜像上语义可能不同** |
| `prefill` 极差几十~几百 % | 服务端复用 KV 前缀，"1 token 的 prefill"混进来了 | 确认每次重复都重启了服务；看日志里 `prompt eval time = ... / 1 tokens` 是否存在；`valid_prefill_repeats < repeats` 即为该情形 |
| 某档 `timing_lines_ok` 为假 / 计时行数不符 | 日志格式或请求数变了（升级 llama.cpp、改了任务数） | 看 `logs/<档>_r01.server.log` 的 `slot print_timing` 行数；确认后提协议版本 |
| 输出为空且 `finish_reason=length` | **预算耗尽**，不是能力不足 | 报告会自动标红；调 `--max-tokens` 或关思考（见 `notes/thinking-mode-and-token-budget.md`） |
| `IsolationError` | 无法切到非特权 uid（不可回退） | 确认以 root 运行、`.venv` 对 others 可读；**不要**改用 `--isolation root` 掩盖（报告会把它标成不可用于安全断言） |
| 报告出现"隔离模式为 root"告警 | 本地用了 `BENCH_ISOLATION=root` | 该轮只用于本地调试，不要发布 |
| 发布失败：`拒绝发布` | 产出未通过 schema 校验 | 按日志指出的字段修；**不要**放宽校验 |
| 发布失败：缺 `CNB_TOKEN` / 权限不足 | 该事件的令牌权限不含 `repo-code:rw` | 查 CNB 文档的事件权限表；必要时把发布改挂到 `push` 事件（可信事件） |
| 提交里只有 JSON、没有日志/产物 | `.gitignore` 的 `*.log` / `artifacts/` 生效了 | 确认 `.gitignore` 末尾有 `!bench/**`（后面的规则覆盖前面的） |
| 任务一直"无输出"被杀 | 单任务无输出超时（默认 10 分钟） | 轮次是逐请求打日志的，正常情况下不会触发；若触发，查是不是卡在模型下载/镜像拉取 |
| 定时任务完全不触发 | 分支名/权限/负责人变更 | **已不适用**（2026-09-24 起不再有定时任务，见 ADR-0023）；保留原文作为历史 |
| `make bench` 报 `[gate ...][FAIL]` | 该轮产出不满足四道闸门之一 | **不要放行**。按闸门号对照 §2 的表定位：① 有残留进程或某次重复被丢弃；② 计时行数≠3 或 token<32；③ 索引里混进单次采样；④ schema 校验不过。修的是**原因**，不是闸门 |
| **能力通过率恒为 0%**，而性能、闸门、构建状态全正常 | 缺 dev 依赖：评测要跑 `mypy --strict`，而 `mypy` 只在 `[project.optional-dependencies].dev` 里，**只有 `make setup` 会装**。症状是每条判定 `mypy_rc=1` / `No module named mypy`（在 `daily/<轮次 id>/capability.json` 里可逐条核对） | 跑测前先 `make setup`（自动入口已固化为 `prepare` 阶段）。⚠️ **不要**把 0% 当作"模型能力"写进任何结论 |

## 8. 保留期与体积

| 内容 | 保留 | 原因 |
| --- | --- | --- |
| `index.json`、`latest.md`、`report.md`、三个 JSON | 长期 | 体积小，是趋势分析的输入 |
| `logs/`、`artifacts/`、`work/` | 30 天（`BENCH_KEEP_DAYS`） | 日志是原件但不是长期资产；`work/` 是判定中间物，**不入库** |

体积量级：约 0.5 MB/轮（压缩前后差异取决于模型产物体积），
按每日一轮估算约 **15 MB/月**。月度回顾时确认一次即可。

**发布语义（2026-09-17 修正）**：`make bench-publish` 是**合并**——只写入本轮自己的
`daily/<轮次 id>/`，索引由 `--merge-into` 按 `round_id` 并入，`latest.md` 覆盖为最新一轮。
**任何情况下都不会删除历史轮次**。早期版本是"整体替换数据子目录"，在 CI 上会删掉
历史轮次（数据根目录只有本轮），已废弃并被测试钉住。

> ⚠️ **轮次目录名的变化（2026-09-25）**：目录名 = **轮次 id**（`<日期>-<标签>`），
> 不再是纯日期；09-25 之前发布的历史轮次仍是**纯日期**目录，两者并存、都可读。
> 起因是一个此前从未触发的覆盖缺陷：按日期命名时，同一天的**第二轮**与第一轮共用目录，
> 而发布脚本对目标目录是"先删后拷" ⇒ 第一轮的日志与产物被删，且索引里第一轮的
> `report` 仍指向该目录（**索引指向错报告、原件永久丢失**）。发现时数据分支上正有
> `2026-09-25-nightly` 一轮（目录内 124 个文件）⇒ 当日那轮"真实发布"被推迟到修复后。
> 反例已由 `tests/unit/test_bench_store.py` 的三条用例钉住（含一条静态检查，
> 防止调用点被改回按日期命名）。

> ⚠️ **保留期清理仍未真正生效（2026-09-24 复核，口径更新）**：
> `BENCH_KEEP_DAYS` 只在**跑轮次**时对**本地数据根**生效，而跑测环境里的
> `.bench-data` 通常也只有本轮 ⇒ 历史轮次的清理依旧**没有执行者**。
> 清理需要在数据分支的工作副本上做（原登记见 devlog 0012 §7）。
> 2026-09-24 起 CI 已不再跑基准，这条"没有执行者"的性质没变、只是原因换了
> ⇒ **不要再按"CI 会清理"去理解**。

## 9. 相关文档

- 决策与取舍：[ADR-0014](../adr/0014-benchmark-automation.md)（§2.2/§2.3 已被下述两篇修订；
  旧 ADR 正文按"只增不改"保留）
- **停用自动跑测 + 闸门迁移**：[ADR-0023](../adr/0023-ci-downgrade-to-manual-trigger.md)
- **额度纪律与成本模型**：[ADR-0024](../adr/0024-quota-discipline-and-cost-model.md)
  （§3.1 的流程与三条硬纪律出自它）
- **退役流水线配置的归档**（原文照录，**不可重新启用**）：
  [`archived-ci-benchmark-pipelines.md`](archived-ci-benchmark-pipelines.md)
- 额度与用量的**实测数据**（含成本模型推导）：
  [`../research/2026-09-25-quota-measurement-and-cost-model.md`](../research/2026-09-25-quota-measurement-and-cost-model.md)
- 停用前的盘点与证据保全：
  [`../research/2026-09-24-ci-consumption-summary.md`](../research/2026-09-24-ci-consumption-summary.md)
- 判据为什么这么定：[`notes/evaluation-pitfalls.md`](../notes/evaluation-pitfalls.md)（情形四）
- 沙箱能力边界：[ADR-0007](../adr/0007-sandbox-capability-matrix.md)、
  [`test-environments.md`](test-environments.md)
- 环境验证清单：[`post-build-checklist.md`](post-build-checklist.md) §6
