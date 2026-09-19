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
  | 最小环境变量 | `foundation/proc.py:97-111`、`:148`、`:235` | `minimal_env()` **不继承** `os.environ`（`run` 与 `spawn` **共用**；`:116` 暂留旧名别名） |
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
  3. **`cwd` 未被 `run()` 校验**：`run()` 不调用 `paths.resolve_within()`，白名单靠**调用方自觉**。
     `SandboxRequest.cwd`「必须落在 `allowed_roots` 内」（`../interfaces/sandbox.md` §2.5）这一不变式
     **在 `proc` 层没有任何承载**。现有两个调用方是 `bench/evaluate.py:155` 与 `:187`
     （后者执行 **模型生成的代码**，隔离档取自 `self._isolation`），其工作目录由 `bench` 自建、
     **未过 `paths`** ⇒ 这就是"白名单靠自觉"的实例：当前该目录是自建的一次性目录、现状可接受，
     但**没有任何机制保证**下一个调用方会去校验。
  4. **`isolation="root"` 无机器检查**：`proc.py:135` 写"在 CI 中不得使用"，但没有任何检查阻止；
     且该模式下 `env=dict(os.environ)`（`proc.py:142`）会**继承凭据**（关联 `T-08`）。
  5. ~~**`preexec_fn` 与 `user=`/`group=` 的施加上下文未验证** 【待验证】~~ ⇒
     **已实测（2026-09-18，`5fddcfa`）**：`tests/security/test_rlimit_isolation.py` 证明
     `run(isolation="user")` 下 `RLIMIT_AS`（2 GiB）**确实生效**（子进程 `mmap` 3 GiB 失败），
     且变异探针证明该结论**依赖 `_apply_limits`**。⇒ 原文"预期仍生效、但未实测"**已被实测取代**。
     **仍未实测**的是 **CPU / FSIZE / NOFILE** 三项（本用例只覆盖 `RLIMIT_AS`）。

- **验证方式**：
  - **已有**（**结构性**，非行为）：`tests/unit/test_bench_encapsulation.py:52-89`、
    `tests/unit/test_architecture_layers.py:244-274`。
  - **已有（行为，2026-09-18，`5fddcfa`）**：`a` 已落地为 `tests/security/test_rlimit_isolation.py`：
    `test_isolated_child_cannot_overallocate_address_space`（子进程 `mmap` 3 GiB > 2 GiB 的
    `RLIMIT_AS` → 断言**未**成功映射），并带**变异探针**
    `test_rlimit_guard_depends_on_apply_limits`（monkeypatch `_apply_limits` 为 no-op ⇒ 攻击得手）
    ⇒ 该行为断言**依赖真实保护**，非恒过；并据此**结清**残余风险 5 的【待验证】项。
  - **仍缺（行为）**：`b. test_isolation_failure_does_not_fall_back`——**尚未落地**。
    写法（沿用原规格）：monkeypatch 使 `subprocess.run` 抛 `PermissionError` → 断言抛
    `IsolationError` 且 `run()` **不回退**为普通执行（`proc.py:159-161`）。
    **环境依赖写法**（以非 root 身份调用 `user=65534`）在本环境不可用（当前以 root 运行，
    `id -u`=0）⇒ **不得**作为唯一判据。
  - **⛔ 一条必须排除的"假断言"**（2026-09-18：**验证者 `verifier-security` 独立核实后修正本条目**，
    团队领导另行逐行核实；取证见 `README.md` §7「归属取证」）：
    **不得**对 `proc.run` 写"子进程写到白名单外路径 → 文件不存在"这一类断言。
    `proc.run`（`proc.py:121-174`）**只**设置 rlimit、`cwd`、`env` 与 uid，**没有任何路径白名单参数**
    ——子进程写到 `/tmp/...` **会成功、文件会存在**。
    路径白名单的唯一入口是 `foundation.paths.resolve_within`，属**调用方**职责
    ⇒ 这类断言**只能**归属 [`T-02`](#t-02-路径穿越) 的 `S2`。
    写成 `proc.run` 的断言是一条**永远不会通过**的假断言——比没有断言更糟：
    它会诱导实现者给 `proc.run` 增加一个不属于它的职责（并在过程中破坏 `R4` 的"唯一入口"语义）。
    **本条记录留存的原因**：这正是"存在验证方式"与"验证方式正确"的差别；
    威胁模型的验证方式**必须同样经过核实**，否则它会以"有验证"的形态误导实现。
  - **【待验证】项的验证方式**：`proc.run([sys.executable, "-c", "import resource;print(resource.getrlimit(resource.RLIMIT_CPU))"], ...)`
    → 断言 soft 限等于 `CPU_LIMIT_S`（证明 `preexec_fn` 在降权后仍然生效）。
- **相关**：`REQ-SEC-05`/`REQ-SEC-09`、`ADR-0006`（规则 S-1/S-2）、`ADR-0007 §4.1`、
  `ADR-0014 §2.8`、`../interfaces/sandbox.md`。

---

## T-02 路径穿越

**状态**：`部分缓解`（2026-09-18 重评：`S2` 的"**拒绝**"半已有可执行行为用例；
**2026-09-19 更正**：原写"**且留审计**"半**无载体**——**该表述有误**，承载点与**两处行为层证据**
均已存在，见下方「2026-09-19 更正（审计半）」；是否升为「已缓解并验证」见提案 **`P-2`**，
**本轮不改状态与计数**）｜ **主导类别**：STRIDE-E（权限提升）

> **2026-09-19 增补（配置面落点）**：新增"**不可信配置决定审计落点**"这一类攻击面（攻击路径 6）
> 与其缓解规格（`../interfaces/audit.md` §2.5，判据 `W1`~`W8`）。
> **落地现状（2026-09-19 复核）**：缓解**已实现**（`foundation/config.py` 的配置期校验 +
> `observability/audit.py` 的装配期校验），`W1`/`W2`/`W4`~`W8` **用例已落地**
> （`tests/security/test_audit_landing_whitelist.py`，`91ea9d5`，含 W7 变异探针与 W8 两层各自独立），
> **`W3`（根内符号链接指向根外）本轮已补**（`9b37e99`：配置期 + 装配期 + 变异探针）
> ⇒ **`W1`~`W8` 全部落地**；**UNC（第 5 类）仍缺显式用例**，但**已判不构成独立绕过面**
> （Linux 下 `//` 折叠为 `/`，实测 `Path("//server/share").resolve() == Path("/server/share")`；
> 跨平台项仍见残余风险 4）。本条**不因此升降状态**：T-02 仍「部分缓解」——**是否升级待 `P-2` 裁决**
> （"且留审计"半的**原表述**已于 2026-09-19 更正，见下）。
>
> **2026-09-19 裁决（`A-3`，见 `README.md` §8.1）**：`test_audit_landing_whitelist.py`
> **不构成**本条"**且留审计**"半的证据——它断言的是**审计落点目录白名单**（攻击面 6），
> 与本半（"拒绝时留下审计记录"）**不是同一落点**。
> ⚠️ **【2026-09-19 更正】**该裁决的**结论（此文件不是证据）不变**，但其中"该半**至今无承载点**"
> 一句**有误**：**承载点存在**（工具层 `_fail` → `audit_tool_call` → `emit`），且已有**两处行为层证据**
> ⇒ **"这份文件不是该半的证据" ≠ "该半没有证据"**（本轮错误的形状正是把二者混同）。
> **更精确的现状**：仅 **`resolve_within` 的非工具层调用点**（配置期 / 装配期）的拒绝不进审计，
> 其"由谁 emit"的选项与代价见 `README.md` §8.2 的 **`Q-1`（范围已收窄，待领导裁决）**。
>
> **2026-09-19 更正（审计半：原"无载体"表述为误）**：
> **原表述**（本轮之前，见 `README.md` §4/§5/§7）：「`S2` 的"且留审计"半**无载体**／
> `resolve_within` 是纯函数不持有 sink ⇒ 该半**无承载点**」。
> **错在哪**：把"**`resolve_within` 这个纯函数不持有 sink**"当成了"**该半没有承载点**"。
> 事实是**承载点不止一处**——**工具层**的路径拒绝**已经**留痕：`tools/files.py` 的
> `ReadFileTool.invoke` 在 `resolve_tool_path` 抛 `PathNotAllowedError` 时走 `_fail`
> （`files.py:121-129`），`_fail` 调 `tools/registry.py::audit_tool_call`（`registry.py:179-211`，
> 构造 `TOOL_CALL` 事件并 `sink.emit`），落盘到 `observability/audit.py` 的 `JsonlAuditSink`；
> `harness/loop.py` **不重复** emit，只把 `result.audit_id` 回填 `TOOL_RESULT`。
> **"纯函数不持有 sink"只说明这一半*不由 `paths` 承担*，不等于没人承担**。
> **行为层证据（两处，均已入库）**：
> ① `tests/security/test_s1_replayable_audit_link.py::test_denied_tool_call_is_audited_and_replayable`
> （**真实** `JsonlAuditSink` + **真实** `ReadFileTool`，断言拒绝后 `audit_id` 落盘且 `query_by_id` 可还原）；
> ② `tests/security/test_t02_path_traversal_audit.py`（`4a35c61`，断言 `kind=TOOL_CALL` 与
> `detail["reason"] == "path_not_allowed"` 这一**安全语义字段**，并含**变异探针**：摘掉 `emit` 后主用例必红）。
> **仍成立的部分（不得一并更正）**：`resolve_within` **自身的**非工具层调用点（配置期 / 装配期）
> 的拒绝**不进审计**——那是 `Q-1` 的范围问题，与本处是两件事。
> **是否据此把本条升为「已缓解并验证」**：见 `README.md` §8.2 的提案 **`P-2`**（**本轮不改计数**）。

- **资产**：A-1（工作目录文件）、A-2（白名单外文件系统）
- **攻击者与前提**：攻击者能控制一个**会被当成路径使用**的字符串——工具参数
  （`READ_FILE` / `WRITE_FILE`）、领域包路径、命令行参数、**以及项目级配置文件
  `.lowspec.toml` 里的 `[audit] directory`**（该文件**跟着仓库走** ⇒ 按 `SECURITY.md` 口径
  属不可信输入，`../interfaces/audit.md` §2.5）。前提是该字符串进入了
  `paths.resolve_within()` 之外的路径构造路径（字符串拼接）。
- **攻击路径**（逐类，均为需要被拒绝的输入）：
  1. `../../etc/passwd`（相对路径上跳）；
  2. 符号链接：白名单内放一个指向白名单外的链接，再经链接访问；
  3. 绝对路径 `/etc/shadow`（绕开"相对根"的直觉）；
  4. 前缀欺骗：`/tmp/foo` 与 `/tmp/foobar`（**字符串前缀**比较会误放行）；
  5. UNC / 设备路径（`\\server\share`、`//`）——**跨平台时才出现**，`platformdirs` 引入后需重看。
  6. **配置面（2026-09-19 新增）**：`.lowspec.toml` 的 `[audit] directory` 指向任意路径 ⇒
     `AuditSink` 以**追加模式**打开该路径并写入（**任意路径追加写**原语）。变体：指向
     **不可写**路径 ⇒ `emit()` 冒泡 ⇒ 会话失败（可用性）；指向**取证看不到**的位置 ⇒
     `REQ-SEC-06` 的可回放性被架空（审计"存在但没人找得到"）。
- **影响**：读 = 机密性（凭据、私钥）；写 = 完整性（**不可逆**，例如覆盖配置或源码）。
- **现有缓解**（**已实现**）：
  - `foundation/paths.py:16-39` `resolve_within()`：先 `expanduser().resolve()`
    （**展开 `..` 与符号链接**），再要求 `resolved == root_resolved or root_resolved in resolved.parents`；
  - **按路径分量比较**（`in resolved.parents`）而非字符串前缀 ⇒ 第 4 类（`/tmp/foobar`）**天然不通过**；
  - 不匹配即抛 `PathNotAllowedError`（`errors.py:18-19`）⇒ **fail-secure**；
  - `roots` 为空 ⇒ **恒拒绝**（默认拒绝，`SECURITY.md` §2 第 1 条）；
  - 机器检查（**部分**）：`test_architecture_layers.py:266-274` 断言 `resolve_within`
    **只允许定义在** `foundation/paths.py`；
    `test_architecture_layers.py:298-306`（V6）断言 `bench/` 不再保留独立实现；
  - **行为用例（2026-09-18 新增，`f436b0b`）**：`tests/security/test_path_traversal_rejected.py`
    （11 个 `@pytest.mark.security` 用例：`..` 语料 6 条 + 绝对路径 + 软链接指向白名单外 +
    深层穿越 + 边界正确性 + 变异验证）与 `test_path_validation_seam.py`（2 个接缝用例）
    ⇒ "拒绝"半**已验证**；`make test-security` → **13 passed / 85 deselected**
    （**这是 `f436b0b` 当时的快照**；同日追加 4 种前缀欺骗 + 3 个边界正确性用例后，
    该文件为 **18 例**，见下方"验证方式"——**保留快照计数并标注口径**，不覆写）。
  - **审计留痕（工具层，"且留审计"半；2026-09-19 更正：载体与证据均已存在）**：
    工具层路径拒绝经 `tools/files.py::_fail`（`files.py:121-129`）→
    `tools/registry.py::audit_tool_call`（`registry.py:179-211`，构造 `TOOL_CALL` 事件、`ok=False`
    ⇒ `outcome=ERROR`、`detail={"reason": "path_not_allowed"}`）→ `JsonlAuditSink.emit` 真实落盘；
    `harness/loop.py` **不重复** emit，只把 `result.audit_id` 回填 `TOOL_RESULT`。
    ⚠️ 该事件的 `outcome` 是 `ERROR` 而非 `DENY`（越权靠 `detail.reason` 表达）——
    口径是否调整见 `README.md` §8.2 的 **`Q-2`**（待裁决）。
  - **配置面落点白名单（2026-09-19 新增；实现与用例均已落地）**：`../interfaces/audit.md` §2.5 的
    `P1`~`P7`——根集合为**常量** `foundation.config.ALLOWED_AUDIT_ROOTS`（配置不得影响它）；
    **两层校验**（配置期 `resolve_within` ⇒ `ConfigError`；装配期 sink 自查 ⇒ `PathNotAllowedError`）；
    **先校验后 `mkdir`**；**禁止**越界后回退默认目录或静默关闭审计。落地位置：
    `foundation/config.py`（`_as_absolute_directory`）与 `observability/audit.py`
    （`JsonlAuditSink.__init__`）——**均已入库**；对抗性用例
    `tests/security/test_audit_landing_whitelist.py`（`91ea9d5`）覆盖 `W1`/`W2`/`W4`~`W8`
    （含 W7 变异探针：把 `resolve_within` 换成直通实现后 W1/W5 失效；W8：两层各自撤一层各有用例失败），
    **`W3`（根内符号链接指向根外）仍缺**。
    ⚠️ **边界（防误引）**：本项是**攻击面 6（配置面落点）**的缓解，**不是**"且留审计"半的缓解——
    二者**不互相替代**（见本条目开头的 `A-3` 裁决段）。
- **残余风险**：
  1. **"唯一入口"的机器检查覆盖面仍不全**：既有检查只覆盖"不得在别处定义同名函数"
     （`test_architecture_layers.py:266-274`）；2026-09-18 新增的接缝检查
     （`test_path_validation_seam.py`：源码中 `raise PathNotAllowedError` 只应出现在
     `foundation/paths.py`）**进一步**堵住了"别处自建白名单并自行抛错"这条绕过，
     但**仍不覆盖**"不得自行做路径判断 / 拼接"——`os.path.join` + 前缀比较在别处出现**不会被拦**。
     ⇒ `ADR-0015 §5.1.1` 的 R4 语义（唯一入口）由"只兑现一半"改善为"**兑现大半、但未完全兑现**"。
  2. **校验与使用之间的 TOCTOU 窗口**：`resolve()` 在**校验时刻**解析符号链接；
     之后若路径分量被替换（`bench` 的一次性工作目录是 `chmod 0o777`，
     `paths.py:42-49` 的 `make_writable_by_all`），解析结果与打开的对象可能不是同一个。
     单人本地场景风险低，**但这条正是 `make_writable_by_all` 的注释所依赖的假设**
     （"仅用于一次性工作目录，不得对仓库目录调用"，**无机器检查**）。
  3. **白名单根自身未被校验**：`roots` 来自调用方；若调用方传入宽根（如 `/`），
     该函数正确地"什么都允许"——**它只保证不越出给定根**。
  4. **Windows 语义未覆盖**：UNC、盘符、大小写不敏感文件系统、8.3 短名。
     `REQ-PLAT-01` 要求跨平台，**当前只在 Linux 验证过** 【待验证】。
     **UNC 补充评估（2026-09-19）**：在 **Linux** 下 `//` 折叠为 `/`
     （实测 `Path("//server/share").resolve() == Path("/server/share")`），
     故 UNC **不构成 Linux 上的独立绕过面**；本项仍属**多平台项**（Windows / 盘符语义未验）。
  5. **配置面缓解的残余（2026-09-19 新增）**：① 允许的根集合仍由**代码**决定——装配代码若显式
     传入宽根，白名单等于被放宽（`resolve_within` **不校验根本身**，与残余风险 3 同一失效模式；
     当前唯一根是 `default_audit_directory()`）；② "是否允许额外根（如 CI 挂载卷）"是**策略待确认项**
     （`../interfaces/audit.md` §5 的 `A1`），**未拍板前按"单一根"实现**；
     ③ 构造期校验**不**覆盖运行期路径替换（TOCTOU），同残余风险 2。
- **验证方式**：
  - **已有（行为，2026-09-18，`f436b0b`；同日追加前缀欺骗用例）**：`S2` 的"**拒绝**"半——
    落地位置 `tests/security/`：`test_path_traversal_rejected.py`（**18 用例**：覆盖第 1~4 类输入 +
    边界正确性 + 变异验证 + 4 种前缀欺骗 + 3 种"根内同前缀文件名"）与
    `test_path_validation_seam.py`（2 接缝用例）。**变异验证（一）**：临时移除 `resolve_within`
    拒绝分支后 **9 个拒绝用例 `DID NOT RAISE`（失败）**，恢复后 `raise` 回到 `paths.py:39`、工作树干净。
  - **工具层"且留审计"半——已有证据（2026-09-19 更正：原写"该半至今无证据"，有误）**：
    ① `tests/security/test_s1_replayable_audit_link.py::test_denied_tool_call_is_audited_and_replayable`
    （**真实** sink 端到端 `query_by_id`）；② `tests/security/test_t02_path_traversal_audit.py`
    （`4a35c61`，**真实** `JsonlAuditSink` + **真实** `ReadFileTool`，断言 `kind=TOOL_CALL` /
    `outcome=ERROR` / `detail.reason == "path_not_allowed"`，含**变异探针**证明非恒过）。
    ⚠️ **防误引（保留并精化）**：`tests/security/test_audit_landing_whitelist.py`
    **仍不构成**该半的证据（它断言**审计落点目录白名单**，属攻击面 6，见上方 `A-3` 裁决段）——
    **但该半的证据在别处**（见本行前两处用例）；**不得**因"这份文件不是证据"误读为"该半无证据"
    （本轮错误的形状正是如此，见 `README.md` §7 的假声明事故登记）。
    `test_path_traversal_rejected.py` 的**旧 docstring** 曾写「该半无法验证」——**那是假声明**，
    已由 `b79f383` 修正（现指向上述两处用例）。
  - **仍未闭合（`Q-1` 的范围问题）**：`resolve_within` 的**非工具层调用点**（配置期 / 装配期）
    在拒绝时**不进审计**——"由谁 emit"的选项与代价见 `README.md` §8.2 的 `Q-1`
    （**范围已收窄**：工具层运行时半已有证据，剩下的只是非工具层调用点）。
  - **第 5 类（Windows / UNC / 盘符）**：本机为 Linux，`REQ-PLAT-01` 的多平台 job 未建；
    **UNC 已判不构成独立绕过面**（Linux 下 `//` 折叠，实测 `Path("//server/share").resolve() ==
    Path("/server/share")`）⇒ 缺口降为"多平台项"，见残余风险 4。
  - **第 4 类（前缀欺骗）已补（2026-09-18）**：`/tmp/foo` vs `/tmp/foobar` 这类
    "**字符串前缀为真、路径分量不为真**"的输入，按**参数化**覆盖 4 种兄弟目录名
    （`-evil` / `2` / `.bak` / `_backup`）＋ 3 种"位于根内、文件名与根同前缀"的**边界正确性**用例
    （`test_sibling_directory_sharing_root_prefix_is_rejected` /
    `test_files_inside_root_sharing_root_name_are_accepted`）。
    **变异验证（二）**：把判据改成字符串前缀式
    （`resolved == root_resolved or str(resolved).startswith(str(root_resolved))`）后，
    4 个前缀欺骗用例**全部 `DID NOT RAISE`（失败）**，而 3 个边界正确性用例**仍通过**
    ⇒ 断言**精准且非恒过**；恢复后 `git diff src/` 为空。
    用例内另有前置断言"该输入**确实**能骗过字符串前缀式判断"——若哪天输入不再构成前缀欺骗，
    它会失败并提醒更换输入（防用例退化为恒过）。
  - **配置面落点的 `W1`~`W8`（`../interfaces/audit.md` §2.5）——2026-09-19 复核后的现状**：
    **已落地 `W1`/`W2`/`W4`~`W8`**（`tests/security/test_audit_landing_whitelist.py`，`91ea9d5`）：
    根外目录 / `..` 上跳 / 绕过配置直接构造 sink / **"拒绝时不创建目录"** /
    **"拒绝时不回退默认目录"** / W7 变异探针（把 `resolve_within` 换成直通实现 ⇒ W1/W5 失效）/
    W8 两层各撤一层各有用例失败；
    **仍缺 `W3`（根内符号链接指向根外）**——该输入**未被任何用例覆盖**（需要在允许根内建一个
    指向根外的符号链接，断言 `ConfigError`）。
  - **【待验证】项**：Windows/UNC 输入 → 在 win_amd64 运行器上跑同一组参数化输入
    （验证方式：CI 多平台 job，`REQ-PLAT-01`）。
- **相关**：`REQ-SEC-05`/`REQ-SEC-06`/`REQ-SEC-09`、`REQ-OBS-01`（审计落点可检索）、
  `ADR-0006 §6`（风险表"路径白名单被绕过"）、`ADR-0015 §7.2`（`S2`）、
  `../interfaces/audit.md` §2.5（配置面落点白名单）。

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
