# Responder — common developer tasks.
# Run `make` (or `make help`) to list them. Everything runs through uv.

.DEFAULT_GOAL := help
UV ?= uv

.PHONY: help uv-sync test lint fix types check docs build lock clean

help:  ## List available commands
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "} {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

uv-sync:  ## Sync the dev virtualenv (develop, test, docs extras)
	$(UV) sync --extra develop --extra test --extra docs

test: uv-sync  ## Run the test suite with coverage
	$(UV) run pytest

lint:  ## Lint with ruff
	$(UV) run ruff check .

fix:  ## Auto-fix lint issues with ruff
	$(UV) run ruff check --fix .

types:  ## Type-check with mypy
	$(UV) run mypy

check: lint types test  ## Run lint, type checks, and tests

docs: uv-sync  ## Build the HTML documentation
	cd docs && $(UV) run make html

build:  ## Build the sdist and wheel
	$(UV) build

lock:  ## Refresh the uv lock file
	$(UV) lock

clean:  ## Remove build artifacts and caches
	rm -rf dist build coverage.xml .pytest_cache .mypy_cache .ruff_cache
	find . -type d -name '__pycache__' -prune -exec rm -rf {} +
