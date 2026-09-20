# 威胁证据覆盖独立复核（T-01 / T-02 / T-08 / T-11 / T-12）

> 复核日期：2026-09-20
> 复核角色：验证工程师（verifier-threat-verify）
> 口径声明：**所有结论均从 `tests/**` 的用例源码独立推导，未采信威胁模型文档的转述，也未采信 `docs/devlog/` 的结论**。
> 本文件是**调研笔记**（`docs/research/`，验证工程师独占产出域），**不改任何威胁状态、不改任何计数、不替架构师落盘**任何 `docs/design/threat-model/` 内容。
> 若下文指出威胁模型某条陈述与仓库事实不符，那是"转交架构师更正"的建议，不是本角色越权修改。
> 方法、格式沿用上一轮 `docs/research/2026-09-20-threat-evidence-coverage.md`（以下简称"第一轮"）。

---

## 0. 复核方法与可复现命令

1. 逐条读取 `docs/design/threat-model/` 三份条目组文件，记录其"当前状态 / 缓解载体 / 现有验证 / 残余风险"。
2. 对每条"缓解载体"与"验证方式"，**回到 `src/` 与 `tests/` 实测**：`read_file` 逐行判断"断言了什么 / 没断言什么"，对不存在的用例做全仓 `grep` 确认。
3. 跑目标用例确认可复现（见下）。

**本次实际执行的命令与结果**：

```text
$ cd /workspace && uv run pytest \
    tests/security/test_rlimit_isolation.py \
    tests/security/test_path_traversal_rejected.py \
    tests/security/test_path_validation_seam.py \
    tests/security/test_t02_path_traversal_audit.py \
    tests/security/test_s1_replayable_audit_link.py \
    tests/security/test_spawn_credentials_canary.py \
    tests/security/test_spawn_env_explicit.py \
    tests/security/test_t08_independent_spawn_env.py \
    tests/security/test_harness_s1_authorization.py \
    tests/security/test_harness_s3_domain_pack.py \
    tests/security/test_no_dynamic_code_in_src.py \
    tests/security/test_g8_tool_defense.py \
    tests/security/test_audit_landing_whitelist.py -q
57 passed in 0.29s                                                              # exit 0

# T-11 缺口 (a) 所依赖的单元层裁决载体确认存在（真实 PolicyEngine）
$ ls tests/security/test_policy_eval_failure.py
tests/security/test_policy_eval_failure.py                                     # 存在

# corpus 行数核对（T-02 "18 例" 口径）
$ grep -vc '^\s*#' tests/security/corpus/traversal_payloads.txt
6                                                                              # 6 条非注释载荷 → 与参数化叠加后共 18 例，见 §1 T-02
```

**本轮基准判据**：每条被判定为"证据名副其实"的行为用例，**必须**带变异探针（摘除真实保护后原断言翻红）或对照组（放松条件下同一路径真的执行），否则视为"恒过 / 绿着但没生效"。

---

## 1. 逐条复核

### T-01 子进程执行与逃逸

**当前状态**：`部分缓解`（§4 表列入，§5 已移出"缺行为层验证"清单）。

**文档声称的验证**：`test_rlimit_isolation.py` 已落地"超限分配（行为 + 变异探针）"——`test_isolated_child_cannot_overallocate_address_space`（子进程 `mmap` 3 GiB > 2 GiB `RLIMIT_AS` → 断言未成功映射）+ `test_rlimit_guard_depends_on_apply_limits`（monkeypatch `_apply_limits` 为 no-op ⇒ 攻击得手）。缺口：越界写 / 越界出站 / `IsolationError` 不回退（`test_isolation_failure_does_not_fall_back` 未落地）。

**独立核实（从源码）**：

- `tests/security/test_rlimit_isolation.py:39` `test_isolated_child_cannot_overallocate_address_space`（标记 `@pytest.mark.security`）：以 `isolation="user"` 起子进程映射 3 GiB（仅虚拟地址，不触物理内存），断言输出中**不含** `ALLOC_OK`。若 `RLIMIT_AS` 未生效，子进程会成功映射并打印 `ALLOC_OK` ⇒ 断言失败。**这是真实的行为断言，非恒过**。
- `:58` `test_rlimit_guard_depends_on_apply_limits`：把 `proc._apply_limits` 换成 no-op，断言 `ALLOC_OK` **出现**（攻击得手），证明上方正向用例依赖真实保护。
- 用例内 docstring（`:12-13`、`:64`）明确记录变异验证手法，与实现一致。

**断言了什么 / 没断言什么**：断言了"`run(isolation='user')` 下 `RLIMIT_AS` 真实生效、子进程无法超分配地址空间"这一**资源上限收敛为拒绝**的落点；**没有**断言：① 越界**写**白名单外路径被拦（`proc.run` 本身不含路径白名单，文档已登记为"假断言"并排除）；② 越界**出站**（无机制，归 `T-10`）；③ `IsolationError` 不回退为普通执行（`test_isolation_failure_does_not_fall_back` 未落地——文档如实标注，且环境以 root 运行使该非 root 判据写法不可用）。

**缺口与文档一致性**：文档列出的缺口（越界写 / 越界出站 / `IsolationError` 不回退）**准确**，与仓库事实一致——`test_isolation_failure_does_not_fall_back` 全仓 `grep` 无命中。`T-13` 的"隔离失败不回退"矩阵仍缺专门探针，但与本处是不同落点。

**升级判据**：**无升级依据**。状态维持 `部分缓解`；文档声称与仓库事实一致，未失真。

**可复现步骤**：`uv run pytest tests/security/test_rlimit_isolation.py -q` → 2 passed。

---

### T-02 路径穿越  ← 当前唯一的「已缓解并验证」

**当前状态**：`已缓解并验证`（2026-09-20 所有者裁决 `P-2`；§8.2 登记）。

**文档声称的验证（升级依据）**：
- 「拒绝」半：`test_path_traversal_rejected.py` **18 例** + `test_path_validation_seam.py` **2 接缝例**，含变异验证（判据换字符串前缀式 ⇒ 4 个前缀欺骗用例全红）；
- 「且留审计」半：`test_s1_replayable_audit_link.py::test_denied_tool_call_is_audited_and_replayable` 与 `test_t02_path_traversal_audit.py`（**两处真实 `JsonlAuditSink` 端到端 + 变异探针**）；
- 三条残余风险（升级后仍连读）：① UNC/盘符跨平台未验；② "别处自建路径判定"未覆盖；③ 配置期/装配期调用点不进审计（`Q-1` 范围外）。

**独立核实（从源码）**：

「拒绝」半：
- `tests/security/test_path_traversal_rejected.py`：`:47` 参数化（corpus 6 条 `..` 载荷）+ `:57` 绝对路径 + `:66` 软链接外指 + `:92` 深层穿越 + `:102` 边界正确性 + `:122` 前缀欺骗（4 参数）+ `:156` 根内同前缀文件名（3 参数）+ `:173` 变异探针。实计数 = 6+1+1+1+1+4+3+1 = **18 例**，与文档口径一致，非虚报。`:173` `test_path_traversal_rejection_depends_on_protection` 把 `resolve_within` 换成直通实现 ⇒ 攻击得手（断言"不在白名单内"），证明非恒过。
- `tests/security/test_path_validation_seam.py:24` `test_only_paths_layer_raises_path_not_allowed`（结构性：`raise PathNotAllowedError` 只应出现在 `foundation/paths.py`）+ `:36` `test_paths_layer_exposes_resolve_within_entrypoint`（保活唯一入口）。2 例属实。

「且留审计」半：
- `tests/security/test_t02_path_traversal_audit.py:59` `test_runtime_path_traversal_rejection_is_audited`：**真实** `JsonlAuditSink` + **真实** `ReadFileTool`（`path="../forbidden.txt"` ⇒ 工具层路径拒绝），断言拒绝发生、`audit_id` 可经 `query_by_id` 从磁盘还原、`kind=TOOL_CALL`、`detail.reason=="path_not_allowed"`（安全语义字段）。`:91` `test_runtime_path_traversal_audit_depends_on_real_emit` 摘掉 `audit_tool_call` ⇒ `audit_id is None`、磁盘无事件 ⇒ 主用例翻红，证明非恒过。
- `tests/security/test_s1_replayable_audit_link.py:85` `test_denied_tool_call_is_audited_and_replayable`：**真实** `JsonlAuditSink` + **真实** `ReadFileTool` 的路径越权拒绝路径，同样断言可回放。`test_authorized_tool_call_audit_is_replayable`（`:48`）覆盖"授权调用也留痕"，与本处互补。

**断言了什么 / 没断言什么**：断言了"**会话内工具层**路径穿越被拒、且该拒绝留痕可回放"这一条缓解有可执行、可回归、带变异探针的证据（符合 §4.1 的"已缓解并验证"定义）；**不证明**宿主整体安全、不证明三条残余风险已消除、不证明配置期/装配期两类不可信输入已被处置。

**三条残余风险与覆盖面对比（重点）**：
- ① UNC/跨平台：`test_path_traversal_rejected.py` 全用 Linux 路径语义，corpus 无 UNC 载荷；`test_audit_landing_whitelist.py:284-296` 显式记录"Linux 下 `//` 折叠为 `/` ⇒ UNC 不构成独立绕过面，不写弱断言"。与文档一致，**诚实**。
- ② "别处自建路径判定"未覆盖：`test_path_validation_seam.py:24` 只查 `raise PathNotAllowedError` 的出现位置，**不查** `os.path.join` + 前缀比较在别处的自建判断。与文档残余风险 2（`:225-229`）一致。
- ③ 配置期/装配期不进审计：本"且留审计"证据仅覆盖**工具层**（两处均用 `ReadFileTool`）。与文档 `Q-1` 范围外裁定一致。

**有无被漏掉的第四类（团队领导追问）**：文档"三条"是**高亮**，并非穷尽——其自身「残余风险」整节（`:224-247`）还列有 **2 条未被任何行为用例覆盖**的项，且**未进入"三条"高亮**：
- 残余风险 2（`:231-235`）：`resolve()` 校验时刻与 `open` 时刻之间的 **TOCTOU 窗口**（一次性 `chmod 0o777` 工作目录被替换）；**无用例**。
- 残余风险 3（`:236-237`）：**白名单根自身未被校验**（`roots` 来自调用方，宽根如 `/` 会让函数"什么都允许"）；**无用例**。
- 残余风险 5（`:243-247`）：配置面缓解的构造期 TOCTOU、允许根由代码决定等；无独立用例。
这三条**都在文档正文里写明**，故不属"被文档完全漏记的第四类"，只是"三条"高亮把它们隐去。结论：**未发现文档根本未提及的真实缺口**；但建议架构师在 `P-2` 结论块补一句"三条高亮之外，TOCTOU 与根自校验仍属未测"。

另记一处**轻微覆盖局限**（非失真）："且留审计"两处证据均只跑 `ReadFileTool`，`WriteFileTool` 路径穿越拒绝的审计未被直接运动；因二者共用 `tools/registry.py::audit_tool_call`，属"代表性覆盖"，不构成误引。

**升级判据**：**"已缓解并验证"名副其实**——证据真实、带变异探针、可回归；三条残余风险（含上述两条未高亮的）均诚实标注为"不阻断升级"。状态维持。

**可复现步骤**：
`uv run pytest tests/security/test_path_traversal_rejected.py tests/security/test_path_validation_seam.py tests/security/test_t02_path_traversal_audit.py tests/security/test_s1_replayable_audit_link.py -q` → 18+2+2+2 = 24 passed（`s1` 文件另含 2 条授权/变异，与本处重叠计 2）。

---

### T-08 基准数据分支的凭据暴露（`CNB_TOKEN`）

**当前状态**：`部分缓解`（§4 表列入）。

**文档声称的验证**：`spawn` 凭据继承已由"行为 canary + 静态守卫"验证（`5fddcfa`+`8e047e6`，随修复由 `xfail` 翻正）；缺口：`run(isolation="root")` 继承路径无机器检查；CLI 消费令牌无用例（`cnb` 不存在）。

**独立核实（从源码）**：

- `tests/security/test_spawn_credentials_canary.py:40` `test_spawned_child_must_not_inherit_credentials`（标记 `@pytest.mark.security`）：父进程注入**合成**哨兵 `CNB_TOKEN`（不含真实令牌），以"不传 `env`"复刻 `runner.py` 调用形态调 `proc.spawn`，断言子进程输出**不含**该哨兵。若 `spawn` 继承 `os.environ`，子进程会打印哨兵 ⇒ 断言失败。**真实凭据继承 canary，非恒过**。
- `tests/security/test_spawn_env_explicit.py:57` `test_all_spawn_calls_pass_env_explicitly`（标记 `@pytest.mark.security`，**静态**）：扫描 `src/` 每个 `spawn(...)` 调用必须显式传 `env=`。与 canary **互补**（反向变异"把 spawn 默认改回继承"不会让本例变红，由运行时 canary 负责）。该检查**非真空**：`bench/runner.py` 确有 `spawn` 调用且已显式传 `env=`，故 offender 为空是"真不变"而非"无对象"。
- `tests/security/test_t08_independent_spawn_env.py:41` `test_spawned_child_env_is_exactly_minimal_whitelist`（标记 `@pytest.mark.security`，**验证工程师独立用例**）：真实子进程 `os.environ` 键集须**恰好等于**验证者独立写死的最小清单 `{PATH,HOME,LANG,LC_ALL,PYTHONDONTWRITEBYTECODE,PYTHONPATH}`，且不得出现 `TOKEN/KEY/SECRET` 后缀键、合成哨兵不在其中。不依赖任何 monkeypatch spy，亦不复用实现者断言逻辑。

**断言了什么 / 没断言什么**：断言了"`spawn` 默认最小环境、子进程不继承父进程凭据（含合成哨兵）"这一**凭据隔离收敛**落点；**没有**断言：① `run(isolation="root")` 路径（仍 `dict(os.environ)`，文档残余风险 3，无用例）；② CLI 在主进程内消费令牌（残余风险 4，`cnb` 不存在，无用例）；③ 隔离逃逸后读宿主凭据（残余风险 1，无用例）。

**缺口与文档一致性**：文档列出的缺口（`run(isolation="root")`、CLI 消费）**准确**，与仓库事实一致。canary + 静态守卫 + 独立验证三者并存，证据名副其实。

**有无被漏掉的第四类（团队领导追问）**：文档「残余风险」整节列 4 项（逃逸/ spawn 已修/ root 继承/ CLI 消费），其中未覆盖的是 ①、③、④ 三项，文档"仍缺"段已显式点名 ③、④；①（逃逸）在正文标"未缓解"。**未发现文档根本未提及的真实缺口**。

**负向发现（清单漂移，仿第一轮 §2「乙/清单与仓库漂移」）**：`supply-chain-and-process.md:188-190` 写"团队领导 2026-09-18 另派独立验证者对修复 `8e047e6` 做对抗性复核（产出落 `tests/security/` 与 `docs/research/`）——**复核结论尚未落盘**；本判定基于当前仓库证据"。但 `tests/security/test_t08_independent_spawn_env.py` 的 docstring 明写"T-08 独立复核…（验证工程师独立用例）"，**即该独立复核的产物已落 `tests/security/`**。⇒ 文档"复核结论尚未落盘"一语**已过时**（仓库现状：独立验证已落地）。方向为"文档低估/陈旧"，**不改变 T-08 状态**（spawn 路径确已闭环），但属 `devlog 0019` 警示的"陈述与仓库脱节"类，建议架构师在 `T-08` 条目把"尚未落盘"改为"独立复核已落 `test_t08_independent_spawn_env.py`"。

**升级判据**：**无升级依据**。状态维持 `部分缓解`；canary/静态守卫/独立验证名副其实，仅"陈旧陈述"需更正。

**可复现步骤**：`uv run pytest tests/security/test_spawn_credentials_canary.py tests/security/test_spawn_env_explicit.py tests/security/test_t08_independent_spawn_env.py -q` → 3 passed。

---

### T-11 越权工具调用与工具滥用

**当前状态**：`部分缓解`（2026-09-19 由「未缓解」升，依据 `S1` 入库）。

**文档声称的三条缺口（a)(b)(c)，不得省略**——本轮逐项核实是否准确：
- (a) harness 路径未端到端跑**真实** `PolicyEngine`：`S1` 注入 `FakePolicyEngine`（决策由替身返回）⇒ "真实策略引擎求值 ⇒ `Session` 拒绝 ⇒ 审计"端到端链无证据；但**攻击路径 3（策略求值出错却返回 allow）已在 `PolicyEngine` 单元层断言**（`test_policy_eval_failure.py`，真实引擎）。
- (b) 「**工具滥用**」半**零行为层证据**：`S1` 只覆盖"越权调用被拒"，不覆盖已授权工具超范围使用 / 平台特权工具使用范围。
- (c) 能力层 `POLICY_DECISION` deny 的真实 sink 端到端**未断言**：`S1` 审计断言只经 `RecordingSink`（假件）；真实 `JsonlAuditSink` 端到端断言落在**工具层路径越权**（`test_s1_replayable_audit_link.py` 第 2 例），不是 `POLICY_DECISION` 那一档。

**独立核实（从源码）**：

- `tests/security/test_harness_s1_authorization.py:27-38` 导入 `FakePolicyEngine` / `RecordingSink`；`:92` `policy = FakePolicyEngine(decision, sink)`、`:86` `sink = RecordingSink()`。确认 `S1` 全程注入**假策略引擎 + 假审计桩**。`:125` `test_s1_unauthorized_tool_not_executed` 断言"未被授权工具不执行 + `TOOL_RESULT.result is None` + 审计含 `TOOL_CALL/DENY`(`denied_reason=policy_denied`) + `POLICY_DECISION` + `audit_id` 对应 DENY 事件"。`:151` 对照组（策略放行）工具真被执行、result 非 None ⇒ 证明非恒过。**这确证 (a)：端到端未用真实引擎；(c)：审计只经 `RecordingSink` 假件**。
- `tests/security/test_policy_eval_failure.py:55` `test_eval_failure_converges_to_deny_without_missing_key`：**真实** `PolicyEngine`（`policy_module.PolicyEngine`），monkeypatch `CapabilitySet.missing` 抛错 ⇒ 断言 `allow=False / requires_confirmation=True / risk_level=CRITICAL` 且 `detail` 无 `missing` 键。`:82`、`:99` 同理。⇒ 确证 (a) 的"单元层已有断言"属实，但此文件**不经 `Session.run`**，与文档一致。
- `tests/security/test_g8_tool_defense.py`（团队领导点名核查归属）：`:50` 相对路径基准（working_dir 非 CWD）、`:79` 裸字符串 `argv` 拒、`:91` `argv` 不经 shell（`; $()` 不解释）、`:104` `IsolationError` 不回退、`:128` 审计失败冒泡。五例落点均为 **`T-02`（路径/traversal）与 `T-01`（shell/argv/隔离失败）** 领域，**没有任何一例**断言"已授权工具被超范围使用 / 平台特权工具越权"。⇒ 文档把 `test_g8_tool_defense.py` 排除出"工具滥用"半的防误引登记（`:384-386`）**正确**，该文件确非 `T-11` 证据。

**断言了什么 / 没断言什么**：`S1` 断言了"**能力未授权 / 未知工具 / 未暴露工具**的调用被拒且不执行、审计可回放（假件）"这一**越权调用拒绝**落点；**没断言** (a) 真实引擎端到端、(b) 工具滥用（授权内超范围 / 平台特权）、(c) 能力层 deny 的真实 sink 端到端。

**「工具滥用」半是否真的零证据（团队领导追问）**：**确认零行为层证据**。T-11 名义范围的"工具滥用"= 已授权工具被用于授权范围外 / 平台特权工具使用范围（`cnb` merge-PR 等）。`S1` 的 `not_exposed`（`:194`）测的是"工具是否暴露"（暴露判定），不是"已暴露工具被滥用"；能力粒度仅 4 个 `Capability`、`RiskLevel` 逐次求值的设计未落地测试；`cnb` 不存在故平台特权面无载体。**与文档一致**。

**升级判据**：**无升级依据**。三条缺口 (a)(b)(c) 均准确，状态维持 `部分缓解`。

**可复现步骤**：`uv run pytest tests/security/test_harness_s1_authorization.py tests/security/test_policy_eval_failure.py tests/security/test_g8_tool_defense.py -q` → 5+3+5 = 13 passed。

---

### T-12 领域包加载代码（动态导入不可信模块）

**当前状态**：`部分缓解`（2026-09-19 由「未缓解」升，依据 `S3` 入库）。

**文档声称的验证**：`S3` `test_harness_s3_domain_pack.py` **4 例 + 变异探针**（`.py`/`.pyc`/`__pycache__` 同拒、副作用不发生、不入 `sys.modules`）；静态守卫 `test_no_dynamic_code_in_src.py`（结构性，非行为）。缺口：`.pth` 导入期执行、`.so`/ctypes 动态加载向量**未显式断言**；`S3` 仅覆盖「领域包」这一载体。

**独立核实（从源码）**：

- `tests/security/test_harness_s3_domain_pack.py:55` `test_s3_rejects_python_source_with_side_effect`（标记 `@pytest.mark.security`）：包内 `.py` 带写文件副作用 ⇒ `DomainPackError` + 标志文件不存在 + `"helper" not in sys.modules`（用例内先 `find_spec` 断言"模块确实可导入"，使"不在 sys.modules"有意义，防恒过）。`:79` `.pyc` 同拒、`:91` `__pycache__` 同拒、`:105` `test_s3_rejection_depends_on_guard` 把 `_reject_python_content` 换空操作 ⇒ `load_pack` 不再抛错、正常装配 ⇒ 原拒绝断言翻红，证明依赖真实保护、非恒过。**4 例 + 变异探针属实**。
- `tests/security/test_no_dynamic_code_in_src.py:31` `test_no_dynamic_code_execution_in_src`（标记 `@pytest.mark.security`，**静态**）：扫描 `src/` 不得出现 `eval(`/`exec(`/`importlib`/`__import__`（词边界）。文档已明确"结构性检查、非行为断言、与 `S3` 互补不互替"——与代码一致。

**断言了什么 / 没断言什么**：断言了"`R5`：领域包目录内 `.py`/`.pyc`/`__pycache__` 不得被导入执行（fail-secure）"这一**领域包载体**落点；**没断言**：① `.pth`（解释器启动时执行代码）、`.so`/`ctypes` 等动态加载向量；② 领域包之外的"读盘 + 加载"入口（checkpoint / repo map / CLI 插件目录）出现自建加载器。

**缺口与文档一致性**：文档列出的缺口（`.pth`/`.so`/ctypes、领域包-only）**准确**，与仓库事实一致。`S3` 变异探针真实存在，非恒过。

**升级判据**：**无升级依据**。状态维持 `部分缓解`；证据名副其实。

**可复现步骤**：`uv run pytest tests/security/test_harness_s3_domain_pack.py tests/security/test_no_dynamic_code_in_src.py -q` → 4+1 = 5 passed。

---

## 2. 负向发现汇总（本轮强制要求的两类）

> 口径（同第一轮）：**甲** = 看起来像证据、实际断言的是另一攻击面（误引风险）；**乙** = 文档声称有证据/有载体、实际无载体或不可执行（假声明风险）。
> 本轮在这 5 条里**未发现**甲、未发现"方向相反（低估）"的乙（文档对 T-01/T-02/T-08/T-11/T-12 的证据描述均与仓库源码一致）。仅发现**一处清单/陈述漂移**（陈旧），列下。

### 甲（误引风险）

| # | 落点 | 现状 | 结论 |
| --- | --- | --- | --- |
| — | T-02 / T-11 | 文档已显式排除 `test_audit_landing_whitelist.py`（攻击面 6）充作 T-02「且留审计」证据、排除 `test_g8_tool_defense.py` 充作 T-11「工具滥用」证据 | 经逐行核实，两处排除**正确**：前者测 `W1~W8` 审计落点白名单、后者测 T-02/T-01 的 shell/argv/隔离失败，**均非**所指控的落点。本轮**无新增甲** |

### 乙（声明失真 / 漂移）

| # | 落点 | 文档声称 | 仓库事实 | 严重度 | 处置 |
| --- | --- | --- | --- | --- | --- |
| 乙-1 | T-08（`supply-chain-and-process.md:188-190`） | "独立验证者复核结论**尚未落盘**" | `tests/security/test_t08_independent_spawn_env.py` docstring 明写为"验证工程师独立复核…独立用例"，即独立复核产物**已落 `tests/security/`** | 低（陈旧，不改状态） | 转交架构师把"尚未落盘"改为"已落 `test_t08_independent_spawn_env.py`"；**不自行改威胁模型** |

> 乙-1 的性质等同第一轮 §4 的"清单与仓库漂移"（陈述落后于仓库现状），危害同为"后续判分/审批基于旧事实"。但 T-08 的 `spawn` 凭据隔离确已闭环、证据名副其实，故**不影响状态判定**，只影响文档时效。

---

## 3. 给领导的建议（逐条升降级与依据）

| 条目 | 现状 | 建议 | 依据 |
| --- | --- | --- | --- |
| T-01 | 部分缓解 | **维持**；文档声称与仓库一致，未失真 | `test_rlimit_isolation.py` 行为+变异探针名副其实；缺口（越界写/出站/`IsolationError` 不回退）准确 |
| T-02 | 已缓解并验证 | **维持**；证据名副其实 | 「拒绝」18 例 + 接缝 2 例 + 「且留审计」2 处真实 sink 端到端，均带变异探针；三条残余风险诚实。建议补充：把 TOCTOU、根自校验两条未高亮的残余风险并入 `P-2` 结论块 |
| T-08 | 部分缓解 | **维持**；更正乙-1 的陈旧陈述（不升降状态） | canary+静态守卫+独立验证三者并存、名副其实；`run(isolation="root")` 与 CLI 消费缺口准确 |
| T-11 | 部分缓解 | **维持**；三条缺口 (a)(b)(c) 准确 | `S1` 注入 `FakePolicyEngine`+`RecordingSink` 证实 (a)(c)；`test_policy_eval_failure.py` 真实引擎单元层证实"单元已有、端到端无"；`test_g8_tool_defense.py` 确非「工具滥用」证据 |
| T-12 | 部分缓解 | **维持**；文档声称与仓库一致 | `S3` 4 例+变异探针名副其实；静态守卫定性准确；`.pth`/`.so`/ctypes 与 domain-pack-only 缺口准确 |

**未做任何状态/计数修改**（遵守团队领导指令与 `CODEBUDDY.md` §10.2 规则 9：完成判定只认仓库；本报告仅作调研结论，落盘权属归架构师/记录员）。

---

## 4. 复核时发现的"绿着但没生效"类静默失败（优先上报）

本轮**未发现**典型"绿着但没生效"：本批 5 条涉及的行为用例（T-01 / T-02 / T-08 / T-11 / T-12）**全部带变异探针或对照组**——`test_rlimit_guard_depends_on_apply_limits`、`test_path_traversal_rejection_depends_on_protection`、`test_runtime_path_traversal_audit_depends_on_real_emit`、`test_s1_control_policy_allowed_invokes_tool`（对照）、`test_s3_rejection_depends_on_guard`、`test_spawned_child_env_is_exactly_minimal_whitelist`、`test_t08` 的"恰等最小清单"断言——均经实测 57 passed 可复现，且探针/对照证明非恒过。

唯一需提示的"口径一致性"项（非用例失效）：T-02 的"三条残余风险"高亮**隐去**了文档自身「残余风险」整节里的 TOCTOU 与根自校验两条未测项（见 §1 T-02）。这不改变"已缓解并验证"的定性，仅建议补一句说明，避免读者以为"三条"即全部未测面。

---

## 5. 与第一轮（T-03/T-04/T-05/T-06/T-07/T-09/T-10/T-13）的关系

第一轮发现的 2 处声明失真（乙-1 `T-03` 低估、乙-2 `T-09` 低估）、3 处误引风险（甲-1~甲-3）、1 处清单漂移（§5 计数未随 `S-new-6` 重算）**均不波及本轮 5 条**——本轮 5 条的证据描述为首次被独立逐行复核，结论为"名副其实 + 一处陈旧漂移（乙-1）"。两轮的"状态维持、仅转交架构师更正失真陈述"基调一致。
