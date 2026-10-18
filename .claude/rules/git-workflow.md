---
description: Branch and worktree naming, commits, delivery mode (remote PR by default), merging, post-merge cleanup, and background-job rules. Use for any git, branch, PR or merge action.
alwaysApply: true
---

# Git workflow

## Git

- One branch + worktree per task. Naming is strict and identical everywhere:
  - Branch: `<type>/<short-name>` — `type` ∈ `feat`/`fix`/`chore`/`refactor`/`test`/`docs`; `short-name` lowercase kebab-case (e.g. `feat/optimizer-config`).
  - Worktree dir: `.claude/worktrees/<type>+<short-name>` (the `/` becomes `+`).
  - Always call `EnterWorktree` with an explicit `name` of `<type>/<short-name>`, then **immediately** rename the auto-generated branch: `git branch -m worktree-<type>+<short-name> <type>/<short-name>`. No `worktree-*` branch ever gets committed to or pushed.
  - `make clean-git` flags any branch or worktree dir that breaks this.
- Conventional commits (`feat`/`fix`/`chore`/`refactor`/`test`/`docs`). Flag when splitting into more commits would give cleaner history.
- Merging into main always needs my explicit go-ahead beforehand — not a report that it already happened — unless I've told you upfront to go straight through to merge.
- Delivery mode — **default: remote** (overrides any global local-merge default), for interactive and background sessions alike.
  - `remote` (default): push the task branch, open a GitHub PR directly against `main`. Never stage a local merge unless I ask for `local` for that task.
  - `local` (only when I say so, per task): main agent merges into local `main` directly, only from an interactive session in the primary checkout, via `make merge-worktree` or the `merge-worktree` skill.
- Before merging, or opening/updating a PR: check the target checkout's `git status`. Foreign uncommitted work (not part of this task) → stop and ask; never stash, discard, or merge over it.
- A branch that's drifted from main (e.g. a rename/refactor landed on main after the branch forked) merges main into itself first and resolves conflicts there — including redoing any mechanical refactor main introduced — before merging into main or opening/updating the PR.
- After merge: run `make clean-git` — it deletes the task branch (local + remote) and its worktree, plus stale locks, only when a merged PR matches the branch tip and it is clean, holds no irreplaceable git-ignored files (e.g. `data/raw_data/`) and is not in use (needs authenticated `gh`). Safe items come with manual commands; anything blocked is reported with a reason (dirty/ignored-data worktrees get a `--force` hint). Never force-delete around it without my go-ahead.
  - `remote`: the merge happens on GitHub, so cleanup is a separate later step; `local`: `merge-worktree` already cleans up its own branch/worktree, `make clean-git` catches the rest.
  - Start of an interactive session: run `make clean-git ARGS=--dry-run` and surface leftovers (background jobs can't clean up after themselves).

## Background jobs

- Isolate in a worktree. Sandbox reaches the worktree and the remote (commit and push both work; opening a PR too, under `remote`), but never the shared main checkout — not before isolating, not after, not even for the merge step itself.
- Finish by pushing the branch, then: under `remote` (default), open the PR and report its URL; under `local`, report the branch as ready for `merge-worktree`.
- Final report also lists the branch + worktree as **pending cleanup** (run `make clean-git` once the PR is merged, from an interactive session).
- Only an interactive session can run `merge-worktree` (a background job never can) — via `make merge-worktree` from a terminal, or the `merge-worktree` skill in an interactive Claude Code session; both prompt for a branch when more than one task worktree exists, stage the merge for review, and — once approved — commit and clean up the branch/worktree automatically.
- No `git fetch` needed first: worktrees of the same repo share one object store, so a branch committed in any worktree is already visible from the primary checkout.
