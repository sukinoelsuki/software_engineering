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
        check clean distclean

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

test-security: ## 仅运行安全与对抗性测试
	$(UV) run pytest -m security

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
clean: ## 清理构建与缓存产物
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage coverage.xml htmlcov
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type d -name "*.egg-info" -prune -exec rm -rf {} +

distclean: clean ## 额外移除虚拟环境
	rm -rf .venv
