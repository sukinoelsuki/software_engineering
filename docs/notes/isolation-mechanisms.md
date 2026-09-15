# 隔离与资源控制：按"机制"分类，而不是按"工具"

> 主题：Linux 上做隔离与资源限制到底有哪几类机制、各自靠什么生效、在本开发环境里哪些可用。
> 来源：[ADR-0007](../adr/0007-sandbox-capability-matrix.md)、
> devlog [0008](../devlog/0008-2026-09-15-环境资产预置与沙箱后端修正.md)；
> 验证方法见 [probe-methodology.md](probe-methodology.md)。

---

## 我以为

隔离能力是**工具**的属性：`bwrap` 强、`firejail` 中、`docker` 强。
所以"选个好工具"就解决了。

## 实际是

隔离能力是**机制**的属性，工具只是机制的外壳。同一个工具（docker）在不同维度上
**可以同时"有效"和"无效"**——取决于宿主是否委派了对应权限。

### 三类机制

| 机制类别 | 依赖什么 | 覆盖的维度 | 谁提供 |
| --- | --- | --- | --- |
| **命名空间 / mount 类** | 内核 namespace（mount、net、pid、user、ipc、uts） | 文件系统视图、网络、PID 可见性、用户身份、Linux capabilities | 容器运行时（docker daemon / bwrap） |
| **cgroup 类** | cgroup 控制器**且**被委派（可创建子组） | 内存上限、进程数上限、CPU 配额与权重、IO 权重 | 容器运行时创建 cgroup；**需要 cgroup 委派** |
| **setrlimit 类** | 内核 per-process rlimit（`setrlimit(2)`） | 地址空间、文件大小、进程数、打开文件数、CPU 时间 | **任意进程自己就能设**（也可由父进程设） |

**关键区别**：前两类**依赖宿主开放权限**，第三类**不依赖**——
只要有进程就有效。这是"资源限制该用哪一类"的判断依据。

### 本开发环境（CNB 容器 + 内置 docker 服务）的事实矩阵

| 项 | 事实 | 判定 |
| --- | --- | --- |
| cgroup 版本（开发容器） | **v1**（命名控制器：memory、cpu,cpuacct、pids、cpuset…） | — |
| 开发容器自身内存上限 | `memory.limit_in_bytes = 17179869184`（**16 GiB**） | ✅ **强制执行** |
| 开发容器自身 CPU 配额 | `cpu.cfs_quota_us = 800000`（= **8 核**） | ✅ **强制执行** |
| 能否自行创建子 cgroup | ❌ `/sys/fs/cgroup/memory` 归 `nobody:nogroup`、模式 `555`、无子组目录 | ❌ 无法委派 |
| 内置 docker 的模式 | **rootless**，`Cgroup Version: 1`，`Cgroup Driver: none` | ⚠️ 见下 |
| 子容器的文件系统隔离 | `--read-only` → 写入报 `Read-only file system` | ✅ 有效 |
| 子容器的网络隔离 | `--network=none` → `wget` 报 `bad address` | ✅ 有效 |
| 子容器的用户/能力 | `--user`、`--cap-drop=ALL` | ✅ 有效 |
| 子容器的内存上限 | `--memory=64m` 下逐页写满 **300 MB** 仍成功 | ❌ **无效** |
| 子容器的进程数上限 | `--pids-limit=16` 下起满 **50** 个进程 | ❌ **无效** |
| `--ulimit nproc/fsize` | `can't fork` / `File size limit exceeded` | ✅ 有效 |
| 进程内 `setrlimit` | `RLIMIT_AS` → `MemoryError`；`RLIMIT_FSIZE` → 报错 | ✅ 有效 |

**根因**：rootless docker 的 `Cgroup Driver: none` —— 它**不做任何 cgroup 管理**，
因此 cgroup 类的参数被**静默忽略**（不报错、不警告）。
而 namespace 类控制不需要 cgroup，所以照常生效。

---

## 由此澄清三个常见误解

| 误解 | 事实 |
| --- | --- |
| "我们没法自由使用 16 GiB 内存" | **反了**：16 GiB 是**被强制执行的我们的上限**，可以用满。受影响的只是"**无法给自己起的子容器设上限**" |
| "cgroup 限制削弱了端侧模型能力 / 能掌控的上下文" | **无关**。上下文上限由 **内存总量 + 模型与 KV 大小**决定，与 cgroup 无关。真正的上下文约束在**目标设备（8 GiB）**，不在开发机 |
| "这和性能关系很大" | 性能瓶颈是 **CPU 算力**（实测生成 ~16.5 tok/s），cgroup 完全不影响算力 |

---

## 因此（对今后的约束）

1. **做隔离设计时，先问"我要限制哪个维度"，再问"这个维度属于哪类机制"**，
   最后才选工具。**不要**用"这个工具很强"来代替这一步。
2. **资源限制一律用 setrlimit**：它在任何环境都有效，且不依赖宿主委派。
   **禁止**依赖 `--memory` / `--pids-limit` / `--cpus` 做资源防护——
   在本环境它们是**静默无效**的。
3. **能强制执行的边界（16 GiB / 8 核）要当作真实上限来规划**：
   实测 llama-server 常驻约 5 GiB，即我们连三分之一都没用到——**当前瓶颈是算力，不是内存**。
4. **无法用 cgroup 模拟目标设备（8 GiB）**：本环境不能创建子 cgroup。
   可行的近似是**对单个进程设 `RLIMIT_AS`**（对 llama-server 这类单进程负载有效），
   但它无法限制"多进程合计"，因此**结论必须标注为近似**。
5. **子容器没有资源上限 = 一个 runaway 子容器能吃掉整个开发容器的 16 GiB**。
   这是真实的自我伤害风险，靠"先提交（P-1）+ setrlimit 设上界 + 无上界用例归入 T3"规避
   （见 [`test-environments.md`](../engineering/test-environments.md)）。
6. **换环境必须重跑能力矩阵**：控制是否生效取决于宿主委派，不能沿用结论。
