.DEFAULT_GOAL := help
STAMPS := .stamps
VENV ?= venv
VENV_BIN := $(VENV)/bin
VENV_MARKER := $(VENV)/.created
BOOTSTRAP_PYTHON ?= python3
GO ?= go
ACTIONLINT_VERSION := v1.7.12
PYTHON := $(VENV_BIN)/python
PIP := $(PYTHON) -m pip
RUFF := $(VENV_BIN)/ruff
MYPY := $(VENV_BIN)/mypy
PYTEST := $(PYTHON) -m pytest
PYDRY := $(VENV_BIN)/pydry
PRE_COMMIT := $(VENV_BIN)/pre-commit
BUILD := $(PYTHON) -m build
TWINE := $(PYTHON) -m twine
ACTIONLINT ?= $(GO) run github.com/rhysd/actionlint/cmd/actionlint@$(ACTIONLINT_VERSION)
INSTALL_STAMP := $(VENV)/.pydry-install

$(STAMPS):
	@mkdir -p $@

.PHONY: help
help: ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Environment ──────────────────────────────────────────────

.PHONY: venv
venv: $(VENV_MARKER) ## Create virtual environment

$(VENV_MARKER):
	$(BOOTSTRAP_PYTHON) -m venv $(VENV)
	@touch $@
	@echo "Activate with: source $(VENV)/bin/activate"

$(INSTALL_STAMP): pyproject.toml $(VENV_MARKER)
	$(PIP) install -e ".[dev]"
	@touch $@

.PHONY: install
install: $(INSTALL_STAMP) ## Install project and dev dependencies

# ── Quality ──────────────────────────────────────────────────

.PHONY: lint
lint: $(INSTALL_STAMP) ## Run linter
	$(RUFF) check .

.PHONY: actionlint
actionlint: ## Lint GitHub Actions workflows
	$(ACTIONLINT)

.PHONY: format
format: $(INSTALL_STAMP) ## Format code
	$(RUFF) format .

.PHONY: format-check
format-check: $(INSTALL_STAMP) ## Check formatting without changes
	$(RUFF) format --check .

.PHONY: typecheck
typecheck: $(INSTALL_STAMP) ## Run type checker
	$(MYPY)

.PHONY: test
test: $(INSTALL_STAMP) ## Run tests
	$(PYTEST)

.PHONY: coverage
coverage: $(INSTALL_STAMP) ## Run tests with coverage report
	$(PYTEST) --cov --cov-report=term-missing

.PHONY: pydry-check
pydry-check: $(INSTALL_STAMP) ## Enforce the repository's pydry policy
	$(PYDRY) check --output .pydry/pydry-report.json

.PHONY: demo-showcase
demo-showcase: $(INSTALL_STAMP) ## Run a quick CLI showcase against ./demo
	$(PYTHON) -m pydry showcase demo

.PHONY: demo-simulate
demo-simulate: $(INSTALL_STAMP) ## Run a visual CLI simulation against ./demo
	$(PYTHON) -m pydry simulate demo

.PHONY: check
check: lint actionlint format-check typecheck test pydry-check ## Run all checks

# ── Packaging ─────────────────────────────────────────────────

.PHONY: dist
dist: $(INSTALL_STAMP) ## Build source and wheel distributions
	$(MAKE) clean
	$(BUILD)

.PHONY: check-dist
check-dist: dist ## Validate built distributions for PyPI
	$(TWINE) check dist/*

# ── Pre-commit ───────────────────────────────────────────────

$(STAMPS)/pre-commit: .pre-commit-config.yaml $(STAMPS)/install | $(STAMPS)
	$(PRE_COMMIT) install
	@touch $@

.PHONY: pre-commit
pre-commit: $(STAMPS)/pre-commit ## Install pre-commit hooks

# ── Cleanup ──────────────────────────────────────────────────

.PHONY: clean
clean: ## Remove build/test artifacts and caches (keeps virtualenvs)
	rm -rf dist/ build/ *.egg-info src/*.egg-info .stamps/ htmlcov/ .pytest_cache/ .mypy_cache/ .ruff_cache/
	rm -f .coverage coverage.xml
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true

.PHONY: clean-all distclean
clean-all: clean ## Remove all artifacts including local virtualenvs
	rm -rf venv/ .venv/

distclean: clean-all ## Alias for clean-all
