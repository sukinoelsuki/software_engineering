# 0020. 工具参数校验器（`ArgumentValidator`）的实现选型：手写 JSON-Schema 子集校验器

- **状态**：**已接受（2026-09-19，所有者批准）**——批准动作已执行：本行改为「已接受」并同步
  [`README.md`](README.md) 索引，**正文不改**（ADR 只增不改）。⇒ `harness/arguments.py`
  **可以开工**（落地清单见 `interfaces/harness.md` §7.2）。
- **日期**：2026-09-19
- **决策者**：Le0n3rd（授权代理提出候选与论证；**批准与否由领导/所有者拍板**）
- **相关**：
  - 上游决策 [`ADR-0015`](0015-layering-and-reuse-boundary.md) §5.2.2 行 `B1` 与 §8.1 的 **`D2`**
    （pydantic **仅两处用途**：信任边界校验 + 工具参数 JSON Schema 生成），
    §5.2.2 的**回退路径保留**条款（"若届时不可安装，回退路径是手写校验，
    `bench/store.py` 已验证该模式可行，**并新增 ADR 记录降级**"）；
  - 契约 [`interfaces/harness.md`](../design/interfaces/harness.md) §3.4（`ArgumentValidator` 的
    `V1`~`V6` 与"实现选型未定 ⇒ 停下上报"）、§3.3 步 2、§5.1 第 8 行、§6.1 `H-3`、§6.2 `S1-c`；
  - 契约 [`interfaces/tools.md`](../design/interfaces/tools.md) §2.3（`parameters_schema` 由来）与 §2.6；
  - 缺口登记 [`design/architecture.md`](../design/architecture.md) §11 的 **`G-2`**；
  - 未决项登记 [`interfaces/README.md`](../design/interfaces/README.md) §6 的 **`U9` ①**；
  - 实测证据：本文 §1.3（本容器、`pydantic 2.13.5`、`uv run python -c` 现场输出）。

---

## 1. 背景与问题

### 1.1 缺的是"哪一套机制"，不是"要不要校验"

信任边界上的参数校验**必须存在**，这一点早已定死：`contracts/tools.py` 的
`ToolCallRequest.arguments_json` 是"**原始 JSON 文本：不可信、尚未解析**"，
`Tool.invoke` **不得**再解析它（`ADR-0015` §5.1.2 的关键约定，`REQ-SEC-03`）。
`harness.md` §3.4 也已把**语义**写死（`V1`~`V6`）：

| # | 契约要求（`V1`~`V6` 摘要） |
| --- | --- |
| `V1` | 按 `spec.parameters_schema` **严格**校验：类型 / `required` / **未知键拒绝** |
| `V2` | 输出只含 schema 声明过的键，值的类型与 schema 一致 |
| `V3` | 失败抛 `ToolArgumentsInvalidError`（`foundation/errors.py`，`BenchError` 子类） |
| `V4` | **错误信息不得回显原始不可信内容**（不含参数值、不含 `arguments_json` 片段；键名经 `sanitize_for_display`） |
| `V5` | 输入大小上限（建议 `64 KiB`）⇒ 超限**直接拒绝、不尝试解析** |
| `V6` | **无状态、纯函数式**（不读文件 / 网络 / 环境，不缓存跨调用状态） |

**未定的只有"用哪一套机制实现"**，而它卡住了装配点：`harness.md` §5.1 的装配清单第 8 行
（`validator`）**无默认值且无实现** ⇒ `cli/` 装配不起来 ⇒ 端到端闭环（`0.1.0` 里程碑的判据）无法推进。
契约对该项的处置路径也是明文：

> 选 pydantic ⇒ 在 `ADR-0015` 修订记录登记（`G-2` 收敛一半）；
> **选手写 ⇒ 须新增 ADR 登记"`D2` 的第一处用途不落地"**（`harness.md` §3.4）。

### 1.2 与 `D2` 的张力：真实被校验的输入**不是**模型类

`D2` 的原文是两处用途：

| 用途 | `D2` 的落法（原文） | 仓库现状 |
| --- | --- | --- |
| ① 信任边界校验 | 工具参数（**按 `ToolSpec.parameters_schema`**）与模型输出解析后的严格校验 | **无载体**（`src/` 无 `pydantic` import） |
| ② 工具参数 JSON Schema 生成 | `parameters_schema` **由模型类生成** | **相反**：3 个内置工具的 `parameters_schema` 是**手写 dict** |

事实（逐行可核，非推测）：

| 事实 | 证据 |
| --- | --- |
| `parameters_schema` 的类型是 `Mapping[str, object]`（**自由形状的 JSON Schema**，不是模型类） | `contracts/tools.py` L57；`interfaces/tools.md` §2.3 |
| 4 个内置工具的 schema 是**手写 dict**，逐个字面写出 | `tools/files.py`（`read_file` / `write_file` / `list_dir`）、`tools/shell.py`（`run_command`） |
| 校验器的输入因此是"**不可信 JSON 文本 + 一个手写 schema dict**" | `harness.md` §3.4 的签名：`validate(*, spec: ToolSpec, arguments_json: str)` |
| `src/` 里**没有**任何 `pydantic` 调用 | `architecture.md` §11 `G-2`（该登记已由领导核实） |

⇒ `D2` 的两处用途**互相依赖却方向相反**：`D2` 设想的是"模型类 ⇒ 生成 schema ⇒ 按 schema 校验"，
而现状是"手写 schema ⇒ 需要按 schema 校验"。要做成 `D2` 的形态，必须**两处同时落地**
（改 `tools/*.py` 的 schema 来源 + 改 `ToolSpec` 让校验器拿到模型类）——
那是一次**契约级**改动（见 §3 方案 C），而不是"选一个实现"。

### 1.3 实测证据（`pydantic 2.13.5`，2026-09-19，本容器）

命令（可复现）：

```bash
uv run python -c "
from pydantic import BaseModel, ConfigDict, ValidationError
class M(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid')
    path: str
    count: int = 1
def probe():
    try:
        M.model_validate_json('{\"path\": 12345, \"count\": \"SENTINEL-7f3a\"}')
    except ValidationError as e:
        return e
e = probe()
print(str(e)); print('SENTINEL-7f3a' in str(e)); print(e.errors()[0].keys())
print(getattr(e, 'errors')(include_input=False))
"
```

三处输出（原文抄录、已删去提示 URL 行）：

| # | 观察 | 对 `V4` 的含义 |
| --- | --- | --- |
| ① | `str(e)` 含 `input_value='SENTINEL-7f3a'` 与 `input_type=str`；`'SENTINEL-7f3a' in str(e) == True` | **默认错误文本回显输入值** ⇒ 与 `V4` **直接冲突** |
| ② | `e.errors()` 的元素键为 `['input', 'loc', 'msg', 'type', 'url']`；`errors(include_input=False)` 可去掉 `input` 键（签名 `(self, /, *, include_url=True, include_context=True, include_input=True)`） | 可以关掉，**但这是一条纪律**：任何一处漏用 `include_input=False`、或误用 `str(e)`，`V4` 即被破 |
| ③ | `M.model_json_schema()` = `{"additionalProperties": false, "properties": {"path": {"title": "Path", "type": "string"}}, "required": ["path"], "title": "M", "type": "object"}` | 生成物**带 `title` 注解字段**（嵌套模型还会带 `$defs`）⇒ 直接发给模型的 schema 与手写形态**不同**；本地 `llama-server` 的 tool-calling 语法转换是否接受这些额外字段**未核验** |

> **`pydantic` 不提供"由 JSON Schema 反建模型"的官方能力**（`create_model` 是"手工列出字段
> 再构造模型类"的 API，不是 schema 解释器；`TypeAdapter` 需要 Python 类型而**不是** schema 文本）。
> ⇒ 选 pydantic必然附带一层**自研的 JSON-Schema → 字段 → `create_model`** 转换代码（见 §3 方案 B）。

---

## 2. 决策驱动因素

| # | 因素 | 说明 | 类型 |
| --- | --- | --- | --- |
| 因素-1 | **`V4` 是安全断言，不能靠纪律** | "不可信内容不回流"在本项目已两次以"结构性质优于纪律"落地（`prompts` 拒收外部内容、`context.assemble` 的 `role is USER` 守卫）。校验器的错误文本是**唯一**会把参数值带回日志/事件/回显的通道 | 硬约束 |
| 因素-2 | **`mypy --strict` 必须通过** | `ADR-0004`；动态 `create_model` 在严格模式下的注解价值接近于零 | 硬约束 |
| 因素-3 | **低资源与可移植**（`D-6`） | `pydantic-core` 是 Rust 编译扩展，Termux/Android **无官方 wheel**（`ADR-0015` §8.2.1 的 `V-j`） | 硬约束 |
| 因素-4 | **依赖纪律**：新增依赖需事先确认 | `ADR-0015` §5.2 的组件清单是**所有者批准过的封闭清单**；本处只需一个封闭子集 | 硬约束 |
| 因素-5 | **单人 + 时间预算** | 校验器的**全部**行为都必须自己写测试钉住；机制越多，出错面越大 | 硬约束 |
| 因素-6 | **可机器检查** | "约定只写在文档里"是本项目已复现的失效模式（`ADR-0015` §7.1 的动机） | 硬约束 |
| 因素-7 | **发给模型的 schema 是可观察协议面** | `chat(tools=...)` 的 schema 会被本地推理框架转成约束语法；形态变化属**外部可观察**行为 | 偏好 |

---

## 3. 候选方案

### 方案 A：手写 **JSON-Schema 子集**校验器（**采纳**）

- 简述：在 `harness/arguments.py` 实现一个**无状态**类，按**本文 §5.1 枚举的封闭子集**
  校验 `parameters_schema`：顶层 `type: object` + `properties` + `required` +
  `additionalProperties: false`，属性侧支持 `type` / 数值边界 / 数组 `items` 与长度边界。
  子集**之外**的校验关键字 ⇒ **拒绝**（声明缺陷），不静默忽略。
- 优点：① `V4` 由**我们自己的错误格式化器**产生 ⇒ 是结构性质而非纪律（因素-1）；
  ② 零新增依赖、零编译扩展（因素-3/4）；③ 子集小 ⇒ 用例可穷举、出错面可控（因素-5/6）；
  ④ 不触碰任何契约类型与 `tools/`（影响面最小）；⑤ `mypy --strict` 下注解完备（因素-2）。
- 缺点：① 校验正确性**完全由我们负责**，没有库背书；② 不支持完整 JSON Schema
  （`enum` / `pattern` / 嵌套 `object` / `$ref` …）⇒ 将来接入 MCP 外部工具时兼容面窄；
  ③ 新增一个 `harness/` 模块（ADR-0015 §5.4.1 的"8 件"变 9 件，须登记）。

### 方案 B：pydantic 严格模式 + **自研 JSON-Schema → 模型** 转换层

- 简述：`validate` 内把 `spec.parameters_schema`（dict）转成 pydantic 模型类
  （`create_model` + 逐关键字映射），再 `model_validate_json(arguments_json)`；
  错误文本由我们自己重写（禁用 `str(e)`）。
- 优点：① 与 `D2` 的"pydantic 做信任边界校验"字面一致；② 类型强制与 `extra="forbid"`
  有成熟实现。
- 缺点：① **`V4` 仍需自写错误重写层**——`input_value=` 默认回显（§1.3 证据①），
  即"用了库省下的代码"被"重写库的错误输出"抵掉（因素-1）；
  ② **必须自研转换层**：JSON-Schema→`create_model` 的映射（`type`/`required`/`additionalProperties`/
  数值与长度边界/数组 `items`）**就是方案 A 的校验逻辑，只是换成了"生成代码"**
  ⇒ 代码量**更大**、出错面更大（因素-5），且多一层"dict → 模型类 → 校验"的间接；
  ③ 把不可信 JSON 交给 Rust 编译扩展解析：**新增信任面与攻击面**（因素-3）；
  ④ 动态模型类的类型退化为 `type[BaseModel]`，`mypy --strict` 下注解收益消失（因素-2）；
  ⑤ 单测可读性下降（失败点藏在动态类里）。

### 方案 C：pydantic 模型类作为**唯一真源**（`parameters_schema` 由模型类生成）

- 简述：把 `tools/*.py` 的 4 个 `parameters_schema` 改为
  `Model.model_json_schema()`，并把模型类交给校验器 ⇒ 完整落地 `D2` 的两处用途。
- 优点：① 与 `D2` 原文**完全**一致；② 声明与校验同源（一个真源）。
- 缺点：① **必须改契约**：`ToolSpec` 需要携带"可校验的东西"（模型类或 validator 对象），
  而 `ToolSpec` 是**零行为契约层**的数据类型（`contracts/` 不得 import 第三方，`R3`）
  ⇒ 只能塞一个 `type[object]` 甚至 `Callable`，把"曾经是纯数据"的契约变成"挂行为"的契约；
  ② 与 `chat(tools=...)` 的描述形态**仍是两处表示**：模型看到的是 JSON Schema（§1.3 证据③ 含
  `title`/`$defs`），我们能精确控制的是模型类 ⇒ 仍是"同一事实两处表述"（`README.md` `C10` 的教训）；
  ③ 影响面最大：`contracts/tools.py` + `tests/unit/test_contracts.py` + 4 个工具的构造期 +
  `tests/unit/test_tools_*.py`；④ 把编译扩展拉进 `tools/`（L2 每个工具目录）（因素-3）；
  ⑤ §1.3 证据③ 的兼容面**未核验** ⇒ 属"未核验方向"（`ADR-0015` §8.1 `D7` 的同一口径：不采用未核验项）。

### 方案 D：引入第三方 `jsonschema` 库做校验

- 简述：`jsonschema.Draft202012Validator(schema).validate(raw)` + 自定义错误渲染。
- 优点：① 子集覆盖最全；② 库已高度成熟。
- 缺点：① **超出已批准的组件清单**（`ADR-0015` §5.2 无此行）⇒ 属 **`F` 类**（新增依赖需事先确认），
  而本处只需要一个封闭子集（因素-4）；② 库的错误消息同样**回显实例值**
  （`'SENTINEL-7f3a' is not of type 'string'`）⇒ `V4` 仍需自写渲染（因素-1）；
  ③ 它支持**完整** JSON Schema，含 `$ref` 与（配置后）远程解析面 —— "能力大于需求"
  本身就是多余的攻击面（`SECURITY.md` 的最小机制取向）。

---

## 4. 权衡对比

权重 1~5（越大越重要）；评分 1~5（越大越好）。

| 评估维度 | 权重 | A 手写子集 | B pydantic+转换层 | C 模型类为真源 | D `jsonschema` |
| --- | --- | --- | --- | --- | --- |
| 满足 `V1`~`V6`（尤其 `V4` 不回显、`V6` 无状态） | 5 | **5** | 2 | 3 | 3 |
| 与 `D2` / 依赖纪律的关系（不越界、不静默放宽） | 4 | **5** | 3 | 1 | 1 |
| 依赖成本与可移植性（因素-3） | 4 | **5** | 3 | 2 | 2 |
| 代码量与出错面（因素-5） | 4 | 4 | 2 | 2 | **5** |
| 影响面（是否需改契约 / 其它模块） | 3 | **5** | 3 | 1 | 4 |
| 可验证性（单测与机器检查可直接钉住） | 4 | **5** | 3 | 3 | 4 |
| 对模型协议面的影响（因素-7） | 3 | **5** | 3 | 2 | **5** |
| **加权合计** | | **131** | 72 | 56 | 90 |

> **决定性的一行是"满足 `V1`~`V6`"**：B 与 D 的默认错误输出都**回显输入值**（§1.3 证据①、方案 D 缺点②），
> 要达到 `V4` 都得写一遍我们自己的错误渲染 ⇒ 它们省下的**恰恰**是本项目最在意的那段
> （"不可信内容不回流"的结构性质），而付出的却是更大的信任面。
> C 的失分集中在"必须改契约 + 两个真源"。

**被否决的方案与理由（记录，防止重复讨论）**：见 §3 各方案的"缺点"，此处只写结论：

| 候选 | 结论 |
| --- | --- |
| B pydantic + 自研 schema→模型 转换层 | **否决**：转换层就是方案 A 的校验逻辑（换成生成代码，更绕），且 `V4` 仍要自写渲染 ⇒ 两处成本都付、两处收益都拿不到 |
| C 模型类为唯一真源 | **否决**：需改零行为契约 + 与 `chat(tools=...)` 的描述仍是两处表示 + 生成 schema 的额外字段（`title`/`$defs`）对本地 tool-calling 的兼容性**未核验**（未核验 ⇒ 不采用） |
| D 引入 `jsonschema` | **否决**：超已批准组件清单（`F` 类）；错误回显仍需自写渲染；支持完整 JSON Schema（含 `$ref`/远程解析面）对本处是多余的攻击面 |

---

## 5. 决策

**决定采用：方案 A —— 手写 JSON-Schema 子集校验器。**

理由（三条）：

1. **`V4` 由此成为结构性质而不是纪律**：错误文本由我们自己的格式化器唯一产生，
   "不回显参数值"是代码结构决定的，不依赖任何调用点记得传 `include_input=False`、或记得别用 `str(e)`。
2. **成本最低且影响面最小**：零新增依赖、零编译扩展，不触碰任何契约类型、不改 `tools/`、
   不改 `tools/*.py` 的 schema —— 只需要一个新模块与一组单测。
3. **与 `D2` 的处置路径一致**：`ADR-0015` §5.2.2 已**明文保留**手写校验这条回退路径
   （"`bench/store.py` 已验证该模式可行，并新增 ADR 记录降级"），本文即该 ADR。

### 5.1 受支持的 JSON-Schema 子集（**封闭清单**；子集外 ⇒ 拒绝）

> **判据取向**：schema 是**我方生成的声明**（内置工具在代码里写死），因此
> "出现不支持的关键字"必定是**声明缺陷**，而**不是**需要向前兼容的外部输入。
> 忽略它 = 让"声明了但没校验"静默存在（fail-open 的形状），故**一律拒绝**。

顶层（对象）：

| 关键字 | 必需 | 语义 / 限制 |
| --- | --- | --- |
| `type` | 是 | 必须为 `"object"`（其它值 ⇒ 声明缺陷） |
| `properties` | 是 | `Mapping`，键为参数名（`str`），值为属性 schema（见下）；可为空映射 |
| `required` | 否 | `list[str]`，每项必须出现在 `properties` 内 |
| `additionalProperties` | **是** | 必须**显式**为 `False`。缺失 / 为 `True` ⇒ 声明缺陷（见下方"为什么要求显式"） |
| `description` | 否 | **注解**，不参与校验（允许且忽略） |

属性（`properties` 的每个值）：允许的关键字**仅**为下表中的集合；其它 ⇒ 声明缺陷。

| `type` | 允许的关键字 | 语义 |
| --- | --- | --- |
| `"string"` | `description` / `minLength` / `maxLength` | 长度按 **Unicode 码点**计；`minLength ≤ maxLength` |
| `"integer"` | `description` / `minimum` / `maximum` / `exclusiveMinimum` / `exclusiveMaximum` | **`bool` 不算整数**（`isinstance(True, int)` 为真 ⇒ 必须显式排除） |
| `"number"` | 同上（边界为数值） | 接受 `int`/`float`，排除 `bool`；必须 `math.isfinite` |
| `"boolean"` | `description` | — |
| `"array"` | `description` / `items` / `minItems` / `maxItems` | `items` 是**单个**属性 schema（不支持元组式 `items`）；长度边界为非负整数 |

**尚未支持**（出现即拒绝，登记为已知限制）：`object` 与 `null` 类型、`enum`、`const`、
`pattern`、`format`、`oneOf`/`anyOf`/`allOf`/`not`、`$ref`/`$defs`、元组式 `items`、
`patternProperties`、`propertyNames`、`uniqueItems`、`multipleOf`。
⇒ 重评触发点见 §7 第 4 条。

**为什么要求 `additionalProperties` 显式存在**：JSON Schema 的默认值是"允许未知键"，
而 `V1` 要求"未知键拒绝"。若"缺失"被**静默**当作 `False`，校验器的语义就不再是标准 JSON Schema，
却仍以 JSON Schema 的名义被读写 —— 那是"同一事实两处表述"的种子。要求显式写出，
语义与标准一致，且四个内置工具**已经**都写着 `additionalProperties: False`（零改动）。

### 5.2 解析与算法（数据结构 + 顺序，写死以免实现漂移）

模块：**`src/agent_sec_perf/harness/arguments.py`**（新增；见 §6 的负面后果 3 与 §8 动作 1）。

```python
class SubsetArgumentValidator:
    """``harness.md`` §3.4 的 ``ArgumentValidator`` 的最小子集实现（无状态、纯函数式）。"""

    def validate(self, *, spec: ToolSpec, arguments_json: str) -> Mapping[str, object]: ...
```

`validate` 的**固定步骤顺序**（每一步失败都抛 `ToolArgumentsInvalidError`，见 §5.3）：

| 步 | 动作 | 为什么是这个顺序 |
| --- | --- | --- |
| 0 | **体积检查**：`len(arguments_json) > _MAX_CHARS`（`65536`）或 UTF-8 字节数 `> _MAX_BYTES`（`65536`）⇒ 拒绝 | `V5`；在**解析之前**拒绝，避免超长输入进入解析器（字符数检查在前是"最便宜的早退"） |
| 1 | `json.loads`，且**三个护栏**：`parse_constant` 拒绝 `NaN`/`Infinity`/`-Infinity`；`object_pairs_hook` 拒绝**重复键**；结果必须是 `dict` | 三者都是 Python `json` 的宽松侧：`NaN`/`Infinity` 是**非标准扩展**（且 `inf` 超时 = 没有超时）；重复键 `json.loads` 静默取后者，而"同一个键两个值"在安全语义上是模糊输入 |
| 2 | **schema 形状检查**：按 §5.1 逐项校验 `spec.parameters_schema` 是**子集内**的合法 shape | 声明缺陷必须在**同一处**被发现与拒绝；否则"我们写错了 schema"会以"模型的参数不合法"的形态出现在审计里（见 §5.3 末的如实登记） |
| 3 | **未知键**：`set(payload) - set(properties)` 非空 ⇒ 拒绝 | `V1` 的 `additionalProperties: false` 语义 |
| 4 | **缺必填**：`required` 中未出现的键 ⇒ 拒绝 | `V1` |
| 5 | **逐属性校验**：按 **`properties` 键名升序**遍历（只在 payload 中存在该键时校验其值） | 顺序写死保证**同一输入必得同一错误消息**（`policy.md` §2.5 的同一取向：确定性是可回放的前提）；输出只含 schema 声明过的键（`V2`），**不补可选键的默认值** |
| 6 | 返回 `dict`（**新对象**，只含已声明的键） | `V2`；不返回解析结果的其它片段 |

**错误只报第一条**（按上表顺序与"键名升序"）：消息长度不随输入增长，`V4` 的压力面最小。

### 5.3 错误与失败语义

| 项 | 规定 |
| --- | --- |
| 异常类型 | `foundation.errors.ToolArgumentsInvalidError`（`V3`；**不新增**异常类型，避免两套基类） |
| 消息形状 | 固定模板：`参数不合法：<键名>（<期望>）`，其中 `<键名>` 经 `sanitize_for_display(limit=32)` 截断；`<期望>` 是**我方常量**（如 `字符串` / `非空字符串` / `整数` / `有限正数` / `字符串数组` / `未声明的键` / `缺少必填键`） |
| 消息**不得**含 | 参数值、值的类型名之外的任何载荷片段、`arguments_json` 的任何子串、异常 `repr`（`V4`） |
| 无状态 | 不读时钟 / 环境 / 文件 / 网络；无实例可变状态；不缓存跨调用结果（`V6`） |
| 上限常量 | `_MAX_CHARS = 65536` / `_MAX_BYTES = 65536`，与 `tools/registry.py::MAX_TOOL_OUTPUT_BYTES` 同量级 |

**如实登记（不得放大为"已解决"）**：契约 §2.7 的 `denied_reason` 是**闭集**
（`{unknown_tool, not_exposed, invalid_arguments, policy_denied, approval_denied}`），
其中**没有**"工具声明缺陷"这一档 ⇒ "我们的 schema 写错了"与"模型给的参数不合法"
在审计里**同形**，都会落到 `invalid_arguments`。这与 `harness.md` §3.3 步 3 已确立的
先例一致（`spec.capabilities` 为空集也按 `invalid_arguments` 处置）。
**若要区分**，属 `harness.md` §2.7 的**契约变更**（新增短码），本文**不代改**。

### 5.4 对 `D2` 的处置：**新增 ADR**（而非只在 `ADR-0015` 修订记录登记）

| 处置 | 内容 |
| --- | --- |
| `D2` 用途 ①**信任边界校验** | **不落地**：信任边界校验由 `harness/arguments.py` 的手写子集校验器承担，**不用** pydantic |
| `D2` 用途 ②**schema 生成** | **同样不落地**：`parameters_schema` 保留**手写 dict**（`tools/files.py` / `tools/shell.py` 零改动）。理由：它是**直接发给模型的 JSON Schema**，手写形态可精确控制协议面；而 `model_json_schema()` 会追加 `title` 等注解字段（§1.3 证据③），这些字段是否被本地 `llama-server` 的 tool-calling 转换接受**未核验** |
| `ADR-0015` 的处理 | **正文一字不改**（ADR 只增不改）。只在其「修订记录」**追加一行指针**指向本文（该节自身声明"事实补充与编排性修订在此登记"） |

**为什么是"新增 ADR"而不是"修订记录里写一句"**（三条，逐条可核）：

1. **契约已明文规定**：`harness.md` §3.4 的处置路径写的就是"选手写 ⇒ 须**新增 ADR**"；
   走另一条路等于绕过已落盘的流程规定。
2. **性质不同**：`ADR-0015` 的既有修订记录处理的是"同一决策的**事实补充**"
   （`V-a~V-o` 核验结果、`D7` 的收口、依赖填装、契约细化）。本文处理的是
   **一个已批准用途被放弃**——即决策**覆盖范围的收缩**，属决策级变更。
3. **防"旧表述继续以现行规则的身份误导"**：若只在修订记录里写一句，`ADR-0015` §5.2.2 的行 `B1`
   与 §5.3 的正文仍以"pydantic 用于信任边界校验"被读到。本项目的规则明确要求
   "**任何规则/决策一经修改，必须回头把所有被它覆盖的旧约束一并更新**"，
   并特别指出"ADR 已接受决策的历史快照不改，**若与现状冲突，在新一篇里说明**" ⇒ 即本文。

### 5.5 与既有规定的关系

| 既有规定 | 本文的关系 |
| --- | --- |
| `harness.md` §3.4 的 `V1`~`V6` | **不变**；§5.1~§5.3 是它的**可实现化**（子集、顺序、消息形状） |
| `harness.md` §3.4 的"与 `ToolArgumentError` 分工" | **不变**：校验器管"不可信输入被拒"（`invalid_arguments`）；工具内部再校验载荷形状（`ToolResult(ok=False)`），两者**不合并、不删任何一处** |
| `contracts/tools.py` / `contracts/harness.py` | **零改动**（不新增字段、不新增类型；`ArgumentValidator` Protocol 已存在） |
| `tests/unit/test_architecture_layers.py` 的 `V1`（契约层不引第三方） | **不受影响**（新模块在 `harness/`，且**不 import** 任何第三方） |
| `ADR-0015` §5.4.1 的目录树 | 新增一个文件（`harness/arguments.py`），理由与影响见 §6 负面后果 3 与 §8 动作 1；**不新增目录** |

---

## 6. 后果

### 正面

- **装配点的阻塞解除**：`harness.md` §5.1 第 8 行的 `validator` 有了确定实现 ⇒ `cli/` 可以装配、
  端到端闭环（`0.1.0` 里程碑判据）可以推进。
- **`V4` 成为结构性质**：不回显参数值不再依赖"记得关 `include_input`、记得别用 `str(e)`"。
- **零新增依赖、零编译扩展**：对 `D-6`（低资源/可移植）与 `ADR-0015` 的依赖纪律都是净零。
- **`G-2` 有确定处置**（待本文获批后关闭）：不再以"没有载体"的形态长期悬挂。

### 负面（必须填写）

| # | 负面 | 说明 |
| --- | --- | --- |
| 1 | **校验正确性完全自负** | 没有库背书：子集边界、`bool`/`int` 区分、`NaN`/重复键、长度与边界比较，全部要自己写用例钉住（§7 的判据即为此） |
| 2 | **不支持完整 JSON Schema** | 见 §5.1 的"尚未支持"清单 ⇒ 将来接入 MCP 外部工具或其 schema 带注解关键字（如 `title`）时会**拒绝**。这是 fail-secure 方向，但会以"外部工具装不进来"的形态出现 |
| 3 | **`harness/` 从 8 件变 9 件** | `ADR-0015` §5.4.1 的目录树与 `harness.md` §3.1 都写"8 件"。⚠️ 更要紧的是：`tests/unit/test_harness_internals.py` 的 `LEAF_UNITS` **未含新模块** ⇒ 若不显式同步，**H2（叶子零依赖）对新模块不生效**——"检查集合与新模块漂移"正是本项目要防的形状。落地动作见 §8 动作 3 |
| 4 | **`pydantic` 成为无调用方的运行期依赖** | 两处用途均不落地 ⇒ `pyproject.toml` 的 `pydantic>=2.13` 与 `uv.lock` 里的 `pydantic-core`（编译扩展）**没有调用方**，体积与平台矩阵成本（`D-6`）却仍在。**去留属 `F` 类**（依赖变更需事先确认）⇒ 本文**不代拍**，见 §8 动作 6 与 §7 第 5 条 |
| 5 | **一个新模块的维护面** | 校验逻辑将来若因 MCP/`enum`/`pattern` 而扩张，会逐步向"再造一个 JSON Schema 引擎"漂移 ⇒ 触发重评的判据见 §7 第 4 条 |

### 风险与缓解

| 风险 | 影响 | 缓解措施 |
| --- | --- | --- |
| 手写校验漏掉一条边界（如 `bool` 被当成 `integer`） | 中 | §7 第 1/2 条的逐项用例 + `tools/registry.py` 已有同款教训（`optional_positive_int_arg` 显式排除 `bool`）可对照；校验器**不**是唯一防线（工具内部仍再校验，见 §5.5） |
| 子集白名单被"悄悄放宽"（后人为了让某个 schema 通过而加关键字） | 中 | §7 第 3 条的**元测试**（未支持关键字必须报错）+ 内置工具 schema ⊆ 子集的机器检查 |
| `pydantic` 长期无调用方却留在依赖里 | 低 | §8 动作 6：由领导裁决去留（三条候选），并登记触发重评点 |
| 子集的确定性顺序被改写（如改用集合迭代顺序） | 低 | §5.2 步 5 与 §5.3 写死顺序；用例断言"同输入 ⇒ 同消息" |

---

## 7. 验证方式

| # | 判据（**可执行**） | 落点 |
| --- | --- | --- |
| 1 | **子集边界逐类覆盖**：`type` / `required` / `additionalProperties` / 数值边界（含 `exclusiveMinimum`）/ 数组 `items` 与长度边界 / 字符串长度，每类**正例 + 反例**各一条 | `tests/unit/test_harness_arguments.py`（新增；扩 `harness.md` §6.1 的 `H-3`） |
| 2 | **不回显（`V4`）**：对**每个**失败分支（类型不符 / 缺必填 / 未知键 / 超限 / 重复键 / 非法 JSON / 子集外关键字），断言异常消息中**不含** sentinel 值、不含 `arguments_json` 的任何片段 | 同上；对抗侧沿用 `tests/security/` 的 `S1-c` |
| 3 | **子集白名单不可静默放宽**：① 元测试——构造一个含未支持关键字（如 `enum`）的 schema，断言**确实**抛 `ToolArgumentsInvalidError`；② 机器检查——遍历 4 个内置工具的 `parameters_schema`，断言其关键字集合 ⊆ §5.1 的白名单 | `tests/unit/test_harness_arguments.py`（新增，登记为 `harness.md` §6.1 的 **`H-11`**） |
| 4 | **触发重评的条件**（满足任一条即**另开 ADR**，不得就地扩子集）：① 引入 MCP 外部工具且其 schema 落在子集外；② 需要 `enum` / `pattern` / 嵌套 `object`；③ 需要 pydantic 承担**模型输出**（`ModelResponse`）的解析；④ 本模块的代码量或用例量超过"再造一个 JSON Schema 引擎"的门槛 | 本文 §7 与 `design/architecture.md` §9 的复核时点 |
| 5 | **依赖去留的判据**：若 `pydantic` 在下一个里程碑（`0.1.0` 端到端可用）仍无调用方 ⇒ 触发 §8 动作 6 的裁决 | `pyproject.toml`（`F` 类动作，由领导执行） |

---

## 8. 后续行动

> **实现侧动作**（`src/` / `tests/` 不是架构师的文件域；此处只给清单，供领导开工令引用）。

- [ ] **动作 1**：新建 `src/agent_sec_perf/harness/arguments.py`，实现
  `SubsetArgumentValidator`（无状态；只依赖 `contracts` + `foundation.errors` +
  `foundation.logging.sanitize_for_display`；**不 import 任何 harness 兄弟模块**）。
  ⚠️ 该模块属 `harness/` 的**第 9 件** ⇒ 需在 `interfaces/harness.md` §3.1 的模块清单与
  `design/architecture.md` §4.3 的状态表中登记（由架构侧在本波落地）。
- [ ] **动作 2**：新增 `tests/unit/test_harness_arguments.py`，落 §7 第 1~3 条。
- [ ] **动作 3**：`tests/unit/test_harness_internals.py` 的 `LEAF_UNITS` 增加 `"arguments"`
  （以及 `LANDED_LEAF_FILES` 若需要），使 **H2 对新模块生效**——否则新模块不受约束（负面后果 3）。
- [ ] **动作 4**：`cli/` 装配点（`harness.md` §5.1 第 8 行）注入该实现。
- [ ] **动作 5**：`contracts/tools.py` 与 `tools/registry.py` 的 `specs()` docstring 由
  "裁剪后"改为"**全量注册集**"（`T6` 裁决的落地，见 `harness.md` §8 的 `T6` 行）。
- [ ] **动作 6**：由领导裁决 `pyproject.toml` 的 `pydantic` 去留（**`F` 类**，本文不代拍）：
  (i) 保留声明但登记"本轮无调用方"；(ii) 从 `dependencies` 移除并在 `ADR-0015` 修订记录登记
  "本轮未采用"；(iii) 保留到 `model/` 的响应解析落地时再评。**建议 (ii)**：未使用的运行期依赖
  即体积与攻击面成本，且与 `D-6` 有已知张力；重新引入时另开 ADR 即可。
- [ ] **动作 7**：本文获批后，把「状态」改为「已接受」并在
  [`docs/adr/README.md`](README.md) 索引登记（**正文不改**）。

---

## 9. 修订记录

| 日期 | 修订 | 依据 |
| --- | --- | --- |
| 2026-09-19 | **初版（提议中）**：给出 4 个候选（手写子集 / pydantic+转换层 / 模型类为真源 / `jsonschema`）与加权对比（131 / 72 / 56 / 90），**推荐手写 JSON-Schema 子集校验器**；定义受支持子集、解析与错误语义、`D2` 两处用途不落地的处置（**新增 ADR**，`ADR-0015` 正文不改）；登记 5 项负面后果与 5 条可执行验证判据 | `harness.md` §3.4（`V1`~`V6` + 处置路径）、§5.1 第 8 行；`ADR-0015` §5.2.2 行 `B1` 与 §8.1 `D2`、§5.2.2 的回退路径条款；`architecture.md` §11 `G-2`；`tools/files.py` / `tools/shell.py` / `tools/registry.py` / `contracts/tools.py` / `harness/loop.py`（**读源码核实**）；`pydantic 2.13.5` 实测（§1.3） |
