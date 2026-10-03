---
name: branch-rescue
description: |
  Diagnose, explain and fix stuck git situations: diverged local/remote,
  merge/rebase conflicts, PRs with conflicts, wrong-branch commits, detached
  HEAD, lost commits, rejected pushes, dirty tree blocking pull. Gives the
  cause, explained fix commands within the repo's own rules, prevention, and an
  HTML local-vs-remote diagram; takes over execution on request. Use on
  "diverged", "conflict", "out of sync", "rejected", "behind/ahead", "wrong
  branch", "lost my commit", or any git-mess question, even unnamed.
---

# Branch rescue

## Scope

- Does: diagnose a git situation (read-only), explain it, plan a fix that complies with the user's rules, teach it, execute only when asked.
- Not for: routine commit/push, merging a finished task branch (`merge-worktree`), leftover-branch cleanup (`make tidy`).
- Default: the user runs the commands; you diagnose and teach. Execution starts on an explicit takeover request.

## Contents

1. [Diagnose](#diagnose): read-only snapshot of the repo
2. [Policy](#policy): rules the fix must obey
3. [Plan, brief, visual](#plan-brief-visual): one plan, told in chat and drawn
4. [Takeover](#takeover): executing the plan at the user's request
5. [Guardrails](#guardrails): what never happens unasked

Situation recipes: `references/playbooks.md`; read only the matching section.

## Diagnose

- `uv run python src/eupy/devtools/branch_rescue.py --fetch` (`--repo <path>` for another repo or worktree). `--fetch` refreshes remote refs; a failed fetch warns and continues stale.
- Output is a JSON snapshot:
  - `state`: `diverged | behind | ahead | in-sync | conflicted | in-progress | detached | no-upstream`
  - `ahead`/`behind`, `base` (shared ancestor), `local_only`/`remote_only` (commit lanes)
  - `both_touched` (files edited on both sides), `predicted_merge` (`clean`, `conflicts`; `null` = not computed, e.g. git < 2.38: treat `both_touched` as the hint)
  - `operation`, `status.conflicted`, `config`, `worktrees`, `upstream_gone`
- Drifted feature branch: add `--against origin/<default>`; default compares with the branch's upstream.
- The cause is not in the snapshot. Get it from `git reflog -20` and `git log --graph --oneline --decorate --all -20`: commits on the wrong branch, merges/resets/rebases, force-updated remotes.
- Read-only git only: `status`, `log`, `reflog`, `diff`, `show`, `merge-base`, `config --get`, `branch -r --contains`, `worktree list`. No script: `git status -sb` plus the log above.

## Policy

Read before planning; the fix follows these, not git folklore.

- Sources: repo `CLAUDE.md`/`AGENTS.md`, `~/.claude/CLAUDE.md`, `~/.claude/rules/*.md`, memory, then `snapshot.config`, hooks, Makefile/CI. Project overrides global; state the clash in one line.
- Extract:
  - integration style: merge vs rebase vs ff-only
  - delivery: PR vs local merge into main; who approves a merge
  - naming for branches/worktrees (applies to backup/rescue/WIP branches you create) and commit format
  - verification before commit or push (tests, lint, regenerated docs)
  - history-rewrite and force-push rules
  - post-merge cleanup
- Cite each rule used as `file:line` under "Policy applied".
- A rule forbids the textbook fix (e.g. rebase banned): take the compliant route, name the rule.
- Rule silent: choose the non-destructive option, mark it "assumption"; ask only if the answer changes the outcome.

## Plan, brief, visual

Build the plan once (steps + policy + prevention); the chat brief and the diagram both render it.

Brief, bullets in this order:

1. **Situation**: facts (branches, counts, files, operation).
2. **Cause**: chain of events, each link backed by a commit/reflog entry.
3. **Risk**: what could be lost or is irreversible, and what protects it.
4. **Fix**: numbered steps; per step the command, then one line on what it does and why here. Variants only where policy leaves a real choice.
5. **Verify**: commands and expected output (`git status -sb`, tests/lint if policy requires).
6. **Prevent**: at most three bullets tied to this cause, mapped to policy (catalogue at the end of `references/playbooks.md`).

Define git terms (reflog, fast-forward, merge base) in half a sentence on first use.

Visual, unless declined:

- Plan file (temp dir or `$CLAUDE_JOB_DIR/tmp`): `{"title", "policy": [..], "steps": [{"cmd", "does", "status": "pending|done|failed"}], "prevent": [..]}`
- `uv run python src/eupy/devtools/branch_rescue.py --html --plan <plan.json> --open` (plus `--repo`/`--against` as in Diagnose). Writes `<git-dir>/branch-rescue/situation.html`, or the OS temp dir if that is not writable; prints the path. Headless: drop `--open`.
- Remote track above, local below, forking at the shared ancestor; lamp = predicted merge (green clean, red conflicts, grey unknown).

## Takeover

Triggers: "take over", "do it", "you run it", or equivalent, at any point.

1. Re-run the snapshot; the user may have run steps since the plan was written.
2. Compare with the state the plan expects. Done steps: mark `done`. Unexpected state: revise the plan, state the delta in one line, continue. Confirm first only if the revision is destructive or changes policy compliance.
3. Before a step that moves or discards history (`reset`, `rebase`, `checkout -B`, `branch -D`, `push --force*`): save a ref, named per repo policy.
4. One step at a time; after each: snapshot, check the expected result, update status, re-render.
5. Run the policy's verification before any commit it gates.
6. Hand back on: a conflict needing judgment on meaning, an action policy reserves for the user (merge into main, force-push, delete), failing verification, state the plan cannot explain.
7. Finish: final snapshot, what changed, leftovers (list backup refs; ask before deleting).

## Guardrails

- No `reset --hard`, `restore`, `switch --discard-changes`, `checkout -- <path>`, `clean -f`, `branch -D/-f`, `update-ref -d`, `stash drop`, `push --force*`, or rewrite/rebase of pushed history without (a) a saved ref to the work and (b) the user's explicit OK, unless policy already authorises it.
- Uncommitted work outside the task: stop and ask. Never discard it or merge over it.
- Parking a dirty tree: WIP commit on a policy-named branch. If you must stash: `git stash push -u -m <unique-tag>`, restore by SHA; the stash stack is shared across worktrees.
- No push, PR, merge into main, or branch/worktree deletion unless policy and the user say so.
- `git worktree list` first: a branch checked out in another worktree cannot be switched to or deleted from here.
- Config changes (`pull.ff`, `rerere.enabled`, hooks) are proposals under Prevent; apply only when asked.
