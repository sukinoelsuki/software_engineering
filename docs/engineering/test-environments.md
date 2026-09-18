# 测试环境分层与成果保护

> **决策依据**：[ADR-0008](../adr/0008-dev-test-environment-strategy.md)（为什么这样分层）、
> [ADR-0007](../adr/0007-sandbox-capability-matrix.md)（各隔离机制在哪些环境有效）。
> 本文只写**怎么做**，不重复论证。
>
> **一句话原则**：隔离的是**执行场所**，不是**代码**。
> 工作区始终共享，破坏性测试放进一次性容器，并且**在容器里物理上改不了源码**。

---

## 1. 分层总表

| 层级 | 影响范围 | 载体 | 资源限制方式 | 网络 | 典型用例 |
| --- | --- | --- | --- | --- | --- |
| **T0** | 仅进程内 | 开发容器 `uv run pytest` | 无 | 正常 | 纯逻辑单测、`make check`、格式与类型 |
| **T1** | 子进程 / 临时文件 | 开发容器内**受限子进程**（L1 档本身） | `setrlimit` + 路径白名单 | 默认拒绝 | 工具权限、路径穿越、rlimit 生效性 |
| **T2** | **可能影响容器自身** | **一次性容器** `docker run --rm` | `--ulimit`（**不要用** `--memory`/`--pids-limit`） | `--network=none` | 越权出站、越界写、危险命令、有界资源耗尽 |
| **T3** | 宿主 / 需干净环境 | 独立容器（CI / 本地 CI 预演）、本地 VM | 由载体决定 | 由载体决定 | 无上界资源耗尽、`bwrap` 等命名空间特性 |

---

## 2. 判定清单：这个用例该放哪一层？

按顺序问三个问题，**第一个"是"即决定层级**：

| # | 问题 | 是 → | 否 → |
| --- | --- | --- | --- |
| 1 | 用例会**故意**耗尽整机资源（无上界的内存/进程/磁盘），或需要**命名空间/内核特性**（如 `bwrap`）？ | **T3** | 问 2 |
| 2 | 用例会写文件、删文件、发网络请求、起子进程、或执行来自模型的**不可信输入**？ | **T2** | 问 3 |
| 3 | 用例只在进程内做纯计算与断言？ | **T0** | **T1** |

> 拿不准时**取高层级**（T2 > T1 > T0）。多跑一次一次性容器的成本，远低于一次误伤。

---

## 3. 各层可直接复制的命令

### 3.1 T0：日常回归

```bash
cd /workspace
make check            # hooks-check + format-check + lint + typecheck + test + security
make test             # 只跑非 benchmark 测试
```

### 3.2 T2：一次性容器（破坏性测试的主载体）

以下命令已在本环境**逐项实测通过**（2026-09-15，证据见
[ADR-0008 §3](../adr/0008-dev-test-environment-strategy.md) 与下方注释）：

```bash
docker run --rm \
  --network=none \
  --read-only \
  --cap-drop=ALL \
  --user 65534:65534 \
  --ulimit nproc=64:64 \
  --ulimit fsize=67108864:67108864 \
  --ulimit nofile=256:256 \
  --tmpfs /tmp:rw,size=64m,mode=1777 \
  --tmpfs /scratch:rw,size=64m,mode=1777 \
  -v "$PWD":/work:ro \
  -w /scratch \
  alpine:3.20 \
  sh -c '<在此写入被测命令>'
```

各参数的作用与**实测结论**：

| 参数 | 作用 | 实测 |
| --- | --- | --- |
| `--rm` | 退出即销毁，不留状态 | ✅ |
| `--read-only` | 根文件系统只读 | ✅ 写入报 `Read-only file system` |
| `-v "$PWD":/work:ro` | **工作区只读共享**：能读源码，**改不了** | ✅ `rm -rf /work/README.md` 被拒 |
| `--tmpfs /scratch:mode=1777` | 可写暂存区（非 root 用户可写） | ✅ uid 65534 可写 |
| `--network=none` | 断网 | ✅ `wget` 报 `bad address` |
| `--cap-drop=ALL` | 丢弃全部 Linux 能力 | ✅ |
| `--user 65534:65534` | 非特权用户 | ✅ |
| `--ulimit nproc/fsize/nofile` | **资源限制（真正生效的机制）** | ✅ `nproc=64 fsize=64MiB nofile=256` |

> ⚠️ **不要写 `--memory` 或 `--pids-limit`**：本环境的 cgroup 无 v2 委派，
> 这两个参数**静默失效**（限 64m 仍能分配 200m；限 16 仍能起 50 个进程），
> 只会制造"已经限制了"的错觉。资源维度一律用 `--ulimit` / `setrlimit`。
> 依据：[ADR-0007 §3.1](../adr/0007-sandbox-capability-matrix.md)。

### 3.3 T3：独立容器 / 干净环境回归

```bash
# 本地 CI 预演（与 .cnb.yml 的 CI 同源：python:3.12-bookworm 容器 + make check）
# 注意钉到发行版：`python:3.12` 是浮动标签，已从 Debian 12 漂到 13（见
# docs/notes/reproducibility-and-gates.md 第四节）
docker run --rm -v "$PWD":/w -w /w python:3.12-bookworm \
  bash -lc 'make setup && make check'
```

- 用途：推送前在**干净环境**复现 CI 结果，无需推送。
- 代价：需拉镜像与重新装依赖（分钟级），因此**不纳入日常循环**，只在准发布或怀疑环境漂移时执行。
- 本地 VM / Termux 上的 `bwrap` 可用性验证同样归入本层。

---

## 4. 成果保护（执行纪律，不是建议）

| 编号 | 规则 | 操作 |
| --- | --- | --- |
| **P-1** | 进入 T2/T3 之前，工作树必须干净 | `git status --porcelain` 为空；否则先 `git add -A && git commit` 或 `git stash` |
| **P-2** | 测试原始数据必须落进仓库才算完成 | 写入 `docs/research/reports/<date>-<topic>/` 后再提交 |
| **P-3** | 会话结束前把状态写进 devlog | 按 [`docs/devlog/README.md`](../devlog/README.md) 的约定追加 |

**为什么 P-1 是硬规则**：未提交的改动是**唯一**无法从 git 恢复的东西。
容器随时可以死（无记忆、可能被平台回收），改动不能丢。

**为什么不做成自动门禁**：误报会诱使"绕过规则"成为习惯，整体安全性反而下降。
这条靠纪律执行，并在 [ADR-0008 §5](../adr/0008-dev-test-environment-strategy.md) 中记录为已知弱点。

---

## 5. 测试报告必须标注的两项

任何归档的测试结论都必须写明：

1. **在哪一层取得**（T0/T1/T2/T3）——决定结论的强度与适用范围；
2. **载体与限制参数**（镜像、`--ulimit`、是否断网）——决定能否复现。

缺这两项的结论**不得**用于安全断言（与 [`definition-of-done.md`](definition-of-done.md) 一致）。

---

## 6. 与 CI 的关系

| 环境 | 跑什么 | 说明 |
| --- | --- | --- |
| CNB `push` / `pull_request` | `make setup && make check`（`python:3.12-bookworm` 容器，与开发镜像同源） | 门禁，见 [`.cnb.yml`](../../.cnb.yml) |
| 本地 T0 | 同上（`make check`） | 提交前自检 |
| 本地 T3 | 同 CI 同源容器 | 干净环境预演，按需 |
| 本地 T2 | 安全对抗性用例 | **CI 暂不纳入**（需要 docker 服务，且属按需执行） |

> T2 后续可考虑纳入 CI 的独立 job，但需先确认 CI 运行器是否提供 docker 服务。
> 在那之前，T2 的结果由人工执行并归档。

---

## 7. 待办

- [ ] 把 §3.2 的长命令封装为 `make sandbox-run CMD='...'`（避免每次手写参数、避免漏项）
- [ ] 在 `.gitignore` 增加 T2 暂存与产物目录条目
- [ ] 探针矩阵（[ADR-0007](../adr/0007-sandbox-capability-matrix.md)）在 T0/T1/T2 三层各跑一次并归档对比
- [ ] 评估 T3 本地 CI 预演的实际耗时，决定是否写入准发布流程
