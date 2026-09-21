# ============================================================================
# CNB 云原生开发环境（WebIDE / VS Code Remote-SSH）
#
# 【平台约定，不可更改】
#   .ide/Dockerfile      —— 开发环境镜像的构建文件（本文件）
#   .ide/settings.json   —— VS Code 设置，必须由本文件 COPY 进镜像
#   同时必须在 .cnb.yml 的 vscode 事件中用 build.by 声明 settings.json，
#   否则构建会报错（构建期依赖必须显式声明）。
#
# 【基础镜像选择理由】
#   python:3.12-bookworm = Debian 12 + Python 3.12，与 CNB 默认环境同为 Debian 12；
#   该镜像基于 buildpack-deps，自带 gcc/make 等构建工具，便于编译需要本地构建的
#   Python 包（tree-sitter、向量库扩展等）。
#
# 【构建耗时与缓存（2026-09-21 实测更正）】
#   **完整构建约 20 分钟**（对照 `cnb-j4r-1k2qgnopp`，2026-09-18：全 26 步 11m12s，
#   其中 11.8 GB 模型下载约 5 分钟、llama.cpp 源码编译约 2 分钟）。
#   ⚠️ **构建期没有层缓存**：平台实际下发的是 `docker build --no-cache …`
#   （两次日志逐字核对：`cnb-j4r-1k2qgnopp` 与 `cnb-rjo-1k3263rlg` 均带 `--no-cache`）
#   ⇒ 下文各阶段"靠层缓存复用 GB 级资产"的排序理由**只剩可读性价值**，
#   **不是省时保证**：改本文件任何一行，都要重下 11.8 GB、重编一次 llama.cpp。
#   真正省时的只有一条：**上一次构建成功**后，镜像会被推到制品库的
#   `…/dockerfile-caches:<build.by 输入哈希>`；输入哈希不变时平台直接复用、整段跳过
#   （实测 `cnb-738-1k31psn59` 仅 41 s）。改本文件或改 `.cnb.yml` 的 `build.by`
#   都会换掉这个哈希 ⇒ 又是一次完整重建。
#   ⚠️ **构建期网络不可靠，所有网络访问都必须有界**：平台规则是"Job 连续 10 分钟
#   无任何输出即超时并被 SIGKILL"（详见 `.ide/fetch-assets.sh` 的
#   【为什么 fetch 必须有界】）——一次连接 stall 就足以让 20 分钟的构建白跑。
#   修改本文件后需：先销毁当前开发环境 → 再重新启动。
# ============================================================================

FROM python:3.12-bookworm

# ---------------------------------------------------------------------------
# 环境变量：字符集（中文输入）、时区、Python 行为
# ---------------------------------------------------------------------------
ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LANGUAGE=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy

USER root

# ---------------------------------------------------------------------------
# 1. 系统基础工具
#    说明：使用腾讯云镜像源加速 apt（与 CNB 官方示例一致做法）。
# ---------------------------------------------------------------------------
RUN set -eux; \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i \
            -e 's|deb.debian.org|mirrors.cloud.tencent.com|g' \
            -e 's|security.debian.org|mirrors.cloud.tencent.com|g' \
            /etc/apt/sources.list.d/debian.sources; \
    fi; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        ca-certificates curl wget gnupg \
        git git-lfs openssh-server \
        build-essential cmake ninja-build pkg-config \
        libcurl4-openssl-dev \
        jq tree ripgrep fd-find less vim tmux htop \
        unzip zip xz-utils sqlite3 \
        zsh; \
    rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# 2. 沙箱与隔离组件
#    本项目需要"无需 KVM 的用户态隔离"来执行 AI 生成的命令：
#      - bubblewrap：非特权用户命名空间沙箱（Flatpak 同款），无需 root
#      - firejail   ：另一套轻量沙箱方案，作为备选
#    以下依赖用于从源码构建 nsjail（能力更强的沙箱，后续按需构建）。
# ---------------------------------------------------------------------------
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        bubblewrap \
        firejail \
        libprotobuf-dev libnl-3-dev libnl-route-3-dev libcap-dev \
        protobuf-compiler bison flex; \
    rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# 3. Node.js 与 pnpm（备用工具链，非主语言）
#    用途：MCP 官方 server / inspector、promptfoo（红队测试）、以及将来若需要
#          用 TypeScript 写 Web UI 或 MCP server。
#    主语言仍为 Python（见 docs/adr/0005），此处仅为避免"要用时还得重建镜像"。
# ---------------------------------------------------------------------------
RUN set -eux; \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -; \
    apt-get install -y --no-install-recommends nodejs; \
    npm install -g pnpm@9; \
    rm -rf /var/lib/apt/lists/* /root/.npm

# ---------------------------------------------------------------------------
# 4. Python 包管理：uv
#    通过 pip 安装而非 curl|sh，减少一次远程脚本执行（供应链更可控）。
# ---------------------------------------------------------------------------
RUN set -eux; \
    pip install --no-cache-dir -U pip uv; \
    uv --version; \
    python -V

# ---------------------------------------------------------------------------
# 5. CodeBuddy（腾讯 AI 编码助手）—— 两种形态，缺一不可
#
#   形态 A：CLI（npm 全局包 @tencent-ai/codebuddy-code，提供 codebuddy 命令）
#     这是 CNB【CodeBuddy Web】入口的**前置条件**：镜像内没有 codebuddy 或
#     版本 < 2.137.0 时，云开发入口页会**直接隐藏该入口**（不报错，静默消失）。
#     出处：https://docs.cnb.build/zh/workspaces/codebuddy-web.html
#   形态 B：VS Code 扩展（Tencent-Cloud.coding-copilot）
#     提供 WebIDE 内的代码补全与对话面板；该扩展已上架 Open VSX
#     （code-server 的默认扩展源即 Open VSX），可直接按 ID 安装。
#     出处：https://open-vsx.org/api/Tencent-Cloud/coding-copilot
#
#   版本固定为 2.151.0（符合本项目的依赖锁定要求，且 >= CNB 要求的 2.137.0）。
#   升级方式：改此行版本号 → 销毁开发环境 → 重新启动（触发镜像重建）。
#   体积提示：该包解包后约 166 MB（含按平台分发的可选依赖）。
# ---------------------------------------------------------------------------
RUN set -eux; \
    npm install -g @tencent-ai/codebuddy-code@2.151.0; \
    codebuddy --version; \
    rm -rf /root/.npm

# ---------------------------------------------------------------------------
# 6. code-server 与 VS Code 扩展
#    安装 code-server 后，CNB 将以【单容器模式】启动开发环境。
#    扩展含：CodeBuddy、Python 开发、Docker、Git、YAML/TOML、Markdown、TS 备用。
#
#    ⚠️ **不要安装 bierner.markdown-mermaid（2026-09-21 实测移除）**：
#      本镜像的 code-server 1.137.0 已**内置** markdown 的 Mermaid 支持
#      （内建扩展 mermaid-markdown-features，engines `^1.104.0`），
#      第三方扩展与它声明**完全相同的 8 个配置键**（markdown-mermaid.lightModeTheme /
#      darkModeTheme / languages / maxTextSize / mouseNavigation.enabled / controls.show /
#      resizable / maxHeight），并同时注入 `markdown.previewScripts` 与
#      `markdown.markdownItPlugins` ⇒ 客户端刷 8 条
#      "Cannot register 'markdown-mermaid.*'. This property is already registered"，
#      且两个渲染器争抢同一个预览注入点，表现为**预览时好时坏**。
#      卸载该第三方扩展后，预览恢复稳定（所有者 2026-09-21 在浏览器内实测确认）。
#      参考：docs/engineering/diagram-conventions.md 的"预览与实际查看"一节。
#
#    扩展源说明：code-server 默认使用 Open VSX（微软 Marketplace 的使用条款禁止
#    非微软产品访问，故不可用）。下列扩展均已逐一核对存在于 Open VSX。
#    V-7（2026-09-15 已确认）：ms-python.vscode-pylance 在 Open VSX 上查无此扩展
#    （API 返回 404），构建期静默安装失败，环境内 code-server --list-extensions
#    证实缺失。已改用 ms-pyright.pyright（MIT、Open VSX 有售，latest 1.1.402）
#    作为类型检查与语言服务引擎。见 docs/devlog/0007。
#    安装失败默认不阻断构建（避免构建期网络抖动炸掉整个镜像），
#    但 CodeBuddy 扩展属于本项目的硬需求，已在下文自检阶段做显式校验。
#
#    关于容器相关扩展：
#      安装 ms-azuretools.vscode-docker（镜像/容器/卷管理、Dockerfile 与
#      compose 支持）。未安装 ms-vscode-remote.remote-containers —— 该扩展
#      面向"本机 Docker Desktop 中开发 devcontainer"，在云端 WebIDE 内不可用。
#
#    ⚠️ **装完必须显式卸载 ms-python.vscode-python-envs（2026-09-21 实测结案）**：
#      它**不**直接列在上面的清单里，而是 **ms-python.python 的 extensionPack 成员**
#      （`--install-extension ms-python.python` 会把它连带装上）。
#      它依赖一个平台专用的 `pet` 二进制（`python-env-tools/bin/pet`），而 Open VSX 的
#      **universal** 包**不含该目录**（实测：扩展根目录下没有 `python-env-tools`）
#      ⇒ 每次激活都走 "JSON CLI fallback"、spawn 失败，再触发
#      `Request 'configure' timed out after 30000ms`（每次卡 30 秒）。
#      实测代价：单次会话 **119 次** spawn 失败 / **86 次** 30 秒超时
#      （旧计数 330/98 含"我的命令被写进同一日志"造成的回声灌水，已更正）。
#      它提供的是"Python 环境管理"视图与命令，本项目**不用**（解释器固定 `.venv`、
#      测试走 CLI、类型检查用 pyright、格式化用 ruff）⇒ **卸载零损失**。
#      验证：卸载 + reload 后，新扩展主机里**连该扩展的日志文件都不再产生**，
#      且无任何新增 pet 报错（详见 docs/devlog/0020 §3.12）。
#      回滚：`code-server --install-extension ms-python.vscode-python-envs`。
#
#    ⚠️ **不要写成 `curl -fsSL … | sh`（2026-09-21 又一次真实构建失败换来的）**：
#      管道既不传播 curl 的退出码、这里也没有 `pipefail` ⇒ **curl 失败时 `sh` 收到空输入、
#      以 0 退出、整步被判为成功**。实测后果（构建 `cnb-rjo-1k3263rlg`）：
#      `curl: (35) OpenSSL SSL_connect: SSL_ERROR_SYSCALL in connection to
#      code-server.dev:443`（第 59.92 s）⇒ **code-server 根本没装上**，
#      紧接着第 12 步 16 个扩展全部 `code-server: not found`，又各自被
#      `|| echo "[warn] …"` 吞掉 ⇒ 一路"绿"到第 14 阶段自检才炸，
#      而那时已经白付了约 9 分钟（含 11.8 GB 模型下载）。
#      现改为**先落盘 → 再执行 → 显式断言 `code-server --version`**：
#      装不上就**当场中断构建**（fail-secure）。与第 14 阶段自检**不重复**——
#      这里挡的是"花 20 分钟才发现"，那里挡的是"静默漏装"。
# ---------------------------------------------------------------------------
RUN set -eux; \
    curl -fsSL --retry 3 --retry-delay 2 --connect-timeout 30 \
        --output /tmp/install-code-server.sh https://code-server.dev/install.sh; \
    sh /tmp/install-code-server.sh; \
    rm -f /tmp/install-code-server.sh; \
    code-server --version

RUN set -eu; \
    for ext in \
        cnbcool.cnb-welcome \
        ms-python.python \
        ms-pyright.pyright \
        ms-python.debugpy \
        charliermarsh.ruff \
        redhat.vscode-yaml \
        tamasfe.even-better-toml \
        ms-azuretools.vscode-docker \
        Tencent-Cloud.coding-copilot \
        mhutchie.git-graph \
        editorconfig.editorconfig \
        davidanson.vscode-markdownlint \
        yzhang.markdown-all-in-one \
        dbaeumer.vscode-eslint \
        esbenp.prettier-vscode \
        usernamehw.errorlens \
    ; do \
        echo "==> 安装扩展：${ext}"; \
        code-server --install-extension "${ext}" \
            || echo "[warn] 扩展安装失败：${ext}（不阻断构建）"; \
    done; \
    code-server --uninstall-extension ms-python.vscode-python-envs \
        || echo "[warn] 卸载 vscode-python-envs 失败 —— 见文件头注释：它缺 pet 二进制，会每轮卡 30 秒"

# ---------------------------------------------------------------------------
# 7. llama.cpp（CPU 版，源码构建）
#    本项目的主推理后端。构建参数：
#      GGML_NATIVE=OFF —— 不在镜像构建机上做本机指令优化，保证镜像在不同
#                          CNB 构建节点上都能运行（可移植性优先）。
#      GGML_CUDA=OFF    —— 本项目不使用 GPU（CNB 的 GPU 无免费额度）。
#      LLAMA_CURL=ON    —— 支持从 URL 直接拉取模型。
#
#    版本固定（V-6，2026-09-15 结案）：**不再跟随 master**。
#    固定到 commit 69eb250670f471586fcec69caacd3c014aefb185（短 SHA 69eb250）——
#    这是 W1 与三档（S/M/L）实测数据所绑定的构建；性能结论只在同版本下可比，
#    因此必须钉住。浅克隆无法直接检出任意 commit，故用 init + fetch <完整 SHA>
#    + checkout，并即时断言 HEAD 等于目标提交，防止静默取到别的版本。
#    升级方式：改下面的 SHA → 销毁开发环境 → 重新启动（触发重建），
#    并在 docs/devlog 中记录「旧版本 → 新版本」的对照数据。
#
#    注：本段曾在「合并 develop 解决冲突」时被 develop 一侧覆盖而丢失（彼时
#    develop 仍跟随 master），本次为回收——可复现性是性能主线的前提，不是可选项。
#
#    ⚠️ 断言里的命令替换必须写成 `$(...)`，不能写成 `$$(...)`
#    （2026-09-16 构建失败根因）：RUN 由 shell 执行，`$$` 展开为 **shell PID**，
#    命令替换不会执行，字符串变成 "12345(git -C ... rev-parse HEAD)"，
#    断言恒定失败 → `set -e` 下第 7 步退出码 1。
#    `$$` 只在 docker-compose.yml 这类"先做变量替换"的文件里才需要转义。
# ---------------------------------------------------------------------------
RUN set -eux; \
    git init -q /opt/llama.cpp; \
    git -C /opt/llama.cpp remote add origin https://github.com/ggml-org/llama.cpp; \
    git -C /opt/llama.cpp fetch -q --depth 1 origin 69eb250670f471586fcec69caacd3c014aefb185; \
    git -C /opt/llama.cpp checkout -q --detach FETCH_HEAD; \
    test "$(git -C /opt/llama.cpp rev-parse HEAD)" = "69eb250670f471586fcec69caacd3c014aefb185"; \
    cmake -S /opt/llama.cpp -B /opt/llama.cpp/build \
        -DCMAKE_BUILD_TYPE=Release \
        -DGGML_NATIVE=OFF \
        -DGGML_CUDA=OFF \
        -DLLAMA_CURL=ON \
        -DLLAMA_BUILD_TESTS=OFF \
        -DLLAMA_BUILD_EXAMPLES=OFF; \
    cmake --build /opt/llama.cpp/build --config Release -j"$(nproc)"; \
    rm -rf /opt/llama.cpp/.git

ENV PATH="/opt/llama.cpp/build/bin:${PATH}"

# ---------------------------------------------------------------------------
# 8. 复制 VS Code 设置
#    写入两个客户端、两种作用域，确保无论通过 WebIDE 还是 Remote-SSH 进入、
#    无论走 User 还是 Machine 作用域，设置都能生效：
#      - code-server（WebIDE）：~/.local/share/code-server/{User,Machine}
#      - VS Code Desktop（Remote-SSH）：~/.vscode-server/data/{User,Machine}
#    注：部分设置项在 Machine 作用域不生效，故同时写入 User 作用域。
#
#    V-4（2026-09-15 实测结论）：四条 COPY 中**只有 code-server 的 Machine 作用域
#    真实生效**（该路径与 CNB 官方示例一致）。其余三条在运行期会被平台自身写入的
#    文件（内容为 .cnb.yml 等 YAML schema）取代，属"写入无效但无害"——
#    平台写入发生在本 COPY 之后，因此不会互相破坏。保留这四条的理由是：
#    在不同平台版本或 Remote-SSH 客户端下生效范围可能不同，删掉的收益小于风险；
#    但**不要**据此认为"只写 Machine 就够"或"四条都一定生效"。
# ---------------------------------------------------------------------------
COPY .ide/settings.json /root/.local/share/code-server/Machine/settings.json
COPY .ide/settings.json /root/.local/share/code-server/User/settings.json
COPY .ide/settings.json /root/.vscode-server/data/Machine/settings.json
COPY .ide/settings.json /root/.vscode-server/data/User/settings.json

# ---------------------------------------------------------------------------
# 9. Git 基础配置与工作目录
#    用户身份（user.name / user.email）不在此写死，由 CNB 注入或本地配置。
# ---------------------------------------------------------------------------
RUN set -eux; \
    git config --system init.defaultBranch main; \
    git config --system core.autocrlf input; \
    git lfs install --system

WORKDIR /workspace

# ---------------------------------------------------------------------------
# 10. 预置端侧模型（两个 4B 级，用于横向能力对比）
#
#     位置：/opt/models —— **在 /workspace 之外**。理由：
#       ① GB 级二进制不得进入版本管理；
#       ② 工作区可能被重新克隆/清空，模型不应随之丢失。
#
#     清单见 .ide/assets/models.txt（sha256、体积、来源 URL 逐项记录）。
#     校验和不符即**中断构建**——即 fail-secure：宁可构建失败，
#     也不交付一个装了错资产的镜像。
#
#     ⚠️ 层序很重要：Docker 层缓存按顺序失效，把 GB 级下载放在靠后位置，
#     改文档 / 改设置都不会触发重新下载；模型清单若未变，重建时直接复用缓存。
# ---------------------------------------------------------------------------
#     ⚠️ 布局约束（本条是被一次真实构建失败逼出来的，勿改回平铺）：
#     脚本用"自身所在目录 + /assets"解析清单（见 fetch-assets.sh 的 ASSETS_DIR），
#     因此必须是 脚本 → /tmp/ide/、清单 → /tmp/ide/assets/。
#     早期版本把两者一起 COPY 进 /tmp/assets/，脚本于是去找 /tmp/assets/assets/，
#     构建在第 17 步以"找不到清单文件"失败。镜像布局必须复刻仓库里的 .ide/ 结构。
# ---------------------------------------------------------------------------
COPY .ide/fetch-assets.sh /tmp/ide/
COPY .ide/assets/models.txt /tmp/ide/assets/
RUN set -eux; \
    bash /tmp/ide/fetch-assets.sh models /opt/models

# ---------------------------------------------------------------------------
# 11. 预置开源 Harness 参考资料（仅离线查阅，不参与构建与运行）
#
#     位置：/opt/references/harness
#     清单见 .ide/assets/references.txt：全部固定到具体**提交 SHA**（不是分支或标签），
#     许可证均为 MIT / Apache-2.0，实测浅克隆合计约 447 MB。
#     **端侧实现为重点参考**（openharness / agentcpm / tinyagent），
#     其余为大型实现对照（SWE-agent / OpenHands / aider / qwen-agent / ollama）。
# ---------------------------------------------------------------------------
COPY .ide/assets/references.txt /tmp/ide/assets/
RUN set -eux; \
    bash /tmp/ide/fetch-assets.sh references /opt/references/harness

# ---------------------------------------------------------------------------
# 12. 预置基准任务集（离线可跑、客观判定）
#
#     位置：/opt/benchmarks
#     清单见 .ide/assets/benchmarks.txt：3 个数据集、合计约 23 MB
#     （HumanEval+ / MBPP+ / BigCodeBench，均为 Apache-2.0）。
#
#     为什么必须预置进镜像：**产品定位是离线低资源环境，评测也必须在无网络下可跑**，
#     否则测出来的能力与真实使用场景不符。
#
#     取数方式有两类，都带 sha256 校验、失败即中断构建：
#       file     —— 直接下载官方文件（HumanEval+ 的 JSONL）
#       hf-rows  —— 经 HF datasets-server 的 rows API 取全部行落为 JSONL
#                   （HF 数据集多为 parquet，读它需引入 pyarrow；
#                     rows API 返回 JSON，用标准库即可，**不引入新依赖**）
#
#     选型依据见 docs/adr/0012-benchmark-task-set-selection.md
#     （含"只取任务定义与判定器、不连带采用它们的 agent harness"这一关键约定）。
# ---------------------------------------------------------------------------
COPY .ide/assets/benchmarks.txt /tmp/ide/assets/
RUN set -eux; \
    bash /tmp/ide/fetch-assets.sh benchmarks /opt/benchmarks

# ---------------------------------------------------------------------------
# 13. CNB 平台能力：cnb-cli + 官方 Skills
#
#     决策依据：docs/adr/0016-cnb-platform-integration-and-remote-write-authorization.md
#               （方案 B：接入官方 Skills 与 cnb-cli，远端写入按「可回退性」分级）
#     目录依据：docs/adr/0017-project-level-agent-skills.md
#
#     ⚠️ **这一段的层序是刻意的，不要往前提**：
#       第 10~12 阶段是 GB 级资产（模型 / 参考仓库 / 基准集），重建时靠层缓存复用。
#       本段的 COPY 若放在它们**之前**，就会使那些层失效——每次改一行 Skill 清单
#       都要多花约 20~25 分钟重新下载（该代价已由第 10 阶段的注释实测记录）。
#       放在第 12 阶段**之后** ⇒ 前序层输入不变、命中缓存，重建只跑本段 + 自检两层。
#
#     ⚠️ 也**不要**把本段逻辑并进 .ide/fetch-assets.sh：那个脚本的位置更靠前
#       （它服务第 10~12 阶段），理由完全一致。详见 .ide/install-cnb-skills.sh 文件头。
#
#     **装什么、钉到哪个版本 / commit、为什么只装 5 个** —— 全部只在
#     `.ide/assets/cnb-skills.txt` 里写一份（**单一真源**），本段不重复列举。
#
#     落点（两层结构，见该清单文件头）：~/.agents/skills/<name>/ 为 canonical，
#     ~/.codebuddy/skills/<name> 为指向它的符号链接（CodeBuddy 读后者）。
#
#     校验：脚本内建 fail-secure（版本 / 摘要不符即中断构建），
#     并在第 14 阶段再断言一次——凡"失败不阻断构建"的环节都必须显式校验一次。
#     升级方式：改 .ide/assets/cnb-skills.txt → 销毁开发环境 → 重新启动（触发重建），
#     并在 docs/devlog 中记录「旧 → 新」的对照。
# ---------------------------------------------------------------------------
COPY .ide/install-cnb-skills.sh /tmp/ide/
COPY .ide/assets/cnb-skills.txt /tmp/ide/assets/
RUN set -eux; \
    bash /tmp/ide/install-cnb-skills.sh install

# ---------------------------------------------------------------------------
# 14. 自检：构建时验证关键组件与预置资产（失败即中断构建，避免交付坏环境）
#
#     原则：凡是"失败不阻断构建"的环节，都必须在这里显式校验一次——
#     镜像构建期的静默失败已经出现过两次：CodeBuddy 漏装（0006 §2.4.1）、
#     扩展在 Open VSX 上 404 导致静默不装（0007 §2.5 V-7）。
#
#     ⚠️ 缓存代价（实测结论）：脚本的 COPY 位于模型下载层**之前**，
#     因此**改动脚本本身也会使 GB 级模型层失效并重新下载**（约 20~25 分钟）。
#     故：脚本改动应与清单改动**合并到同一次重建**，不要分多次改。
# ---------------------------------------------------------------------------
RUN set -eux; \
    python -V; \
    uv --version; \
    node -v; \
    pnpm --version; \
    command -v bwrap; \
    command -v git-lfs; \
    command -v cmake; \
    command -v llama-server; \
    codebuddy --version; \
    code-server --version; \
    code-server --list-extensions; \
    code-server --list-extensions | grep -qix 'Tencent-Cloud.coding-copilot'; \
    code-server --list-extensions | grep -qix 'ms-pyright.pyright'; \
    cnb --version; \
    bash /tmp/ide/install-cnb-skills.sh verify; \
    bash /tmp/ide/fetch-assets.sh verify /opt/models /opt/benchmarks; \
    test -d /opt/references/harness; \
    test -n "$(find /opt/references/harness -mindepth 1 -maxdepth 1 -type d -print -quit)"; \
    du -sh /opt/models /opt/benchmarks /opt/references/harness
