.PHONY: help sync lint format test check build clean

help: ## Show this help.
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  %-10s %s\n", $$1, $$2}'

sync: ## Create/update the virtual environment.
	uv sync --all-groups

lint: ## Run the linter.
	uv run ruff check .

format: ## Auto-format the code.
	uv run ruff format .
	uv run ruff check --fix .

test: ## Run the test suite.
	uv run pytest

check: lint test ## Run everything CI runs.

build: ## Build a source and wheel distribution.
	uv build

clean: ## Remove build and cache artifacts.
	rm -rf build dist .pytest_cache .ruff_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
