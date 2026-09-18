# 安全扫描门禁：配置生效性核查（Bandit）

- **状态**：**待所有者裁决**（本文只登记问题、证据与候选修法；**架构师无门禁强度决策权**）
- **日期**：2026-09-18
- **登记人**：首席架构师
- **相关**：[`definition-of-done.md`](definition-of-done.md)（`make check` 的组成）、
  [`testing-strategy.md`](testing-strategy.md) §5、`SECURITY.md` §3/§6、
  [`ADR-0014`](../adr/0014-benchmark-automation.md) §2.9.1（豁免登记与 E1~E3 的**命令形态**）、
  [`doc-consistency-report.md`](doc-consistency-report.md)（同为「宣称已有 / 实际不生效」类问题）

> **为什么独立成文**：本问题不属于"测试策略"（静态扫描不是测试），也不适合塞进 DoD 清单
> （DoD 是**规范**，本文是**待裁决的现状 + 证据**）。把现状混进规范正是本项目反复吃亏的漂移模式
> （`ADR-0015 §5.5` 的历史残留、分支表三处口径）。本文就是一个可以长期承载
> "**门禁配置是否真的生效**"这一类问题的落点。

---

## 0. 一句话结论

**同一个 bandit、同一份 `pyproject.toml`，两个门禁对同一份代码的判定可以不同**——
`pre-commit` 的 bandit 传了 `-c pyproject.toml`（配置生效），
而 `make check`（→ CI）**没传**（配置不生效）。
另有 **`targets` 键在两条路径上都不生效**（bandit CLI 从不读取它），属**死配置**。

**方向上当前没有放过任何告警**（反倒是 `make check` 更严），所以这不是"已被攻破"，
而是 **`SECURITY.md` §3 `S-6`（安全相关检查不得被静默绕过）所指向的"口径不一致"**，
与一致性报告 `A-9`（`make test-security` 绿着但零覆盖）**同族**。

---

## 1. 问题：三处口径不一致

| 门禁 | 实际命令 | `[tool.bandit]`（`pyproject.toml:180-185`）是否生效 |
| --- | --- | --- |
| `make check` → `security-bandit`（`Makefile:88`） | `bandit -q -r src` | **否** |
| CI（`.cnb.yml:48` / `:84` / `:204` → `make check`） | 同上（继承 Makefile） | **否** |
| `pre-commit` 钩子（`.pre-commit-config.yaml:99`） | `bandit -c pyproject.toml -q -r src` | **是** |

`[tool.bandit]` 的声明（`pyproject.toml:180-185`）：

```text
[tool.bandit]
targets = ["src"]
exclude_dirs = [".venv", "build", "dist", "tests", "third_party", "vendor"]
skips = ["B101"]
```

⇒ **`skips = ["B101"]` 只在 `pre-commit` 生效**；`exclude_dirs` 同理。
⇒ **`targets = ["src"]` 在任何路径下都不生效**（见 §2.3）。

> 项目规则要求"`Makefile` / `.pre-commit-config.yaml` / `.cnb.yml` 三处门禁口径必须一致"。
> 本条即为该要求的一个**实际违反**：`Makefile` 与 pre-commit 口径不同，CI 继承 `Makefile` 因而与 pre-commit 不同。

---

## 2. 证据（可复现）

> **口径**：以下命令均在 `/workspace`（仓库根）执行，使用项目 `.venv` 内的
> **bandit 1.9.4**（与 `pyproject.toml` 的 `security` extra 及 `.pre-commit-config.yaml:96` 的 `rev` 一致）。
> **探针文件创建在仓库之外**（`/tmp`）——`src/` 不是架构师可写域，且仓库外目标才能把
> "配置发现"这一变量单独隔离出来。**探针文件与临时目录已清理**（见 §2.4）。

### 2.1 代码级证据：自动发现只认 `.bandit`

```text
$ ls -la .bandit
ls: cannot access '.bandit': No such file or directory          # 仓库内不存在 .bandit

$ grep -n "fnmatch|\.bandit" bandit/cli/main.py
7:import fnmatch
57:                for filename in fnmatch.filter(filenames, ".bandit"):    # ← 自动发现只匹配 .bandit
70:            LOG.info("Found project level .bandit file: %s", bandit_files[0])

$ sed -n '26,60p' bandit/core/config.py
26:    def __init__(self, config_file=None):
35:        self.config_file = config_file
38:        if config_file:                                    # ← 没有 config_file 就不读任何配置文件
46:            if config_file.endswith(".toml"):              # ← toml 只在显式 -c 时解析
56:                        tomllib.load(f).get("tool", {}).get("bandit", {})
```

**读法**：bandit **不会**因为"仓库根有 `pyproject.toml`"就去读 `[tool.bandit]`；
两条路径的差别**完全来自命令行是否传 `-c`**。

### 2.2 差异化探针 A：`skips` 是否生效（目标在仓库外，隔离"配置发现"这一个变量）

```text
$ printf 'def f() -> bool:\n    assert True\n    return True\n' > /tmp/tm-probe2/a.py

$ uv run bandit -q /tmp/tm-probe2/a.py                     # 不带 -c（= make check 的形态）
>> Issue: [B101:assert_used] Use of assert detected. ...
   Location: /tmp/tm-probe2/a.py:2:4
A1_rc=1                                                    # ← B101 被报出

$ uv run bandit -q -c pyproject.toml /tmp/tm-probe2/a.py   # 带 -c（= pre-commit 的形态）
A2_rc=0                                                    # ← 无任何输出，B101 被跳过
```

⇒ **同一份代码、同一个 bandit**：不带 `-c` **红**，带 `-c` **绿**。这就是"两个门禁判定不同"的直接演示。

### 2.3 差异化探针 B：`exclude_dirs` 与 `targets`

```text
$ uv run bandit -q -r tests                                # 不带 -c
B1_rc=1
    Total lines of code: 1244
    Total potential issues skipped due to specifically being disabled (e.g., #nosec BXXX): 0
        Low: 128
        Medium: 5
    Files skipped (0)                                      # ← exclude_dirs 里的 "tests" 未生效
（其中 B101:assert_used 计 127 处）

$ uv run bandit -q -c pyproject.toml -r tests               # 带 -c
B2_rc=0
B2_输出字节数=0                                             # ← exclude_dirs + skips 同时生效

$ uv run bandit -q                                         # 无位置目标 · 不带 -c
C1_rc=2  （打印用法）
$ uv run bandit -q -c pyproject.toml                       # 无位置目标 · 带 -c
C2_rc=2  （打印用法）                                       # ← targets=["src"] 即使带 -c 也不生效
```

**`targets` 不生效的代码依据**：`bandit/cli/main.py` 中 `b_conf.get_option(...)` 只用于
`profiles` / `tests` / `skips` / `log_format`（`:114` / `:120` / `:121` / `:611`），
**全文没有 `get_option("targets")`**；而 `:606` 是 `if not args.targets:` ⇒ 无位置参数时
直接报用法并 `exit 2`。⇒ **`targets` 键与 `-c` 无关，永远不生效。**

### 2.4 证据的边界（如实标注，勿当已证事实）

| # | 未验证 / 需注意 | 说明 |
| --- | --- | --- |
| 1 | **`pre-commit` 侧未实际运行该 hook** | 其依据是**逐字读取** `.pre-commit-config.yaml:99` 的 `args` 数组，**加上** A1/A2 已实测的 `-c` 效果。运行验证命令为 `pre-commit run bandit --all-files`，但**本轮未执行**：pre-commit 会 stash 未暂存改动，而当前**共享索引里有他人在途的暂存内容**（见 `docs/design/threat-model/supply-chain-and-process.md` 的 `T-07`）——为避免扰动共享工作树，留作复核项。 |
| 2 | **`B1` 的行数是快照** | `tests/` 正在被并行扩展（本轮两次测量分别为 `1119` 与 `1244` 行），故**绝对值会漂移**；结论用的是**带 / 不带 `-c` 的差异**，与行数无关。 |
| 3 | **配置项的意图属推断** | `exclude_dirs` 含 `tests`、`skips` 含 `B101`，且 ruff 侧对应的 `S101` 在 `pyproject.toml:82` 的注释是"允许在测试中使用 assert" ⇒ **推断**其意图同为"为 `tests/` 的 `assert` 留空间"。但**没有任何文档写明该意图**，故这是**推断而非事实**，需所有者确认（见 §5）。 |
| 4 | **未评估其它工具的配置生效性** | 本文只核了 bandit。`ruff` / `mypy` 的配置走各自的原生发现机制（`ruff.toml`/`pyproject.toml`、`--config-file=pyproject.toml` 已显式传入），**不在本次范围**；但"配置是否真的生效"这一类核查应推广（见 §6）。 |

---

## 3. 影响评估

**首先说方向**：不生效的两项是 `skips`（豁免）与 `exclude_dirs`（排除），
生效的是 bandit 的默认检查集 ⇒ **`make check`/CI 实际比配置声明的更严**。
⇒ **当前没有任何告警是因为这个缺陷被放过的。** 这不是"漏洞"，是"口径不一致"。

但产生三个**真实**问题：

1. **"本地绿、CI 红"这一类难以定位的故障已被埋好。**
   `pre-commit`（本地提交）**放行** `src/` 里的 `assert`（B101 被跳过），
   `make check`/CI **会红**。当前 `src/` 恰好没有 `assert`，所以分歧**未暴露**；
   一旦有人（或某个自动修复环）在 `src/` 写下不变量断言，就会撞上
   **"钩子绿、`make check` 红"** ——这正是 `.pre-commit-config.yaml:13-15` 自己警告过的失效模式
   （当时是 ruff `0.6.9` vs `0.16.7`，这次是 bandit 的 `-c` 口径）。
   这类故障的定位成本极高：**同一命令、同一配置，结果不同**。
2. **`targets = ["src"]` 是死配置，且在关键时刻不兜底。**
   若有人删掉 `Makefile` 的 `-r src`（以为配置里有 `targets` 兜底），
   bandit 会直接 `rc=2` 报用法 —— 不是"按配置扫描 `src`"。**期望与行为的偏差是静默的**。
3. **与 `A-9` 同族：`SECURITY.md` §3 `S-6`（检查不得被静默绕过）的实质是"口径必须可信"。**
   `pyproject.toml:174-185` 的注释与 `[tool.bandit]` 段共同把读者引向"配置已生效"的假设；
   而读者据此做的任何推理（例如"B101 已被全局跳过，所以我不必在意 `assert`"）**只对一半路径成立**。

---

## 4. 候选修法（两个）与代价

> **前提**：三个候选都要先回答同一个问题——**`skips = ["B101"]` 到底要不要？**
> 它在两条路径上的净效果是"**放宽**"。本项目的安全基线是**最高优先级**，
> 因此"**为了配置生效而放宽门禁**"不是默认选择（见 §5 的推荐与需确认项）。

### 候选 A：给 `make check` 的 bandit 也加 `-c pyproject.toml`（以 pre-commit 为准）

- **改动**：`Makefile:88` → `bandit -c pyproject.toml -q -r src`。
- **效果**：三处口径一致；`skips`/`exclude_dirs` 在所有路径生效；`pyproject.toml` 成为唯一真源。
- **代价 / 风险**：
  1. **净效果是把 `make check`/CI 对 `src/` 的 `B101` 检查放掉**——即**门禁强度下降**，
     需所有者**明确同意**；
  2. `ADR-0014 §2.9.1` 的 `E1`~`E3` 写的是 `uv run bandit -r src`（同为不带 `-c` 的形态）
     ⇒ **必须同步**改，否则"登记与实测"再次分叉（本项目已吃过多处副本漂移的亏）；
  3. `targets` 键仍无效 ⇒ 建议**同时删除**该键（或加注释说明目标由命令行决定）。

### 候选 B：从 `pre-commit` 去掉 `-c pyproject.toml`（以 `make check` 为准）—— **推荐先做**

- **改动**：`.pre-commit-config.yaml:99` → `["-q", "-r", "src"]`。
- **效果**：三处口径一致，且**不放宽任何规则**（保持当前更严的检查）。
- **代价 / 风险**：
  1. `[tool.bandit]` 整段变成**完全不生效的死配置** ⇒ **必须**同步删除该段，
     或在原处加注"本段不被任何门禁读取（bandit 只自动发现 `.bandit`）"。
     只去掉 `-c` 而不处理 `pyproject.toml`，就是**留下本问题本身**（"写着但没生效"）；
  2. 若将来确实需要跳过某条规则，**没有可用机制**（需重新引入 `.bandit`，或另开 ADR 记录豁免）；
  3. `exclude_dirs` 一并失效——当前无害（目标只有 `src`），但若将来目标扩大需重新评估。

### 候选 C（备选，一次解决一致性问题）：把配置迁到 `.bandit`（bandit 自动发现）

- **效果**：两条路径**自动一致**，无需在任一处写 `-c`；`skips`/`exclude_dirs` 生效。
- **代价 / 风险**：
  1. **新增仓库根文件**，与"配置集中在 `pyproject.toml`"的取向分叉；
     且**必须同时**处理 `pyproject.toml` 的 `[tool.bandit]`（删除或标注），否则新增一个"两处表述"的漂移源；
  2. `.bandit` 还能承载**命令行参数**（`bandit/cli/main.py:457-500` 的 ini 语义）⇒ 隐蔽行为面变大；
  3. **同样会启用 `skips = ["B101"]`** ⇒ 与候选 A 相同的强度后果；
  4. `targets` 键在 `.bandit` 里**同样无效**（该键与位置无关）。

---

## 5. 推荐与需所有者确认的点

**推荐：先做候选 B**（保持检查强度、一次消除口径不一致），并按 §4-B 的代价 1 处理 `pyproject.toml`。
理由：本项目安全基线为最高优先级，在**没有明确理由要跳过 `B101`** 时，
不应为了"让配置生效"而放宽门禁；而当前 `skips = ["B101"]` 的**唯一可解释意图是为 `tests/` 的 `assert` 留空间**，
而 bandit 的目标**只有 `src/`**——**该意图在当前目标下不成立**（属 §2.4 第 3 条的推断，需确认）。

**需所有者确认（门禁强度变更，须所有者拍板）**：

| # | 待确认 | 影响 |
| --- | --- | --- |
| Q1 | `skips = ["B101"]` 是否**有意**要？当初的理由是什么？ | 决定走候选 A（接受放宽）还是 B（不放宽） |
| Q2 | 若选 B，是否同意**删除** `pyproject.toml` 的 `[tool.bandit]` 整段（或改为"不生效"注释）？ | `pyproject.toml` 不属架构师域 |
| Q3 | 是否同意把"**配置生效性**"作为一条**长期核查项**（先 bandit，后 ruff/mypy）？ | 见 §6 |

> **不在架构师域、故未改动**：`Makefile`、`.pre-commit-config.yaml`、`.cnb.yml`、`pyproject.toml`。
> 本文只提供结论与证据，供裁决。

### 5.1 处置与验证（2026-09-18 所有者裁决后 · **只增**）

> **阅读提示**：本文首部「状态：待所有者裁决」与 §4 的"候选"、§5 的"推荐"**保留原样、不改写**
> （属历史快照）；本节记录**裁决结果与验证**，是 §5 的**续写**——首部「待裁决」这一状态即由本节取代。

**裁决**：采纳**候选 B**（`pre-commit` 去掉 `-c`，以 `make check` 的"不读配置"为唯一口径），
且**不保留** `skips = ["B101"]`——即**不为"让配置生效"而放宽门禁**（§5 的方向）。

**处置（提交 `9804a0a`，所有者 2026-09-18 批准）**：

- `.pre-commit-config.yaml` 的 bandit 钩子去掉 `-c pyproject.toml` ⇒ 两处门禁统一为**不读配置文件**；
- 删除 `pyproject.toml` 中已失效的 `[tool.bandit]` 段（`targets` / `exclude_dirs` / `skips`），
  **保留**其中 B603/B607 的立场说明与豁免政策文字（那是安全决策记录，不是死配置）；
- **净效果不放宽任何规则**——反而因不再跳过 `B101` 而**更严**。

**新的不变式（硬性）**：

> **Bandit 配置不得存在，除非它被 `make check` 与 `pre-commit` __两处同时引用__；
> 只被一处引用 ＝ 两套口径。**

理由：本问题的**唯一根因**不是"配错了某一条"，而是**同一份配置被两条路径以不同方式读取**
（§1~§3）⇒ 声明与生效分叉。上式把"口径必须一致"变成**可判定的条件**：仓库内一旦出现 bandit
配置（`[tool.bandit]` 或 `.bandit`），**必须**在 `Makefile` 与 `.pre-commit-config.yaml`
**两处都显式引用**它；只在一处引用即违反本不变式。

**§5 的 Q1~Q3 处置**：Q1 = 不需要 `skips=["B101"]`（该意图在"目标只有 `src/`"时不成立，§5）；
Q2 = 同意删除 `[tool.bandit]` 整段（保留政策文字）；Q3 = 记为**后续核查项**，本次**未**推广到 ruff/mypy。

**验证方式（可执行；下列第 1、2 条本文写作时均已实跑）**：

1. `make check` 全绿（含 `bandit -q -r src`）——本次实跑：**98 passed** + bandit / pip-audit 无发现；
2. `uv run pre-commit run bandit --all-files` → **Passed**（证明去掉 `-c` 后钩子不会变红）；
3. **不变式核查**：`grep -n "tool.bandit" pyproject.toml` 只应命中**注释**（说明该段已删），
   且 `ls .bandit` → `No such file`；将来若任一处重新引入 bandit 配置，必须按上式**两处同时引用**。

---

## 6. 复核方式（改完后如何确认修好）

1. **一致性断言（改完后必须两者同结果）**：

   ```text
   # 期望：两条命令的“是否报 B101”一致（当前不一致）
   uv run bandit -q /tmp/<probe>/a.py                    # 0 或 1
   uv run bandit -q -c pyproject.toml /tmp/<probe>/a.py  # 与上一行一致 ⇒ 口径已统一
   ```

   （探针文件为 `def f() -> bool:` + `assert True`；探针目录须在**仓库之外**，
   否则会被 `exclude_dirs` 干扰。）

2. **`targets` 死配置的处理确认**：`[tool.bandit]` 中不应再有 `targets` 键
   （或已在注释中写明"不生效"）；`bandit -q`（无位置目标）报用法错误属**预期**，不是缺陷。

3. **登记口径同步**：`ADR-0014 §2.9.1` 的 `E1`~`E3` 命令形态与本文件 §1 的表**必须一致**；
   改完跑一次 `E1`/`E2`/`E3`（判据见该 ADR）并在其修订记录中留痕。

4. **推广项（Q3 若通过）**：`ruff` 的 `allowed-confusables`、`per-file-ignores`、
   `mypy` 的 `overrides` 等**声明式配置**同样值得一次"生效性核查"——
   本次已证明"写在配置里"不等于"被门禁读取"。

---

## 7. 变更记录

- **2026-09-18（初稿）**：建立本文。问题、代码级证据、三组差异化探针、
  影响评估、两个候选修法与推荐、复核方式。
  证据来源：`bandit 1.9.4` 实测（命令与原始输出见 §2）+ 逐字读取
  `Makefile:88` / `.pre-commit-config.yaml:99` / `.cnb.yml:48,84,204` / `pyproject.toml:180-185`。
  探针文件与临时目录已清理并复核不存在。
- **2026-09-18（处置与验证）**：**新增 §5.1** 记录所有者裁决（采纳候选 B）与处置（`9804a0a`）、
  **新不变式**（"配置不得存在，除非被 `make check` 与 `pre-commit` 两处同时引用"）与验证方式
  （`make check` 98 passed；`uv run pre-commit run bandit --all-files` → Passed）。
  **§0~§6 的原有问题、证据与判断一律不改写**（只增）。
