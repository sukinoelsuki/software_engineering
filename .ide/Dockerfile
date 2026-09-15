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
# 【构建耗时】
#   首次构建约 5~15 分钟（主要是 llama.cpp 源码编译）。镜像会被推送到制品库，
#   之后启动开发环境直接复用，无需重建。
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
#    扩展含：CodeBuddy、Python 开发、Docker、Git、YAML/TOML、Markdown/Mermaid、TS 备用。
#
#    扩展源说明：code-server 默认使用 Open VSX（微软 Marketplace 的使用条款禁止
#    非微软产品访问，故不可用）。下列扩展均已逐一核对存在于 Open VSX。
#
#    V-7 已结案（2026-09-15 实测，证据见 docs/devlog/0007 §4.3）：
#      - ms-python.vscode-pylance **不在 Open VSX**（API 返回 404），
#        环境内 `code-server --list-extensions` 也确认它确实静默缺失 ——
#        构建不报错，只是少一个扩展；
#      - 故改用 ms-pyright.pyright（MIT、Open VSX 返回 200）承担编辑器侧类型提示。
#        注意：类型检查的**门禁**由 mypy --strict 负责，编辑器扩展只影响交互体验，
#        换掉它不改变任何验收标准。
#
#    安装失败默认不阻断构建（避免构建期网络抖动炸掉整个镜像），
#    代价是"缺失了也不报错"。因此**任何扩展清单改动都必须在环境内**
#    用 `code-server --list-extensions` 与清单做集合比对（含被依赖带入的扩展），
#    不能只看构建日志。CodeBuddy 扩展属于硬需求，已在下文自检阶段做显式校验。
#
#    关于容器相关扩展：
#      安装 ms-azuretools.vscode-docker（镜像/容器/卷管理、Dockerfile 与
#      compose 支持）。未安装 ms-vscode-remote.remote-containers —— 该扩展
#      面向"本机 Docker Desktop 中开发 devcontainer"，在云端 WebIDE 内不可用。
# ---------------------------------------------------------------------------
RUN set -eux; \
    curl -fsSL https://code-server.dev/install.sh | sh

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
        bierner.markdown-mermaid \
        dbaeumer.vscode-eslint \
        esbenp.prettier-vscode \
        usernamehw.errorlens \
    ; do \
        echo "==> 安装扩展：${ext}"; \
        code-server --install-extension "${ext}" \
            || echo "[warn] 扩展安装失败：${ext}（不阻断构建）"; \
    done

# ---------------------------------------------------------------------------
# 7. llama.cpp（CPU 版，源码构建）
#    本项目的主推理后端。构建参数：
#      GGML_NATIVE=OFF —— 不在镜像构建机上做本机指令优化，保证镜像在不同
#                          CNB 构建节点上都能运行（可移植性优先）。
#      GGML_CUDA=OFF    —— 本项目不使用 GPU（CNB 的 GPU 无免费额度）。
#      LLAMA_CURL=ON    —— 支持从 URL 直接拉取模型。
#
#    版本固定（V-6 已处理，2026-09-15）：**不再跟随 master**。
#    固定到 commit 69eb250670f471586fcec69caacd3c014aefb185（短 SHA 69eb250，
#    提交日期 2026-09-15，PGP 已签名）—— 这是 W1 实测数据所绑定的构建，
#    性能结论（见 docs/research/2026-09-15-on-device-model-validation.md）
#    只在同版本下可比，因此必须钉住。
#    浅克隆无法直接检出任意 commit，故用 init + fetch <完整 SHA> + checkout，
#    并即时校验 HEAD 确实是目标提交（防止静默取到别的版本）。
#    升级方式：改下面的 SHA → 销毁开发环境 → 重新启动（触发重建），
#    并在 docs/devlog 中记录"旧版本 → 新版本"的对照数据。
# ---------------------------------------------------------------------------
RUN set -eux; \
    git init -q /opt/llama.cpp; \
    git -C /opt/llama.cpp remote add origin https://github.com/ggml-org/llama.cpp; \
    git -C /opt/llama.cpp fetch -q --depth 1 origin 69eb250670f471586fcec69caacd3c014aefb185; \
    git -C /opt/llama.cpp checkout -q --detach FETCH_HEAD; \
    test "$$(git -C /opt/llama.cpp rev-parse HEAD)" = "69eb250670f471586fcec69caacd3c014aefb185"; \
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
#    实测（2026-09-15，见 docs/devlog/0007 V-4）：**只有 Machine 那一份真正生效**。
#      - ~/.local/share/code-server/Machine/settings.json  → 保留本文件 ✅
#      - ~/.local/share/code-server/User/settings.json     → 被 CNB 平台启动时
#        写入的默认设置覆盖（1017 B，含 cnb-welcome.locale 等平台自有配置）
#      - ~/.vscode-server/data/Machine/settings.json       → 同样被覆盖
#      - ~/.vscode-server/data/User/settings.json          → 平台在运行期重建该目录
#    结论：不要指望 User 与 Remote-SSH 这几条路径生效（它们是冗余保险，
#    保留成本为零，但如果某天想让某项设置生效，只能改 Machine 那一份，
#    并且必须**销毁重建环境**才会重新写入）。
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
# 自检：构建时验证关键组件是否可用（失败即中断构建，避免交付一个坏环境）
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
    code-server --list-extensions | grep -qix 'Tencent-Cloud.coding-copilot'
