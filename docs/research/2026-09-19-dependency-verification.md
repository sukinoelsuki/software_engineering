# 复用组件的核验结果（`ADR-0015` §8.2 的 V-a ~ V-o）

- 日期：2026-09-19
- 关联：[`ADR-0015`](../adr/0015-layering-and-reuse-boundary.md) §5.2（组件清单）、§7.4（可安装性与类型兼容）、
  §8.2（待核验事实台账）；[`sdlc.md`](../engineering/sdlc.md) §3.1（M0 出口准则的 `G1`/`G2`）；
  触发：devlog 0015 §3.2 的阶段盘点
- 性质：**核验结论 + 可复现命令**（不是决策）。**回填位置是 `ADR-0015` 对应表格**，
  决策级变更（如 D7 构建后端）**仍需所有者拍板**，本文不代替决策
- 环境：开发容器（linux x86_64，Python 3.12.14）；隔离 venv 位于 **`/tmp/depcheck`（仓库之外）**
  ——核验**不修改** `pyproject.toml` / `uv.lock`，因此**不等同于"引入依赖"**

---

## 1. 为什么写这篇

`ADR-0015` 的组件清单里有多项标着 **【待核验】**，而项目规则要求
"未经核验的许可证与体积不得写进结论性表述"（`SECURITY.md` 供应链条款）。
这些【待核验】同时是 **M0 出口准则 `G1`** 的判据（`sdlc.md` §3.1）。

**核验的意义**：起草时写的若干数字**已经过期或不准**——本轮实测发现
`urllib3` 最新版是 **2.8.0**（ADR 写 2.7.0）、`platformdirs` 是 **4.11.10**（ADR 写 4.11.9）、
`pydantic-core` 被钉在 **2.46.5**、`Rich` 的传递依赖**不止 `pygments`**。
⇒ **核验不是形式：不更新这些数字，"依赖已锁定"这句就是错的。**

---

## 2. 可复现命令

```bash
# ① PyPI 元数据（版本 / 许可证 / 依赖 / wheel 矩阵）
curl -s "https://pypi.org/pypi/<pkg>/json" | python3 -c "import json,sys;d=json.load(sys.stdin);i=d['info'];print(i['version'], i.get('license_expression') or i.get('license'), i.get('requires_dist'))"
# 或按精确版本： curl -s https://pypi.org/pypi/pydantic-core/2.46.5/json

# ② 隔离环境（仓库之外，不动项目依赖）
uv venv /tmp/depcheck --python 3.12
uv pip install --python /tmp/depcheck/bin/python typer rich pydantic structlog platformdirs urllib3 tree-sitter tree-sitter-python mypy

# ③ 类型兼容：最小样例跑 mypy --strict（不落临时文件，用 -c）
/tmp/depcheck/bin/mypy --strict -c '<最小样例>'

# ④ 运行期行为探针（V-n / V-o）：/tmp/depcheck/vn_vo_probe.py
/tmp/depcheck/bin/python /tmp/depcheck/vn_vo_probe.py
```

> 探针文件位于 `/tmp`，**不是仓库产物**；其行为与输出摘要记录于本文 §3 / §4。

---

## 3. 逐项结果

### 3.1 事实类（V-a ~ V-j、V-m）

| # | 项 | 结论（2026-09-19 实测） |
| --- | --- | --- |
| **V-a** | `hatchling` 许可证 / 版本 / 依赖 | **MIT**；最新 **1.32.3**；纯 Python（`py3-none-any`）；运行期依赖 `packaging>=24.2`、`pathspec>=0.10.1`、`pluggy>=1.0.0`、`tomlkit>=0.11.1`、`trove-classifiers`（`tomli` 仅 py<3.11）⇒ **`D7` 的阻塞已解除**（是否采用仍需所有者拍板） |
| **V-b** | `pydantic` 精确传递依赖 | `annotated-types>=0.6.0`、**`pydantic-core==2.46.5`**、`typing-extensions>=4.14.1`、`typing-inspection>=0.4.2`；`pydantic-core` 是编译扩展（`cp312` wheel：manylinux aarch64 **1.86 MB** / win_amd64 **1.95 MB**） |
| **V-c** | `Rich` 传递依赖 | `markdown-it-py>=2.2.0`、`pygments<3.0.0,>=2.13.0` ⇒ **ADR 原文只提 `pygments`，实际还有 `markdown-it-py`（含 `mdurl`）** |
| **V-d** | `tree-sitter-python` 语法包 | **0.25.0**，**MIT**；`cp310-abi3` wheel 覆盖 **aarch64**（0.11 MB）/ **win_amd64**（0.07 MB）/ **win_arm64**；其 `core` extra 声明 `tree-sitter~=0.24`（`~=` 允许 0.x 内更高版本）⇒ **与主包 0.26.0 兼容，运行期已实测**（见 §3.2） |
| **V-f** | `pytest-xdist` | **3.8.0**，**MIT**；依赖 `execnet>=2.1`、`pytest>=7.0.0` |
| **V-g** | `hypothesis` 许可证 | **MPL-2.0**（6.168.0）——**不是**疑似的"未定"。判断见 §5 第 3 条 |
| **V-h** | `openai` SDK 3.x 依赖 | 3.16.0 / Apache-2.0；依赖 `anyio<5,>=4.10.0`、**`httpx2<3,>=2.7.0`**、`jiter<1,>=0.16.0`、`pydantic<3,>=1.10.13`、`sniffio`、`typing-extensions<5,>=4.14` ⇒ **进一步支持 §5.2.6 的"本期不引入"**（它自带一个 `httpx2`，与"统一薄客户端"的路线冲突） |
| **V-i** | `Typer` 的 vendored Click 许可证 | 0.27.2 wheel 的 `METADATA` 为 **`License-Expression: MIT`**；**确含 vendored `typer/_click/`**，并**随 wheel 分发许可证文件** `typer/_click/LICENSE.txt`（"Copyright 2014 Pallets" + BSD 式条款，即 BSD-3-Clause）⇒ **许可证归属清晰、合规，前提是保留该文件**（wheel 已保留） |
| **V-j** | aarch64 / Termux 可安装性 | **aarch64**：编译类组件 `pydantic-core`（manylinux2014_aarch64）、`tree-sitter`（0.61 MB）、`tree-sitter-python`（abi3）**均有官方 wheel**；其余组件**全部为 `py3-none-any`**（typer / rich / structlog / platformdirs / urllib3 / hatchling / pytest-xdist / annotated-doc / shellingham / markdown-it-py / mdurl）⇒ 平台无关。**Termux/Android**：仍**无官方 wheel**（原风险结论不变），按"目标设备阶段验证"，不在 M0 判据内 |
| **V-m** | `urllib3` 许可证 / 版本 / 依赖 | **MIT**；最新 **2.8.0**（**ADR 原文写 2.7.0，已过期**）；`py3-none-any`；**无必装运行期依赖**（`brotli`/`h2`/`pysocks`/`zstd` 全为 extra）⇒ D3 的"体积与可移植性"依据成立 |

### 3.2 类型兼容（V-k / V-l / V8）

| 组件 | 最小样例 | 结果 |
| --- | --- | --- |
| `pydantic` 2.13.5 | `BaseModel` + `ConfigDict(strict=True, extra="forbid")` + `model_validate_json` + `model_json_schema` | ✅ `mypy --strict` **无 `# type: ignore`**；运行期：合法载荷通过、`{"path": 1}` 与多余字段**均被拒**（各 1 个错误）、schema 可枚举属性（可直接作为 `ToolSpec.parameters_schema`） |
| `structlog` 26.1.0 | 自定义脱敏 processor（`WrappedLogger` / `EventDict` 注解）+ `get_logger().bind()` | ✅ `mypy --strict` 通过（typed 签名无摩擦，ADR 的 V-k 顾虑未出现） |
| `typer` 0.27.2 | `Typer()` + `@app.command()` + 参数与 `bool` flag | ✅ `mypy --strict` 通过 |
| `urllib3` 2.8.0 | `PoolManager().request(...)` | ✅ 通过——**但注意返回类型**：`request()` 返回 `BaseHTTPResponse`，标成 `HTTPResponse` 会报 `assignment` 错误（**不需要 `# type: ignore`，用对类型即可**） |
| `tree-sitter` 0.26.0 + `tree-sitter-python` 0.25.0 | `Parser(Language(language()))` + 解析 + 字段访问 | ✅ `mypy --strict` 通过；运行期解析成功（`module` → `function_definition`，`child_by_field_name("name")` = `f`，起止 `Point` 正确） |

### 3.3 运行期行为（V-n / V-o）

| # | 项 | 结论 |
| --- | --- | --- |
| **V-n** | `urllib3` 是否默认读 `HTTP(S)_PROXY` | **实测：不读。**在 `HTTP_PROXY=http://127.0.0.1:1`（必然失败的地址）下，`urllib.request.getproxies()` 返回该值，而 `urllib3.PoolManager()` 的 `proxy` 为 `None`，**直连本机服务仍返回 200** ⇒ **D3 改选理由第 ③ 条成立**（对"出站默认拒绝 + 白名单放行"的默认值取向更合适） |
| **V-o-1** | 出站超时 | ✅ 可实施（`urllib3.Timeout(connect=..., read=...)`）。**注意抛出的异常类型**：超时以 **`MaxRetryError`** 抛出（`reason=ReadTimeoutError`），不是裸 `ReadTimeoutError`。且 `Retry.DEFAULT = Retry(total=3)` ⇒ **默认会重试 3 次**——安全实现应显式传 `retries=False` 或受限 `Retry`（重试会放大出站次数） |
| **V-o-2** | 限制响应体大小 | ✅ 可实施：`preload_content=False` + 分块读取 + 计数，实测在 1 MiB 应答上**读到 64 KiB 即主动停止**并 `release_conn()` |
| **V-o-3** | SSE 流式 | ✅ 可行（`stream()` 逐块读取），**但必须自带行缓冲**：实测一个 chunk 内可能**跨越两个事件的边界**（首个 chunk 为 `event-0\n\ndata: event-1`）⇒ **不得假设"一个 chunk = 一个事件"** |

### 3.4 隔离环境实装版本（V7 的输入，平台 linux x86_64）

```text
typer 0.27.2   · rich 15.0.0 (+ markdown-it-py 4.2.0, mdurl 0.1.2, pygments 2.21.0,
                            shellingham 1.5.4, annotated-doc 0.0.5)
pydantic 2.13.5 (+ pydantic-core 2.46.5, annotated-types 0.8.0,
                  typing-inspection 0.4.4, typing-extensions 4.16.0)
structlog 26.1.0 · platformdirs 4.11.10 · urllib3 2.8.0
tree-sitter 0.26.0 · tree-sitter-python 0.25.0 · mypy 2.3.1
```

> 取值方式：`python -c "import importlib.metadata as md; print(md.version('<pkg>'))"`（在 `/tmp/depcheck` 内）。

> 安装无解析冲突；**未出现 `click`**（Typer 0.26+ 已内嵌，与 ADR 判断一致）。
> **注意**：这里列出的是"隔间环境实装版本"，`V7`（写入项目后的 `uv tree`）**仍待 `pyproject.toml` 填装后补记**。

---

## 4. 结论

1. **14 项【待核验】全部有结论**（V-e 已随 D3 关闭，不计）；`ADR-0015` §8.2 与 §5.2 的对应单元格已回填。
2. **`D7`（构建后端）的阻塞已解除**：`hatchling` 为 MIT、纯 Python、依赖均为成熟纯 Python 包
   ⇒ 建议采用 `hatchling`；**是否决定仍待所有者拍板**（改 `pyproject.toml` 的构建后端属决策级变更）。
3. **`hypothesis` 为 MPL-2.0**（文件级 copyleft）：本项目**只在测试期使用、不随产物分发**，
   与该许可证兼容；但它**仍未纳入**（`G2` 原为"备选、非本期必须"），若纳入需在 ADR 登记许可证。
4. **三个数字被更正**：`urllib3` 2.7.0 → **2.8.0**；`platformdirs` 4.11.9 → **4.11.10**；
   `Rich` 传递依赖补 `markdown-it-py`。`pydantic-core` 为 **2.46.5**（由 pydantic 钉定，非独立选型）。
5. **"已核验"的边界**：aarch64 只核到 **"官方 wheel 存在"**，**未在 aarch64 真机安装**；
   Termux 无官方 wheel（原结论不变）。⇒ 表述不得超出"wheel 存在 + 本平台 x86_64 实装通过"。
6. **两个实现注意点**（进 `src/` 时必读）：出站客户端**显式关掉默认重试**（`Retry(total=3)`）；
   SSE 解析**自带行缓冲**（chunk 边界 ≠ 事件边界）。
