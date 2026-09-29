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

.PHONY: clean-cache
clean-cache: ## Remove Python/ruff/pytest caches
	@echo "🚀 Removing __pycache__, .ruff_cache, .pytest_cache"
	@find . -type d -name '__pycache__' -not -path './.venv/*' -exec rm -rf {} +
	@rm -rf .ruff_cache .pytest_cache

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
	git worktree unlock "$$path" 2>/dev/null || true; \
	git worktree remove --force "$$path"; \
	git branch -d "$$branch"; \
	if git ls-remote --exit-code --heads origin "$$branch" >/dev/null 2>&1; then \
		git push origin --delete "$$branch"; \
	fi; \
	echo "Done: '$$branch' merged, pushed, and cleaned up."

define TIDY_SCRIPT
# tidy: summarise and remove leftovers after PRs merge -- task worktrees, local/remote branches,
# stale scheduler lock. Args: --yes (no prompt), --dry-run (never delete). Needs bash >= 3.2, git, gh.
#
# An item is SAFE only if a PR from that branch was MERGED at exactly the branch tip and none is
# open, and it is not the base of an open (stacked) PR (git ancestry alone cannot tell "merged"
# from "just created, no commits yet"); a worktree must also be clean, hold no git-ignored files
# beyond rebuildable ones (.venv, caches), not be the one we run from, and not be locked by a
# live/unknown owner. Anything else is BLOCKED with a reason -- never forced. --dry-run still
# runs `git fetch --prune` (refreshes remote-tracking refs only).
set -uo pipefail
GH=$${GH:-gh}
DRY=0
YES=0
for a in "$$@"; do
  case "$$a" in
    --yes|-y) YES=1 ;;
    --dry-run) DRY=1 ;;
    *) echo "tidy: unknown argument '$$a' (use ARGS=--yes or ARGS=--dry-run)" >&2; exit 2 ;;
  esac
done

US=$$'\037'                       # field separator for records (non-whitespace: keeps empty fields)
NAME_RE='^(feat|fix|chore|refactor|test|docs)/[a-z0-9]+(-[a-z0-9]+)*$$'
here=$$(git rev-parse --show-toplevel 2>/dev/null) || { echo "tidy: not inside a git repo" >&2; exit 2; }
WORK=$$(mktemp -d)
trap 'rm -rf "$$WORK"' EXIT
ITEMS="$$WORK/items"; PRS="$$WORK/prs"; WTS="$$WORK/wts"
: > "$$ITEMS"

git fetch --prune --quiet >/dev/null 2>&1 || true
primary=$$(git worktree list --porcelain | sed -n '1s/^worktree //p')
task_root="$$primary/.claude/worktrees"
g() { git -C "$$primary" "$$@"; }
q() { printf '%q ' "$$@"; }        # shell-quoted command words, for manual instructions + eval
item() { printf '%s\037%s\037%s\037%s\037%s\n' "$$1" "$$2" "$$3" "$$4" "$$5" >> "$$ITEMS"; }  # status kind name reason cmd

base=$$(g symbolic-ref --short refs/remotes/origin/HEAD 2>/dev/null || echo main)
default=$${base#origin/}

# PR index: headRefName <TAB> state <TAB> headRefOid <TAB> baseRefName
note=""
if ! "$$GH" pr list --state all --limit 500 --json headRefName,state,headRefOid,baseRefName \
     --jq '.[] | [.headRefName, .state, .headRefOid, .baseRefName] | @tsv' > "$$PRS" 2>/dev/null; then
  : > "$$PRS"
  note="PR state unavailable (gh missing or unauthenticated); nothing can be proven merged, so all is blocked"
fi
pr_open()       { awk -F'\t' -v b="$$1" '$$1==b && $$2=="OPEN" {f=1} END{exit !f}' "$$PRS"; }
pr_merged_any() { awk -F'\t' -v b="$$1" '$$1==b && $$2=="MERGED" {f=1} END{exit !f}' "$$PRS"; }
pr_closed_any() { awk -F'\t' -v b="$$1" '$$1==b && $$2=="CLOSED" {f=1} END{exit !f}' "$$PRS"; }
pr_is_base()    { awk -F'\t' -v b="$$1" '$$4==b && $$2=="OPEN" {f=1} END{exit !f}' "$$PRS"; }   # stacked PR
pr_merged_at()  { awk -F'\t' -v b="$$1" -v t="$$2" '$$1==b && $$2=="MERGED" && $$3==t {f=1} END{exit !f}' "$$PRS"; }

# integrated BRANCH TIP -> 0 if safely integrated; sets WHY either way
integrated() {
  local b=$$1 t=$$2 n
  if pr_open "$$b"; then WHY="open PR"; return 1; fi
  if pr_is_base "$$b"; then WHY="base branch of an open (stacked) PR"; return 1; fi
  if pr_merged_at "$$b" "$$t"; then WHY="PR merged at this tip"; return 0; fi
  if pr_merged_any "$$b"; then WHY="PR merged, but this tip differs from the merged one (commits after it, or moved)"; return 1; fi
  if pr_closed_any "$$b"; then WHY="PR closed without merging"; return 1; fi
  if g merge-base --is-ancestor "$$t" "$$base" 2>/dev/null; then
    WHY="no merged PR found (fresh branch, or gh unavailable); tip is in $$base"; return 1
  fi
  n=$$(g cherry "$$base" "$$t" 2>/dev/null | grep -c '^+')
  WHY="$$n commit(s) not in $$base and no merged PR"; return 1
}

PROTECTED=" $$default $$(g branch --show-current) $$(git branch --show-current) "   # never touched
KEPT=" "                                                # branches of worktrees we keep (reported there)
is_protected() { case "$$PROTECTED" in *" $$1 "*) return 0 ;; esac; return 1; }

# worktrees: path US branch US lock-reason
git worktree list --porcelain | awk -v RS= -F'\n' '{
  p=""; b=""; l=""
  for (i = 1; i <= NF; i++) {
    if ($$i ~ /^worktree /) p = substr($$i, 10)
    else if ($$i ~ /^branch /) b = substr($$i, 19)
    else if ($$i ~ /^locked/) { l = substr($$i, 8); if (l == "") l = "locked" }
  }
  printf "%s\037%s\037%s\n", p, b, l
}' | tail -n +2 > "$$WTS"

while IFS=$$US read -r path branch lock; do
  case "$$path" in "$$task_root"/*) ;; *) [ -n "$$branch" ] && PROTECTED="$$PROTECTED$$branch "; continue ;; esac
  label=$${path#"$$primary"/}
  if [ "$$path" = "$$here" ]; then PROTECTED="$$PROTECTED$$branch "; continue; fi   # never the one we stand in
  if [ -z "$$branch" ]; then item blocked worktree "$$label" "detached HEAD; inspect manually" ""; continue; fi
  KEPT="$$KEPT$$branch "
  if is_protected "$$branch"; then
    item blocked worktree "$$label" "branch '$$branch' is protected (default, or checked out in the primary/current checkout)" ""; continue
  fi
  cmds=""
  if [ -n "$$lock" ]; then
    pid=$$(printf '%s' "$$lock" | sed -n 's/.*pid \([0-9][0-9]*\).*/\1/p')
    if [ -z "$$pid" ] || ps -p "$$pid" >/dev/null 2>&1; then
      item blocked worktree "$$label" "locked, not provably stale ($$lock)" ""; continue
    fi
    cmds="$$(q git worktree unlock "$$path")&& "
  fi
  if [ ! -d "$$path" ]; then
    KEPT=$${KEPT%"$$branch "}
    item delete worktree "$$label" "directory missing (stale registration; prunes all stale entries)" "$$cmds$$(q git worktree prune)"
    continue
  fi
  integrated "$$branch" "$$(g rev-parse --verify --quiet "refs/heads/$$branch")"; ok=$$?
  dirty=$$(git -C "$$path" status --porcelain 2>/dev/null)
  # git-ignored files are lost with the worktree; only rebuildable ones are tolerated
  lost=$$(git -C "$$path" status --porcelain --ignored 2>/dev/null | grep '^!! ' \
         | grep -Ev '^!! (\.venv/|.*__pycache__/|\.ruff_cache/|\.pytest_cache/|.*\.egg-info/|.*\.pyc$$|.*\.DS_Store$$)')
  if [ -n "$$dirty" ]; then
    item blocked worktree "$$label" "uncommitted changes (after reviewing: $$(q git worktree remove --force "$$path")): $$(printf '%s' "$$dirty" | tr '\n' ';')" ""
  elif [ -n "$$lost" ]; then
    item blocked worktree "$$label" "git-ignored files would be lost (after reviewing: $$(q git worktree remove --force "$$path")): $$(printf '%s' "$$lost" | cut -c4- | tr '\n' ';')" ""
  elif [ $$ok -ne 0 ]; then
    item blocked worktree "$$label" "branch not integrated: $$WHY" ""
  else
    KEPT=$${KEPT%"$$branch "}
    item delete worktree "$$label" "$$WHY" "$$cmds$$(q git worktree remove "$$path")"
  fi
done < "$$WTS"

g for-each-ref --format='%(refname:lstrip=2) %(objectname)' refs/heads > "$$WORK/local"
while read -r b t; do
  is_protected "$$b" && continue
  case "$$KEPT" in *" $$b "*) continue ;; esac
  if integrated "$$b" "$$t"; then item delete branch "$$b" "$$WHY" "$$(q git branch -D "$$b")"
  else item blocked branch "$$b" "$$WHY" ""; fi
done < "$$WORK/local"

g for-each-ref --format='%(refname) %(objectname)' refs/remotes/origin/ > "$$WORK/remote"
while read -r ref t; do
  b=$${ref#refs/remotes/origin/}
  if [ "$$b" = HEAD ] || is_protected "$$b"; then continue; fi
  if integrated "$$b" "$$t"; then item delete remote "origin/$$b" "$$WHY" "$$(q git push "--force-with-lease=refs/heads/$$b:$$t" origin ":refs/heads/$$b")"
  else item blocked remote "origin/$$b" "$$WHY" ""; fi
done < "$$WORK/remote"

lf="$$primary/.claude/scheduled_tasks.lock"
if [ -e "$$lf" ]; then
  pid=$$(sed -n 's/.*"pid"[[:space:]]*:[[:space:]]*\([0-9][0-9]*\).*/\1/p' "$$lf" 2>/dev/null | head -n 1)
  if [ -z "$$pid" ]; then item blocked lockfile ".claude/scheduled_tasks.lock" "unreadable lock content; inspect manually" ""
  elif ps -p "$$pid" >/dev/null 2>&1; then item blocked lockfile ".claude/scheduled_tasks.lock" "session pid $$pid alive" ""
  else item delete lockfile ".claude/scheduled_tasks.lock" "pid $$pid not running" "$$(q rm -f "$$lf")"; fi
fi

# naming convention: surviving branches and worktree dirs
awk -F"$$US" '$$1=="delete" {print $$3}' "$$ITEMS" > "$$WORK/going"
while read -r b; do
  is_protected "$$b" && continue
  grep -qxF -- "$$b" "$$WORK/going" && continue
  [[ $$b =~ $$NAME_RE ]] && continue
  h=$${b#worktree-}; h=$${h/+//}; cmd=""
  if [[ $$h =~ $$NAME_RE ]]; then cmd=$$(q git branch -m "$$b" "$$h"); fi
  item warn naming "$$b" "branch name must be <type>/<short-name> (feat|fix|chore|refactor|test|docs)" "$$cmd"
done < <(g for-each-ref --format='%(refname:lstrip=2)' refs/heads)
while IFS=$$US read -r path branch lock; do
  case "$$path" in "$$task_root"/*) ;; *) continue ;; esac
  [ -n "$$branch" ] || continue
  label=$${path#"$$primary"/}; want=$${branch//\//+}
  [ "$${path##*/}" = "$$want" ] && continue
  grep -qxF -- "$$label" "$$WORK/going" && continue
  item warn naming "$$label" "worktree dir should be $$want" ""
done < "$$WTS"

# ---- report ----
[ -n "$$note" ] && printf 'note: %s\n\n' "$$note"
if [ ! -s "$$ITEMS" ]; then echo "Nothing to tidy."; exit 0; fi
group() {   # status title
  local n s k nm why cmd
  n=$$(awk -F"$$US" -v s="$$1" '$$1==s' "$$ITEMS" | wc -l | tr -d ' ')
  [ "$$n" -gt 0 ] || return 0
  echo "$$2 ($$n)"
  while IFS=$$US read -r s k nm why cmd; do
    [ "$$s" = "$$1" ] || continue
    echo "  [$$k] $$nm -- $$why"
    if [ "$$s" = warn ] && [ -n "$$cmd" ]; then echo "        fix: $$cmd"; fi
  done < "$$ITEMS"
  echo
}
group delete "SAFE TO DELETE"
group blocked "BLOCKED (kept)"
group warn "NAMING WARNINGS"

ndel=$$(awk -F"$$US" '$$1=="delete"' "$$ITEMS" | wc -l | tr -d ' ')
[ "$$ndel" -gt 0 ] || exit 0
manual() {
  echo "To delete manually (from $$primary):"
  awk -F"$$US" '$$1=="delete" {print "  " $$5}' "$$ITEMS"
  echo
}
if [ $$DRY -eq 1 ] || { [ $$YES -eq 0 ] && [ ! -t 0 ]; }; then
  manual
  [ $$DRY -eq 1 ] || echo "Not a terminal: nothing deleted. Re-run with ARGS=--yes to delete."
  exit 0
fi
if [ $$YES -eq 0 ]; then
  manual
  printf 'Delete %s safe item(s)? [y/N] ' "$$ndel"
  read -r ans
  case "$$ans" in y|Y) ;; *) echo "Nothing deleted."; exit 0 ;; esac
fi

FAILS="$$WORK/fails"; : > "$$FAILS"
while IFS=$$US read -r s k nm why cmd; do
  [ "$$s" = delete ] || continue
  if out=$$(cd "$$primary" && eval "$$cmd" 2>&1 </dev/null); then echo "  deleted [$$k] $$nm"
  else printf '%s\037%s\037%s\037%s\n' "$$k" "$$nm" "$$(printf '%s' "$$out" | tr '\n' ' ')" "$$cmd" >> "$$FAILS"; fi
done < "$$ITEMS"
g worktree prune 2>/dev/null

if [ -s "$$FAILS" ] || awk -F"$$US" '$$1=="blocked" {f=1} END{exit !f}' "$$ITEMS"; then
  echo; echo "NOT DELETED"
  while IFS=$$US read -r k nm err cmd; do echo "  [$$k] $$nm -- failed: $$err"; echo "      $$cmd"; done < "$$FAILS"
  awk -F"$$US" '$$1=="blocked" {print "  [" $$2 "] " $$3 " -- " $$4}' "$$ITEMS"
fi
[ ! -s "$$FAILS" ]
endef
export TIDY_SCRIPT

.PHONY: tidy
tidy: ## Summarise + delete merged worktrees/branches/stale locks (ARGS=--yes: no prompt, ARGS=--dry-run: no delete)
	@bash -c "$$TIDY_SCRIPT" tidy $(ARGS)

.PHONY: help
help:
	@uv run python -c "import re; \
	[[print(f'\033[36m{m[0]:<20}\033[0m {m[1]}') for m in re.findall(r'^([a-zA-Z_-]+):.*?## (.*)$$', open(makefile).read(), re.M)] for makefile in ('$(MAKEFILE_LIST)').strip().split()]"

.DEFAULT_GOAL := help
