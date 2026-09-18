# T-08 独立对抗性复核（验证工程师）

- 复核对象提交：`8e047e6`（fix(security): spawn 默认最小环境，堵住常驻子进程继承凭据）
- 文档同步：`6256c28` / `4a7a8f6` / `804b251`（均属 `docs/design/**`、`docs/adr/**`，不在本域）
- 复核日期：2026-09-18
- 复核人：验证工程师（独立实例，结论只由本笔记记录的**自己跑出来的证据**支撑，
  不采信提交信息、任何回报或转述——按 team-lead 独立性要求。）

> 独立性纪律：本笔记所有结论均可复现；凡是"没穷尽"或"本环境无法端到端验证"的，
> 一律标为 **未验证** 或 **未发现缺口但未穷尽**，不写成"没有问题"。

---

## 0. 复现环境

- OS：Linux（云实例）；Python 3.12（`.venv`）。
- 跑测试：`PYTHONPATH=src .venv/bin/python -m pytest -q <用例>`；全量门禁 `make check`。
- **本环境无 `llama-server` 二进制与模型**——任何涉及"真实服务进程实际环境"的结论均为**未验证**。

---

## ① 子进程出口是否全部受约束

### 枚举（在 `src/**` 全量搜索 `subprocess` / `os.system` / `os.exec*` / `posix_spawn` / `Popen`）

结果：**所有"启动新进程"的代码都集中在 `src/agent_sec_perf/foundation/proc.py` 这一个封装层**，
仓内无其他 `subprocess.Popen` / `subprocess.run` / `os.system` / `os.exec*` / `posix_spawn` 调用。
（搜索命令：`rg -n 'subprocess|os\.system|os\.exec|posix_spawn|Popen' src/`）

封装层三个出口与各自 env 策略（逐行核对 `proc.py` 当前内容）：

| 出口 | env 来源 | 当前是否泄漏凭据 |
| --- | --- | --- |
| `run(isolation="user")` | `minimal_env(cwd)` | 否（最小环境） |
| `run(isolation="root")` | `dict(os.environ)`（**全量继承**） | **是**——但属"刻意未动"的残余，见下 |
| `run_inherit_env()` | 不传 env ⇒ 继承父进程全量环境 | 见"重点目标"判定 |
| `spawn(env=None)` | 修复后 = `minimal_env(cwd)`；`env=` 显式传入则 `dict(env)` | 否（默认最小；显式继承需调用方写明） |

`scripts/**` 中的 `.sh`（CI 脚本）属 shell 层，不是 Python 子进程封装，且 `scripts/bench/publish.sh`
仅用 `CNB_TOKEN` 做 git credential helper（合法用途），**不启动 `llama-server`**，
因此不构成对 Python 封装层的绕过。

### `run_inherit_env()` 的每一个调用点（重点目标）

独立复跑确认，全仓调用点共 **2 处**，均不在 `src` 之外的生产代码：

1. `src/agent_sec_perf/bench/evaluate.py:178`
   `proc.run_inherit_env([python, "-m", "mypy", "--strict", ..., target])`
   —— `mypy` 是**静态类型检查**，对 `target`（模型产物文件）只做解析/类型推导，
   **不执行**该文件的代码。凭据在环境中只是"存在"而从不外发。

2. `src/agent_sec_perf/bench/rounds.py:414`
   `proc.run_inherit_env([binary, "--version"], cwd=model_dir, ...)`
   —— 仅取二进制 `--version` 字符串做环境指纹，**不加载模型、不执行模型产物**。

**结论（① 重点目标）：`run_inherit_env()` 当前**没有**任何一个调用点在执行"模型产物 / 模型生成的代码"**。
架构师在契约中"有意继承、只用于自有工具链静态检查"的断言，**独立验证成立**。
因此不存在与 T-08 同族的"第二条泄漏路径"。

### `run(isolation="root")` 残余（与提交自陈一致，独立确认）

- `RunParams.isolation` 默认 `"user"`（`protocol.py:130`），故模型产物默认走最小环境，安全。
- 但 `isolation` 允许取 `"root"`（`ISOLATION_MODES = ("user","root")`），且**无程序化拦截**禁止 CI 使用；
  唯一约束是 `report.py:181-183` 的一句**文本警告**（"模型产物在未隔离进程里执行"）。
- 一旦 `--isolation root` 被传入，执行模型产物的 `proc.run` 将继承含 `CNB_TOKEN` 的完整父环境——
  这是与 T-08 同族的**未缓解残余**，仅被文字警告而非 fail-secure 拦截。
- 本笔记记为 **未发现缺口但未穷尽** 之外的**已确认残余**（证据：`protocol.py` + `report.py` 源码）。

---

## ② 除环境变量外，还有没有别的凭据泄漏通道

逐一判定：

- **argv 是否带令牌**：`bench/protocol.py:170 server_argv()` 返回的参数列表仅含
  `llama-server -m <model_path> -c <ctx> -t/-tb <threads> -np 1 --host/--port ...`，
  **无任何令牌/密钥字段**（host/port 是配置非凭据）。`runner.py:121` 用
  `[binary, *server_argv(...)[1:]]` 拼出 argv，不引入凭据。
  ⇒ **结论：argv 通道无凭据泄漏（已验证）**。

- **spawn 的 `cwd` / `log_path` 指向目录里的可读写面**：
  `minimal_env` 把 `HOME` 与 `PYTHONPATH` 都设为 `cwd`（= `log_path.parent`）。
  子进程（第三方 `llama-server`）对该目录有读写权；但 `spawn` 不改文件系统命名空间，
  子进程仍保有对整个宿主文件系统的读权限——这是 **T-12（文件系统隔离）** 范畴的既有残余，
  不是 T-08 的 env 泄漏；本笔记不重复判定 T-12，仅标注二者边界。
  ⇒ **结论：T-08 视角的 env 泄漏已堵；目录可达面是另一维度的未缓解项（属 T-12，未在本任务验证）**。

- **日志重定向是否把父进程敏感内容写进证据目录**：
  `spawn` 把子进程 `stdout+stderr` 重定向到 `log_path`，捕获的是**子进程自身输出**
  （`llama-server` 的 `print_timing` 等性能证据），**不捕获父进程任何内容**；
  父进程也不向该文件写入。
  ⇒ **结论：日志重定向不泄漏父进程凭据（已验证）**。

---

## ③ 改默认值的副作用面（必须写"未验证"）

`minimal_env` 仅提供 `PATH / HOME / LANG / LC_ALL / PYTHONDONTWRITEBYTECODE / PYTHONPATH`。
真实 `llama-server`（C++ 二进制）在该环境下**可能**受影响（本环境无二进制，**无法端到端验证**）：

- `LD_LIBRARY_PATH`：CUDA / oneAPI / 后端加速库常经此变量定位 `.so`；缺失可能导致
  `llama-server` 启动失败（`cannot open shared object file`）或回退到纯 CPU 后端（行为改变）。
- `HOME` 被设为工作目录：若 `llama-server` 在 `~` 下读配置/缓存/证书，`minimal_env` 的
  `HOME=workdir` 会让这些默认路径失效（功能或性能变化）。
- `TMPDIR`：缺失时回退 `/tmp`——一般无害，但某些后端把临时张量落 `TMPDIR`，可能与预期不符。
- `CUDA_VISIBLE_DEVICES` / `CUDA_HOME` / `OMP_NUM_THREADS` / `GGML_*`：影响 GPU 选择与线程数，
  缺失可能改变吞吐与 NUMA 行为（性能维度，非安全）。

**结论（③）：未验证。** 本环境无 `llama-server` 二进制与模型，无法判定上述任一项是否真的发生。
要验证需要的条件：
1. 一个真实 `llama-server` 二进制（含其动态库依赖，最好记录 `ldd` 输出与 build 版本）；
2. 至少一个模型权重文件；
3. 在"干净最小环境"与"完整父环境"两种条件下分别启动，对比：
   启动是否成功（exit code / 是否打印 `error loading`）、实际使用的后端
   （`--version` 与日志中的 `build` / `backend` 行）、以及吞吐/P99 是否变化；
4. 重复 ≥3 次并报极差（本项目性能纪律）。

此项应作为 T-08 修复的**开放验证项**交给后续在具备二进制+模型的机子上补证。

---

## ④ 验证独立性：实现者单测之外有无独立用例抓住回归（变异探针）

> 进行中：独立用例 `tests/security/test_t08_independent_spawn_env.py` 已落地（干净代码下 1 passed）。
> 变异证据（哪几个用例在变异后变红）将在追加提交中填入。纪律：变异经 monkeypatch 或
> "改-测-还原单命令"完成，禁止把变异留在共享工作区；每次变异后 `git diff` 复核已还原。

---

## 三态总览（截至本笔记）

- **已验证**：① 封装层三出口 env 策略与调用点枚举；① `run_inherit_env` 不执行模型产物；
  ② argv 无令牌、日志重定向不泄漏父内容；④ 独立运行时 canary 用例在干净代码下通过。
- **未验证**：③ `llama-server` 在最小环境下是否启动失败/行为改变（无二进制+模型）。
- **未发现缺口但未穷尽**：② 中 `cwd` 目录的文件系统可达面（属 T-12，本任务不覆盖）；
  全仓 `subprocess` 枚举已全量覆盖，但 shell 脚本层（非 Python 封装）未做凭据流追踪。
- **已确认残余**：`run(isolation="root")` 仍全量继承父环境，且无程序化拦截禁止 CI 使用
  （仅 `report.py` 文本警告）——与 T-08 同族的未缓解项。

---

回报：（本轮首笔，④ 变异证据待追加）
状态：进行中
改动：新增 tests/security/test_t08_independent_spawn_env.py（独立运行时 canary）
验证：干净代码下该用例 1 passed；①②③ 已用源码证据得出独立结论
遗留：④ 变异探针证据待本会话追加提交
需领导裁决：③ 的端到端验证条件是否纳入后续排期
