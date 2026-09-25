# CNB 云原生开发/构建：**平台行为实测清单**（可复用资产）

- 性质：**事实清单**（不是本项目的决策记录）。另一项目可直接引用，无需读本仓库的 ADR。
- 实测日期：2026-09-25（北京）；平台：CNB SAAS 社区版
- 证据等级：✅ **实测**（有构建号/日志/API 返回值）、📄 **官方文档**、⚠️ **推断**（含样本数）
- 来源：探针 E1 `sn=cnb-gnb-1k3ao4u18`、探针 E2 `sn=cnb-m69-1k3arkfd8`、
  `cnb charge get-quota`、[官方文档](https://docs.cnb.cool/zh/)
- 本仓库背景文档：[ADR-0025](../adr/0025-benchmark-automation-moves-to-dev-bucket.md)、
  [探针协议与结果](../research/2026-09-25-bucket-attribution-probe.md)

> ⚠️ **引用规则**：每条都标了证据等级。⚠️ 推断项**不得**当作机制使用；
> n=1 的实测项在复现第二次之前，也**不得**写成"平台保证如此"。

---

## 1. 计费：两个桶，`total` 与 `free` 必须分开看

✅ `cnb charge get-quota --slug <组织>`（2026-09-25）：

| 计费项 | `total` | `free` | 备注 |
| --- | --- | --- | --- |
| `ci_in_sec`（构建-CPU） | 576000 s = **160 核时** | **160 核时** | `total == free` ⇒ **160 是硬顶，无提升空间** |
| `dev_in_sec`（开发-CPU） | 63360000 s = **17600 核时** | 5760000 s = 1600 核时 | 免费 1600，超出部分 **¥0.125/核时** |
| `ci_gpu_in_sec` / `dev_gpu_in_sec` | **0** | 0 | ⚠️ **GPU 节点一开就付费且无额度** |
| `credit_in_milli` | 7166000 | 500000 | AI credits（NPC、内置 CodeBuddy 等） |

⭐ **最容易犯的错**：拿 `used` 比 `free` 得出"超额"。
正确问法是两个：**"还能不能跑"（看 `total`）** 与 **"要不要钱"（看 `free`）**。
本项目曾把"开发桶超出免费额度 912.7 核时"写成"超额 157%"——**容量其实还剩 ≈15000**。

📄 每个**顶级组织**独立结算、按顶级组织共享；月初扣上月费用；用量管理页可查
（[pricing.md](https://docs.cnb.cool/zh/pricing.md)）。

---

## 2. 分桶判据：有没有 `services: [vscode]`

📄 [workspace-vs-build.md](https://docs.cnb.cool/zh/workspaces/workspace-vs-build.md)：
"云原生开发本质上是一条声明了 `service.vscode` 的云原生构建流水线……
分配**开发节点**，计费计入**云原生开发用量**"。

⭐ **推论（已实测）**：区分"构建"还是"开发"的判据是**有没有声明 `services: [vscode]`**，
**与触发方式无关** ⇒ 用 `api_trigger` / `branch.create` / `web_trigger` 自动拉起的流水线，
只要声明了 vscode 服务，就**仍然走开发桶**。

✅ 三条独立证据（探针 E1，事件 `api_trigger_quota_probe`）：

1. 它出现在 `cnb workspace list-workspaces --status running` 里；
2. 平台自打标签：`ARCH=amd64,cpus=1,memory=2,vscode=远程开发`；
3. 用量增量：`ci_in_sec` **+110 s**（= 两次 push 的 1 核门禁），`dev` 冻结量 **+37800 s**。

⇒ **可复用的自检方法**：看流水线标签里有没有 `vscode=远程开发`
（`cnb build get-build-logs --sn <sn>` 的 `pipelines[...].labels`）。

⚠️ 反向风险：**删掉 `services: [vscode]` 就静默掉回构建桶**，
流水线状态、日志、产物全都看不出区别 ⇒ 建议钉成机器检查（本项目已这么做）。

---

## 3. 环境生命周期（**最有价值的一组**）

### 3.1 ⭐ stages 正在跑的时候，**不会**被离线心跳杀掉

✅ **决定性证据（E2）**：声明 `keepAliveTimeout: 1m`，而 stages 跑了 **2 分钟**——
stages **完整跑完、未被中断**（`[probe2] t+0min / t+1min / stages-done` 全在）。
回收只发生在 stages **结束之后**。

⇒ **无人值守的自动化跑测是安全的**：只要脚本在跑，就不会被判离线回收。
（📄 官方 `workspace-recycling.md` 只写了"10 分钟未进入页面"这类面向用户的简化表述，
**没有**说明"stages 期间是否豁免"——这条是实测补的。）

### 3.2 `keepAliveTimeout` = **离线宽限期**，不是总时长；**下界是 5 分钟**

✅ 两次实测：

| 探针 | 声明 | 计时基准 | 判定释放 | 实际延迟 |
| --- | --- | --- | --- | --- |
| E2（1 核） | `1m` | 07:22:39 | 07:27:39 | **+5m** |
| E3（**8 核**，真实跑测） | `5m` | 07:45:06 | 07:50:06 | **+5m**（8 核同样成立） |
| 真实发布轮（**8 核**，`sn=cnb-til-1k3b07aqj`） | `5m` | — | — | `BeforeEnd` 阶段 **304 s ≈5m**（8 核上第三次成立） |
| E1（1 核） | `20m` | 06:31:45 | 06:51:45 | **+20m**（精确） |

✅ **E3 是端到端演练**（`sn=cnb-obc-1k3aslg93`，8 核，真实跑 `make bench`）：
`Offline recycling: 5m` → `The user has been offline (more than 5m) and starts to release the workspace.`
→ `- Service vscode beforeEnd [5m 4s]`；总 `duration 725965 ms`（**12.1 min**）；
stages 内跑测 `duration: 2m 23s`，四道闸门全过，`status: success`。
⇒ **"下界 5 分钟"在 8 核上同样成立**（此前只有 1 核证据）。

⇒ 平台**每 5 分钟**做一次连接检查（日志可见 `docker pull orangeci/check-is-remote-connections`）；
实际释放 = 基准 + **max(声明值, 一个检查周期)**。
⇒ **把声明值设得比 5 分钟更小没有意义**（E2 设 1m 仍是 +5m）。

⭐ **可直接复用的取值规则**：
- 想让环境"跑完尽快释放" ⇒ 设 **`5m`**（下界，空转 ≈5 分钟）；
- 想让人在跑完后还能进去看 ⇒ 设 20~30m；
- ⚠️ **不要设成小时级**（本项目一度设 90m ⇒ 8 核环境白活 90 分钟 ≈12 核时）。

### 3.3 释放流程与**可自检的日志**

✅ `beforeEnd` 阶段会打印当前环境的两条配置（**这是最好的自检点**）：

```text
- Service vscode beforeEnd...
Remote address (webide): https://…/vscode-web/<pipelineId>/
Maximum available: 18h
Offline recycling: 20m            ← 我声明的 keepAliveTimeout，确认生效
```

随后每 5 分钟一次：

```text
The user has been offline (not more than 20m) waiting for the user to connect.
用户已离线（离线时长未超过 20m），等待用户重连。
...
The user has been offline (more than 20m) and starts to release the workspace.
用户已离线（离线时长超过 20m），开始释放开发环境。
Backup status: no_backup, reason: 没有未提交的修改需要备份, time: 2026-09-25 06:51:45
```

释放动作（✅ E1 `end` stage）：`docker kill <pipelineId>-vscode` →
`docker kill <pipelineId>-network-bus` → `Service git-clone-yyds stoped`。

### 3.4 `endStages` **确实会执行**，且早于 vscode 服务停止

✅ E1：`[probe][endStages] 2026-09-24T22:53:52Z`（vscode 停止在 22:53:53）；
✅ E2：`[probe2][endStages] 2026-09-24T23:29:44Z`。

⇒ **可以用作"销毁前的最后兜底"**（例如外推产物、打印收尾信息）。
⚠️ 但**不得作为唯一的外推点**：它的触发时机由平台决定（宽限期到期，通常晚于跑测结束数分钟）。
⇒ 正确姿势：**产物外推放在 `stages` 的最后一步**，把 `endStages` 当保险。

### 3.5 代码备份：每 5 分钟把状态推到 `refs/stashes/...`

✅ E1 `beforeEnd` 日志（每约 5 分钟一次）：

```text
GIT_NOTES_REF=refs/notes/remote-stashes git notes add -f -m '{"id":"…","vscode":true,
  "status":"running","fileCount":0,"stashCount":0,"fileList":[],…}' HEAD
git push --no-verify -f origin <note_sha>:refs/stashes/<id>/<branch>/meta
```

- 载体是 **git notes + `refs/stashes/...`**，**不是代码分支**；
- 状态字段：`status` 由 `running` → `closed`，`fileCount` / `stashCount` 记录待备份内容；
- 无改动时：`Backup status: no_backup, reason: 没有未提交的修改需要备份`。

⚠️ **对跑测类任务的约束**：工作区里**未提交**的产物会被计入备份（`fileCount > 0`）
并被推到远端 stash ref。⇒ 中间产物应放在 `.gitignore` 覆盖的位置，或及时外推 + 清理。

### 3.6 程序化关闭：需要**账号级**权限，流水线内令牌做不到

✅ `cnb workspace workspace-stop --sn <sn>` 返回：

```text
403 / errcode 10023
The token's authorization scope does not match this request.
Missing required scopes: account-engage:rw
```

⇒ 流水线内的 `CNB_TOKEN` 是**仓库级**，关闭工作区属**账号级**。
⇒ **别指望"跑完自己关掉自己"**；要快速释放只能靠 §3.2 的宽限期，或人工在页面关。

### 3.7 三条回收机制与一次真实触发

📄 [workspace-recycling.md](https://docs.cnb.cool/zh/workspaces/workspace-recycling.md)：
① 心跳（默认 10 分钟，可用 `keepAliveTimeout` 声明）、② 最大保持 **18 h**、③ 不过夜
（使用 > 8 h **且**处于凌晨 4–6 点 ⇒ 强制回收）。

✅ **实测到一次 ③**（2026-09-25 05:35）：环境存活 ≈8.0 h、页面开着、落在 4–6 点内被回收
（⚠️ n=1；且"使用时间"的定义文档未说明，见 §7）。

✅ 平台注入的时长上限（比文档更硬）：`CNB_PIPELINE_MAX_RUN_TIME = 72000000 ms`（**20 h**，构建）、
`CNB_VSCODE_MAX_RUN_TIME = 64800000 ms`（**18 h**，开发）。

---

## 4. 触发方式（都能带 `services: [vscode]`）

📄 [custom-dev-pipeline.md](https://docs.cnb.cool/zh/workspaces/custom-dev-pipeline.md)
推荐的四个事件：`vscode` / `branch.create` / `api_trigger` / `web_trigger`。

| 方式 | 本项目取舍 | 复用建议 |
| --- | --- | --- |
| `api_trigger` | ✅ 采用 | **可编程**（`cnb build start-build --event api_trigger_x --branch …`）、不建分支、只有显式调用才跑 |
| `vscode` | ✅ 保留（人工入口） | 日常开发的入口 |
| `branch.create` | ❌ | ⚠️ 会为**每一个**新建分支拉起环境，副作用不可控 |
| `web_trigger` | ❌ | 与 `api_trigger` 重叠但不可编程 |
| `crontab` | ❌ | 定时不等人、不看当天有没有别的事（"按需"场景别用） |

⚠️ **分支键不要用多个 glob 重叠**：多个 glob 同时命中会**并行执行**
⇒ 用**精确分支键**（`test/amd64-8`）而不是 `test/*`，按分支名分派机器规格。

---

## 5. 机器矩阵（含两条禁用口径）

📄 [build-node.md](https://docs.cnb.cool/zh/build/build-node.md) + ✅ `get-quota` 实测：

| 架构 | `runner.tags` | 核数（默认） | 内存 | 额度 | 可用性 |
| --- | --- | --- | --- | --- | --- |
| amd64 | `cnb:arch:amd64` | 1~64（8） | cpus×2 GiB | 开发桶 17600 | ✅ |
| arm64/v8 | `cnb:arch:arm64:v8` | 1~**16**（8） | cpus×2 GiB | 同上 | ✅ |
| amd64+GPU | `cnb:arch:amd64:gpu` / `:gpu:L40` | 固定 16 | 48 GB 显存 | **`total = 0`** | ❌ 无额度 |
| riscv64 / loongarch64 | — | — | — | — | ❌ **无真机**，只能 qemu |
| 自托管 `namespace: group` | 自定义 | `cpus` 无效 | 宿主机 | 不计费 | ❌ **仅构建、不支持云原生开发** |

⚠️ 两条禁用口径（建议照抄）：
1. **第三方转载页把 arm64 写成 1~8 核，官方是 1~16 ⇒ 以官方为准。**
2. **qemu 模拟的架构，其结果只允许进"正确性/可移植性"结论，❌ 禁止进性能表**（无真机 ⇒ 不可比）。

---

## 6. 其它容易踩的坑

| # | 坑 | 依据 |
| --- | --- | --- |
| 1 | **Job 无输出 10 分钟即超时**（与 `keepAliveTimeout` 是**两个独立的 10 分钟**） | 📄 grammar.md §Job timeout |
| 2 | 阶段脚本由 `/bin/sh`（dash）执行 ⇒ `set -o pipefail` 会直接退出码 2 | ✅ 本项目 2026-09-16 故障 |
| 3 | 镜像用浮动标签（如 `python:3.12`）会随上游漂移 ⇒ **钉到发行版** | ✅ 同上 |
| 4 | 不声明 `runner.cpus` ⇒ 按**默认 8 核**计费（1 核能干的活会贵 8 倍） | 📄 build-node.md |
| 5 | `sandbox: true` 会让 `CNB_TOKEN` 失效 ⇒ 与"需要令牌发布"冲突 | 📄 grammar.md |
| 6 | `lock` 在 Pipeline / Stage / Job 三级都可用 ⇒ 并发控制可以做**机制**而非纪律 | 📄 grammar.md |
| 7 | **`--sha` 不能跨分支**：git-clone **只 fetch `--branch` 指定的 ref**，随后 `git checkout <sha>` ⇒ 该提交必须在**该分支上可达**，否则 `fatal: reference is not a tree`（退出码 128，Prepare 阶段失败）。⇒ 想验证"某个提交"必须让环境分支**快进**到它 | ✅ `sn=cnb-k12-1k3av99mv`（2026-09-25） |
| 8 | **开发节点机型不固定，且"同机型"仍有宿主级方差**：同一 `runner.tags`（`cnb:arch:amd64`）可落在不同 CPU 型号（历史 8 轮 `AMD EPYC 9754 128-Core`、2026-09-25 的 `AMD EPYC 9K65 192-Core`）；**而且 `cpu_model` 完全相同**的三次运行里，同档 prefill 为 **121.68 → 109.50 → 107.58**（≈**10%** 波动）⇒ 跨轮可比性只能靠签名，而"**同签名内的方差**"是**未量化**的噪声源，**不得**假定"同签名 ⇒ 同环境" | ✅ `sn=cnb-8g5-1k3avbsr4` / `cnb-til-1k3b07aqj` / `cnb-93a-1k3b6mmsn` 的 `env.json` 与索引，2026-09-25 |
| 9 | **镜像缓存看日志才知**：`local image cache miss` → `docker pull …/dockerfile-caches:<内容哈希>` → `remote image cache hit`。`.ide/Dockerfile` 与其 `build.by` 输入不变时**不重建**；Prepare 耗时随缓存位置在 **14 s ~ 4.6 min** 之间波动 | ✅ `sn=cnb-8g5-1k3avbsr4`（4.6 min）与 `sn=cnb-til-1k3b07aqj`（14 s） |
| 10 | **可归因的核时只有构建级 `metricCoreHours`**。⚠️ **不得**用组织 `charge get-volume` 的 `dev_in_sec` 增量做**单次归因**——它含**其他仓库与其他会话**的用量：实测一轮完整跑测 = `metricCoreHours` **5.83**，而同期组织 `dev_in_sec` **+57.3** 核时。（该条**更正**了本清单早前的表述：先前一次"2.71 vs 2.70 吻合"是**巧合**） | ✅ 2026-09-25（`sn=cnb-93a-1k3b6mmsn` 与 `charge get-volume` 两次读数） |
| 11 | **`stop-build` 能停掉尚未开跑的构建**：`status: cancel`，`run` 未开始 ⇒ **无产物落库**（发现配置不对时的止损手段） | ✅ `sn=cnb-01p-1k3b06kbh`（2026-09-25） |
| 12 | **分支键与事件的匹配语义（最容易配错的一处）**：多个键同时匹配某分支时，**只有声明了该事件的键**才产出流水线。⇒ 把跑测事件单独挂在 `develop:`（而 `"**"` 不含该事件）**不会**让门禁跑两遍。⚠️ **反例**：若 `develop:` 也声明 `push`，同一个 push 会被两个键**各触发一次**（真跑两遍）。另：同一键下**不同事件名互不冲突** ⇒ 事件名可以当"机器 / 架构"的**分派键** | ✅ 2026-09-25 查证：`trigger-rule.md`（多键并行执行）+ `grammar.md`（分支键下是「事件名 → Pipeline」映射）。⚠️ 本仓库此前"加 `develop:` 会让门禁跑两遍"的记载**只有在该键也声明 `push` 时成立**（属**半对**） |

### 6.1 宿主级方差：**同一签名下的实测台账**（2026-09-25）

⚠️ **机器是随机分配的**：`comparison_signature` 含 `cpu_model`，但**同一个 `cpu_model` 字符串下
仍有可观散布**。只要将来又落到同型号的机器上，这些读数就能与本表并起来看
⇒ **保留它们、并按"签名 + 轮次 id + `sn`"区分存放**（别当成"重复数据"丢掉）。

| 轮次 id（数据分支） | `sn` | tiers × R | S 档 prefill (tok/s) | S 档 gen (tok/s) | 说明 |
| --- | --- | --- | --- | --- | --- |
| `2026-09-25-amd64-8`（第 1 版） | `cnb-8g5-1k3avbsr4` | S × 3 | **121.68** | 27.24 | 发布路径验收轮；**随后被同 id 覆盖** |
| `2026-09-25-amd64-8`（第 2 版） | `cnb-til-1k3b07aqj` | S × 3 | **109.50** | 26.56 | 修 `make setup` 后重跑；**又被同 id 覆盖** |
| `2026-09-25-amd64-8`（现役） | `cnb-93a-1k3b6mmsn` | S,M,L × 10 | **107.58** | 33.84 | 完整一轮（现役索引条目） |

三条签名的 `cpu` 字段**完全相同**（`AMD EPYC 9K65 192-Core Processor`）：
- prefill 散布 **≈13%**（121.68 → 107.58）；
- gen 散布 **≈24%**（26.56 → 33.84），且**方向与 prefill 不一致**。

⚠️ **不得**把这些差异解释成"代码变慢/变快"：三次之间**测量路径没有变化**
（只有文档与"装 `mypy`"这类非测量改动）⇒ 差异来自**宿主 / 负载 / 次序**等未控因素，**成因未定**。
⇒ **可复用的结论**：跨轮比较**只能**看签名；而"**同签名内仍有 ≈10%~25% 量级的宿主级方差**"
是当前**未量化**的噪声源（处置方向未定：记为噪声量级，或把更强的宿主标识编进签名）。

> ⚠️ **为什么"被覆盖"的读数也写在这里**：`round_id` 是幂等键，**同 label 重跑会原地替换**
> ⇒ 前两版**不在** `index.json` 里，只存在于数据分支的 git 历史（提交 `0ac255c` / `2b03a74`）。
> 台账的作用就是让它们**可见**。**约定**：验证性 / 对照性跑测用**独立 label**，不要覆盖
> （见 [`benchmark-automation.md`](../../engineering/benchmark-automation.md) §2 的注）。

---

## 7. 尚未验证（**不要当成机制**）

| # | 项 | 为什么重要 |
| --- | --- | --- |
| V1 | `keepAliveTimeout` 能否突破 18 h / 不过夜 | 决定能不能跑超长任务 |
| V2 | "使用时间"的定义（存活时长 or 累计活跃） | 不过夜判据的分母 |
| V3 | 5 分钟检查周期是否稳定（负载/规格变化时） | §3.2 的下界是否可靠 |
| ~~V4~~ | ~~8 核下的释放延迟是否与 1 核一致~~ | ✅ **已解决**（E3：8 核同样 +5m） |
| V5 | 离线计时基准的精确定义（`beforeEnd` 起点 or 末次连接） | E1/E2 的两个基准对不齐 |
| V6 | 备份在有大量未提交产物时的行为与体积上限 | §3.5 的风险边界 |

---

## 8. 复现方法（探针模板，成本极低）

一次探针 ≈ **1 核 × 10 分钟 ≈ 0.17 核时**，能同时测：桶归属、stages 不被杀、宽限期、释放日志。

```yaml
# 分支键用**精确名**，避免与既有 glob 重叠
test/keepalive-probe:
  api_trigger_keepalive_probe:
    - name: keepalive-probe
      runner: { cpus: 1 }
      services:
        - name: vscode
          options: { keepAliveTimeout: 1m }   # 想测别的阈值就改这里
      docker: { image: python:3.12-bookworm }
      stages:
        - name: probe-short
          script: |
            set -eu
            echo "[probe2] start $(date -u +%FT%TZ)"
            i=0
            while [ "$i" -lt 2 ]; do
              echo "[probe2] t+${i}min $(date -u +%FT%TZ)"
              i=$((i + 1)); sleep 60
            done
            echo "[probe2] stages-done $(date -u +%FT%TZ)"
      endStages:
        - name: probe-end
          script: |
            set -eu
            echo "[probe2][endStages] $(date -u +%FT%TZ)"
```

```bash
git branch test/keepalive-probe develop && git push origin test/keepalive-probe
cnb build start-build --repo <slug> --branch test/keepalive-probe \
    --event api_trigger_keepalive_probe            # 记下 sn
cnb workspace list-workspaces --slug <slug> --status running   # 看是否存活
cnb build get-build-stage --repo <slug> --sn <sn> \
    --pipelineId <sn>-001 --stageId beforeEnd      # ★ 看 Maximum available / Offline recycling
```

**读日志的正确姿势**：关键证据全在 `beforeEnd`（配置与离线判定）与 `end`（释放动作）两个 stage，
**不在**你自己写的 stage 里。
