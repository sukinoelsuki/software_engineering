# 威胁证据覆盖独立复核（T-03 / T-04 / T-05 / T-06 / T-07 / T-09 / T-10 / T-13）

> 复核日期：2026-09-20
> 复核角色：验证工程师（verifier-threat-coverage）
> 口径声明：**所有结论均从源码与用例源码独立推导，未采信威胁模型文档里的转述，也未采信 `docs/devlog/` 的结论**。
> 本文件是**调研笔记**（验证工程师独占产出域 `docs/research/`），**不改任何威胁状态、不改任何计数、不替架构师落盘**任何 `docs/design/threat-model/` 内容。
> 若下文指出威胁模型的某条陈述与仓库事实不符，那是"转交架构师更正"的建议，不是本角色越权修改。

---

## 0. 复核方法与可复现命令

1. 逐条读取 `docs/design/threat-model/` 的三份条目组文件，记录其"当前状态 / 缓解载体 / 现有验证 / 残余风险"。
2. 对每条声称的"缓解载体"与"验证方式"，**回到 `src/` 与 `tests/` 实测**：
   - 用 `search_content` / `list_dir` 定位符号与文件；
   - 直接 `read_file` 读取用例源码，逐行判断"断言了什么 / 没断言什么"；
   - 对不存在的用例名做全仓 `grep`，确认其确实不存在。
3. 跑选定的用例确认可复现（见下）。

**本次实际执行的命令与结果**：

```text
# 环境（按团队领导指令）
$ cd /workspace && uv sync --extra dev --extra security
Resolved 76 packages in 1ms / Checked 76 packages in 1ms   # exit 0

# 关键用例实测（可复现证据）
$ uv run pytest tests/security/test_harness_snew.py \
    tests/unit/test_tools_registry.py \
    tests/unit/test_bench_encapsulation.py::test_exemption_markers_declare_exactly_the_expected_rules \
    tests/unit/test_remote_write_authorization_consistency.py -q
.........................................                                [100%]
41 passed in 0.16s                                                              # exit 0

# 确认两个被威胁模型点名"应有（缺验证）"的用例确实不存在
$ grep -rE "test_commit_touches_only_declared_paths|test_member_report_without_commit_is_not_counted_as_done" tests/
# → 无任何输出（确认不存在）

# 确认注入语料集确实不存在
$ ls tests/security/corpus/
traversal_payloads.txt        # 仅路径穿越语料，无注入语料
```

**§5「缺行为层验证」口径回顾**（取自 `README.md` §5，写死）："该条目当前的验证方式中，尚无可通过
`make check` / `make test-security` 执行的**行为级（对抗性）用例**；结构性机器检查不计入"。
本复核的一个重要判断点：**该口径本身是否被正确执行**——即被列为"缺验证"的条目，是否真的没有
`@pytest.mark.security` 行为级用例。

---

## 1. 逐条复核

### T-03 不可信输入被当作指令（指令-数据混淆）

**当前状态**：`未缓解`（§4 表 / §5 清单均列其为"验证方式：无"）。
**缓解载体（文档声称）**：`untrusted-input-and-agentic.md:30-46` 称缓解"**全部是设计与契约，无实现载体**"，
残余风险 1 明写"`HARNESS` 的'信任边界校验'（D2：pydantic 严格校验）**一行代码都没有——`harness/` 尚未建立**"。

**独立核实的缓解载体（与文档相反）**：`harness/` 已落地（与 README §0 的"9 件已落地"一致），且指令-数据分离
是**有实现**的：

- `src/agent_sec_perf/harness/prompts.py:137-144` `build_system_message`：**不接受任何外部内容参数**，
  SYSTEM 位内容只可能来自模块常量模板；
- `prompts.py:147-154` `build_user_message`：用户内容一律 `role=USER`、永不进 SYSTEM；
- `prompts.py:157-185` `pack_context_message`：领域包片段**强制** `role=USER`；
- `src/agent_sec_perf/harness/context/__init__.py:84-139` `assemble`；**结构性守卫在 `:116-122`**：
  `data_context` 中任一条非 `USER` 角色消息 ⇒ `raise ValueError`（"领域包片段永不进入 SYSTEM 位置"）；
- `src/agent_sec_perf/harness/loop.py:516-518`：`arguments = self._validator.validate(...)`，
  原始 JSON **只在此处被解析**（信任边界，对应攻击路径 3"工具内再 `json.loads`"的封堵点）。

**现有验证的真实内容**：**存在** `@pytest.mark.security` 行为级用例
`tests/security/test_harness_snew.py:452-509`（S-new-6，含 3 条用例 + 1 条变异探针）：

- `test_snew6_data_context_system_role_rejected`（`:452`）：`data_context` 带 `role=SYSTEM` ⇒ 装配期 `ValueError`；
- `test_snew6_data_context_never_enters_system_position`（`:466`）：合法 `USER` 数据的内容**永不出现在 SYSTEM 消息**；
- `test_snew6_rejection_depends_on_guard`（`:484`）：摘掉 `assemble` 的守卫 ⇒ 原断言翻红（证明非恒过）。

S-new-6 标记 `@pytest.mark.security`，**会被 `make test-security` 收集** ⇒ 它**属于 §5 口径下的
"行为级对抗性用例"**。

**断言了什么 / 没断言什么**：断言了"**领域包片段（不可信数据）不得进入 SYSTEM 指令位**"这一具体落点；
**没有**断言 T-03 全部三条攻击路径——特别是 (a) 观察文本（`TOOL` 角色）改变工具选择/控制流（这是模型行为，
代码层不可强制）、(b) 策略求值把 `arguments` 字符串拼接进命令/路径/正则（仅被参数校验间接覆盖）、
(c) 工具内再解析原始 JSON（仅被 `loop.py:518` 单一解析点间接覆盖）。

**缺口清单**：要推进为"已缓解并验证"，仍缺：① 覆盖 (a) 的"注入观察文本不改变控制流"行为用例
（需基线对照，且当前无模型侧断言能力）；② 覆盖 (b)/(c) 的"参数绝不进入命令/路径拼接"探针；
③ 注入语料集（`tests/security/corpus/`，当前仅路径穿越语料）。

**升级判据**：**无升级依据**。`未缓解` 的**状态**可维持——S-new-6 只覆盖指令-数据边界的一个具体落点
（包片段→SYSTEM），未触及 T-03 的核心威胁（模型驱动的指令/数据混淆）。但**文档的"无验证 / harness 未建立"
陈述必须更正**（见 §2 乙-1）。状态不变 ≠ 文档陈述正确。

**本轮负向发现**：见 §2 乙-1（文档**低估**了实现与验证——典型的"声明失真"，只是方向相反）。

---

### T-04 提示注入（直接 / 间接）与上下文污染

**当前状态**：`未缓解`（§5："注入语料集不存在"）。

**独立核实**：`tests/security/corpus/` 仅有 `traversal_payloads.txt`，**无注入语料**（`ls` 实测确认）。
全仓 `grep "inject|instruction|prompt|untrusted|trusted"` 在 `tests/security/` 只命中
`test_harness_snew.py`（角色守卫，非注入）与 `_harness_fakes.py`——**确认无注入行为用例**。

**现有验证的真实内容**：**无任何行为级用例**。文档称"缺验证"属实。经 `ADR-0016` 登记的
"经 Skill `description` 的常驻注入面"其缓解为**预登记、无载体**（与文档一致）。

**缺口清单**：① 注入语料集（直接 5~10 + 间接 5~10）；② 间接注入行为用例
（`test_repo_file_with_embedded_instructions_...`）；③ 持久化变体专例
（`test_checkpoint_roundtrip_does_not_promote_injected_text_to_instructions`）；④ Skill 文本覆盖规则
只能人工核对（文档已诚实标注"不可由 CI 断言"）。

**升级判据**：**无升级依据**。维持 `未缓解`，文档陈述准确。

**本轮负向发现**：
- **甲（相邻，需防误引）**：`test_harness_snew.py:452-509`（S-new-6，见 T-03）常被误读为"提示注入防御"。
  它实际断言的是**角色位守卫**（数据不得进 SYSTEM 角色），与"注入文本改变模型行为"是**不同落点**。
  建议在架构师侧明确：S-new-6 归属 T-03（指令-数据分离），**不得**充作 T-04（提示注入）的证据。
  当前文档未把 S-new-6 当作 T-04 证据，属"潜在误引陷阱"，提前登记以防复发。

---

### T-05 子代理输出是不可信输入

**当前状态**：`部分缓解`（缓解=上游去毒 + 常驻纪律 + 完成判定只认仓库）。

**独立核实**：文档"验证方式"列出的三条可执行替代
（`test_member_report_without_commit_is_not_counted_as_done`、
`test_empty_commit_is_rejected_as_completion_signal`、
`test_commit_touches_declared_domain_paths`）**全仓 grep 均无命中**——确认不存在。
这与文档"可执行的替代／缺验证"的自述一致（诚实）。

**现有验证的真实内容**：**无自动化行为用例**。缓解靠：
① 上游 harness 非破坏性去毒（`agent-teams.md` §4，不可自测）；
② `CODEBUDDY.md` §10.4 常驻纪律（领导不得据回报改控制流）；
③ §10.2 规则 9「完成判定只认仓库 `回报：` 块」。
属"**靠人执行**"类，按 §4.1 定义 legitimately 记为`部分缓解`。

**缺口清单**：① 成员回报不带提交即不被计为完成（机器检查）；
② 空提交（`--allow-empty`）被拒为完成信号；③ `回报：` 所在提交须触及该成员声明路径
（与 T-07 复用同一检查）。三者当前均无机器检查。

**升级判据**：**无升级依据**。维持 `部分缓解`，文档陈述准确。

**本轮负向发现**：未发现甲/乙。文档自述"缺验证"属实，未把纪律误写为机制。

---

### T-06 安全豁免被静默放宽

**当前状态**：`部分缓解`（唯一有机器检查的一条）。

**独立核实**：`tests/unit/test_bench_encapsulation.py:129-153`
`test_exemption_markers_declare_exactly_the_expected_rules` 确实存在并据实测通过。

**现有验证的真实内容（断言 / 未断言）**：
- 断言三件事（`:139-153`）：① 豁免标记**只出现在** `foundation/proc.py`（`wrong_place == []`）；
  ② 标记后**只含规则号**、无自由文本（`free_text == []`）；
  ③ 每行 id 集合**恰好等于**预期集合 `_EXPECTED_MARKERS`（`:147-153`）。
- 这是**结构性机器检查**（AST/正则扫描 `src/`，见 `:25` `SRC_ROOT`）——**证明"代码长成约定要求的样子"，
  不证明"攻击被挡住"**。
- **未断言**：E1（`Test in comment` 告警计数=0）、E2（skip 计数=4）、E3（`--ignore-nosec` 恰好 4 条）——
  文档已诚实写明这三项目前靠人工（`Makefile` 只看 bandit 退出码）。
- **扫描范围缺陷**：`:25` 的 `SRC_ROOT` 仅 `src/`，`tests/`、`scripts/`、`.cnb.yml`、`docs/`
  里的豁免标记不受检查（与文档残余风险 1 一致，属实）。

**缺口清单**：① 把 E1~E3 写成自动断言；② 把 E4 扫描范围从 `src/` 扩到 `tests/` `scripts/`
并显式白名单；③ 显式化 bandit 配置发现（加 `-c pyproject.toml` 或迁 `.bandit`，见文档残余风险 3）。

**升级判据**：**无升级依据**。维持 `部分缓解`，文档对"结构性 / 非行为"的分类准确。

**本轮负向发现**：未发现甲/乙。文档对 E4 是"结构性检查"的定位正确，未误称为行为证据。

---

### T-07 共享 git 索引导致跨域混入

**当前状态**：`部分缓解`（缓解=人工 `-o` 纪律 + 两阶段核对）。

**独立核实**：文档"应有（缺验证）"的 `test_commit_touches_only_declared_paths` **全仓 grep 无命中**
——确认不存在。文档"缺验证"自述属实。

**现有验证的真实内容**：**无机器检查**。缓解全部靠 `CODEBUDDY.md` §10.2 规则 8 的人工纪律
（`git commit -o -m "<msg>" -- <显式路径>` + 先 `git diff --cached --name-status` 核对再提交）。
属"**靠人执行**"类，legitimately `部分缓解`。

**缺口清单**：① `test_commit_touches_only_declared_paths`（路径集合 ⊆ 声明白名单）机器检查；
② 或提交门禁加"路径 ⊆ 白名单"检查（需领导裁决，不在架构师域）。

**升级判据**：**无升级依据**。维持 `部分缓解`，文档陈述准确。

**本轮负向发现**：
- **甲（潜在误引陷阱）**：`tests/unit/test_remote_write_authorization_consistency.py`
  （**注意在 `tests/unit/`，非 `tests/security/`**）断言的是 **A~F 远端写入授权分级在 5 处文档载体间
  口径一致**（解析 `{类:(范围,规则)}` 后两两相等，含变异探针）。它与 T-07 / ADR-0016 的"A 类事后报告"
  同源但**不是同一落点**——它验证的是"**文档文字一致**"，**不验证**"提交路径 ⊆ 声明白名单"这一
  T-07 的核心判据。名称里的 `remote_write_authorization` 极易让人误以为它是 T-07 的提交域检查。
  **当前文档未把该用例当作 T-07 证据**（T-07 仍标"缺验证"），属"潜在误引"，提前登记。

---

### T-09 供应链投毒（依赖 / 权重 / 二进制 / 工具描述）

**当前状态**：`部分缓解`（§5："验证方式：机制未实现"；残余风险 3："`description_digest`…MCP 未接入
⇒ 防 rug-pull 的**能力当前为零（既无实现、也无被测对象）**"）。

**独立核实——文档陈述与仓库事实不符**：
- **实现存在**：`src/agent_sec_perf/tools/registry.py:137-154` `description_digest`（sha256，
  `name+description+parameters_schema` 规范化）；`:157-171` `_verify_digest`；在 `ToolRegistry.__init__`
  的 `:107` 调用 `_verify_digest(spec)`，**启动时 fail-secure 拒绝**（外部来源缺摘要 / 摘要不一致 ⇒
  `ToolRegistrationError`）。
- **单测存在**：`tests/unit/test_tools_registry.py` 有多条针对摘要校验的断言（均实测通过）：
  - `:174-176` `test_external_without_digest_is_rejected`：外部来源无摘要 ⇒ 拒绝；
  - `:180-182` `test_external_with_mismatched_digest_is_rejected`：摘要不一致 ⇒ 拒绝；
  - `:186-188` `test_external_with_matching_digest_is_accepted`：匹配 ⇒ 接受；
  - `:192-197` `test_digest_mismatch_blocks_the_whole_registration`：fail-secure（拒绝启动，不静默剔除）。
  另有 `:73-102` 对摘要规范化（键序不敏感、随内容变化）的断言。

**现有验证的真实内容（断言 / 未断言）**：`test_tools_registry.py` 断言了"**外部来源工具描述摘要
不一致 ⇒ 拒绝使用**"这一 fail-secure 路径（用 `FakeTool(source="mcp:demo")` 模拟外部来源）。
**未断言**：① 真实 MCP 来源端到端（当前无任何 MCP 接入，摘要检查在运行期**从未被真实外部工具触发**）；
② 权重 / 二进制摘要校验（`model/assets.py` 未实现）；③ 依赖 / Skill / CLI 的版本·commit·摘要锁定
（构建期 `V2~V4`，无载体）。

**缺口清单（仍成立的部分）**：权重/二进制摘要、Skill/CLI 锁定确为"无载体"；且 `description_digest`
缺少 `@pytest.mark.security` 的**对抗性**端到端用例（现有的是 `@pytest.mark.unit` 功能断言）。
但"**既无实现、也无被测对象**"这一表述对 `description_digest` 不成立。

**升级判据**：**无升级依据**。维持 `部分缓解`——理由不是"机制未实现"，而是：
① `description_digest` 仅在启动期对**已注册**工具生效，而当前运行期**无任何外部来源工具**，
  其防护面实质为零（文档"MCP 未接入 ⇒ 能力当前为零"这部分成立）；
② 权重/二进制/Skill/CLI 锁定确无载体；③ 缺 `@pytest.mark.security` 对抗性用例。
**但文档的"机制未实现 / 既无实现、也无被测对象"必须更正**（见 §2 乙-2）。

**本轮负向发现**：见 §2 乙-2（文档**低估**了 `description_digest` 的实现与单测——"声明失真"，方向相反）。

---

### T-10 网络出站默认拒绝失效

**当前状态**：`未缓解`（§5："执行机制未实现"）。

**独立核实**：`src/agent_sec_perf/harness/loop.py:604-614` 构造 `ExecutionContext` 时
`network_allowed=False` 为**硬编码常量**，且 `:611-613` 注释明写：
"⚠️ 本轮**恒为** False：出站白名单与云端客户端均未实现。`NETWORK_OUTBOUND` 已授予**不等于**
可以出站（`T-10` 保持未缓解）"。确认**无出站执行机制**。

**现有验证的真实内容**：**无**。§5 称"缺验证"属实。无"请求未发出"类行为断言。

**缺口清单**：① `test_outbound_request_without_capability_is_denied_and_audited`
（断言**请求未发出**而非发出后被拦 + 审计事件）；② `test_proxy_env_is_not_trusted_by_default`
（`HTTP_PROXY` 注入 ⇒ 请求不经该代理）；③ `V-n`/`V-o`（`urllib3` 代理行为、超时、响应体上限、
SSE 逐行解析的源码级核验）。

**升级判据**：**无升级依据**。维持 `未缓解`，文档陈述准确。

**本轮负向发现**：
- **甲（强，需防误引）**：`tests/security/test_harness_snew.py:190-207`
  `test_snew2_network_context_always_denied`（@pytest.mark.security）断言
  `ctx.network_allowed is False`。它**仅断言一个硬编码 Python 布尔常量**（`loop.py:613` 写死），
  **完全不断言**"出站请求被实际拦截/拒绝"——后者才是 T-10 的威胁本体。若有人把 S-new-2 当作
  "T-10 默认拒绝已验证"的证据，就是**典型的'看起来像证据、实际断言的是另一攻击面'**：它证明的是
  "上下文标志恒为 False"（fail-secure 的*标志位*），而非"出站流量被挡住"（*实际*攻击面）。
  真正的 T-10 判据（请求未发出）至今无载体、无用例。建议架构师在 T-10 条目显式排除 S-new-2
  作为证据（类比 T-02 对 `test_audit_landing_whitelist.py` 的防误引登记）。

---

### T-13 隔离机制静默失效 / 静默降级（fail-open）

**当前状态**：`部分缓解`（缓解=契约与规则已定，探针未实现）。

**独立核实**：`src/agent_sec_perf/security/` **不存在**（`list_dir` 显示仅有 `contracts/sandbox.py`
接口，无实现）；全仓 `grep "fail.open|probe|隔离探针"` 在 `tests/` 无 T-13 专用探针用例
（命中的 `test_isolation_mode_guard.py` 是 CLI `--isolation` 参数守卫，非探针矩阵）。
确认"探针未实现"属实。

**现有验证的真实内容**：**无行为级用例**。缓解为规则 S-1/S-2（契约层要求有效性自检探针 +
禁止静默降级），但**无可执行探针**。文档"缺验证"自述准确。

**缺口清单**：① `ADR-0007 §3.1` 探针矩阵写成可执行测试，**必须含"应当失败"的用例**
（负向 + 区分性：`--read-only` 下 `touch /x` 必失败、`--network=none` 下出站必失败、
`ulimit` 超限必失败、`cgroup` 类须配 `--shm-size` 对照）；② 当前即可执行的最小冒烟
（`unshare --net echo ok` / `bwrap ...` 预期失败）。

**升级判据**：**无升级依据**。维持 `部分缓解`，文档陈述准确。

**本轮负向发现**：未发现甲/乙。文档"探针未实现 / 无载体"与仓库一致。

---

## 2. 负向发现汇总（本轮强制要求的两类）

> 口径（取自任务书与 `docs/devlog/0019` §3.9 教训）：
> **甲** = 看起来像证据、实际断言的是另一攻击面（误引风险）；
> **乙** = 文档声称有证据/有载体、实际无载体或不可执行（假声明风险）。
> 本轮**额外发现**：本项目这批条目里更常见的是**反向失真**——文档**低估**了实现/验证
>（声称"无"，实际"有"）。反向失真同样是 `devlog 0019` 所警示的"声明失真"，且危害相同：
> 它会让后续判分、审批基于错误事实。故乙-1/乙-2 按"声明失真"登记，**方向为低估**。

### 甲（误引风险）

| # | 落点 | 看起来像… | 实际断言的是 | 严重度 | 当前是否被文档误用 |
| --- | --- | --- | --- | --- | --- |
| 甲-1 | T-10 | T-10"出站默认拒绝已验证" | `test_snew2_network_context_always_denied`（`test_harness_snew.py:190-207`）只断言 `network_allowed` **硬编码常量**为 False，不断言请求被实际拦截 | 强 | 未被误用（T-10 仍标"缺验证"），但必须**提前防误引** |
| 甲-2 | T-07 | T-07"提交域检查已验证" | `test_remote_write_authorization_consistency.py`（`tests/unit/`）只断言 A~F 分级**文档文字一致**，不验证提交路径 ⊆ 白名单 | 中 | 未被误用（T-07 仍标"缺验证"），潜在误引陷阱 |
| 甲-3 | T-04 | T-04"提示注入防御已验证" | `test_harness_snew.py:452-509`（S-new-6）只断言**角色位守卫**（数据不进 SYSTEM 角色），非注入行为 | 弱/相邻 | 未被误用，提前登记以防复发 |

### 乙（声明失真——方向为"低估"）

| # | 落点 | 文档声称 | 仓库事实 | 严重度 |
| --- | --- | --- | --- | --- |
| 乙-1 | T-03 | "`harness/` 尚未建立"、"验证方式：无"（`untrusted-input-and-agentic.md:41-46`、§4:195、§5:314） | `harness/` 已落地；指令-数据分离有实现（`prompts.py:137-185`、`context/__init__.py:116-122`、`loop.py:516-518`）；且存在 `@pytest.mark.security` 行为用例 S-new-6（`test_harness_snew.py:452-509`，被 `make test-security` 收集） | 强 |
| 乙-2 | T-09 | "`description_digest`…**既无实现、也无被测对象**"（`supply-chain-and-process.md:341-356` 残余风险 3）；§5 "机制未实现" | 实现存在（`registry.py:107/137-171`）；单测存在（`test_tools_registry.py:174-197` 等） | 强 |

> 乙-1 / 乙-2 的**正确结论不是"应升级状态"**，而是：**文档陈述与仓库不符，须由架构师更正**。
> 状态维持现状的理由依然成立（T-03 的 S-new-6 只覆盖指令-数据边界的一个落点；T-09 的摘要检查在运行期
> 因无外部工具而防护面为零、且缺权重/二进制/Skill/CLI 载体）。

---

## 3. 给领导的建议（逐条升降级与依据）

| 条目 | 现状 | 建议 | 依据 |
| --- | --- | --- | --- |
| T-03 | 未缓解 | **状态维持**；但**转交架构师更正**"harness 未建立 / 无验证"的失真陈述，并把 S-new-6 纳入条目"现有验证" | 乙-1 证明文档低估；S-new-6 仅覆盖边界一落点，不足以整体升级 |
| T-04 | 未缓解 | 维持；登记甲-3 防误引 | 无注入语料、无行为用例，文档准确 |
| T-05 | 部分缓解 | 维持 | 文档"缺验证"属实，靠人纪律 legitimately 记部分缓解 |
| T-06 | 部分缓解 | 维持 | E4 结构性检查分类准确，文档无误 |
| T-07 | 部分缓解 | 维持；登记甲-2 防误引 | 无机器检查，文档准确 |
| T-09 | 部分缓解 | **状态维持**；但**转交架构师更正**"既无实现、也无被测对象 / 机制未实现"的失真陈述 | 乙-2 证明实现+单测存在；运行期防护面为零+缺其他载体 ⇒ 不足以升级 |
| T-10 | 未缓解 | 维持；在条目显式排除 S-new-2 作为证据（仿 T-02 防误引登记） | 甲-1 证明 S-new-2 只断言常量，非实际出站拦截 |
| T-13 | 部分缓解 | 维持 | 探针未实现，文档准确 |

**未做任何状态/计数修改**（遵守团队领导指令与 `CODEBUDDY.md` §10.2 规则 9：完成判定只认仓库；
本报告仅作调研结论，落盘权属归架构师/记录员）。

---

## 4. 复核时发现的"绿着但没生效"类静默失败（优先上报）

本轮**未发现**典型的"绿着但没生效"（用例恒过却无真实保护）案例——已实测的用例均带变异探针
（S-new 系列、`test_bench_encapsulation.py` 的 E4 变异探针），且 41 个目标用例全绿可复现。
但需提示一处**口径一致性静默失效**：§5「缺行为层验证」清单把 T-03 列入"无验证"，而仓库里
`@pytest.mark.security` 的 S-new-6 **本应使其移出该清单**（按 §5 自身口径）。这说明 §5 的计数
**未随 S-new-6 入库而重算**——属于"清单与仓库事实漂移"，不是用例失效，但同样是
`devlog 0019` 警示的"绿着/数着但口径脱节"类问题，建议架构师在更正乙-1 时一并重算 §5 计数。
（注意：本报告**不改**该计数，仅提示。）
