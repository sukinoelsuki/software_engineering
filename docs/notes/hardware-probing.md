# 硬件探测：同一个"核数"有六个答案

> 主题：做"动态硬件适配"之前，必须先解决**探测本身是否可信**。
> 来源：2026-09-15 在 CNB 开发容器内的实测；
> 设计应用见 [ADR-0010](../adr/0010-dynamic-hardware-adaptation.md)。

---

## 我以为

`lscpu` / `nproc` / `os.cpu_count()` / `/proc/cpuinfo` 读的是同一个事实，
随便用一个都行——它们都是"本机有多少核"。

## 实际是

**同一个容器里，"核数"至少有六个不同答案，其中一个是错的：**

| 来源 | 实测值 | 可靠性 |
| --- | --- | --- |
| `os.sched_getaffinity(0)` | **8** | ✅ **权威**——内核告诉我们"你能调度到哪些 CPU" |
| `nproc` | 8 | ✅ 尊重 affinity |
| `/sys/fs/cgroup/cpuset/cpuset.cpus` | **81-88** | ✅ 权威——我们被绑定在**宿主机的第 81~88 号物理核**上 |
| `cpu.cfs_quota_us / cfs_period_us` | 800000 / 100000 | ✅ 权威——配额 = **8 核** |
| `/proc/cpuinfo` 的 `processor` 数 / `/proc/stat` 的 cpu 行数 | 8 | ⚠️ 已被平台虚拟化，此处恰好正确 |
| `/sys/devices/system/cpu/online` | 0-7 | ⚠️ 同上 |
| **`/sys/devices/system/cpu/present`** | **0-383** | ❌ 泄露宿主机拓扑 |
| **`lscpu` 的 `CPU(s)`** | **384** | ❌ **直接给出错误答案** |

宿主真实规格是 `AMD EPYC 9K65 192-Core Processor`（单路、192 核、开超线程 = 384 逻辑核），
我们只被分配了其中 **8 个**（而且是**特定的** 81~88 号核）。

**为什么这很危险**：`lscpu` 是最"顺手"的命令。若按它推导线程数，
会得到"384 核"→ 启动 384 个推理线程 → 在一个只有 8 核配额的容器里**严重超订**，
性能大幅劣化，而且**不会报错**。

## 因此

1. **核数的唯一可信来源是"可调度集合"与"配额"**：
   - 首选 `os.sched_getaffinity(0)`（Python）或 `sched_getaffinity(2)`；
   - 交叉验证 `/sys/fs/cgroup/cpuset/cpuset.cpus` 与 `cpu.cfs_quota_us / cfs_period_us`。
   - **不要**用 `lscpu`、`/sys/devices/system/cpu/present` 推导可用并行度。
2. **探测必须"多源 + 记录来源"**：不能只存一个数字，要存"值 + 它来自哪个文件/调用"。
   当不同来源冲突时，**以可调度集合为准**，并把冲突本身记入探测报告
   （冲突是"环境与预期不符"的早期信号）。
3. **内存同理，要看"预算"而不是"总量"**：
   `/proc/meminfo` 的 `MemTotal` 是容器可见总量（本环境 16 GiB），
   还要与 `/sys/fs/cgroup/memory/memory.limit_in_bytes`（同样是 16 GiB）比对取**较小值**。
   在别的平台上两者可能不一致（`MemTotal` 可能报宿主机总量）。
4. **探测到"物理能力"不代表"可用预算"**：
   本环境 CPU 物理上是 EPYC 9K65、内存物理上远大于 16 GiB，
   但**我们能用的是 8 核 / 16 GiB**。适配必须以**预算**为准，否则结论无法外推到真实设备。
5. **探测结果必须进日志与测试报告**：否则性能数据无法复现，也无法解释"为什么选了这套配置"。

## 附：一个仍需实测的问题

Python 的 `os.cpu_count()` 在**语义上**不等价于 `sched_getaffinity`：
文档说它返回"系统中的逻辑 CPU 数"（即宿主机视角），但在本环境它返回了 8。
不同 Python 版本/平台行为可能不同，**因此不要依赖它**——
用 `len(os.sched_getaffinity(0))`，它对"我能用几个核"这个问题语义明确。**【待验证】**：在本地 VM 与 Termux 上复测该差异。
