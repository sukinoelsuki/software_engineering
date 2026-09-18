# 条目组 A：执行与隔离

> 覆盖 `T-01` / `T-02` / `T-10` / `T-13`。本组是**唯一已有实现载体**的一组
> （`foundation/` 已实现），因此也是唯一存在"部分缓解"而非"纯纸面"的一组。
>
> 共同前提：**不可信代码会在我们自己的进程旁边执行**（模型产物、工具请求的命令）。
> 这也解释了本组为何风险最高——`SECURITY.md` §2 的"最小权限 + 纵深防御 + 故障安全"
> 三条原则全部压在这一组上。

---

## T-01 子进程执行与逃逸

**状态**：`部分缓解` ｜ **主导类别**：STRIDE-E（权限提升）｜ **智能体特有**：沙箱逃逸与资源耗尽

- **资产**：A-3（执行环境与宿主）、A-1/A-2（工作目录与白名单外文件系统）、A-7（"隔离已生效"这一结论）
- **攻击者与前提**：攻击者能**让不可信代码进入执行路径**——即模型产出了要执行的代码/命令，
  或工具参数被诱导成一条危险命令。前提是"模型输出被视为数据"这条链在某处失效（关联 `T-03`/`T-11`）。
- **攻击路径**：
  1. 诱导模型输出一段代码（例如"生成一个测试文件"）；
  2. 该代码经工具执行 → 进入 `foundation.proc.run()`；
  3. 代码尝试 ① 写白名单外路径；② 起子进程放大资源；③ 读宿主敏感文件；
  4. 若隔离未生效，攻击者获得宿主侧能力（在 CI 中即得 `CNB_TOKEN`，见 `T-08`）。
- **影响**：机密性 + 完整性 + 可用性三条同时受损。**不可逆性：高**——越界写入、
  进程表被打满、宿主被拖垮都不会自动回滚。CI 场景下还有凭据泄漏的不可逆后果。
- **现有缓解**（**已实现**，可逐行核对）：

  | 机制 | 位置 | 说明 |
  | --- | --- | --- |
  | 不经 shell、参数为列表 | `foundation/proc.py:147-158`、`:191-198`、`:229-236` | 三处调用点；全仓无 `shell=True` 由机器检查强制 |
  | 非特权 uid/gid | `foundation/proc.py:33-34`、`:156-157` | `65534:65534`（nobody） |
  | 资源上限 | `foundation/proc.py:113-118` | `RLIMIT_CPU`/`AS`/`FSIZE`/`NOFILE`，**只有 setrlimit 可靠**（cgroup 静默失效，见 `T-13`） |
  | 最小环境变量 | `foundation/proc.py:97-110`、`:142` | `_isolated_env()` **不继承** `os.environ` |
  | 可执行文件绝对路径 | `foundation/proc.py:84-94` | `resolve_binary()`（避免 PATH 污染与 `B607`） |
  | 隔离失败**不回退** | `foundation/proc.py:159-161`、`errors.py:30-34` | `PermissionError` → `IsolationError`（fail-secure） |
  | 超时 | `foundation/proc.py:162-164` | 超时即失败，**不重试**（重试会掩盖问题） |
  | 唯一入口（机器检查） | `tests/unit/test_bench_encapsulation.py:52-61`、`test_architecture_layers.py:244-252` | `subprocess` 只许出现在 `foundation/proc.py` |
  | 封装层判定按**相对路径** | `test_bench_encapsulation.py:29`、`test_architecture_layers.py:32` | 同名文件无法绕过 |
  | 豁免面登记 | `ADR-0014 §2.9`（4 处 = 1×B404 + 3×B603） | 见 `T-06` |

- **残余风险**：
  1. **L1 档没有内核级文件系统 / 网络隔离**（`ADR-0006 §6`；`../interfaces/sandbox.md` §3 明写
     L1 下 `FILESYSTEM`/`NETWORK` 的 `enforced` 必须**如实为 `False`**）。
     ⇒ 越界"能被拦住"目前只是**第 N 层拦截**，且**拦截所需的策略层与审计层都还没实现**。
  2. **`argv[0]` 未被 `run()` 强制为绝对路径**：`resolve_binary()` 是**独立 helper**，
     `run()` 不调用它。`proc.py:131` 写的是"**应**为绝对路径"——**建议而非强制**。
  3. **`cwd` 未被 `run()` 校验**：`run()` 不调用 `paths.resolve_within()`，
     白名单靠**调用方自觉**。`SandboxRequest.cwd`「必须落在 `allowed_roots` 内」这一不变式**无承载**。
  4. **`isolation="root"` 无机器检查**：`proc.py:135` 写"在 CI 中不得使用"，但没有任何检查阻止；
     且该模式下 `env=dict(os.environ)`（`proc.py:142`）会**继承凭据**（关联 `T-08`）。
  5. **`preexec_fn` 与 `user=`/`group=` 的施加上下文未验证** 【待验证】：
     若 rlimit 在**降权之后**施加，非特权进程只能**下调**限额——本项目正是下调，预期仍生效，
     但**未实测**。这是"探针覆盖不足就会误判"的同一族风险（`ADR-0007 §3.1` 的教训）。

- **验证方式**：
  - **已有**（**结构性**，非行为）：`tests/unit/test_bench_encapsulation.py:52-89`、
    `tests/unit/test_architecture_layers.py:244-274`。
  - **应有**（行为，**缺验证**）——三条用例规格，可直接照写：
    `a. test_child_process_cannot_write_outside_allowed_roots`：经 `proc.run` 执行
    `python -c "open('/tmp/..越界..','w')"` → 断言**文件不存在**且返回值表达失败；
    `b. test_child_process_hits_rlimit_instead_of_host`：子进程内分配超过
    `ADDRESS_SPACE_LIMIT_B`（`proc.py:39`）→ 断言子进程内 `MemoryError`，宿主不受影响；
    `c. test_isolation_failure_does_not_fall_back`：构造无法降权的上下文调用
    `run(isolation="user")` → 断言抛 `IsolationError` 且**命令未被启动**（fail-secure）。
  - **【待验证】项的验证方式**：`proc.run([sys.executable, "-c", "import resource;print(resource.getrlimit(resource.RLIMIT_CPU))"], ...)`
    → 断言 soft 限等于 `CPU_LIMIT_S`（证明 `preexec_fn` 在降权后仍然生效）。
- **相关**：`REQ-SEC-05`/`REQ-SEC-09`、`ADR-0006`（规则 S-1/S-2）、`ADR-0007 §4.1`、
  `ADR-0014 §2.8`、`../interfaces/sandbox.md`。

---

## T-02 路径穿越

**状态**：`部分缓解` ｜ **主导类别**：STRIDE-E（权限提升）

- **资产**：A-1（工作目录文件）、A-2（白名单外文件系统）
- **攻击者与前提**：攻击者能控制一个**会被当成路径使用**的字符串——工具参数
  （`READ_FILE` / `WRITE_FILE`）、领域包路径、命令行参数。前提是该字符串进入了
  `paths.resolve_within()` 之外的路径构造路径（字符串拼接）。
- **攻击路径**（逐类，均为需要被拒绝的输入）：
  1. `../../etc/passwd`（相对路径上跳）；
  2. 符号链接：白名单内放一个指向白名单外的链接，再经链接访问；
  3. 绝对路径 `/etc/shadow`（绕开"相对根"的直觉）；
  4. 前缀欺骗：`/tmp/foo` 与 `/tmp/foobar`（**字符串前缀**比较会误放行）；
  5. UNC / 设备路径（`\\server\share`、`//`）——**跨平台时才出现**，`platformdirs` 引入后需重看。
- **影响**：读 = 机密性（凭据、私钥）；写 = 完整性（**不可逆**，例如覆盖配置或源码）。
- **现有缓解**（**已实现**）：
  - `foundation/paths.py:16-39` `resolve_within()`：先 `expanduser().resolve()`
    （**展开 `..` 与符号链接**），再要求 `resolved == root_resolved or root_resolved in resolved.parents`；
  - **按路径分量比较**（`in resolved.parents`）而非字符串前缀 ⇒ 第 4 类（`/tmp/foobar`）**天然不通过**；
  - 不匹配即抛 `PathNotAllowedError`（`errors.py:18-19`）⇒ **fail-secure**；
  - `roots` 为空 ⇒ **恒拒绝**（默认拒绝，`SECURITY.md` §2 第 1 条）；
  - 机器检查（**部分**）：`test_architecture_layers.py:266-274` 断言 `resolve_within`
    **只允许定义在** `foundation/paths.py`；
    `test_architecture_layers.py:298-306`（V6）断言 `bench/` 不再保留独立实现。
- **残余风险**：
  1. **"唯一入口"的机器检查只覆盖"不得在别处定义同名函数"**，
     **不覆盖**"不得自行做路径判断/拼接"——`os.path.join` + 前缀比较在别处出现**不会被拦**。
     `ADR-0015 §5.1.1` 的 R4 语义（唯一入口）目前**只兑现了一半**。
  2. **校验与使用之间的 TOCTOU 窗口**：`resolve()` 在**校验时刻**解析符号链接；
     之后若路径分量被替换（`bench` 的一次性工作目录是 `chmod 0o777`，
     `paths.py:42-49` 的 `make_writable_by_all`），解析结果与打开的对象可能不是同一个。
     单人本地场景风险低，**但这条正是 `make_writable_by_all` 的注释所依赖的假设**
     （"仅用于一次性工作目录，不得对仓库目录调用"，**无机器检查**）。
  3. **白名单根自身未被校验**：`roots` 来自调用方；若调用方传入宽根（如 `/`），
     该函数正确地"什么都允许"——**它只保证不越出给定根**。
  4. **Windows 语义未覆盖**：UNC、盘符、大小写不敏感文件系统、8.3 短名。
     `REQ-PLAT-01` 要求跨平台，**当前只在 Linux 验证过** 【待验证】。
- **验证方式**：
  - **应有**（行为，**缺验证**）——即 `ADR-0015 §7.2` 的 **`S2`**
    `test_path_traversal_is_rejected_and_audited`：对上述 5 类输入逐一断言
    **抛 `PathNotAllowedError` 或返回拒绝**、**绝不回退为放行**、且留下审计记录。
    落地位置：`tests/security/`；**当前 `S2` 未落地**。
  - 建议补一条**参数化**用例覆盖第 4 类前缀欺骗（`/tmp/foo` vs `/tmp/foobar`），
    它是"看起来实现了其实没有"的高发点。
  - **【待验证】项**：Windows/UNC 输入 → 在 win_amd64 运行器上跑同一组参数化输入
    （验证方式：CI 多平台 job，`REQ-PLAT-01`）。
- **相关**：`REQ-SEC-05`/`REQ-SEC-09`、`ADR-0006 §6`（风险表"路径白名单被绕过"）、`ADR-0015 §7.2`（`S2`）。

---

## T-10 网络出站默认拒绝失效

**状态**：**`未缓解`** ｜ **主导类别**：STRIDE-I（信息泄漏）｜ **智能体特有**：工具滥用

- **资产**：A-3（宿主）、A-5（凭据——出站即可外传）、A-4（数据完整性）
- **攻击者与前提**：攻击者能影响一次出站请求的目标或内容：模型输出里的 URL、
  被读入文件/网页中的 URL、工具参数里的端点、或**环境变量里的代理**。
- **攻击路径**：
  1. 不可信内容里出现一个 URL，被"顺手"取用；
  2. 请求发出，把宿主上的敏感内容（如读到的文件、环境变量）带出去；
  3. 变体：请求**不设超时**或**不限制响应体大小** ⇒ 挂死或内存耗尽（可用性）；
  4. 变体：经 `HTTP(S)_PROXY` 环境变量把流量导向攻击者可控的代理。
- **影响**：机密性（数据外传，**不可逆**）、可用性（资源耗尽）。合规上属"未授权出站"。
- **现有缓解**（**均为设计 / 契约，尚无实现载体**）：
  - `ExecutionContext.network_allowed` **默认 `False`**（`../interfaces/tools.md` §2.4 与约定 `C6`）；
  - `SandboxRequest.network_allowed` **默认 `False`**（`../interfaces/sandbox.md` §2.5）；
  - `Capability.NETWORK_OUTBOUND` 需**显式授予**（`../interfaces/policy.md` §2.1）；
  - `ModelClient.chat(timeout_s=60.0)` 有默认超时，`max_tokens` 可控（`../interfaces/model.md` §4）；
  - 选型上的**默认值取向**：`urllib3` 的代理是**显式**的（需自行构造 `ProxyManager`），
    而 `httpx`/`requests` 默认读 `HTTP(S)_PROXY`——这正是 `ADR-0015` `D3` 改选 `urllib3` 的理由之一；
  - `SECURITY.md` §3 `S-5`：不可信事件（PR）触发的 CI 不得访问密钥。
- **残余风险**：
  1. **L1 档没有进程级网络隔离**（`ADR-0006 §6`）⇒ 在这一档"默认拒绝"**只可能**由策略层实现，
     而策略层**尚未实现** ⇒ 当前**实际上没有任何出站控制**。
  2. **`urllib3` 的代理行为断言未逐行核对源码** ⇒ `ADR-0015 §8.2` 的 `V-n` 仍是【待核验】；
     `V-o`（**出站超时、响应体上限、SSE 流式**三个写法）亦未验证。
     ⇒ **不得**把"`urllib3` 默认不读环境变量"当作已确认事实使用。
  3. **响应体上限无契约载体**：`SECURITY.md` 要求"限制响应体大小"，
     但 `ModelClient.chat` 的签名（`../interfaces/model.md` §4）**没有**任何字节上限参数
     ⇒ 该要求当前**在契约层无落点**（属**契约缺口**，见下"需领导裁决"）。
- **验证方式**：
  - **应有**（行为，**缺验证**）：
    `test_outbound_request_without_capability_is_denied_and_audited`：
    在未授予 `NETWORK_OUTBOUND` 时发起出站 → 断言**请求未发出**（而非发出后被拦）+ 审计事件存在；
    （**不能用"连接被拒"当作通过判据**——那可能是网络本身不通，属探针覆盖不足，见 `T-13`。）
  - `test_proxy_env_is_not_trusted_by_default`：注入 `HTTP_PROXY=http://127.0.0.1:1` →
    断言请求**不**经该代理（证明默认取向）。
  - **【待验证】项**：`V-n`/`V-o` —— 读 `urllib3` 源码的 `ProxyManager` / 环境变量处理章节；
    写三段最小样例验证超时、分块读取计数、SSE 逐行解析。
- **相关**：`REQ-SEC-07`、`REQ-MODEL-03`、`ADR-0015 §5.2.4`（`HTTP-1`/`HTTP-2`）与 §8.2（`V-n`/`V-o`）、
  `SECURITY.md` §3 `S-5`。

---

## T-13 隔离机制静默失效 / 静默降级（fail-open）

**状态**：`部分缓解` ｜ **主导类别**：安全断言完整性（**本项目自有的类别**，
对应 STRIDE 的 "R" 与 "S" 的混合：结论不可信）

- **资产**：**A-7（"隔离已生效"这一结论本身）**，并**传导**到 A-3/A-5
  ——它是唯一一条"自身不是数据、却决定所有其他结论真假"的资产。
- **攻击者与前提**：不需要传统攻击者。**触发条件就是"工具/后端在我们看不见的地方没生效"**：
  - `firejail --net=none` **退出码 0 但网络未阻断**（`ADR-0006 §4.1` 实测）；
  - `docker --memory=64m` / `--pids-limit=16` **退出码 0 但不生效**（`ADR-0007 §3.1` 实测）；
  - 探针**只看退出码**（不看 stderr、不看"目标操作是否真的被阻止"）。
- **攻击路径**：
  1. 探测阶段只看"命令是否成功" ⇒ 得出"隔离可用"；
  2. 该结论被写进文档、被后续设计引用（例如"我们已隔离执行模型产物"）；
  3. 真实攻击到来时，越界操作**成功**；
  4. 更糟的分支：**降级静默发生**——系统以较弱档位继续运行，无人知道。
- **影响**：**不可逆性：极高**。它不破坏数据，它破坏**判据**——错误的结论会持续传播到
  设计、文档、验收标准中（本项目已为此付出一次代价：`ADR-0006` 的初判"当前环境无法提供强隔离"
  被 `ADR-0007` 修正；以及 `--shm-size` 的区分性探针教训）。
- **现有缓解**（**契约与规则已定，探针未实现**）：
  - **规则 S-1**（`ADR-0006 §5.2`）：后端必须实现**有效性自检探针**（尝试越界操作并确认被阻止）；
    **禁止**以"命令退出码为 0"作为隔离生效的判据；
  - **规则 S-2**（同处）：降级必须**结构化记录**，并在审计与用户可见输出中显式提示
    ——**禁止静默降级**；
  - **逐维度矩阵**（`ADR-0007 §4.2` + `../interfaces/sandbox.md` §2.2）：
    `enforced=True` **只能来自负向探针**；`mechanism == NONE ⇒ enforced 必须为 False`；
    **`evidence` 为空字符串视为未验证** ⇒ 按 `enforced=False` 处理（fail-secure）；
  - `IsolationMatrix.dimensions` **未列出的维度**一律按未隔离处理（default-deny）；
  - `tier == L0 ⇒ 调用方必须拒绝执行`（不是"降级执行"）；
  - 失败即拒绝的实现范例：`proc.py:159-161`（`IsolationError` 不回退）。
- **残余风险**：
  1. **探针本身未实现**（`security/sandbox/` 不存在）⇒ 上述全部缓解**目前无执行载体**；
  2. **"探针自身误判（假阳性）"没有缓解机制**（`ADR-0006 §6` 风险表已列该风险）：
     探针说"可用"而实际不可用，会**比没有探针更危险**（给了虚假的把握）；
  3. **区分性探针尚未成为约定**：`ADR-0007 §3.1` 的教训是"探针必须能把两种解释区分开"
     （`--memory` vs `--shm-size` 默认值），但**没有一条机器检查**要求新探针具备该性质；
  4. **`cgroup` 类限制被保留在枚举里**（`IsolationMechanism.CGROUP`，`../interfaces/sandbox.md` §2.1）
     ——这是**刻意的**（为了让探针如实记录"尝试过但未生效"），但也意味着
     **枚举存在 ≠ 该机制可用**，读者可能误读。
- **验证方式**：
  - **应有**（**缺验证**）：把 `ADR-0007 §3.1` 的探针矩阵写成可执行测试，
    **必须包含"应当失败"的用例**（反向验证，`ADR-0007 §6.2`）：
    - 正向：容器内 `echo ok` 成功；
    - **负向（关键）**：`--read-only` 下 `touch /x` **必须失败**、`--network=none` 下出站**必须失败**、
      `ulimit` 超限分配**必须失败**；
    - **区分性**：cgroup 类限制必须配对照（如 `--shm-size`），避免把默认值现象当成限制生效；
    - 任一项"意外成功" ⇒ 该维度判**不可用**（fail-secure）。
  - 一处**当前就可执行**的最小验证（不需新代码）：`unshare --net echo ok` 与
    `bwrap --bind / / --tmpfs /tmp /bin/echo ok` 在当前 CNB 环境**预期失败**
    （`ADR-0006 §4.1` 的实测），可作为"探针能报出不可用"的冒烟。
  - 落地位置：探针脚本 + `tests/security/`（对应 `REQ-SEC-09`）。
- **相关**：`ADR-0006` §4.1/§5.2/§6、`ADR-0007` §3.1/§4.2/§6、`ADR-0014 §2.9.1`（同一族教训：
  **探针必须覆盖工具的全部可观测输出**：退出码 / stderr / 实际抑制效果）、`../interfaces/sandbox.md`。
