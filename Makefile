.PHONY: install
install: ## Install the virtual environment and install the pre-commit hooks
	@echo "🚀 Creating virtual environment using uv"
	@git init
	@uv sync
	# @uv run pre-commit install

.PHONY: check
check: ## Run code quality tools.
	@echo "🚀 Checking lock file consistency with 'pyproject.toml'"
	@uv lock --locked
	@echo "🚀 Linting code: Running pre-commit"
	@uv run pre-commit run -a
	# @uv run mypy  # re-enable once tool.mypy.files is set for the new source layout

.PHONY: test
test: ## Test the code with pytest
	@echo "🚀 Testing code: Running pytest"
	@uv run python -m pytest --doctest-modules

.PHONY: build
build: clean-build ## Build wheel file
	@echo "🚀 Creating wheel file"
	@uvx --from build pyproject-build --installer uv

.PHONY: clean-build
clean-build: ## Clean build artifacts
	@echo "🚀 Removing build artifacts"
	@uv run python -c "import shutil; import os; shutil.rmtree('dist') if os.path.exists('dist') else None"

.PHONY: docs-test
docs-test: ## Test if documentation can be built without warnings or errors
	@uv run mkdocs build -s

.PHONY: docs
docs: ## Build and serve the documentation
	@uv run mkdocs serve

.PHONY: merge-worktree
merge-worktree: SHELL := /bin/bash
merge-worktree: ## Merge a finished task branch into main and clean up its worktree; prompts when several exist
	@set -euo pipefail; \
	if [ -n "$$(git status --porcelain)" ]; then \
		echo "Primary checkout has uncommitted changes -- resolve those first. Aborting."; \
		git status --short; \
		exit 1; \
	fi; \
	entries=$$(git worktree list --porcelain | awk '/^worktree /{wt=$$2} /^branch /{b=$$2; sub("refs/heads/","",b); if (wt ~ /\.claude\/worktrees\//) print wt"\t"b; wt=""}'); \
	if [ -z "$$entries" ]; then \
		echo "No task worktrees found under .claude/worktrees/. Nothing to merge."; \
		exit 0; \
	fi; \
	branches=$$(printf '%s\n' "$$entries" | cut -f2); \
	set -- $$branches; \
	if [ $$# -eq 1 ]; then \
		branch=$$1; \
		echo "One task branch found: $$branch"; \
	else \
		echo "Multiple task branches found -- pick one:"; \
		select branch in "$$@"; do [ -n "$$branch" ] && break; done; \
	fi; \
	path=$$(printf '%s\n' "$$entries" | awk -F'\t' -v b="$$branch" '$$2==b{print $$1}'); \
	echo "Staging merge of '$$branch' into $$(git branch --show-current) (--no-ff --no-commit)..."; \
	git merge --no-ff --no-commit "$$branch"; \
	echo; \
	git status; \
	echo; \
	read -r -p "Commit this merge? [y/N] " ans; \
	if [ "$$ans" != "y" ] && [ "$$ans" != "Y" ]; then \
		echo "Left staged, not committed. Run 'git commit' when ready, then clean up '$$branch' / '$$path' yourself (see AGENTS.md 'After merge')."; \
		exit 0; \
	fi; \
	git commit --no-edit; \
	echo "Pushing main..."; \
	git push; \
	echo "Cleaning up '$$branch'..."; \
	git branch -d "$$branch"; \
	if git ls-remote --exit-code --heads origin "$$branch" >/dev/null 2>&1; then \
		git push origin --delete "$$branch"; \
	fi; \
	git worktree unlock "$$path" 2>/dev/null || true; \
	git worktree remove --force "$$path"; \
	echo "Done: '$$branch' merged, pushed, and cleaned up."

.PHONY: help
help:
	@uv run python -c "import re; \
	[[print(f'\033[36m{m[0]:<20}\033[0m {m[1]}') for m in re.findall(r'^([a-zA-Z_-]+):.*?## (.*)$$', open(makefile).read(), re.M)] for makefile in ('$(MAKEFILE_LIST)').strip().split()]"

.DEFAULT_GOAL := help
