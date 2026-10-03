# Situation playbooks

Read only the section matching `snapshot.state` / the user's symptom. Pick the merge or rebase variant from Policy; if policy is silent, merge (never rewrites history). Commands are templates: replace `<…>`. Helper branches (`<task-branch>`, `<rescue>`, `<wip>`, backups) take names that satisfy the repo's naming policy.

## Contents

- [Diverged](#diverged) · [Committed on the wrong branch](#committed-on-the-wrong-branch)
- [Behind](#behind) · [Ahead / rejected push](#ahead--rejected-push) · [Squash-merged PR](#squash-merged-pr)
- [Conflicts mid-merge or mid-rebase](#conflicts-mid-merge-or-mid-rebase) · [PR shows conflicts](#pr-shows-conflicts)
- [Detached HEAD](#detached-head) · [Lost commits](#lost-commits) · [Remote was rewritten](#remote-was-rewritten)
- [No upstream / upstream gone](#no-upstream--upstream-gone) · [Dirty tree blocks the operation](#dirty-tree-blocks-the-operation)
- [Prevention catalogue](#prevention-catalogue)

## Diverged

- Signal: `state=diverged`; `git status -sb` shows `[ahead N, behind M]`.
- Cause: both sides got commits after their last common ancestor. Typical: committed locally while a PR landed on the remote, or two machines pushed.
- Route first: local commits on a branch policy reserves (default/protected, `worktree-*`)? Use [Committed on the wrong branch](#committed-on-the-wrong-branch). Local branch's PR was squash-merged? Use [Squash-merged PR](#squash-merged-pr). Otherwise the table below.
- Check `predicted_merge` and `both_touched`: disjoint files means a clean combine either way.

| Variant | Commands | What it does |
|---|---|---|
| Merge (default) | `git fetch` | Refresh remote refs. |
| | `git merge <upstream>` | Joins both histories with a merge commit; your commits keep their hashes. Conflicts stop here (see below). |
| Rebase (only if policy allows) | `git rebase <upstream>` | Replays your commits on top of the remote tip; hashes change. Safe only for unpushed commits. |
| ff-only | `git pull --ff-only` | Refuses when diverged. A guard, not a fix. |


## Committed on the wrong branch

- Signal: local commits on `main`/default (or a `worktree-*` branch) where policy requires a task branch; often shows up as diverged.
- Cause: committing before creating the branch.
- Not pushed yet (check `git branch -r --contains <sha>` is empty):

| Command | What it does |
|---|---|
| `git fetch` | Current remote refs. |
| `git branch <task-branch>` | Names the commits so nothing is lost when the next step moves `main`. |
| `git reset --hard <upstream>` | Moves local `main` to the remote tip. Safe only because of the previous step. Needs user OK. |
| `git switch <task-branch>` | Continue on the task branch. |
| `git merge main` (or rebase, per policy) | Bring the branch up to date; resolve conflicts here, not on the hosting site. |
| `git push -u origin <task-branch>` | Publish; open the PR if delivery is by PR. |

- Worktrees: the default branch is usually checked out in the primary checkout, so `reset --hard` / `switch main` fail from a task worktree. Check `git worktree list`; that one step is the user's, from the primary checkout.
- Already pushed to the shared branch: do not reset. Ask; usual route is `git revert <sha>` on the shared branch and re-land the work via a branch. Rewriting a shared branch needs explicit approval.

## Behind

- Signal: `state=behind`. Nothing local is at risk.
- Clean tree: `git pull --ff-only`. It moves your branch pointer forward; no merge commit.
- Dirty tree: see [Dirty tree](#dirty-tree-blocks-the-operation).

## Ahead / rejected push

- `state=ahead`: nothing to integrate. Push (`git push`) or open the PR per policy.
- "rejected (non-fast-forward)": the remote moved after your last fetch. `git fetch`, re-snapshot: it is now [Diverged](#diverged).
- "protected branch": push a task branch and open a PR instead.

## Squash-merged PR

- Signal: the PR merged on the host, yet the old task branch shows diverged/ahead of main although its content is in main as one new commit.
- Cause: squash/rebase merges write new commits, so the branch's originals are not ancestors of main.
- Fix: `git switch <default>`, `git pull --ff-only` (takes the squashed commit). Delete the stale branch: `git branch -d` refuses (not an ancestor); `-D` only after confirming the PR is merged (`gh pr view <branch> --json state`) and with the user's OK.
- More work on the same topic: branch fresh from the updated default instead of merging the old branch.

## Conflicts mid-merge or mid-rebase

- Signal: `state=conflicted`; `status.conflicted` lists files; `operation` is `merge` or `rebase`.
- Cause: both sides edited the same lines.

| Command | What it does |
|---|---|
| `git status` | Lists "both modified" files; tells which operation is open. |
| `git diff --name-only --diff-filter=U` | Just the unresolved files. |
| `git log --merge -p -- <file>` (merge) or `git show REBASE_HEAD` (rebase) | The commits from each side that touch the file, to learn each side's intent. |
| edit `<file>`: keep/combine between `<<<<<<<`, `=======`, `>>>>>>>`, delete the markers | Produces the final content. |
| `git add <file>` | Marks the file resolved. |
| `git merge --continue` / `git rebase --continue` | Finishes the commit / moves to the next replayed commit. |
| `git merge --abort` / `git rebase --abort` | Back to the state before the operation started. Always available until the final continue. |

- Generated files (lockfiles, catalogues, built docs): regenerate with the project's generator after taking either side; never hand-merge. Check policy for the generator.
- Rebase quirk: "ours" is the branch being replayed onto, "theirs" is your commit.
- Optional: `git config rerere.enabled true` remembers resolutions for repeated conflicts (propose, don't set).

## PR shows conflicts

- Cause: the base branch moved after the PR branch forked.
- Fix locally on the PR branch, never on the hosting site: `git fetch`, then merge (or rebase, per policy) `origin/<base>` into it, resolve per [Conflicts](#conflicts-mid-merge-or-mid-rebase), run policy verification, `git push`.
- After a rebase the push needs `--force-with-lease` (refuses if someone else pushed meanwhile): get explicit approval.

## Detached HEAD

- Signal: `state=detached`; `git branch --show-current` prints nothing.
- Cause: checked out a commit, tag, or remote ref directly; or stopped inside a rebase.
- Commits made here belong to no branch. Save them: `git switch -c <rescue>` (creates a branch at HEAD and moves onto it).
- No new commits: `git switch <branch>` to leave.
- `operation=rebase`: it is the rebase's normal detached state, not an error; continue or abort it.

## Lost commits

- Signal: "my commits are gone" after reset, rebase, branch delete, bad checkout.
- Cause: the branch pointer moved; the commits still exist (unreachable ones expire from the reflog after ~30 days).

| Command | What it does |
|---|---|
| `git reflog -30` | Every position HEAD had, newest first, with the command that moved it. |
| `git branch <rescue> <sha>` | Pins the lost commit to a branch so it cannot be garbage-collected. |
| `git reset --hard ORIG_HEAD` | Only for undoing the merge/rebase/pull just made: restores the pre-operation tip. First check `git log -1 ORIG_HEAD`: the next operation overwrites it. Needs user OK. |

## Remote was rewritten

- Signal: diverged right after a teammate (or CI) force-pushed; `git reflog show origin/<branch>` shows the old and new remote tips.
- Preserve your work first: `git branch <backup>`.
- Merge route: `git merge origin/<branch>` (old commits come back as duplicates; resolve).
- Rebase route: `git rebase --onto origin/<branch> <old-remote-tip> <branch>` replays only your commits onto the new tip.

## No upstream / upstream gone

- Signal: `state=no-upstream`; push says "no upstream branch".
- `upstream_gone: true`: the remote branch was deleted (typically its PR merged, then `fetch --prune`). Do not `push -u` it back; see [Squash-merged PR](#squash-merged-pr), or delete the local branch once merged.
- `git push -u origin <branch>` publishes and records the tracking link. For an existing remote branch: `git branch -u origin/<branch>`.

## Dirty tree blocks the operation

- Signal: "Your local changes would be overwritten".
- Simplest: `git pull --autostash` stashes, pulls, re-applies; a conflict on re-apply stays in the tree for you to resolve.
- Manual stash: `git stash push -u -m <unique-tag>`, run the operation, `git stash apply <sha>`, then drop that entry. Never bare `git stash pop` (the stack is shared across worktrees).
- Changes belong on another branch: `git switch -c <wip>`, `git add -A && git commit -m wip`, go back, run the operation, `git cherry-pick -n <wip>` to bring the changes back uncommitted.
- Changes outside the task: stop and ask.

## Prevention catalogue

Pick what matches the cause; map each to policy; propose, do not apply.

- Branch before the first edit; never commit on the default branch (a `pre-commit` hook can refuse: `git branch --show-current` equals `main`).
- Start of session: `git fetch && git status -sb`; end: push or PR.
- `git config pull.ff only` turns a silent merge into a loud stop.
- `git config fetch.prune true` drops dead remote branches.
- `git config rerere.enabled true` for repeated conflicts.
- Keep long-lived branches current: merge/rebase the base into them regularly, in small steps.
- One writer per branch; separate worktrees for parallel tasks.
- Protect the default branch on the hosting site so direct pushes fail.
- Prefer `--force-with-lease` over `--force`, and only on your own branches.
