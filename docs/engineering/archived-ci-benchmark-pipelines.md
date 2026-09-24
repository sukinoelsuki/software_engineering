# 归档：退役的基准跑测流水线配置（**不可执行，不得重新启用**）

> ⚠️ **本文件是历史证据，不是当前配置。**
> 其中的流水线已于 **2026-09-24** 随
> [ADR-0023](../adr/0023-ci-downgrade-to-manual-trigger.md) **全部退役**：
> 组织级「云原生构建」免费额度只有 **160 核时/月**且**按顶级组织共享**
> （额度与用量的 API 实测见
> [`../research/2026-09-25-quota-measurement-and-cost-model.md`](../research/2026-09-25-quota-measurement-and-cost-model.md) §3.1/§3.2）。
>
> **把本文件的内容抄回 `.cnb.yml` 会立刻重新产生构建桶消耗**（实测高峰一天 ≈19 核时，
> 见该文 §3.4）。机器检查会拦下它：
> `tests/unit/test_cnb_config.py::test_no_pipeline_runs_benchmarks_automatically`
> （黑名单：`crontab:` / `bench/nightly:` / `web_trigger_bench` / `bench-round` / `bench-publish`）。

---

## 1. 为什么保留而不是删除（所有者 2026-09-25 指令）

| 理由 | 说明 |
| --- | --- |
| **成果证明** | "一套可复现、有闸门、能发布数据的自动化跑测链路"是已取得的成果，需要可复读的载体 |
| **引用不失效** | [ADR-0014](../adr/0014-benchmark-automation.md) 与 `docs/devlog/0012`、`0013` 都引用这些段落的行号与设计；删除会让引用指向不存在的对象 |
| **可复读** | git 历史需要额外操作才能读懂；归档文本让第三方**不必翻历史**即可复核"当年怎么做的" |
| **答辩/交付** | 课程交付与答辩需要"过程与配置"的直观证据 |

## 2. 退役前后对照

| 项 | 退役前（2026-09-24 之前） | 退役后 |
| --- | --- | --- |
| 触发事件 | `bench/nightly` 的 `push`；`crontab: 0 4 * * 2-6,0`；`crontab: 0 4 * * 1`；`web_trigger_bench` | **无**（改人工 `make bench`） |
| `runner.cpus` | 8（内存 16 GiB） | 跑测仍 8 核，但**只在借来的开发环境里** |
| 数据出口 | `endStages` → `make bench-publish` → `bench/data` | **不变**（`bench/data` 仍是唯一出口） |
| 代码与脚本 | `src/agent_sec_perf/bench/`、`scripts/bench/publish.sh` | **保留并在用**（`make bench` 复用 `bench-round` / `bench-publish`） |
| 分支 | `bench/nightly`、`bench/data` | **保留**（前者不再被读取，后者仍是数据出口） |
| 四道可信闸门 | 由 CI 的 stage 结构**顺带**保证 | **迁移到脚本**（`scripts/bench/gates.py`，判据未变） |

## 3. 退役时的台账（可核对）

| 项 | 值 | 来源 |
| --- | --- | --- |
| 已入库轮次 | **7 轮**（2 × `push` + 5 × `nightly`） | `bench/data` 的 `index.json` |
| 已入库核时（轮次内） | **35.973 核时** | 同上 |
| 另有"花了额度但没入库" | 09-17 夜轮 ≈6.59 核时（发布 `Error 141`） | `docs/devlog/0012` |
| 文件数 | **678**（`bench/`），其中 `bench/daily/` **676** | `git ls-tree` 实测（2026-09-24） |
| **可比序列** | **5 条**同签名 nightly | `index.json` 的 `signature` 字段 |
| 本项目构建桶总消耗 | **90.44 核时**（09-01~09-24，占免费额度 **56.7%**） | `cnb charge get-repos-volume` |

## 4. 原文照录（摘自提交 `a1e5924` 的 `.cnb.yml` 第 205~391 行）

> **逐字原文**，未做任何改写。`bench-round` / `bench-publish` 等命令**当前仍然有效**
> （它们现在由 `make bench` 调用），只是**不再由任何流水线触发**。

```yaml
# ============================================================================
# 基准自动化（仅挂在 bench/nightly 分支）
#
# 为什么单独一条分支（而不是挂在 develop 上）：
#   1. **crontab 只支持单一明确分支名**（不支持 glob），必须选一条；
#   2. 定时任务取**该分支 HEAD** 的代码 ⇒ 把测试代码与日常开发分开，
#      才能保证"同一份协议反复测"——协议一变，前后数据就不可比（ADR-0014）；
#   3. 数据写入独立的数据分支 `bench/data`，**不回写本分支**，
#      因此不存在"CI 推数据 → 又触发一轮测试"的死循环。
#
# 三种触发各司其职（互不遮挡，见 docs/engineering/benchmark-automation.md）：
#   push        —— 改了测试代码立刻跑一轮（R=3，S/M 两档，快速反馈）
#   crontab     —— 周二~周日 04:00 夜轮（R=10，三档）；周一 04:00 深跑（R=20 + 线程 4 对照）
#   web_trigger —— 手动补跑（页面上点一下，不改变量）
#
# 关键设计点：
#   - `runner.cpus: 8` ⇒ 内存 = 8 × 2 GiB = 16 GiB；**不能降到 4 核**：
#     L 档常驻 8.70 GiB，4 核只有 8 GiB，会 OOM（ADR-0014）；
#   - 复用 `.ide/Dockerfile` 的镜像（模型与 llama.cpp 已预置）：构建输入未变时命中
#     制品库缓存，因此 nightly 不必每晚重新下载 GB 级权重；
#   - `lock` 串行化：保证同一时刻**只有一轮**基准在跑，否则两个 runner 抢 CPU
#     会让吞吐数字失去可比性（这是数据有效性的要求，不是省钱）；
#   - 发布放在 `endStages`：即使测量阶段失败也把**已完成的部分数据**落盘
#     （半份数据比没有数据好，但会被标记为 partial）；
#   - 发布阶段**不吞错误**：`make bench-publish` 失败会让构建变红。
#     2026-09-16 首次真实运行正是被 `|| echo "[warn] ..."` 掩盖成"绿着但没数据"
#     ——最难发现的一类失败，比直接报错糟得多。
# ============================================================================
bench/nightly:
  # --------------------------------------------------------------------------
  # 推送即跑：验证新的测试代码能跑通，不求统计精度
  # --------------------------------------------------------------------------
  push:
    - name: bench-push
      runner:
        cpus: 8
      docker:
        build:
          dockerfile: .ide/Dockerfile
          by:
            - .ide/settings.json
            - .ide/assets/models.txt
            - .ide/assets/benchmarks.txt
            - .ide/assets/references.txt
            - .ide/fetch-assets.sh
            - .ide/install-cnb-skills.sh
            - .ide/assets/cnb-skills.txt
      lock:
        key: bench-cpu
        wait: true
        timeout: 3600
      stages:
        - name: verify
          script: |
            set -eu
            make setup LOCAL_HOOKS=0
            make check LOCAL_HOOKS=0
        - name: round-quick
          script: |
            set -eu
            make bench-round BENCH_TIERS=S,M BENCH_REPEATS=3 BENCH_LABEL=push
      endStages:
        - name: publish
          script: |
            set -u
            make bench-publish

  # --------------------------------------------------------------------------
  # 夜轮：周二~周日 04:00（Asia/Shanghai）三档各重复 10 次
  # --------------------------------------------------------------------------
  "crontab: 0 4 * * 2-6,0":
    - name: bench-nightly
      runner:
        cpus: 8
      docker:
        build:
          dockerfile: .ide/Dockerfile
          by:
            - .ide/settings.json
            - .ide/assets/models.txt
            - .ide/assets/benchmarks.txt
            - .ide/assets/references.txt
            - .ide/fetch-assets.sh
            - .ide/install-cnb-skills.sh
            - .ide/assets/cnb-skills.txt
      lock:
        key: bench-cpu
        wait: true
        timeout: 3600
      stages:
        - name: prepare
          script: |
            set -eu
            make setup LOCAL_HOOKS=0
        - name: round-nightly
          timeout: 90m
          script: |
            set -eu
            make bench-round BENCH_TIERS=S,M,L BENCH_REPEATS=10 BENCH_LABEL=nightly
      endStages:
        - name: publish
          script: |
            set -u
            make bench-publish

  # --------------------------------------------------------------------------
  # 深跑：周一 04:00 —— 更多重复（20 次）+ 线程数对照（4 线程，R=5）
  #   线程 4 的轮次与线程 8 的轮次**不是同一条序列**（比较签名不同），
  #   用途是回答"核数变少时该降哪一档"，即 REQ-PERF-05/06 的取数。
  # --------------------------------------------------------------------------
  "crontab: 0 4 * * 1":
    - name: bench-deep
      runner:
        cpus: 8
      docker:
        build:
          dockerfile: .ide/Dockerfile
          by:
            - .ide/settings.json
            - .ide/assets/models.txt
            - .ide/assets/benchmarks.txt
            - .ide/assets/references.txt
            - .ide/fetch-assets.sh
            - .ide/install-cnb-skills.sh
            - .ide/assets/cnb-skills.txt
      lock:
        key: bench-cpu
        wait: true
        timeout: 7200
      stages:
        - name: prepare
          script: |
            set -eu
            make setup LOCAL_HOOKS=0
        - name: round-deep
          timeout: 150m
          script: |
            set -eu
            make bench-round BENCH_TIERS=S,M,L BENCH_REPEATS=20 BENCH_LABEL=deep
        - name: round-threads4
          timeout: 60m
          script: |
            set -eu
            make bench-round BENCH_TIERS=S,M,L BENCH_REPEATS=5 BENCH_THREADS=4 BENCH_LABEL=deep-t4
      endStages:
        - name: publish
          script: |
            set -u
            make bench-publish

  # --------------------------------------------------------------------------
  # 手动补跑：页面上点一下即可（不改变量，等价于夜轮）
  # --------------------------------------------------------------------------
  web_trigger_bench:
    - name: bench-manual
      runner:
        cpus: 8
      docker:
        build:
          dockerfile: .ide/Dockerfile
          by:
            - .ide/settings.json
            - .ide/assets/models.txt
            - .ide/assets/benchmarks.txt
            - .ide/assets/references.txt
            - .ide/fetch-assets.sh
            - .ide/install-cnb-skills.sh
            - .ide/assets/cnb-skills.txt
      lock:
        key: bench-cpu
        wait: true
        timeout: 3600
      stages:
        - name: prepare
          script: |
            set -eu
            make setup LOCAL_HOOKS=0
        - name: round-manual
          timeout: 90m
          script: |
            set -eu
            make bench-round BENCH_TIERS=S,M,L BENCH_REPEATS=5 BENCH_LABEL=manual
      endStages:
        - name: publish
          script: |
            set -u
            make bench-publish
```

## 5. 如需复原（**仅供理解历史，不推荐执行**）

1. 把 §4 的 YAML 重新并入 `.cnb.yml` 的顶层（它原本是**独立的顶层分支键** `bench/nightly`）；
2. 同步把 `web_trigger_bench:` 键加回；
3. **必须**同时修改 `tests/unit/test_cnb_config.py` 的
   `test_no_pipeline_runs_benchmarks_automatically`（否则门禁会拦下）——
   ⚠️ 这是一次**改门禁强度**（`F` 类）的动作，须**事先确认**并新增 ADR；
4. **先算成本**：`cpus × 时长`；
   定时夜轮一轮 ≈6.1~7.3 核时，深跑 ≈16 核时，且**高峰一天可达 ≈19 核时**
   ——在构建桶只剩 ≈69 核时余量、且要与同组织其它项目共享的前提下，这不划算。

## 6. 与其它文档的关系

- 决策：[ADR-0023](../adr/0023-ci-downgrade-to-manual-trigger.md)（为什么停、
  闸门搬到哪）、[ADR-0024](../adr/0024-quota-discipline-and-cost-model.md)（停完之后按什么成本纪律跑）
- 运行手册：[`benchmark-automation.md`](benchmark-automation.md) §2 的 `make bench`
- 停用前的盘点与证据保全：
  [`../research/2026-09-24-ci-consumption-summary.md`](../research/2026-09-24-ci-consumption-summary.md)
- 额度与成本模型：
  [`../research/2026-09-25-quota-measurement-and-cost-model.md`](../research/2026-09-25-quota-measurement-and-cost-model.md)
