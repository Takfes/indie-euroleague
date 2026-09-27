---
name: merge-worktree
description: |
  Use in indie-euroleague when a task/background-job branch under
  .claude/worktrees/ is finished and needs merging into local main. Stages a
  `git merge --no-ff --no-commit`, shows the diff for review, waits for
  explicit approval, then commits and cleans up automatically (push main,
  delete the branch locally and on origin if pushed, remove the worktree).
  Only usable from an interactive session sitting in the primary checkout --
  a worktree-isolated background job can never reach it, so don't try this
  from one.
---

# Merge a Finished Task Branch

## Overview

indie-euroleague task and background-job branches finish inside an isolated
`.claude/worktrees/<name>` worktree and get pushed to origin, but the
isolated session that did the work can never reach the shared primary
checkout to merge itself -- that's a hard sandbox rule for worktree-isolated
sessions, not a policy choice (see AGENTS.md's "Subagents & git" section).
This skill is the interactive-session counterpart that actually performs
that merge.

## When to Use

- A background job or subagent reports a task branch/worktree ready to
  merge.
- You want to merge a specific `.claude/worktrees/` branch into local main.

## Precondition

Run this only from an **interactive** session in the primary checkout (repo
root, not a path under `.claude/worktrees/`). Verify with
`git rev-parse --show-toplevel` if unsure. A background job will be refused
by the sandbox if it tries any of this -- don't attempt it from one; tell
the user to run it from an interactive session instead.

## Procedure

1. Check the primary checkout is clean: `git status --porcelain`. Anything
   there that isn't part of this merge -- stop and ask; never stash,
   discard, or merge over foreign uncommitted work.
2. List candidates: `git worktree list --porcelain`, taking each `worktree`
   path that contains `.claude/worktrees/` and its following
   `branch refs/heads/<name>` line.
   - Zero candidates -> tell the user, stop.
   - One candidate -> use it.
   - More than one -> ask the user which one (AskUserQuestion or a plain
     question), showing each branch's last commit for context:
     `git log -1 --format='%s (%ad)' --date=short <branch>`.
3. No `git fetch` needed -- worktrees of the same repo share one object
   store, so the branch is already visible locally.
4. Stage the merge: `git merge --no-ff --no-commit <branch>`.
   - Conflict, or the branch has drifted behind main -> resolve it on the
     spot (re-verify lint/tests after), per AGENTS.md's drift-merge rule.
5. Show `git status` / `git diff --cached --stat` and wait for the user's
   explicit go-ahead before continuing -- the staged diff is the approval
   request, not the approval itself.
6. Once approved, commit and clean up automatically, without asking again
   (per AGENTS.md's "After merge" step):
   - `git commit --no-edit`
   - `git push` (main to origin)
   - `git worktree unlock <path>` (ignore failure if it wasn't locked), then
     `git worktree remove --force <path>` -- force is safe here: the
     worktree's tracked content is already merged into main, so anything
     left is disposable build clutter (`.venv`, `.pytest_cache`, etc.). This
     must run **before** the branch delete below: `git branch -d` refuses to
     delete a branch that's still checked out in a worktree, and the branch
     stays checked out there until the worktree itself is removed.
   - `git branch -d <branch>` (local; safe delete -- it just merged, so this
     always succeeds now that the worktree holding it is gone)
   - if a same-named remote branch exists
     (`git ls-remote --exit-code --heads origin <branch>`), delete it:
     `git push origin --delete <branch>`

## Terminal equivalent

`make merge-worktree` does the same end-to-end, with a numbered `select`
prompt when more than one branch is found and a `[y/N]` prompt in place of
step 5's conversational approval.
