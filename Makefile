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

.PHONY: help setup lint format format-check typecheck test test-cov test-security \
        security security-bandit security-audit commit-check changelog bump \
        check branch-status clean distclean \
        bench-round bench-publish bench-verify-assets

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
	@if [ "$$CI" = "true" ]; then \
		echo ">> CI 环境：跳过 pre-commit 钩子安装"; \
	else \
		echo ">> 安装 pre-commit 钩子"; \
		$(UV) run pre-commit install --hook-type pre-commit --hook-type commit-msg; \
	fi
	@echo ">> 完成。运行 make check 进行首次自检。"

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
test: ## 运行测试（快速回归，排除 benchmark）
	$(UV) run pytest $(PYTEST_XDIST) -m "not benchmark"

test-cov: ## 运行测试并生成覆盖率报告
	$(UV) run pytest -m "not benchmark" --cov --cov-report=term-missing --cov-report=xml

test-security: ## 仅运行安全与对抗性测试（安全测试层尚未建立，见一致性报告 A-9）
	@$(UV) run pytest -m security; \
	code=$$?; \
	if [ $$code -eq 5 ]; then \
		echo ">> 提示：当前没有 security 标记的用例（tests/security/ 尚未建立，属 Phase 1 产出）。"; \
		echo ">> 该目标未被 make check 引用，不影响门禁；测试层建立后本提示会自动消失。"; \
		exit 0; \
	fi; \
	exit $$code

# ---------------------------------------------------------------------------
# 安全
# ---------------------------------------------------------------------------
security: security-bandit security-audit ## 运行全部安全检查

security-bandit: ## 静态安全扫描
	$(UV) run bandit -q -r $(SRC)

security-audit: ## 依赖漏洞审计
	$(UV) run pip-audit

# ---------------------------------------------------------------------------
# 提交与版本
# ---------------------------------------------------------------------------
commit-check: ## 校验最近一条提交信息是否符合 Conventional Commits
	$(UV) run cz check --rev-range HEAD~1..HEAD

changelog: ## 根据提交历史生成 CHANGELOG（写入文件）
	$(UV) run cz changelog --incremental

bump: ## 按提交历史自动提升版本号并打标签（需人工确认）
	$(UV) run cz bump

# ---------------------------------------------------------------------------
# 聚合
# ---------------------------------------------------------------------------
check: format-check lint typecheck test security ## 完整自检（提交 PR 前必须全绿）

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
