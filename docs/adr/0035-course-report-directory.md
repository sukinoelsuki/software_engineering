# 课程报告目录：`docs/course-report/` 与"报告不复述事实"的边界

- **状态**：已接受（2026-11-02，所有者指令：单开一个文档分支撰写软件工程课程报告）
- **编号说明**：接续现有最大编号 `0034`；`0029`~`0031` 历史上未成文，不复用。
- **关联**：[`ADR-0033`](0033-course-deliverables-directory.md)（课程交付物目录）、
  [`ADR-0034`](0034-capability-pack-kernel.md)（报告第 5、6 章的材料）、
  [`docs/README.md`](../README.md) 的文档地图。

## 1. 背景

软件工程课程要求提交一份期末报告。项目已有两代课程交付物（实验 1 需求分析、实验 2 软件设计），
放在 [`docs/course-deliverables/`](../course-deliverables/README.md)；但那是**按实验任务书**组织的
单要素文档，不是把需求、设计、框架、实现与演示串成一篇的期末报告。

同时，报告要引用 `pack/`（能力包内核）的实测数据，而该模块目前只在
`feat/11-capability-pack-kernel` 分支上，尚未合入 `develop`。

## 2. 驱动因素

| # | 因素 | 说明 |
| :--- | :--- | :--- |
| 1 | 体裁不同 | 实验交付物是"按评分项逐条填"，期末报告是"讲清一个项目"；混在一个目录里会让读者分不清该读哪份 |
| 2 | 读者不同 | 实验交付物给评审逐项核对，报告给任课教师通读；前者的证据附录、AI 协作记录不必重复进正文 |
| 3 | 只读约束 | 报告是**视图**，数字必须能指回 `docs/` 与代码；若混入 `docs/design/` 会制造第二份真源 |
| 4 | 目录必须有理由 | 项目规则要求"新目录的建立必须先在 ADR 中说明理由"（`CODEBUDDY.md` §1 的目录约定） |

## 3. 候选方案

| 方案 | 内容 | 结论 |
| :--- | :--- | :--- |
| A | 放进 `docs/course-deliverables/` | ❌ 与两代实验交付物混放；该目录的维护节奏是"按实验节点成册"，报告是另一条线 |
| B | 放进 `docs/research/` 或 `docs/design/` | ❌ 报告不是调研结论，也不是设计；且会与权威源并列，制造"哪份为准"的歧义 |
| C | 放仓库根目录 | ❌ 与工程文件混排；根目录已有明确用途 |
| **D（采纳）** | 新建 `docs/course-report/`（`README.md` + `report.md`） | ✅ 与 `course-deliverables/` 平级、互不修改；报告只读其它文档；目录名英文、文档可中文（同 ADR-0033） |

## 4. 决策

1. 新建 **`docs/course-report/`**，放 `report.md`（正文源）与 `README.md`（分工、章节表、
   证据索引、格式与待办）；
2. **报告不复述事实**：每个数字给出文件、命令或一次实跑；冲突时以 `docs/requirements/`、
   `docs/design/`、`docs/adr/`、`docs/devlog/` 与 `src/` 为准；
3. **不回写**：报告里发现的文档矛盾登记在 `README.md` §7，由对应文档的责任人处理；
4. **分支基线**：`docs/course-report` 从 `feat/11-capability-pack-kernel` 的 `f4938d8` 拉出
   （第 5、6 章依赖尚未合入 `develop` 的 `pack/` 与 `ADR-0034`）；
5. **不新增依赖**：报告只用 Markdown 与 Mermaid，不引入 docx 生成脚本（`python-docx` 属 F 类事项）。

## 5. 后果（含负面）

**正面**：报告与实验交付物各归其位；数字有索引可查；写作口径（参考的三份公开材料）留档，
下次写长文档不必重新找。

**负面 / 已知局限**：

1. **分支耦合**：`feat/11` 合入 `develop` 前，本分支不能单独合入 `develop`；合并后需重建或合并，
   否则报告链接与代码树对不上；
2. **多一处索引**：本 ADR、`docs/README.md`、`docs/course-report/README.md` 三处登记，
   漏一处会被读成"文档不存在"；
3. **页数未测**：环境无 LibreOffice，报告不给页数承诺；
4. **不能替代 docx**：若课程要求 Word 提交，仍需人工排版或另行决策引入 `python-docx`。

## 6. 验证方式

| # | 判据 | 命令 / 证据 |
| :--- | :--- | :--- |
| 1 | 目录与文件存在 | `ls docs/course-report/` ⇒ `README.md`、`report.md` |
| 2 | 图表结构合法 | `python3 .codebuddy/skills/diagram-authoring/scripts/mermaid_check.py docs/course-report` ⇒ 0 错误 0 警告 |
| 3 | 报告引用的数字可复算 | `docs/course-report/README.md` §3 的出处逐条复跑 |
| 4 | 未回写其它文档 | `git show --stat` 只含本目录、本 ADR、两处索引、开发日志 |
| 5 | 门禁不受影响 | `make check`（本次无代码改动，仍应全绿） |
