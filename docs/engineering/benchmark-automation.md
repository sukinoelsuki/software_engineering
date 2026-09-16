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
| 同上 `bench/daily/<日期>/report.md` | 该轮的完整报告（含超阈告警） | `git show FETCH_HEAD:bench/daily/2026-09-17/report.md` |
| 同上 `bench/daily/<日期>/{env,perf,capability}.json` | 环境指纹 / 性能 / 能力 | 逐字段可机读 |
| 同上 `.../logs/*.server.log` | 服务端日志（性能数字的**原件**） | 有争议时以它为准 |

**在别的分支上取数**：`git fetch origin bench/data` 即可，不必切分支，
也不需要改工作区——数据分支与开发分支互不干扰。

## 2. 三条常用命令

```bash
# 本地跑一轮（默认参数：三档 × 10 次；本地数据根目录 .bench-data，不入库）
make bench-round

# 快速自检（S 档 3 次，约 3 分钟）：改完测量代码先跑这个
make bench-round BENCH_TIERS=S BENCH_REPEATS=3 BENCH_LABEL=local

# 校验预置资产摘要（复用构建期脚本，不引入第二个真源）
make bench-verify-assets

# 发布数据（CI 用；本地演练必须显式加 DRY_RUN/BENCH_ALLOW_LOCAL）
DRY_RUN=1 BENCH_ALLOW_LOCAL=1 bash scripts/bench/publish.sh
```

**更新基准分支（重要）**：定时任务读的是 `bench/nightly` 的 HEAD 代码，
所以它必须与 `develop` 上**已验证**的基准代码保持一致：

```bash
# 在 develop 上跑完 make check 之后，同步过去（推送会立即触发一轮即时测试）
git push origin develop:bench/nightly
```

不要直接在 `bench/nightly` 上开发：它是"被定时任务读取的快照"，
不是开发分支——在它上面提交会让协议在没有 `make check` 把关的情况下生效。

可覆盖的变量：`BENCH_DATA_ROOT`、`BENCH_TIERS`、`BENCH_REPEATS`、`BENCH_THREADS`、
`BENCH_LABEL`、`BENCH_KEEP_DAYS`、`BENCH_MODEL_DIR`、`BENCH_ISOLATION`。

## 3. 自动触发（都挂在 `bench/nightly` 分支上）

| 触发 | 规模 | 何时用 |
| --- | --- | --- |
| `push` | S/M × R=3 | 改了测试代码，推上去自动验证 |
| `crontab: 0 4 * * 2-6,0` | 三档 × R=10 | 夜轮（周二~周日 04:00，Asia/Shanghai） |
| `crontab: 0 4 * * 1` | 三档 × R=20 + 线程 4 × R=5 | 深跑（周一 04:00） |
| `web_trigger_bench` | 三档 × R=5 | 页面手动补跑 |

所有触发共用一把锁（`bench-cpu`）：**同一时刻只有一轮在跑**。
并发测量会让吞吐数字失去可比性，所以这一步是硬约束，不是优化。

> **改流水线前必读两条硬规则**（`tests/unit/test_cnb_config.py` 会检查）：
>
> 1. **阶段脚本由镜像的 `/bin/sh` 执行**（Debian 12 上是 dash），
>    因此不要写 `set -euo pipefail` 这类 bash 专有语法——dash 会在第一行报
>    `Illegal option -o pipefail` 并以退出码 2 中止，**后续 stage 也不会执行**；
> 2. **镜像必须钉到发行版**（`python:3.12-bookworm`），不要用 `python:3.12` 这类浮动标签：
>    后者已从 Debian 12 漂到 Debian 13，导致同一份配置在不同时间行为不同，
>    并与开发镜像（bookworm）分叉。
>
> 这两条是 2026-09-16 那次 `bench-push` 失败的完整根因（§7 前两行）。

## 4. 参数与预算

`runner.cpus: 8` ⇒ 内存 16 GiB（内存 = 核数 × 2 GiB）。**不要降到 4 核**：
L 档常驻 8.70 GiB，4 核只有 8 GiB 会 OOM。

| 规模 | 实测耗时（2026-09-16，S 档 3 次 = 163 s）推算 |
| --- | --- |
| S × R=10 | ≈ 9 min |
| M × R=10 | ≈ 11 min |
| L × R=10 | ≈ 23 min |
| 三档 × R=10 + 判定 | ≈ 50 min（含每轮模型加载） |

月预算约 310 核时（≈ 配额的 1.8%）。**余量不要靠加重复用掉**：噪声只随 √n 下降；
把余量投向扩任务集与参数维度（见 ADR-0014 §2.3）。

## 5. 改协议 / 改任务集的正确姿势

任何会改变测量口径的改动（启动参数、提示词、判定方式、任务集）都**必须**：

1. 提升 `src/agent_sec_perf/bench/protocol.py` 的 `PROTOCOL_VERSION`；
2. 复核变异点：`t3_pure.py` 夹具里 `if start <= merged[-1][1]:` 必须仍能被替换命中，
   否则评测会**主动报错**（这是刻意的，避免把"夹具变了"误读成"模型变弱了"）；
3. 跑 `make bench-round BENCH_TIERS=S BENCH_REPEATS=3` 确认能跑通；
4. 在 devlog 里记一条：协议从哪个版本到哪个版本、为什么、旧数据如何处理。

> 记忆锚点：**跨版本的数据不得放在同一条序列上比较**。协议变了，之前的数字就只是历史。

## 6. 首次上线验证清单（第一夜之后逐项确认）

- [ ] 定时任务确实触发了（构建历史里有 `bench-nightly`，触发方式为定时）
- [ ] `bench/data` 分支出现了当天的目录，且 `index.json` 多了一条
- [ ] 该条记录的 `env.trigger.event` 与预期一致（`crontab`）、`env.isolation` 为 `user`
- [ ] `logs/` 与 `artifacts/` **都在**（若缺失，先查 `.gitignore` 的 `!bench/**` 豁免）
- [ ] 报告里的 `prefill` 极差在个位数百分比量级（若出现几十上百，先查缓存陷阱，见 §7）
- [ ] 实测耗时与核时接近 §4 的估算（偏离过大说明规模参数需要复核）
- [ ] 记录实际生效的 `CNB_CPUS`/`CNB_MEMORY`（确认 8 核 / 16 GiB 的假设成立）

## 7. 排障：症状 → 原因 → 处置

| 症状 | 最可能的原因 | 处置 |
| --- | --- | --- |
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
| 定时任务完全不触发 | 分支名/权限/负责人变更 | 定时任务只能挂**单一明确分支**；执行身份是"最后修改该配置的人"，账号被移出仓库会导致失败 |

## 8. 保留期与体积

| 内容 | 保留 | 原因 |
| --- | --- | --- |
| `index.json`、`latest.md`、`report.md`、三个 JSON | 长期 | 体积小，是趋势分析的输入 |
| `logs/`、`artifacts/`、`work/` | 30 天（`BENCH_KEEP_DAYS`） | 日志是原件但不是长期资产；`work/` 是判定中间物，**不入库** |

体积量级：约 0.5 MB/轮（压缩前后差异取决于模型产物体积），
按每日一轮估算约 **15 MB/月**。月度回顾时确认一次即可。

## 9. 相关文档

- 决策与取舍：[ADR-0014](../adr/0014-benchmark-automation.md)
- 判据为什么这么定：[`notes/evaluation-pitfalls.md`](../notes/evaluation-pitfalls.md)（情形四）
- 沙箱能力边界：[ADR-0007](../adr/0007-sandbox-capability-matrix.md)、
  [`test-environments.md`](test-environments.md)
- 环境验证清单：[`post-build-checklist.md`](post-build-checklist.md) §6
