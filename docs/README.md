# 文档地图

本目录是项目所有**工程性文档**的入口。代码回答"怎么做"，文档回答"**为什么这么做**"。

---

## 目录结构

| 目录 | 内容 | 何时更新 |
| --- | --- | --- |
| [`adr/`](adr/) | 架构决策记录（Architecture Decision Records） | 每次涉及选型、架构、依赖、流程的决策 |
| [`proposals/`](proposals/) | 立项与选型提案（含论证过程） | 立项阶段与大方向选择 |
| [`requirements/`](requirements/) | 需求规格（SRS）、用例、验收标准 | 需求新增或变更时 |
| [`design/`](design/) | 架构设计、模块设计、接口契约、**威胁模型** | 设计变更时 |
| [`engineering/`](engineering/) | 工程流程规范：工作流、生命周期、DoD、测试策略 | 流程调整时 |
| [`research/`](research/) | 前沿 AI 生态调研与实验结论（含失败结论） | 每次调研或实验收敛时 |
| [`devlog/`](devlog/) | **开发日志**（按议题/阶段分篇，过程记录） | 每次提交 |

---

## 核心文档速查

| 我想知道…… | 看这里 |
| --- | --- |
| **开发环境构建完成后要做什么** | [`engineering/post-build-checklist.md`](engineering/post-build-checklist.md) |
| 怎么切分支、怎么写提交信息、怎么发布 | [`engineering/git-workflow.md`](engineering/git-workflow.md) |
| 项目分几个阶段？现在到哪了？ | [`engineering/sdlc.md`](engineering/sdlc.md) |
| 什么算"做完了"？ | [`engineering/definition-of-done.md`](engineering/definition-of-done.md) |
| 测试怎么写？要覆盖到什么程度？ | [`engineering/testing-strategy.md`](engineering/testing-strategy.md) |
| 安全要求是什么？漏洞怎么报？ | [`../SECURITY.md`](../SECURITY.md) |
| 某个技术决策为什么这么定？ | [`adr/`](adr/) |
| base project 为什么选它？ | [`proposals/0001-base-project-selection.md`](proposals/0001-base-project-selection.md) |
| AI 代理该怎么协作？ | [`../CODEBUDDY.md`](../CODEBUDDY.md) |

---

## 写作约定

- 语言：中文（技术术语保留英文）；代码块、路径、标识符用英文。
- 先结论后论据；对比用表格。
- 引用上游内容必须给出**出处**（路径 + 版本/提交，或 URL + 访问日期）。
- 不确定的内容标注 **【待验证】**，并写明验证方式。禁止把推测写成结论。
- 文档与代码**同一 PR 内同步**更新；没有文档的改动视为未完成。
