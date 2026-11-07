| §5 |# 课程报告（软件工程）
| §5 |
| §5 |> 这是**交给课程的那份报告**：`report.md` 是正文源（可评审、可 diff）。
| §5 |> 本目录只放报告本身与它的说明，不改动 `docs/` 其它文档，也不产生新事实。
| §5 |
| §5 |## 1. 与既有课程交付物的分工
| §5 |
| 文件 | 体裁 | 读者 | 状态 |
| :--- | :--- | :--- | :--- |
| [`../course-deliverables/experiment-1-requirements/`](../course-deliverables/experiment-1-requirements/README.md) | 实验 1：需求分析文档包（10 个 md，含 AI 协作记录、人工核实、证据附录） | 任课教师、评审 | 已成册 v1.3（2026-09-28） |
| [`../course-deliverables/experiment-2-design/软件设计文档.md`](../course-deliverables/experiment-2-design/软件设计文档.md) | 实验 2：软件设计文档（12 项设计要素 + 49 条需求追溯矩阵） | 任课教师、评审 | v1.3（2026-10-12） |
| **本目录**（`report.md`） | 期末报告：把需求、设计、框架、实现与演示串成一篇 | 任课教师 | 初稿（2026-11-02） |
| §5 |
| §5 |三者都是**视图，不产生新事实**：数字能在 `docs/requirements/`、`docs/design/`、`docs/engineering/`、`docs/devlog/` 或一次实跑里找到出处。冲突时以项目内部文档与代码为准，并在本文件 §6 登记。
| §5 |
| §5 |## 2. 章节结构
| §5 |
| §5 |八章，对应软件工程课程的六个要素（需求分析、设计分析、项目框架、实现与验证、展示 demo、过程与协作）：
| §5 |
| 章 | 内容 | 主要来源 |
| :--- | :--- | :--- |
| 1 | 项目与环境说明 | `docs/requirements/srs.md` §1/§6.3；`docs/design/architecture.md` §1.2 |
| 2 | 需求分析 | `srs.md` §2~§10；实验 1 需求分析文档 |
| 3 | 系统设计 | `architecture.md`；`docs/design/interfaces/`；`threat-model/README.md`；`ADR-0015`/`ADR-0034` |
| 4 | 工程框架与实现现状 | `src/`；`Makefile`；`tests/`；`ADR-0019`；`devlog` |
| 5 | 能力包内核 | `ADR-0034`；`docs/engineering/capability-pack-manual.md` §2~§8 |
| 6 | 展示 demo | `docs/design/showcase-incident-triage.md`；`docs/engineering/demo-showcase.md`；`devlog 0031`/`0032` |
| 7 | 问题与挑战 | `devlog 0032` §5/§6；`notes/evaluation-pitfalls.md`；威胁模型修订记录 |
| 8 | 角色分工、课程落点与总结 | `CODEBUDDY.md` §10；`docs/engineering/git-workflow.md` §4；本报告各章 |
| §5 |
| §5 |### 2.1 模板与偏离
| §5 |
| §5 |课程**没有**在仓库内提供期末报告模板（实验 1/2 的任务书只给了交付项，见实验 1 的 README §1）。因此本报告沿用参考项目 `cnb.cool/Mybase_Le0n3rd/BigData` 的 `docs/course-report` 分支的八章骨架（2026-11-02 访问），按软件工程的要素重排。逐条偏离登记如下，避免"看起来像模板"：
| §5 |
| # | 做法 | 理由 |
| :--- | :--- | :--- |
| 1 | 题目写在 front matter，不占章节号；八个部分各自成章 `# 1` ~ `# 8` | 题目是文档标题，不是内容；占一个章节号会让目录里出现一条没有内容的顶格条目 |
| 2 | 表题在表格**上方**，图题在图**下方** | 中文排版惯例 |
| 3 | 表、图编号按章走（表 1-1 / 图 6-1） | 章变了，`1-x` 的连续编号不再成立 |
| 4 | 编号与标题之间用全角空格（`# 1　项目与环境说明`） | GB/T 7713.1—2006 6.3.2 要求空一个字宽 |
| 5 | 图用 Mermaid 内联，不引入图片文件 | 可 diff、可评审、零新增依赖；结构自检见 §4 |
| 6 | 正文中文，代码、路径、命令、判据原文保持英文 | 项目语言约定（`CODEBUDDY.md` §4） |
| §5 |
| §5 |### 2.2 写作口径（本报告遵守的规则）
| §5 |
| §5 |写之前参考了三份公开材料，不复制原文，只取规则：
| §5 |
| 来源 | 取用的部分 | 访问日期 |
| :--- | :--- | :--- |
| Humanizer-zh（`op7418/Humanizer-zh`，GitHub） | 31 种 AI 写作模式的**检查线索**（铺垫代替陈述、公式化节奏、意义拔高、装饰性排版等） | 2026-11-02 |
| Wikipedia: Signs of AI writing | 上述清单的上游依据（本项目未直接抓取，按 Humanizer-zh 的转述使用） | 2026-11-02 |
| 阮一峰《中文技术文档的写作规范》（`ruanyf/document-style-guide`） | 中英文混排空格、句长、标点、英文处理 | 2026-11-02 |
| §5 |
| §5 |落到本报告上的六条：先说事、不写"汇报如下"式预告；一个信息点不用两个分句；加粗只给要跳读的数；正文里不用 `⇒`/`→` 当节奏器；删掉删了也不影响意思的句子；**风格让位于事实**——不把"可能"写成"确定"，不把"未测"写成"通过"。
| §5 |
| §5 |本项目**没有**把上述材料做成 Skill（`.codebuddy/skills/` 下没有写作类 Skill）。理由：Skill 文本进入指令面，新增需登记来源、版本与摘要；而本报告是一次性交付物，规则写在本文档里足够。若后续要复用，应新建 Skill 并按其来源与版本登记（见 §7 待办）。
| §5 |
| §5 |## 3. 证据索引（报告里的数字去哪复核）
| §5 |
| 报告位置 | 内容 | 出处 |
| :--- | :--- | :--- |
| §1 表 1-1 | 8 vCPU / 16 GiB / 无 GPU / 无 KVM | `docs/research/2026-09-14-cnb-quota-and-hardware.md`；`architecture.md` §1.2 |
| §1 | 约 3.4 token/s、单次补全可超 450 s | `README.md` §5.1 的参数表 |
| §2.2 | 49 条 / 9 组 | `srs.md` §7（逐条可数） |
| §2.2 | `REQ-PERF-04` 的四份产物 | `srs.md` §7 该行；`Makefile` 的 `bench-round` |
| §3.1 | `R1`~`R5` 与 9 条机器检查 | `ADR-0015` §5.1/§7.1；`tests/unit/test_architecture_layers.py` |
| §3.2 | 策略失败收敛为拒绝、审计失败冒泡 | `docs/design/interfaces/policy.md` §2.5；`architecture.md` §2.4/§5.3 |
| §3.3 表 3-1 | 15 条威胁、1/12/2 | `docs/design/threat-model/README.md` §4.1 |
| §3.4 | `pack/` 的候选与采纳 | `ADR-0034` §3.1/§4/§5 |
| §4.1 表 4-1 | 模块与状态 | `architecture.md` §4；`ls src/agent_sec_perf/` |
| §4.2 表 4-2 | 53 / 18 / 1 / 1076 | 三条 `ls ... \| wc -l`；`uv run pytest -q --collect-only` |
| §4.2 | 1065 passed / 1 skipped | `devlog 0032` §3.8（2026-11-02） |
| §4.3 | 32 篇 ADR、32 篇日志、6 份契约 | `docs/adr/`、`docs/devlog/`、`docs/design/interfaces/` |
| §5.3 | 自证覆盖 4 条、未覆盖 7 条 | `capability-pack-manual.md` §7.4 |
| §5.4 表 5-3 | `F1`~`F9` | `capability-pack-manual.md` §8；`pack plan --json` |
| §6 表 6-1 | 案例现场 | `examples/showcase/incident-01/scene/`；`case.toml` |
| §6.3 表 6-2 | 11 条机检 | `docs/design/showcase-incident-triage.md` §4.3；`tests/unit/test_cli_demo.py` |
| §6.4 表 6-3 | 0.6B / 1.7B / 2B / 4B | `devlog 0032` §4.1/§4.2 |
| §6.4 | 6 次 11/11、249~258 s | `devlog 0032` §3.7 |
| §6.4 | 3 份样本、`3/3`、维度判定 | `devlog 0032` §3.8；`pack review` 的报告字段见手册 §7.5 |
| §6.5 表 6-4 | L1、3/8 维度 | `docs/design/showcase-incident-triage.md` §5.1；`agent-sec-perf doctor --json` |
| §7.2 | 授权 ≠ 暴露 | `docs/design/showcase-incident-triage.md` §4.6 |
| §7.3 | 三次"取消动作" | 同上 §4.2 |
| §5 |
| §5 |## 4. 自检
| §5 |
| §5 |```bash
| §5 |# 图表结构自检（不渲染；检查括号、subgraph/end 配平、占位符等）
| §5 |python3 .codebuddy/skills/diagram-authoring/scripts/mermaid_check.py docs/course-report
| §5 |
| §5 |# 报告里引用的数字，按 §3 的出处逐条复跑；最小集：
| §5 |ls tests/unit/test_*.py | wc -l
| §5 |ls tests/security/test_*.py | wc -l
| §5 |uv run pytest -q --collect-only | tail -1
| §5 |```
| §5 |
| §5 |`make check` 不覆盖本目录（没有代码改动），但提交前仍应跑一次，确认没有碰到别人的工作树。
| §5 |
| §5 |## 5. 格式与打印
| §5 |
| §5 |- **正文源是 `report.md`**（518 行）；**交付件是 `report.docx`**（入库），由 `build.py` 生成，**不要手改**。
| §5 |- **PDF 是本地核对产物**：`report.pdf` 由 LibreOffice 渲染，已 gitignore，**不入库**。
| §5 |- 构建环境另需 `libreoffice-writer`、`fonts-noto-cjk`、`poppler-utils`（仅本机，不进镜像）。
| §5 |- 构建：`uv pip install -r docs/course-report/requirements-doc.txt`，再跑 `uv run python docs/course-report/build.py`（`--no-pdf` 只出 docx）。
| §5 |- 实测页数：封面 1 + 目录 1 + 正文 14 = **16 页**（2026-11-02，LibreOffice 渲染）。目录页码是两遍渲染量出来的，页码域随分节重置；中文字体用 Noto CJK 替代宋体/黑体，**页数以 Word/WPS 为准**。
| §5 |- **若课程限页**，按下面的顺序删，不要按章删（每章都有对方没有的信息）：
| §5 |  ① 第 4 章的模块表压成一段；② 第 5 章的表 5-2（命令与产物）删掉，只留四步链路；
| §5 |  ③ 第 7 章的表 7-1 与 §7.3 的表各留三行；④ 第 8 章的表 8-2 删掉（正文已覆盖）。
| §5 |  **不要删**：§1 的环境约束、§6.6 的测量口径、§7.4 的静默失败——这三处是报告可信度的支点。
| §5 |
| §5 |## 6. 与仓库其它文档的关系
| §5 |
| §5 |- **只读**：本目录不修改 `docs/requirements/`、`docs/design/`、`docs/adr/`、`docs/devlog/`。
| §5 |- **不回写**：报告里发现的文档矛盾，登记到本文 §7，由对应文档的责任人处理。
| §5 |- **分支基线**：本分支 `docs/course-report` 从 `feat/11-capability-pack-kernel` 的 `f4938d8` 拉出。选这个基线而不是 `develop`，是因为报告第 5、6 章的材料（`pack/`、`ADR-0034`、`devlog 0032`、`capability-pack-manual.md`）**尚未合入 `develop`**；基于 `develop` 写会得到一堆失效链接。代价与处置写在 §7。
| §5 |
| §5 |## 7. 待办与已知边界
| §5 |
| §5 |1. **分支基线耦合**：`feat/11` 合入 `develop` 后，本分支应改为从 `develop` 重建（或直接合并），否则报告与代码树会对不上。
| §5 |2. **封面待填**：`report.docx` 的五条横线（专业班级/学号/姓名/指导教师/报告日期）由本人手填，不要写进 `build.py`。
| §5 |3. **Word 分页可能与渲染不同**：docx 交给 Word 打开时若提示更新域，按提示更新后以 Word 页码为准。
| §5 |4. **写作 Skill 未立项**：见 §2.2；若要复用，需按 Skill 登记规则补来源与版本。
| §5 |5. **报告里的数字会漂移**：§3 的出处都写了文件与命令，但用例数、模块数、威胁分布会随开发变化。打印前按 §4 复跑一遍，不要沿用旧值。
| §5 |
| §5 |## 8. 不得放大的结论
| §5 |
| §5 |- 隔离是 **L1**：文件系统与网络没有内核级隔离，只有应用级拦截（白名单 + 默认拒绝 + 审计）。
| §5 |- 2B / 4B 通过的是**一条案例**，不是通用 agent 能力；量化不同，墙钟不可横向比较。
| §5 |- 演示墙钟是**观测值**，不用于性能结论；性能基线由 `bench/` 轮次承担。
| §5 |- 需求基线为 **v0.1.3**，49 条编号沿用；`T-04`、`T-10` 在威胁模型中保持未缓解。

## 9. 课程模板版报告（2026-11-07，本次提交件）

课程提供了模板 `《软件工程》项目报告撰写内容及规范化参考样本.docx`（生成时从仓库根目录复制，**原文件未改动**；若需重建 docx，须把模板放回 `build_template.py` 的 `TEMPLATE` 路径）。本目录新增四件产物：

| 文件 | 作用 |
| :--- | :--- |
| `report-template.md` | 模板版正文源（六章 + 附录，可 diff、可评审） |
| `make_figures.py` | 生成 12 张图；结构图用 Graphviz，终端图由真实命令输出渲染 |
| `build_template.py` | 复制模板 docx 并在副本上填充，生成 docx/pdf |
| `make_deliverables.py` | 汇总电子材料并打包 zip |

构建：`uv run python docs/course-report/make_figures.py` → `uv build` → `uv run python docs/course-report/build_template.py` → `uv run python docs/course-report/make_deliverables.py`。构建期依赖：`python-docx`、Pillow、Graphviz `dot`、LibreOffice、Noto CJK、poppler-utils（**仅本机，不进 `pyproject.toml`**）。

版式按模板"任务书"执行：章标题黑体小 2 加粗居中、二级黑体 4 号、三级黑体小 4、正文宋体小 4 且 1.5 倍行距、表题在表上方、图题在图下方、章间分页；封面、任务书、页眉与分节页码沿用模板本身。实测页数：封面 1 + 任务书 1 + 目录 1 + 正文 33 = **36 页**（目录页码两遍渲染回填，复跑核对无偏移；**Word/WPS 打开后请以实际分页为准**）。

**为什么 docx 与 PDF 会分叉**：模板指定仿宋/黑体/宋体_GB2312/Times New Roman 等 Windows 字体，本机由 Noto CJK 等替代；且模板带 `w:docGrid`（每行 312 twips），LibreOffice 会把 1.5 倍行距对齐到网格，行高明显大于 Word，任务书因此溢出到第 2 页。已对"任务书+目录"这一节取消文档网格对齐（正文节不动，故目录页码不受影响），任务书回到 1 页。打印以核对过的 **PDF** 为准：第 1~3 页单面，第 4~36 页双面（长边翻转）。

封面个人信息栏（专业班级/学号/姓名/指导教师）在仓库版本中留空，打印版由所有者手填；PDF 与电子材料压缩包不入库。信息栏（课程名称～报告日期共 7 行）改为**固定宽度表格**：整块水平居中、标题列左对齐、内容列居中且底边框等长。原先靠空格加下划线字符凑对齐，在 Word 里看着正常、LibreOffice 换字体后即错位，故改为表格；`题目` 行按模板原样保留。旧八章版 `report.md` / `report.docx` 保留为写作参考，**不是**本次提交件。电子材料压缩包已校验不含 `.git`、虚拟环境与缓存文件。
