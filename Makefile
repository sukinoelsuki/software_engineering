# ============================================================================
# 开发任务入口（单一事实来源：所有检查都通过 make 暴露，便于 CI 复用）
#
# 用法：make help
# 约定：CI 中执行的命令必须与本文件保持一致，禁止 CI 里另写一套。
# ============================================================================

SHELL := /bin/bash
.DEFAULT_GOAL := help

UV      ?= uv
PYTHON  ?= python3
SRC     ?= src
TESTS   ?= tests
PKG     ?= agent_sec_perf

# 是否在测试中启用并行执行
PYTEST_XDIST ?= -n auto

# ---------------------------------------------------------------------------
# 本地 git 钩子层：**显式开关**，不再从 `$CI` 推断
#
# 为什么改（2026-09-18 实测，一致性报告 A-11 / 新增 A-15）：
#   `$CI` 的**存在性**对"云开发工作区"与"CI 流水线"是同一个值，语义却不同——
#   本工作区实测 `CI` 未设置，而更早的工作区 `CI=true` 只是"云环境"（不是流水线）。
#   用它当判据 ⇒ 钩子被静默跳过 ⇒ 文档声称的本地防线（detect-private-key、
#   commit-msg 校验）**从未运行**且长期无人察觉。凡"声称已生效的缓解措施"
#   必须能被实测，故这里改为**谁需要关闭，谁自己写 LOCAL_HOOKS=0**。
#
#   语义：LOCAL_HOOKS=1（默认）⇒ 安装钩子，并**断言它真的存在**（hooks-check）；
#        LOCAL_HOOKS=0        ⇒ 不安装、也不断言（见 .cnb.yml：CI 流水线用不到本地钩子）。
# ---------------------------------------------------------------------------
LOCAL_HOOKS ?= 1

.PHONY: help setup hooks-check lint format format-check typecheck test test-cov test-security \
        security security-bandit security-secrets security-audit commit-check changelog \
        release bump \
        check branch-status clean distclean quota \
        bench bench-round bench-publish bench-verify-assets

# ---------------------------------------------------------------------------
help: ## 显示所有可用目标
	@echo "可用目标："
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------------------------------------------------------------------------
setup: ## 安装开发环境（虚拟环境 + 依赖 + git hooks）
	@echo ">> 安装 uv（若缺失）"
	@command -v $(UV) >/dev/null 2>&1 || pip install -q uv
	@echo ">> 同步依赖"
	$(UV) sync --extra dev --extra security
	@if [ "$(LOCAL_HOOKS)" = "0" ]; then \
		echo ">> LOCAL_HOOKS=0：跳过 pre-commit 钩子安装（并跳过钩子存在性断言）"; \
	else \
		echo ">> 安装 pre-commit 钩子"; \
		$(UV) run pre-commit install --hook-type pre-commit --hook-type commit-msg; \
		echo ">> 复验钩子确实已安装"; \
		$(MAKE) --no-print-directory hooks-check; \
	fi
	@echo ">> 完成。运行 make check 进行首次自检。"

hooks-check: ## 断言本地 git 钩子**真的已安装**（fail-secure；CI 以 LOCAL_HOOKS=0 显式跳过）
	@if [ "$(LOCAL_HOOKS)" = "0" ]; then \
		echo ">> hooks-check：LOCAL_HOOKS=0（CI 流水线不需要本地钩子），跳过"; \
	else \
		bash scripts/check-local-hooks.sh; \
	fi

# ---------------------------------------------------------------------------
# 代码质量
# ---------------------------------------------------------------------------
lint: ## 静态检查（ruff check）
	$(UV) run ruff check .

format: ## 自动格式化（ruff format + ruff check --fix）
	$(UV) run ruff format .
	$(UV) run ruff check --fix .

format-check: ## 检查格式是否符合规范（CI 使用，不修改文件）
	$(UV) run ruff format --check .
	$(UV) run ruff check .

typecheck: ## 类型检查（mypy）
	$(UV) run mypy

# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------
test: ## 运行测试（快速回归，排除 benchmark 与 slow）
	$(UV) run pytest $(PYTEST_XDIST) -m "not benchmark and not slow"

test-cov: ## 运行测试并生成覆盖率报告
	$(UV) run pytest -m "not benchmark and not slow" --cov --cov-report=term-missing --cov-report=xml

test-security: ## 仅运行安全与对抗性测试（**零用例视为失败**，fail-secure）
	@$(UV) run pytest -m security; \
	code=$$?; \
	if [ $$code -eq 5 ]; then \
		echo ">> 失败：没有任何 security 标记的用例。"; \
		echo ">> 安全测试层是项目基线的一部分（tests/security/），零用例说明测试层缺失或标记丢失。"; \
		echo ">> fail-secure：本目标不得在'没有用例'时静默返回 0（2026-09-18 修正）。"; \
		exit 1; \
	fi; \
	exit $$code

# ---------------------------------------------------------------------------
# 安全
# ---------------------------------------------------------------------------
security: security-bandit security-secrets security-audit ## 运行全部安全检查

security-bandit: ## 静态安全扫描
	$(UV) run bandit -q -r $(SRC)

security-secrets: ## 密钥泄漏扫描（复用 pre-commit 的 detect-private-key，避免两套口径）
	$(UV) run pre-commit run detect-private-key --all-files

security-audit: ## 依赖漏洞审计
	$(UV) run pip-audit

# ---------------------------------------------------------------------------
# 提交与版本
# ---------------------------------------------------------------------------
commit-check: ## 校验最近一条提交信息是否符合 Conventional Commits
	$(UV) run cz check --rev-range HEAD~1..HEAD

changelog: ## 根据提交历史生成 CHANGELOG（写入文件）
	$(UV) run cz changelog --incremental

bump: ## 【已废除】版本号不再由提交历史推导 —— 见 git-workflow.md §5
	@echo ">> 拒绝执行：版本号只表达「到达了哪个里程碑」，只能取 pyproject.toml 的"
	@echo ">> [tool.lowspec.releases] 台账中列出的值，由 \`make release VERSION=x.y.z\` 落定。"
	@echo ">> 用提交历史推导会绕过里程碑判据（一致性报告 A-17 / ADR-0019）。"
	@echo ">> 只想看推导结果、不写入：uv run cz bump --dry-run"
	@exit 1

release: ## 落定版本号到里程碑（VERSION=x.y.z；含门禁，失败即回滚）
	@test -n "$(VERSION)" || { echo "用法：make release VERSION=x.y.z（例如 0.0.1）"; exit 1; }
	@bash scripts/release.sh $(VERSION)

# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------
# hooks-check 放在最前：本地防线缺失时应**立刻**失败并给出修复命令，
# 而不是等 100+ 个测试跑完再说（见 hooks-check 目标与 scripts/check-local-hooks.sh）。
check: hooks-check format-check lint typecheck test security ## 完整自检（提交 PR 前必须全绿）

# ---------------------------------------------------------------------------
# 额度核对（只读；数据来自平台 API）
#
# 为什么做成目标：ADR-0024 要求"**月末用平台数据核对一次**"并把数字写进 devlog。
# 手敲四条命令容易漏、更容易凭印象报数字；做成目标后"读数"是唯一动作。
# ⚠️ 它**故意不放进 `check`**：需要 `cnb` CLI 与组织级读权限（`group-resource:r`），
#    在缺这些的环境里会失败，而它**不是质量门禁**（是记账）。
# 口径与判据：docs/adr/0024-quota-discipline-and-cost-model.md §5（Q1）。
# ---------------------------------------------------------------------------
CNB_ORG ?= Mybase_Le0n3rd

quota: ## 核对组织额度与用量（只读；需 cnb CLI 与组织读权限）
	@command -v cnb >/dev/null 2>&1 || { \
		echo ">> 需要 cnb CLI（登记见 ADR-0016；镜像内由 .ide/install-cnb-skills.sh 安装）"; \
		exit 1; \
	}
	@echo ">> 1/4 组织月度额度（看 ci_in_sec / dev_in_sec 的 free）"
	@cnb charge get-quota --slug $(CNB_ORG)
	@echo ">> 2/4 组织本月用量"
	@cnb charge get-volume --slug $(CNB_ORG)
	@echo ">> 3/4 按仓库拆分：云原生构建（ci）"
	@cnb charge get-repos-volume --slug $(CNB_ORG) --type charge_type_ci
	@echo ">> 4/4 按仓库拆分：云原生开发（dev）"
	@cnb charge get-repos-volume --slug $(CNB_ORG) --type charge_type_dev
	@echo ">> 请把读数记入最新一篇 devlog（预计 vs 实际）"

# ---------------------------------------------------------------------------
# 分支卫生（只读，不阻断）
# ---------------------------------------------------------------------------
# 列出"已产出但未合入 develop"的分支与开放 PR。开发环境每次从 develop 拉起、
# 重启即清空上下文，未合入的工作在下次会话中不可见（见 ADR-0013），因此把它
# 做成机器检查而不是记性问题。
# 故意**不加入 check**：它是提醒，不是门禁——挂在 CI 上会让它变成永远的红灯。
branch-status: ## 分支卫生自检：列出未合入 develop 的分支与开放 PR
	@bash scripts/check-branch-hygiene.sh --base develop \
		$(if $(STRICT),--strict,) $(if $(OFFLINE),--offline,)

# ---------------------------------------------------------------------------
# 基准自动化（决策见 docs/adr/0014-benchmark-automation.md，运行方式见
# docs/engineering/benchmark-automation.md）
#
# 所有参数都用变量暴露：CI 与本地跑的是**同一条命令**，CI 只改变量，
# 不另写一套流程（与 .cnb.yml 头部约定一致）。
# ---------------------------------------------------------------------------
BENCH_DATA_ROOT ?= .bench-data
BENCH_TIERS     ?= S,M,L
BENCH_REPEATS   ?= 10
BENCH_THREADS   ?= 8
BENCH_LABEL     ?= local
BENCH_KEEP_DAYS ?= 30
BENCH_MODEL_DIR ?= /opt/models
# 隔离模式：user = 非特权 uid + 资源上限 + 最小环境（默认；CI 必须用这个）
BENCH_ISOLATION ?= user
# 唯一入口：`make bench`（四道可信闸门在它调的脚本里；CI 已停用自动跑测，见 ADR-0023）
BENCH_DRY_RUN   ?= 0

bench: ## 一键跑测：跑一轮 → 四道可信闸门 → 校验入库（BENCH_DRY_RUN=1 只跑不发布）
	bash scripts/bench/run.sh

bench-round: ## 跑一轮基准并落盘（BENCH_TIERS/BENCH_REPEATS/BENCH_THREADS/BENCH_LABEL 可覆盖）
	PYTHONPATH=$(PWD)/src $(UV) run python -m agent_sec_perf.bench.rounds \
		--data-root $(BENCH_DATA_ROOT) \
		--model-dir $(BENCH_MODEL_DIR) \
		--tiers $(BENCH_TIERS) \
		--repeats $(BENCH_REPEATS) \
		--threads $(BENCH_THREADS) \
		--label $(BENCH_LABEL) \
		--isolation $(BENCH_ISOLATION) \
		--keep-days $(BENCH_KEEP_DAYS)

bench-verify-assets: ## 校验预置资产摘要（复用构建期脚本，不引入第二个真源）
	bash .ide/fetch-assets.sh verify $(BENCH_MODEL_DIR) /opt/benchmarks

bench-publish: ## 校验并发布数据到数据分支（只推送被允许的分支；CI 专用）
	bash scripts/bench/publish.sh

# ---------------------------------------------------------------------------
clean: ## 清理构建与缓存产物
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type d -name "*.egg-info" -prune -exec rm -rf {} +

distclean: clean ## 额外移除虚拟环境
	rm -rf .venv
