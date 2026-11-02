# 能力包内核：使用与移植手册

- 适用版本：`0.2.0`
- 对应工作树：`feat/11-capability-pack-kernel`，2026-11-02
- 实测环境：8 vCPU / 16 GiB / 无 GPU；`llama.cpp`；`MiniCPM5-2B-Q4_K_M`
- 读者：第一次跑通的人；要把示例换成自己任务的人；要把产品装到另一台机器的人；要现场讲解的人

---

## 1. 这个产品是什么

端侧模型只有权重。它不会自己读现场文件，不会按固定字段写报告，也不会因为提示里写了"不要编造"就真的不编造。

这里的 NIS 指本仓库已有的运行时：`harness/` 负责会话与工具调度，`security/` 负责策略与能力，`observability/` 负责审计，`tools/` 是执行面。`pack` 不另起一套运行时，它把案例编译成这套运行时认识的动作面。

能力包内核把这些事情拆成可判定的部分：

- 案例声明现场、动作面、任务与判据；
- 编译成 bundle，逐文件算 sha256，判据用正例和反例自证；
- 运行时由已有的 harness 执行，内核只读状态与证据；
- 失败时给出 `F1`~`F9` 的结构性修复目录，不让"再改改提示"成为默认动作。

产物有三件：

| 产物 | 位置 | 用途 |
| --- | --- | --- |
| bundle | 由 `--out` 指定 | 可分发、可校验的执行单位 |
| evidence | `bundle/evidence/<case>-<UTC>.json` | 一次运行的机检结果、事件摘要与终稿 |
| status | 由内核计算，不落盘 | `verified` / `evaluated` / `broken`，决定下一步 |

它不做什么，需要先说清楚：

| 不是 | 原因 |
| --- | --- |
| 模型训练或微调 | 权重不变；内核改的是动作面，不是模型能力 |
| 通用 agent 能力认证 | 只认证 `task + pack + model` 这一组，不反推模型会不会别的任务 |
| 自动修代码 | 内核只判定；修复由总控改声明式文件，再重新 `build` |
| 沙箱 | 隔离等级按运行时报告；当前环境只到 L1，应用级拦截，不是内核级隔离 |

### 1.1 它凭什么不是一次性脚本

| 维度 | 一次性演示脚本 | 这个内核 |
| --- | --- | --- |
| 任务定义 | 提示词写死在代码里 | `case.toml` 声明，换任务不改 `src/` |
| 可校验 | 人眼看输出 | manifest 逐文件 sha256，判据用正反例自证 |
| 状态 | 打印日志 | `status` 返回阶段、阻塞、允许的下一步 |
| 失败处理 | 改提示、换模型 | `F1`~`F9` 白名单，只允许结构性修法 |
| 分发 | 复制仓库 | wheel 可安装，`pack example` 带示例 |
| 边界 | 讲者口头说明 | 证据 JSON 与限制清单写死 |

它的普适价值在方法，不在某一个模型。同一个包，2B 与 4B 都能完成同一个案例；换案例只需要换声明式文件，内核不改。它能被移植，是因为案例、判据、动作面和证据都是显式对象，不是散落在提示词里的经验。

但它目前只验证了一个案例。这个限制写在 §12，不能省略。

### 1.2 什么任务适合

适合：

- 现场可以文件化：日志、配置、报告、清单，模型能通过工具读到；
- 产物可以机检：字段、片段、行级修订，能写成判据；
- 任务能拆成两三个阶段，每个阶段有独立的成功条件；
- 工具面窄，风险可以逐个声明；
- 失败需要归因，而不是"再试一次"。

不适合：

- 开放式写作或闲聊，没有可判定产物；
- 判据只能靠人眼，写不出正例与反例；
- 需要几十步长链路、跨工具并发或外部网络；
- 想用它证明"模型具备通用 agent 能力"。

---

## 2. 五个对象

先把对象分清，后面的命令才有意义。

| 对象 | 文件 | 作用 |
| --- | --- | --- |
| 案例 | `case.toml` | 唯一声明源：现场、动作面、任务、判据 |
| 动作面 | `pack/pack.toml`、`pack-revise/pack.toml` | 每个阶段暴露哪些工具、要求哪些能力、风险如何声明 |
| 判据 | `case.toml` 的 `[case]` 与 `[selftest]` | 产物要满足什么，以及用什么样例证明判据分得开对错 |
| bundle | `manifest.json`、`case/`、`README.md` | 可分发单位；逐文件摘要，证据不参与完整性校验 |
| 证据与状态 | `evidence/*.json`、`pack status` | 一次运行的事实，以及内核据此算出的下一步 |

案例是输入单位，bundle 是分发单位，状态是控制面接口。三者不要混着叫"包"。

---

## 3. 安装

### 3.1 从源码

```bash
git clone https://cnb.cool/Mybase_Le0n3rd/Native-Intelligent-Systems.git
cd Native-Intelligent-Systems
make setup
uv run agent-sec-perf --help
```

`make setup` 会装依赖、装 git 钩子。之后既可以用 `uv run agent-sec-perf`，也可以在虚拟环境里直接调用 `agent-sec-perf`。

### 3.2 从 wheel

wheel 是运行期包。它带内核、CLI 和内置示例案例，不带仓库里的 `docs/` 与测试。

```bash
uv build --out-dir dist
uv venv .venv-demo
uv pip install --python .venv-demo/bin/python dist/agent_sec_perf-0.2.0-py3-none-any.whl
.venv-demo/bin/agent-sec-perf doctor
```

wheel 内的示例位于：

```text
site-packages/agent_sec_perf/data/examples/showcase/incident-01
```

这个路径由 `pyproject.toml` 的 `force-include` 从仓库 `examples/` 映射进 wheel。单一真源仍是 `examples/`，构建时只做路径映射，不复制第二份文件。

### 3.3 这台机器需要什么

| 项 | 要求 | 不满足时 |
| --- | --- | --- |
| Python | ≥ 3.12 | `doctor` 第一项就报 fail |
| 推理后端 | `llama-server` 在 `PATH` 中 | `doctor` 报 fail；也可用 `/opt/llama.cpp/build/bin/llama-server` 的绝对路径加入 `PATH` |
| 模型 | 一个支持工具调用的 GGUF | `doctor` 报"端侧模型" fail；`pack run` 无法装配 |
| 审计目录 | 用户状态目录可写 | `pack run` 的审计回放会失败 |
| 端口 | 8080 空闲 | `llama-server` 启动失败，现象像"模型加载失败" |

模型默认从 `/opt/models` 查找。换目录用 `--model-dir`，换文件用 `--model`。

### 3.4 可移植性实测

2026-11-02，在一台临时虚拟环境里安装 wheel，工作目录设为 `/tmp`，不依赖仓库：

```bash
/tmp/portable3/bin/agent-sec-perf pack example --out /tmp/portable3-example
/tmp/portable3/bin/agent-sec-perf pack build /tmp/portable3-example --out /tmp/portable3.bundle
/tmp/portable3/bin/agent-sec-perf pack run /tmp/portable3.bundle \
  --model /opt/models/MiniCPM5-2B-Q4_K_M.gguf --json --keep
```

结果：`ok=true`，11/11 机检通过，墙钟约 4 分钟，证据写入：

```text
/tmp/portable3.bundle/evidence/incident-01-20261102T084636Z.json
```

`doctor` 在这次安装里也能找到示例，说明 wheel 不依赖源码树。

---

## 4. 五分钟跑通

下面这条路径在源码仓库和 wheel 安装里都成立。

```bash
# 1. 把内置示例复制到可写目录
agent-sec-perf pack example --out /tmp/incident-01

# 2. 编译、校验、看状态
agent-sec-perf pack build /tmp/incident-01 --out /tmp/ip.bundle
agent-sec-perf pack verify /tmp/ip.bundle
agent-sec-perf pack status /tmp/ip.bundle

# 3. 看失效模式目录（不跑模型）
agent-sec-perf pack plan

# 4. 真跑（约 4 分钟，2B 模型）
agent-sec-perf pack run /tmp/ip.bundle \
  --model /opt/models/MiniCPM5-2B-Q4_K_M.gguf --keep

# 5. 看状态与证据
agent-sec-perf pack status /tmp/ip.bundle
```

每一步应该看到什么：

| 步骤 | 关键输出 | 说明 |
| --- | --- | --- |
| `pack example` | `ok=true`、`case=...` | 示例已复制；可以改 `case.toml` 与现场文件 |
| `pack build` | `files=9`、`selftest.ok=true` | 9 个案例文件加 README；manifest 不计入 |
| `pack verify` | `ok=true`、`problems=[]` | 逐文件摘要一致，判据自证通过 |
| `pack status` | `stage=verified`、`next_actions` 含 `run` | 还没有运行证据 |
| `pack plan` | `catalog` 含 `F1`~`F9` | 每条有 `signal` / `action` / `forbidden` |
| `pack run` | `ok=true`、11 条 checks | 证据写入 `evidence/` |
| `pack status` | `stage=evaluated` | 已有一次运行证据 |

时间预算：`build`、`verify`、`status`、`plan` 都在秒级；`run` 是唯一的分钟级步骤。2B 模型在本环境实测 249~258 s，4B 模型约 8~10 分钟。换机器、换模型都要重新测。

---

## 5. 命令参考

### 5.1 命令总表

| 命令 | 作用 | 是否跑模型 |
| --- | --- | --- |
| `pack example --out DIR` | 复制内置示例 | 否 |
| `pack build CASE --out DIR` | 编译案例为 bundle | 否 |
| `pack verify BUNDLE` | 校验摘要与判据自证 | 否 |
| `pack status BUNDLE` | 报告阶段与允许的下一步 | 否 |
| `pack plan [BUNDLE]` | 列出 `F1`~`F9` | 否 |
| `pack run BUNDLE` | 用端侧模型跑一次，写证据 | 是 |
| `doctor` | 环境体检与隔离矩阵 | 否（会跑隔离探针） |

### 5.2 `pack example`

```bash
agent-sec-perf pack example --out DIR [--force] [--json]
```

- `--out` 必填；目标不能与示例源重叠，也不能是源目录的父目录或子目录。
- 目标非空时默认拒绝；`--force` 只覆盖同名文件，**不删除**目标里的其他文件。
- JSON 输出字段：`ok`、`case`、`source`。

### 5.3 `pack build`

```bash
agent-sec-perf pack build CASE --out DIR [--version 0.1.0] [--stamp HASH] [--force] [--json]
```

- `CASE` 是包含 `case.toml` 的目录。
- `--out` 非空时默认拒绝；`--force` 会先删掉整个输出目录再重建。**不要把它指向仓库或家目录。**
- `--stamp` 只写进 manifest，不参与判据；省略时同输入字节一致。
- JSON 字段：`ok`、`bundle`、`name`、`version`、`files`、`selftest`。

### 5.4 `pack verify`

```bash
agent-sec-perf pack verify BUNDLE [--json]
```

检查四件事：

1. manifest 里的每个文件都在、大小与 sha256 一致；
2. 目录里没有清单外的多余文件（`evidence/` 除外）；
3. 路径是相对路径、不含 `..`、没有符号链接；
4. 判据自证仍然通过。

退出码：`0` 通过，`1` 校验不通过（返回 `problems`），`3` 装配期故障（bundle 不可读）。

### 5.5 `pack status`

```bash
agent-sec-perf pack status BUNDLE [--json]
```

只读。返回 `stage`、`blockers`、`evidence`、`next_actions`。状态由内核计算，不接受外部声称。

状态机：

```text
unbuilt ──build──► verified ──run──► evaluated
                      │
                      └── problems ──► broken
```

### 5.6 `pack plan`

```bash
agent-sec-perf pack plan [BUNDLE] [--json]
```

不跑模型。输出 `F1`~`F9` 的 `code` / `failure` / `signal` / `action` / `forbidden`。给总控读时用 `--json`。

### 5.7 `pack run`

```bash
agent-sec-perf pack run BUNDLE [--model PATH] [--model-dir DIR] [--workspace DIR] [--keep] [--json]
```

- 省略 `--model` 时按案例的 `[model].preferred` 查找。
- `--keep` 保留临时工作区；工作区里有 `report.md`、`reasoning-*.md`、`llama-server.log`。
- `--json` 时 stdout 只出 JSON，进度与表格走 stderr。
- 退出码：`0` 全部机检通过；`1` 有 check 失败；`3` 装配期故障（案例、bundle、模型）。

### 5.8 `doctor`

```bash
agent-sec-perf doctor [--model-dir DIR] [--json]
```

八项体检：Python、产品版本、配置、审计落点、推理后端、模型、示例案例、隔离能力。

`fail` 表示产品跑不起来；`warn` 表示能跑但口径被削弱。隔离 L1 是 warn，不是 fail。

### 5.9 退出码

`agent-sec-perf run`（旧命令，不在本手册的 `pack` 链路里）：

| 码 | 含义 |
| --- | --- |
| 0 | 完成 |
| 1 | 任务失败 |
| 2 | 达到步数上限 |
| 3 | 装配或配置故障 |
| 4 | 审计写入失败 |
| 5 | 其它未预期异常 |

`pack` 子命令只使用 `0` / `1` / `3`。`scripts/pack-demo.sh` 另外用 `2` 表示前置条件不满足，见第 10 节。

---

## 6. 案例契约

### 6.1 `case.toml` 字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `[case].id` | 非空字符串 | 案例 id，也是 bundle 名 |
| `[case].title` | 非空字符串 | 人读标题 |
| `[case].scene` | 相对路径 | 现场目录；模型只能看到这里的输入 |
| `[case].pack_draft` | 相对路径 | 诊断阶段的动作面 |
| `[case].pack_revise` | 相对路径 | 修订阶段的动作面 |
| `[case].min_tool_calls` | 正整数 | 机检要求的最少工具调用次数 |
| `[case].min_read_calls` | 正整数 | 最少 `read_file` 次数 |
| `[case].min_write_calls` | 正整数 | 最少写次数（含 `revise_report`） |
| `[case].report_file` | 纯文件名 | 产物名；不能带路径 |
| `[case].report_substrings` | 非空字符串数组 | 终稿必须出现的片段 |
| `[case].report_forbidden` | 字符串数组 | 终稿不能出现的片段 |
| `[case].report_preserved_keys` | 非空字符串数组 | 修订前后必须逐字保留的 `KEY` |
| `[case].report_revised_key` | 非空字符串 | 只允许变化的那一行 |
| `[case].task_draft` | 非空字符串 | 诊断阶段的任务文本 |
| `[case].task_revise` | 非空字符串 | 修订阶段的任务文本 |
| `[selftest].positive` | 相对路径 | 正例，必须让全部内容判据通过 |
| `[selftest].negative` | 相对路径 | 反例，必须至少让一条内容判据失败 |
| `[model].preferred` | 非空字符串 | 默认模型文件名 |
| `[model].max_completion_tokens` | 正整数 | 单次补全预算 |
| `[model].request_timeout_s` | 正数 | 单次请求超时 |
| `[model].ready_timeout_s` | 正数 | 模型加载超时 |

约束很硬：缺字段、类型不对、路径越界、样例缺失，都会在 `build` 阶段抛 `CaseSpecError`，退出码 3。**不回落默认值**，因为"案例写错了却按默认值跑起来"会让一次失败看起来像成功。

### 6.2 领域包字段

| 字段 | 说明 |
| --- | --- |
| `[pack].name` / `version` / `description` | 包标识与人读说明 |
| `[prompt].fragments` | 领域提示片段，只写"做什么"与不可协商的约束 |
| `[tools].allowlist` | 本阶段暴露的工具名；不在表里的工具对模型不存在 |
| `[security].capabilities` | 本阶段需要的能力，必须是全局授予集合的子集 |
| `[security.risk_overrides]` | 把狭窄工具的风险声明为 `low`；通用 `write_file` 不得这样声明 |

阶段拆包的原因是可判定性。诊断阶段看不到 `revise_report`，修订阶段看不到 `write_incident_report`。这样"阶段串味"不会发生，而不是靠提示里写"现在不要用 X"。

本案例的裁剪发生在五层：

| 层 | 本案例的值 | 效果 |
| --- | --- | --- |
| 能力档位 | `standard` | 决定模型能看到多少工具；不改变授权 |
| 全局授予 | `read_file`、`write_file` | 授权上限；领域包只能收窄 |
| 阶段 `allowlist` | 诊断：`read_file` / `list_dir` / `write_incident_report`；修订：`read_file` / `revise_report` | 两个阶段的工具面互斥 |
| 工具形状 | `write_incident_report` 只收四个字段；`revise_report` 只收 `key` + `value` | "自由写全文"这个动作不存在 |
| 风险覆盖 | 三个窄工具声明为 `low` | 通用 `write_file` 仍是 `HIGH`，且不在 `allowlist` |

这五层叠起来，才是"能力集裁剪"。只在提示里写"不要重写全文"不算裁剪，因为那个动作仍然在工具面上。

### 6.3 做自己的案例

1. `pack example --out /tmp/my-case`，拿到一份可运行的骨架；
2. 改 `scene/` 里的现场文件，只放模型应该看到的输入；
3. 改 `task_draft` 与 `task_revise`，把步骤和硬约束写清楚；能判定的要求写进判据，不要只写"请仔细"；
4. 改 `report_*` 判据，写正例与反例；正例必须全过，反例至少失败一条；
5. 改两个 `pack.toml` 的 `allowlist`，只暴露本阶段需要的工具；
6. `pack build`。自证不过就改判据或样例，不要为了让模型通过而放宽判据；
7. `pack run`。失败时先看 checks，再按 `F1`~`F9` 选修复动作。

---

## 7. bundle、证据与状态

### 7.1 bundle 里有什么

```text
<bundle>/
  manifest.json          # 每个文件的 sha256 + 判据覆盖范围
  README.md              # 能力边界声明（人读）
  case/                  # 案例原件
    case.toml
    scene/
    pack/pack.toml
    pack-revise/pack.toml
    selftest/positive.md
    selftest/negative.md
  evidence/              # 运行证据，不参与完整性校验
```

`build` 输出 `files=9` 时，计数是 `case/` 下 8 个文件加 `README.md`；`manifest.json` 本身不计入。

### 7.2 篡改演示

```bash
cp -r /tmp/ip.bundle /tmp/ip.bundle.tampered
printf '\n# tampered\n' >> /tmp/ip.bundle.tampered/case/case.toml
agent-sec-perf pack verify /tmp/ip.bundle.tampered --json
echo "exit=$?"
```

期望：`ok=false`、`problems=["大小不一致：case/case.toml"]`、`exit=1`。只改一个字节也会被发现，因为清单存的是 sha256，不是修改时间。

### 7.3 evidence 字段

| 字段 | 内容 |
| --- | --- |
| `schema` | `capability-pack/evidence/v1` |
| `case_id` | 案例 id |
| `bundle_manifest_sha256` | 这次运行对应的 manifest 摘要 |
| `model.path` | 实际使用的 GGUF 路径 |
| `isolation.tier` | 隔离等级，如 `l1` |
| `checks` | 11 条机检的 `name` / `ok` / `detail` |
| `stages` | 每个阶段的退出码、状态、工具调用与结果 |
| `report` | 终稿全文 |

证据是运行时产物，不入库；仓库只保留结论与复跑命令。

### 7.4 判据覆盖

`build` 会打印"覆盖 4 项，未覆盖 7 项"：

- 覆盖的是内容判据：报告非空、必含项、禁含项、键完整；
- 未覆盖的是事件流与审计判据：阶段完整、两个终态、`seq` 连续、工具集使用、工具真的执行、修订保真、审计回放。

这说明自证只证明"判据分得开对错"，不证明"这次运行已经通过"。后者要一次真跑。

---

## 8. 失效模式 `F1`~`F9`

`pack plan` 的输出是一份白名单。它限制修复动作：同一类失效只允许同一类结构性修改。

| 编号 | 失效 | 允许的修复 | 禁止 |
| --- | --- | --- | --- |
| `F1` | 工具未暴露（授权 ≠ 暴露） | 调整档位预算或 `allowlist`，让目标工具真正可见 | 提高全局权限；把所有风险降成 `low` |
| `F2` | 格式不合规 | 改成封闭字段 schema；在工具侧加格式闸门 | 在提示里重复"不要写尖括号" |
| `F3` | 拒绝后放弃 | 让错误信息可执行；给重试预算 | 放宽校验让它通过 |
| `F4` | 阶段串味 | 按阶段拆包，会话级暴露面固定 | 在提示里写"现在不要用 X" |
| `F5` | 参数形状不封闭 | 改成封闭字段表（`additionalProperties: false`） | 让校验器接受任意键 |
| `F6` | 值幻觉 | 在工具内校验取值是否逐字出现在证据里 | 在提示里写"不要编造" |
| `F7` | 幂等重复 | 在工具成功消息里引导；记为"重复但幂等" | 在 harness 层去重 |
| `F8` | 判据误伤 | 判据归一化；用正反例自证 | 放宽到"看起来对" |
| `F9` | 工具暴露但不用 | 拆小阶段；收窄工具形状；任务里点名工具与参数 | 再加一个同义工具；只提高步数预算 |

`F1` 与 `F9` 的区分来自实测。0.6B 模型在案例上反复写错 `log_timeout_ms`，工具拒绝了两次，然后放弃，属于 `F6` 加 `F3`。1.7B 模型反复调用 `read_file` 八次，`write_incident_report` 明明在 `allowlist` 里却一次都没选，属于 `F9`。两者的观测信号像，修法相反。

完整文本以 `pack plan --json` 为准；上表是给人看的摘要。

---

## 9. 样例讲解：从裸模型到一次工作流

这条链路对应三件事：`case.toml` 与两个 `pack.toml` 是能力集裁剪，`build` / `verify` 是打包，`run` 是在 NIS 框架上执行。下面按终端屏幕逐段讲。

命令是：

```bash
cd /workspace
scripts/pack-demo.sh
```

脚本做六步：前置检查、体检、构建、校验、状态与修复目录、真跑。下面是每一步终端会出现的文字，以及可以照着讲的内容。

### 9.1 时间分配

| 时间 | 屏幕 | 讲什么 |
| --- | --- | --- |
| 0:00–0:30 | 0/6 前置检查 | 固定代码版本、模型、输出目录 |
| 0:30–1:00 | 1/6 `doctor` | 环境没问题；隔离 L1 是如实报告 |
| 1:00–1:40 | 2/6 `build` | 9 个文件、判据自证 4 项 |
| 1:40–2:10 | 3/6 `verify` | 摘要一致；篡改会被发现 |
| 2:10–2:40 | 4/6 `status` + `plan` | 状态由内核算；F1~F9 是白名单 |
| 2:40–3:10 | 5/6 运行前表 | 动作面：允许根、工具集、两阶段 |
| 3:10–6:30 | 5/6 流式输出 | 思考、工具调用、策略、结果 |
| 6:30–7:20 | 5/6 机检表 | 11 条判据，重点讲两条 |
| 7:20–7:50 | 5/6 终稿 | 四行报告，前三个值来自现场 |
| 7:50–8:10 | 6/6 `status` | `verified` 变 `evaluated` |

真跑本身约 4 分 15 秒（2026-11-02，思考预算 512，实测 255 s）；讲解可以覆盖等待时间，总时长约 8 分钟。只有 2 分钟时看 9.12。

速查（每屏一句）：

| 屏幕 | 一句话 |
| --- | --- |
| 0/6 | 固定代码版本、模型、输出目录，结果可回到这三个值 |
| 1/6 | 环境没问题；隔离 L1 是如实报告，不是失败 |
| 2/6 | 案例编译成 9 个文件的 bundle；判据自证覆盖 4 条内容判据 |
| 3/6 | 逐文件摘要一致；改一个字节就会被发现 |
| 4/6 | 状态由内核算；`F1`~`F9` 只允许结构性修复 |
| 5/6 前置 | 两个阶段两个包，工具面按阶段收窄 |
| 5/6 流式 | 思考只是展示；工具调用、策略、结果是三件事 |
| 5/6 机检 | 11 条判据；拒绝非法输入是治理生效，不算失败 |
| 5/6 终稿 | 前三个值来自现场，第四行按复核意见改 |
| 6/6 | `verified` 变 `evaluated`，证据可复核 |

### 9.2 屏幕 0：前置检查

终端：

```text
== 0/6 前置检查 ==
分支：feat/11-capability-pack-kernel
提交：4e4340d docs(devlog): 规划演示阶段的四步与本期非目标
模型：/opt/models/MiniCPM5-2B-Q4_K_M.gguf
包：  /tmp/ip.bundle
```

讲解：

> 先固定三个值：代码在哪个提交、用哪个模型、包写到哪里。后面所有结果都能回到这三个值。脚本会用 `--force` 重建 `/tmp/ip.bundle`，所以这次看到的 `verified` 不是上一次留下的状态。

不要逐字念分支名。提一句"这是一个未合入的功能分支，演示完会按流程合回"就够。

### 9.3 屏幕 1：环境体检

终端：

```text
== 1/6 环境体检（agent-sec-perf doctor） ==

│ Python 版本 │ ✓ ok   │ 3.12.14（要求 ≥ 3.12）                       │
│ 产品版本    │ ✓ ok   │ 0.2.0                                        │
│ 配置加载    │ ✓ ok   │ 已授予能力：（空：默认拒绝一切工具）          │
│ 审计落点    │ ✓ ok   │ /root/.local/state/lowspec/audit/audit.jsonl │
│ 推理后端    │ ✓ ok   │ /opt/llama.cpp/build/bin/llama-server        │
│ 端侧模型    │ ✓ ok   │ AgentCPM-Explore、MiniCPM5-2B 等 4 个        │
│ 演示案例    │ ✓ ok   │ /workspace/examples/showcase/incident-01      │
│ 隔离能力    │ ! warn │ l1（3/8 个维度有负向证据）                    │
```

讲解：

> 体检先把"看起来像模型不行"的环境问题排掉：解释器、产品版本、审计目录、推理后端、模型文件、示例资产。配置加载显示已授予能力为空，这是默认拒绝；`pack run` 不使用用户配置，它自己固定授予读和写，再由领域包收窄。
>
> 隔离这一行是 warn，不是 fail。当前只到 L1：用户维度、地址空间、文件大小有负向证据；文件系统、网络、能力集、CPU 时间没有隔离。产品把没做到的维度写进审计，不把"装了 bwrap"说成"有沙箱"。

这一段是安全口径的重点。不要为了好听说"我们有沙箱"。

### 9.4 屏幕 2：构建

终端：

```text
== 2/6 构建能力包（--force 保证从「还没有证据」开始） ==
已构建能力包：/tmp/ip.bundle
文件 9 个；判据自证通过（覆盖 4 项，未覆盖 7 项）
下一步：agent-sec-perf pack verify <目录> --json
```

讲解：

> 这一步把案例编译成 bundle。9 个文件是案例原件加 README：`case.toml`、两个阶段的 `pack.toml`、三个现场文件、两个自证样例，再加一份 README。`manifest.json` 本身不计入。
>
> "判据自证通过"要讲准：正例让 4 条内容判据全过，反例至少让一条失败。这说明判据分得开对错，不说明这次运行已经通过。事件流与审计的 7 条判据还没有自证，要等真跑。

### 9.5 屏幕 3：校验

终端：

```text
== 3/6 校验能力包（逐文件 sha256 + 判据自证） ==
校验通过
  判据自证：正例全过=True，反例至少失败一项=True
```

讲解：

> `verify` 做两件事：逐文件比对 sha256，再跑一次判据自证。它不跑模型。这个包是可以分发的，别人拿到后能确认文件没被改过，也能确认判据本身还有效。
>
> 想现场加一个十秒的对照：复制一份 bundle，往 `case/case.toml` 追加一行，再 `verify`，退出码会变成 1，并指出"大小不一致：case/case.toml"。只改一个字节也会被发现。

如果时间不够，这个篡改演示可以跳过，只讲结论。

### 9.6 屏幕 4：状态与修复目录

终端：

```text
== 4/6 状态与修复目录（状态必须是 verified；plan 是 F1~F9 白名单） ==
 阶段       verified
 证据       （无）
 阻塞       （无）
 → run      agent-sec-perf pack run /tmp/ip.bundle --model <GGUF 路径>
 → inspect  agent-sec-perf pack verify /tmp/ip.bundle --json

                         失效模式 → 允许的结构性修复
┃ 编号 ┃ 失效            ┃ 观测信号        ┃ 允许的修复      ┃ 禁止            ┃
│ F1   │ 未暴露（授权 ≠ 暴露） │ 目标工具不在暴露面 │ 调档位/allowlist │ 提高全局权限    │
│ F6   │ 值幻觉          │ 格式对，取值不在现场 │ 工具内校验证据   │ 提示里写"不要编造" │
│ F9   │ 暴露但不用      │ 工具在 allowlist 内 │ 拆阶段/收窄形状  │ 加同义工具      │
...
```

讲解：

> 状态是内核算的，不是模型说的。`verified` 的下一步只有 `run` 和 `inspect`，没有"直接宣布通过"这个动作。
>
> `plan` 是这次方法论的沉淀：`F1` 到 `F9`，每条写清观测信号、允许的结构性修复、明确禁止的修法。现场只挑一条讲。比如 `F6` 值幻觉：格式写对了，但取值不在现场文件里；允许的修法是让工具在写入前校验值是否逐字出现，禁止的是在提示里再写一遍"不要编造"。
>
> 再补一句 `F1` 和 `F9` 的区别：`F1` 是工具根本没暴露，`F9` 是工具暴露了但模型不选。两者看起来都是"反复调别的工具"，修法相反。这个区分是 0.6B 和 1.7B 两次失败逼出来的。

### 9.7 屏幕 5：运行前的动作面

终端：

```text
== 5/6 用端侧模型跑一次（流式显示思考；约 4 分钟） ==
演示案例：离线日志根因定位与报告修订（incident-01）
 工作区  /tmp/lowspec-showcase-xxxx/workspace
 允许根  工作区 + 案例根（其余路径不可读）
 领域包  诊断 pack / 修订 pack-revise（按阶段收窄暴露面）
 工具集  读 read_file / list_dir；写 write_incident_report；改 revise_report（目标固定）
 档位    standard（决定工具暴露预算；不改变授权）
 流程    阶段 A 诊断写初稿 → 阶段 B 读回并只改结论行
 模型    /opt/models/MiniCPM5-2B-Q4_K_M.gguf
 隔离    l1（未达到 L2 的原因会写入审计）
```

讲解：

> 这一段是这次运行的完整动作面。工作区是临时目录；允许根只有工作区和案例根，模型读不到别的路径。两个阶段用两个包：诊断阶段看不到 `revise_report`，修订阶段看不到 `write_incident_report`。这不是提示里叮嘱出来的，是工具列表里就没有。
>
> 档位 `standard` 只决定模型能看到多少工具，不改变授权。授权是固定的 `read_file` 和 `write_file`，领域包只能在授予集合上收窄，不能扩大。

### 9.8 屏幕 5：流式输出

终端（节选）：

```text
=== 阶段：draft ===
· 模型生成中（流式：思考实时显示，结束后折叠）
│ Let me start by reading the app.log file first.
· 模型：请求工具
→ 调用 read_file
· 策略 read_file
✓ 结果 read_file
│ First read: app.log. First ERROR line is at 09:13:52...
│ Now second read: service.toml.
· 模型：请求工具
→ 调用 read_file
· 策略 read_file
✓ 结果 read_file
│ 现在我需要调用 write_incident_report。
· 模型：请求工具
→ 调用 write_incident_report
· 策略 write_incident_report
✓ 结果 write_incident_report
· 终态：completed
└ 思考已折叠（906 字符，全文：reasoning-draft.md）

=== 阶段：revise ===
· 模型生成中（流式：思考实时显示，结束后折叠）
· 模型：请求工具
→ 调用 read_file
· 策略 read_file
✓ 结果 read_file
· 模型：请求工具
→ 调用 revise_report
· 策略 revise_report
✓ 结果 revise_report
· 终态：completed
└ 思考已折叠（410 字符，全文：reasoning-revise.md）
```

讲解：

> 灰色竖线是模型的思考，边生成边显示。它只用于让人看到本地模型在逐 token 跑，不进事件流、不进审计、也不参与判据。阶段结束会折叠成一行，全文落在工作区的 `reasoning-draft.md`。
>
> "模型：请求工具"是模型选择调用；"调用 read_file"是工具调用事件；"策略 read_file"是策略求值；"结果 read_file"是工具真正执行并返回。三行分开，是为了让"模型想做什么"和"系统允许做什么"不混在一起。
>
> 诊断阶段读了两次文件：先 `app.log`，再 `service.toml`。然后调用 `write_incident_report`。这个工具只收四个字段，行格式由工具生成；写入前会校验三个取证值是否逐字出现在现场文件里。模型不需要自己拼报告，也没有机会把格式写乱。
>
> 修订阶段只做两件事：读回 `report.md`，再调用 `revise_report` 改 `VERDICT` 一行。其余三行由工具保留，模型无法重写全文。

终端行的来源与去向：

| 终端行 | 产生者 | 含义 | 进证据 |
| --- | --- | --- | --- |
| `=== 阶段：draft ===` | CLI | 新开一个会话 | 否 |
| `· 模型生成中` | CLI 展示层 | 流式开始 | 否 |
| `│ ...` | 模型 `reasoning_content` | 思考增量 | 否（只落 `reasoning-*.md`） |
| `· 模型：请求工具` | `model_response` 事件 | 模型选择调用 | 是 |
| `→ 调用 X` | `tool_call` 事件 | 工具调用请求 | 是 |
| `· 策略 X` | `policy_decision` 事件 | 策略求值 | 是 |
| `✓ 结果 X` | `tool_result` 事件 | 工具执行结果 | 是 |
| `· 终态：completed` | `task_finished` 事件 | 会话结束 | 是 |
| `└ 思考已折叠` | CLI 展示层 | 思考全文落盘 | 否 |

这张表是现场最容易被问到的"这行是什么"。灰色思考不是证据，事件才是。

如果终端出现这一行：

```text
· 模型：段有 "timeout_ms = 8000" - 确实有?4. verdict = "待复核"...
```

讲解：

> 这是 llama-server 在思考预算边界把 `</think>` 结束标记漏进了 content 通道，属于显示层噪声。它没有进入 `report.md`，也没有进入审计。看到时不用在这里停下来分析模型，继续看机检表。

旧配置 `--reasoning-budget 128` 在 2026-11-02 的一次运行里出现过这行；当前配置改为 512，同一天的复跑没有再出现。换模型或改预算后仍可能遇到，所以保留这段处置。没有出现就跳过。

### 9.9 屏幕 5：机检表

终端：

```text
                                    机检结果
┃ 检查            ┃ 结果   ┃ 证据                                              ┃
│ 阶段完整        │ ✓ pass │ 实际 draft → revise；要求 draft → revise          │
│ draft 终态      │ ✓ pass │ 退出码=0，TASK_FINISHED 条数=1，status=completed  │
│ draft seq 连续  │ ✓ pass │ 14 条事件，区间 0..13                             │
│ revise 终态     │ ✓ pass │ 退出码=0，TASK_FINISHED 条数=1，status=completed  │
│ revise seq 连续 │ ✓ pass │ 10 条事件，区间 0..9                              │
│ 工具集使用      │ ✓ pass │ 共 5 次（read_file 3，写 1，revise_report 1）      │
│ 工具真的执行    │ ✓ pass │ 执行 5/5 次（成功 5，拒绝 0，未执行 0）            │
│ 落盘报告        │ ✓ pass │ report.md：命中全部 4 项                          │
│ 修订保真        │ ✓ pass │ 三个 KEY 逐字保留；VERDICT 已变更                 │
│ 审计回放        │ ✓ pass │ 2 个会话审计 10 行；5/5 个 audit_id 可回放        │
│ 降级留痕        │ ✓ pass │ 2 个会话各记一条；tier=l1                         │
```

讲解：

> 机检分四层。事件流看阶段是否完整、两个终态、`seq` 是否连续；工具层看调用次数和是否真的执行；产物层看报告必含项和修订保真；审计层看每个 `audit_id` 能不能从落盘 JSONL 还原。
>
> 重点讲两条。"工具真的执行"不是"工具必须都成功"。工具拒绝非法输入是治理生效，单独记为"工具拒绝生效"，不算失败。这次没有拒绝，5 次全部成功。
>
> "修订保真"证明它只改了 `VERDICT`。初稿里的 `CODE`、`LOG_TIMEOUT_MS`、`CONFIG_TIMEOUT_MS` 三行在终稿里逐字未变。只看终稿分不出"按意见改了一行"和"重写了一份新报告"，所以这条必须单独判。

### 9.10 屏幕 5：终稿

终端：

```text
最终报告（落盘内容）：
  CODE=E42
  LOG_TIMEOUT_MS=3000
  CONFIG_TIMEOUT_MS=8000
  VERDICT=配置值 8000 大于日志值 3000，日志侧超时更短

演示成功：模型读了两份现场文件、写了报告、按复核意见只改结论行；产物与审计都可核对。
```

讲解：

> 前三个值都能在两份现场文件里找到：`E42` 和 `3000` 来自 `app.log` 第一条 ERROR，`8000` 来自 `service.toml` 的 `[upstream.payment]`。第四行是复核意见要求的结论。
>
> 报告由工具按字段生成。模型没有机会自由写一段散文。报告能被机检，审计能回放，所以"跑完了"和"跑对了"是分开的。

### 9.11 屏幕 6：运行后的状态

终端：

```text
== 6/6 运行后的状态（必须是 evaluated） ==
 阶段       evaluated
 证据       incident-01-20261102T084041Z.json
 阻塞       （无）
 → plan     agent-sec-perf pack plan /tmp/ip.bundle --json
 → inspect  agent-sec-perf pack verify /tmp/ip.bundle --json

证据：/tmp/ip.bundle/evidence/incident-01-20261102T084041Z.json
工作区：/tmp/lowspec-showcase-xxxx/workspace
终稿：/tmp/lowspec-showcase-xxxx/workspace/report.md
思考：/tmp/lowspec-showcase-xxxx/workspace/reasoning-draft.md
思考：/tmp/lowspec-showcase-xxxx/workspace/reasoning-revise.md
```

讲解：

> 状态从 `verified` 变成 `evaluated`，因为 `evidence/` 里多了一次运行证据。证据 JSON 里有 manifest 摘要、实际模型路径、隔离等级、11 条 checks、两阶段事件摘要和终稿。任何人拿这个文件都能复核，不需要相信演示者。
>
> 工作区里保留了 `report.md`、两份思考全文和 `llama-server.log`。想看模型当时怎么想的，打开 `reasoning-draft.md`；想看服务端有没有异常，打开 `llama-server.log`。

### 9.12 只有 2 分钟

不要现场跑模型。直接展示：

```bash
agent-sec-perf pack status /tmp/ip.bundle
ls /tmp/ip.bundle/evidence/*.json
cat /tmp/lowspec-showcase-*/workspace/report.md
```

讲三句：

> 这个包已经跑过一次，状态是 `evaluated`，证据里有 11 条机检和两阶段事件。
> 报告的四行里，前三个值来自现场文件，第四行是按复核意见改的。
> 审计里每个 `audit_id` 都能回放，所以这不是"看起来对"，是"可以核对"。

**不要**在展示前跑 `scripts/pack-demo.sh --prepare`，它会用 `--force` 重建 `/tmp/ip.bundle`，把这份证据覆盖掉。要保留就先复制：

```bash
cp -a /tmp/ip.bundle /tmp/ip.bundle.keep
```

### 9.13 口径边界

可以说：

- 同一案例、同一模型、同一包，连续 6 次 11/11，墙钟 249~258 s；
- 2B 模型能完成这个案例；
- 判据自证覆盖 4 条内容判据，7 条事件流与审计判据未自证；
- 隔离是 L1，应用级拦截加逐维度留痕；
- 失败可归因到 `F1`~`F9`，修复动作是白名单。

不能说：

- "2B 具备通用 agent 能力"；
- "全部判据都已验证"；
- "我们有沙箱"；
- "内核可以自动修复"；
- "这是性能结论"。

---

## 10. 移植到另一台机器

### 10.1 安装路径

源码仓库、wheel、sdist 三种方式都能跑内核。区别在附带资产：

| 方式 | 内核 | 内置示例 | 文档与测试 |
| --- | --- | --- | --- |
| 源码仓库 | 有 | 有 | 有 |
| wheel | 有 | 有 | 无 |
| sdist | 有 | 有 | 有 |

wheel 的安装步骤见 §3.2。装完先跑 `doctor`，再用 `pack example` 拿示例。

### 10.2 换模型

换模型不是改一个文件名就完事。至少重测四个值：

| 值 | 位置 | 为什么 |
| --- | --- | --- |
| `max_completion_tokens` | `case.toml` 的 `[model]` | 给少了，思考会把预算吃光，工具调用发不出来 |
| `request_timeout_s` | 同上 | 弱硬件生成慢，默认 60 s 不够 |
| `ready_timeout_s` | 同上 | 大模型加载更久 |
| `--reasoning-budget` | `cli/demo.py` 的 `DEMO_SERVER_ARGS` | 太小会把结束标记漏进 content；太大拖长等待 |

本环境的实测：2B Q4 在 1536 token 预算、600 s 超时下通过；4B 也能通过，但墙钟约 8~10 分钟。换机器要重新测，不要照抄数字。

### 10.3 换案例

案例是目录，不是代码。复制示例后改声明式文件即可，不需要改 `src/`。

需要改的只有：

- `scene/`：模型能看到的现场；
- `case.toml`：任务、判据、模型参数；
- `pack/pack.toml` 与 `pack-revise/pack.toml`：每阶段的工具与能力；
- `selftest/`：正例与反例。

改完 `pack build`。自证不过，先怀疑判据或样例，不要先怀疑模型。

### 10.4 不能直接搬走的东西

| 项 | 原因 |
| --- | --- |
| L1 隔离结论 | 取决于宿主机；换机器要重新跑 `doctor` 的负向探针 |
| 墙钟数字 | 单次采样，不是基准；CPU 核数、量化、模型大小都会变 |
| 审计目录 | 默认在用户状态目录；换用户会变 |
| `/opt/models` | 本镜像的预置路径；别的机器用 `--model` 或 `--model-dir` |
| `scripts/pack-demo.sh` | 源码仓库的现场脚本；wheel 用户直接用 `pack` 命令 |

---

## 11. 安全边界

这套东西的安全结论只来自可核对的事实。

- 默认拒绝：没有显式授予时，工具调用一律拒绝。
- 领域包只能收窄：`[tools].allowlist` 是授予集合的子集，不能扩大权限。
- 阶段隔离：诊断与修订用两个包，会话级暴露面固定。
- 工具形状：`write_incident_report` 只收四个字段，行格式由工具生成；`revise_report` 只能改一行。
- 证据校验：三个取证值必须逐字出现在现场文件里，否则工具拒绝并点名字段。
- 路径：允许根只有工作区与案例根；`report_file` 是纯文件名；bundle 拒绝符号链接与 `..`。
- 执行：不经过 shell；参数以列表传入；统一封装。
- 审计：特权操作留结构化 JSONL；`audit_id` 可回放。
- 隔离：当前 L1，用户、地址空间、文件大小有负向证据；其余维度如实记未隔离。

不要把通用 `write_file` 的风险声明改成 `low` 来让演示好看。那会扩大影响面，属于下调安全默认，必须先走 ADR。

---

## 12. 已知限制

- 只有一个案例。方法论的可迁移性还没有第二个案例验证。
- `pack` 没有 `apply`。内核不替总控改声明式文件；改文件后重新 `build`。
- 没有 `pack fit`。显存与上下文查表是增强，不在当前范围。
- 失败归因仍要人读证据。内核给 checks 与 evidence，`F1`/`F9` 的判定由人比对 `allowlist` 与审计后得出。
- 判据自证只覆盖内容维度 4 条；事件流 7 条要一次真跑。
- 墙钟是单次或少量重复观测，不是性能结论。
- 当前镜像没有 0.6B/1.7B。失败路径需要自备小模型，不要现场下载。
- llama-server 在思考预算边界可能把 `</think>` 漏进 content 通道。这是显示层噪声，报告与审计不受影响；已在 §9.8 说明。

---

## 13. 附录

### 13.1 目录速查

```text
src/agent_sec_perf/
  pack/            # 内核：案例、判据、bundle、状态、修复目录、示例定位
  cli/             # 命令面：pack、doctor、demo、run
  harness/         # 执行：会话、工具调度、事件流
  security/        # 策略、能力、隔离探针
  observability/   # 审计
  tools/           # 文件、受限写、修订
examples/showcase/incident-01/   # 内置示例（单一真源）
docs/engineering/capability-pack-manual.md  # 本手册
docs/engineering/pack-runbook.md            # 现场运行手册
```

### 13.2 术语

| 术语 | 含义 |
| --- | --- |
| 案例 | 一次任务的声明：现场、动作面、任务、判据 |
| 动作面 | 模型在某一阶段能看到的工具与能力 |
| 判据 | 产物必须满足的条件，以及证明判据有效的正反例 |
| bundle | 编译后的可分发单位，带逐文件摘要 |
| 证据 | 一次运行的机检结果、事件摘要与终稿 |
| 总控 | 读 `status` / `plan`、决定下一步的上层模型或人 |

### 13.3 相关文档

- 现场运行手册：[`pack-runbook.md`](pack-runbook.md)
- 决策记录：[`../adr/0034-capability-pack-kernel.md`](../adr/0034-capability-pack-kernel.md)
- 方法提案：[`../proposals/0005-capability-pack-compiler.md`](../proposals/0005-capability-pack-compiler.md)
- 案例设计：[`../design/showcase-incident-triage.md`](../design/showcase-incident-triage.md)
- 威胁模型：[`../design/threat-model/README.md`](../design/threat-model/README.md)
