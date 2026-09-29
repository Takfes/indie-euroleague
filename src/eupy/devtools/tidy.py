#!/usr/bin/env python3
"""Find and remove leftovers after PRs merge: task worktrees, local/remote branches, stale locks.

Read-only by default: prints a summary of what is safe to delete, what is blocked (and why), and
naming-convention warnings, then asks once before deleting the safe items. Everything it would run
is also printed as plain commands, so you can delete by hand instead.

An item is *safe* only when all hold:

* a PR from this branch was merged at exactly this tip and none is open (git ancestry alone is not
  enough: a just-created branch with no commits looks identical to a merged one);
* for a worktree: it is clean (no uncommitted changes), not the worktree you are running from, and
  not locked by a live session (a lock whose PID is dead counts as stale and is unlocked first).

Anything else is reported as *blocked* with a reason -- never forced. Needs an authenticated `gh`;
without it everything is blocked, which is the safe direction. Known gap: git-ignored files inside
a worktree (e.g. `data/raw_data/`) are not checked and are lost with it. `--dry-run` still runs
`git fetch --prune` (refreshes remote-tracking refs only).

Naming convention (see AGENTS.md): branch `<type>/<short-name>` (type in feat/fix/chore/refactor/
test/docs, short-name lowercase kebab-case); worktree dir `.claude/worktrees/<type>+<short-name>`.

Usage:
    python src/eupy/devtools/tidy.py [--yes] [--dry-run]
    make tidy [ARGS=--yes]

    (no flag)  summary, then prompt before deleting (summary only when stdin is not a terminal)
    --yes      delete every safe item without asking, then report what was not deleted
    --dry-run  summary and manual commands only, never delete

Data I/O: none (reads git/gh state; deletes worktrees, branches and the stale scheduler lock).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

TYPES = ("feat", "fix", "chore", "refactor", "test", "docs")
BRANCH_RE = re.compile(rf"^({'|'.join(TYPES)})/[a-z0-9]+(-[a-z0-9]+)*$")
WORKTREES_SUBDIR = Path(".claude") / "worktrees"
LOCK_FILE = Path(".claude") / "scheduled_tasks.lock"

# headRefName -> [(state, headRefOid), ...]; state is OPEN / MERGED / CLOSED
PrIndex = dict[str, list[tuple[str, str]]]


@dataclass
class Item:
    """One finding. `status`: delete (safe) | blocked | warn. `commands` are argv lists."""

    kind: str
    name: str
    status: str
    reason: str
    commands: list[list[str]] = field(default_factory=list)


@dataclass
class Context:
    """Repo facts shared by the finders."""

    repo: Path  # primary checkout
    current: Path  # where the user is running from
    prs: PrIndex
    base: str  # integration target, e.g. origin/main
    task_root: Path
    worktrees: list[dict[str, str | None]]
    protected: set[str] = field(default_factory=set)  # branches never touched
    kept_worktree_branches: set[str] = field(default_factory=set)


def run(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command in `cwd`, capturing text output."""
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=check)  # noqa: S603


def git(repo: Path, *args: str) -> str:
    """Return stripped stdout of `git <args>` run in `repo`; raise on failure."""
    return run(["git", *args], repo).stdout.strip()


def git_ok(repo: Path, *args: str) -> bool:
    """True when `git <args>` exits 0."""
    return run(["git", *args], repo, check=False).returncode == 0


def pid_alive(pid: int) -> bool:
    """True if a process with this PID exists (signal 0 probe)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def load_pr_index(repo: Path) -> tuple[PrIndex, str | None]:
    """PR states per head branch via `gh`; returns (index, warning-or-None)."""
    try:
        out = run(
            ["gh", "pr", "list", "--state", "all", "--limit", "500", "--json", "headRefName,state,headRefOid"],
            repo,
        ).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        return {}, f"PR state unavailable ({exc.__class__.__name__}); relying on git ancestry only"
    index: PrIndex = {}
    for pr in json.loads(out):
        index.setdefault(pr["headRefName"], []).append((pr["state"], pr["headRefOid"]))
    return index, None


def base_ref(repo: Path) -> str:
    """Integration target: origin/<default> when it exists, else local main."""
    head = run(["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"], repo, check=False)
    if head.returncode == 0 and head.stdout.strip():
        return head.stdout.strip()
    return "main"


def integration(ctx: Context, branch: str, tip: str) -> tuple[bool, str]:
    """Is `tip` of `branch` safely integrated into the base? Returns (integrated, reason)."""
    states = ctx.prs.get(branch, [])
    if any(state == "OPEN" for state, _ in states):
        return False, "open PR"
    if any(state == "MERGED" and oid == tip for state, oid in states):
        return True, "PR merged at this tip"
    # Git ancestry alone can't tell "merged" from "just created, nothing committed yet", so it
    # never authorises deletion -- only explains why we are keeping the branch.
    if any(state == "MERGED" for state, _ in states):
        return False, "PR merged, but branch has commits after it"
    if git_ok(ctx.repo, "merge-base", "--is-ancestor", tip, ctx.base):
        return False, f"no merged PR found (fresh branch, or gh unavailable); tip is in {ctx.base}"
    unique = [ln for ln in git(ctx.repo, "cherry", ctx.base, tip).splitlines() if ln.startswith("+")]
    return False, f"{len(unique)} commit(s) not in {ctx.base} and no merged PR"


def parse_worktrees(repo: Path) -> list[dict[str, str | None]]:
    """Parse `git worktree list --porcelain` into dicts (path, branch, locked)."""
    entries: list[dict[str, str | None]] = []
    for block in git(repo, "worktree", "list", "--porcelain").split("\n\n"):
        entry: dict[str, str | None] = {"path": None, "branch": None, "locked": None}
        for line in block.splitlines():
            key, _, value = line.partition(" ")
            if key == "worktree":
                entry["path"] = value
            elif key == "branch":
                entry["branch"] = value.removeprefix("refs/heads/")
            elif key == "locked":
                entry["locked"] = value or "locked"
        if entry["path"]:
            entries.append(entry)
    return entries


def expected_dir(branch: str) -> str:
    """Worktree directory name for a branch: `/` becomes `+`."""
    return branch.replace("/", "+")


def live_lock_reason(lock: str | None) -> str | None:
    """Reason string unless the worktree lock is provably stale (names a PID that is dead)."""
    if lock is None:
        return None
    match = re.search(r"pid (\d+)", lock)
    if match and not pid_alive(int(match.group(1))):
        return None
    return f"locked, not provably stale ({lock})"


def worktree_item(ctx: Context, wt: dict[str, str | None]) -> Item | None:
    """Classify one task worktree; None when it is not ours to judge."""
    path, branch, lock = Path(wt["path"] or "").resolve(), wt["branch"], wt["locked"]
    if ctx.task_root not in path.parents:
        if branch:
            ctx.protected.add(branch)
        return None
    if path == ctx.current or path in ctx.current.parents:
        ctx.protected.add(branch or "")
        return None  # the worktree we are standing in is never a candidate
    label = str(path.relative_to(ctx.task_root.parents[1]))
    if not branch:
        return Item("worktree", label, "blocked", "detached HEAD; inspect manually")
    ctx.kept_worktree_branches.add(branch)
    if not path.exists():
        ctx.kept_worktree_branches.discard(branch)
        return Item(
            "worktree", label, "delete", "directory missing (stale registration)", [["git", "worktree", "prune"]]
        )
    if live := live_lock_reason(lock):
        return Item("worktree", label, "blocked", live)
    ok, why = integration(ctx, branch, git(ctx.repo, "rev-parse", branch))
    if dirty := git(path, "status", "--porcelain"):
        hint = f"after reviewing: git worktree remove --force {path}"
        return Item("worktree", label, "blocked", f"uncommitted changes ({hint}):\n{dirty}")
    if not ok:
        return Item("worktree", label, "blocked", f"branch not integrated: {why}")
    ctx.kept_worktree_branches.discard(branch)
    commands = [["git", "worktree", "unlock", str(path)]] if lock else []
    return Item("worktree", label, "delete", why, [*commands, ["git", "worktree", "remove", str(path)]])


def local_branch_items(ctx: Context) -> list[Item]:
    """Local branches that are integrated (delete) or not (blocked)."""
    items = []
    refs = git(ctx.repo, "for-each-ref", "--format=%(refname:short) %(objectname)", "refs/heads")
    for line in refs.splitlines():
        branch, tip = line.split()
        if branch in ctx.protected or branch in ctx.kept_worktree_branches:
            continue  # protected, or reported through its (kept) worktree
        ok, why = integration(ctx, branch, tip)
        cmds = [["git", "branch", "-D", branch]] if ok else []
        items.append(Item("branch", branch, "delete" if ok else "blocked", why, cmds))
    return items


def remote_branch_items(ctx: Context) -> list[Item]:
    """origin/* branches that are integrated (delete) or not (blocked)."""
    items = []
    prefix = "refs/remotes/origin/"
    refs = git(ctx.repo, "for-each-ref", "--format=%(refname) %(objectname)", prefix)
    for line in refs.splitlines():
        ref, tip = line.split()
        name = ref.removeprefix(prefix)
        if name == "HEAD" or name in ctx.protected:
            continue
        ok, why = integration(ctx, name, tip)
        cmds = [["git", "push", "origin", "--delete", name]] if ok else []
        items.append(Item("remote", f"origin/{name}", "delete" if ok else "blocked", why, cmds))
    return items


def lock_file_item(ctx: Context) -> list[Item]:
    """The scheduler lock: stale (delete) when its PID is dead, blocked when alive."""
    path = ctx.repo / LOCK_FILE
    if not path.exists():
        return []
    try:
        pid = int(json.loads(path.read_text())["pid"])
    except (ValueError, OSError, KeyError, TypeError):
        return [Item("lockfile", str(LOCK_FILE), "blocked", "unreadable lock content; inspect manually")]
    if pid_alive(pid):
        return [Item("lockfile", str(LOCK_FILE), "blocked", f"session pid {pid} alive")]
    return [Item("lockfile", str(LOCK_FILE), "delete", f"pid {pid} not running", [["rm", "-f", str(path)]])]


def naming_items(ctx: Context, planned: list[Item]) -> list[Item]:
    """Warn about surviving branches / worktree dirs that break the naming convention."""
    going = {i.name for i in planned if i.status == "delete"}
    items = []
    for name in git(ctx.repo, "for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines():
        if name in ctx.protected or name in going or BRANCH_RE.match(name):
            continue
        hint = re.sub(r"^worktree-", "", name).replace("+", "/", 1)
        cmds = [["git", "branch", "-m", name, hint]] if BRANCH_RE.match(hint) else []
        rule = f"branch name must match <type>/<short-name> ({'/'.join(TYPES)})"
        items.append(Item("naming", name, "warn", rule, cmds))
    for wt in ctx.worktrees[1:]:
        path, branch = Path(wt["path"] or "").resolve(), wt["branch"]
        label = str(path.relative_to(ctx.task_root.parents[1])) if ctx.task_root in path.parents else ""
        if label and branch and path.name != expected_dir(branch) and label not in going:
            items.append(Item("naming", label, "warn", f"worktree dir should be {expected_dir(branch)}"))
    return items


def build_plan(repo: Path, current: Path, prs: PrIndex) -> list[Item]:
    """Assemble findings. `repo` = primary checkout, `current` = where the user is running."""
    base = base_ref(repo)
    worktrees = parse_worktrees(repo)
    primary = Path(worktrees[0]["path"] or repo).resolve()
    ctx = Context(
        repo=primary,
        current=current.resolve(),
        prs=prs,
        base=base,
        task_root=primary / WORKTREES_SUBDIR,
        worktrees=worktrees,
        protected={base.removeprefix("origin/")},
    )
    items = [i for wt in worktrees[1:] if (i := worktree_item(ctx, wt))]
    ctx.protected |= {git(ctx.current, "branch", "--show-current"), git(primary, "branch", "--show-current")}
    items += local_branch_items(ctx) + remote_branch_items(ctx) + lock_file_item(ctx)
    return items + naming_items(ctx, items)


def format_cmd(argv: list[str]) -> str:
    """Shell-quoted one-line rendering of an argv list."""
    return shlex.join(argv)


def print_summary(items: list[Item], note: str | None) -> None:
    """Print findings grouped by status."""
    if note:
        print(f"note: {note}\n")
    for status, title in (("delete", "SAFE TO DELETE"), ("blocked", "BLOCKED (kept)"), ("warn", "NAMING WARNINGS")):
        group = [i for i in items if i.status == status]
        if not group:
            continue
        print(f"{title} ({len(group)})")
        for i in group:
            first, *rest = i.reason.splitlines()
            print(f"  [{i.kind}] {i.name} -- {first}")
            for extra in rest:
                print(f"        {extra}")
        print()
    if not items:
        print("Nothing to tidy.")


def print_manual(items: list[Item]) -> None:
    """Print the deletion commands so they can be run by hand."""
    cmds = [format_cmd(c) for i in items if i.status == "delete" for c in i.commands]
    if cmds:
        print("To delete manually:")
        for cmd in cmds:
            print(f"  {cmd}")
        print()


def execute(items: list[Item], repo: Path) -> list[tuple[Item, str]]:
    """Run every safe item; return the (item, error) pairs that failed."""
    failed: list[tuple[Item, str]] = []
    for item in (i for i in items if i.status == "delete"):
        for argv in item.commands:
            result = run(argv, repo, check=False)
            if result.returncode:
                failed.append((item, (result.stderr or result.stdout).strip()))
                break
        else:
            print(f"  deleted [{item.kind}] {item.name}")
    run(["git", "worktree", "prune"], repo, check=False)
    return failed


def print_leftovers(items: list[Item], failed: list[tuple[Item, str]]) -> None:
    """Report what was not deleted after an execute pass, with manual commands for failures."""
    left = [i for i in items if i.status == "blocked"]
    if not (failed or left):
        return
    print("\nNOT DELETED")
    for item, err in failed:
        print(f"  [{item.kind}] {item.name} -- failed: {err}")
        for cmd in item.commands:
            print(f"      {format_cmd(cmd)}")
    for item in left:
        print(f"  [{item.kind}] {item.name} -- {item.reason.splitlines()[0]}")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--yes", "-y", action="store_true", help="delete safe items without asking")
    parser.add_argument("--dry-run", action="store_true", help="never delete; print commands only")
    args = parser.parse_args(argv)

    here = Path.cwd()
    run(["git", "fetch", "--prune", "--quiet"], here, check=False)
    primary = Path(parse_worktrees(here)[0]["path"] or here)
    prs, note = load_pr_index(primary)
    items = build_plan(primary, here, prs)
    print_summary(items, note)
    deletable = [i for i in items if i.status == "delete"]
    if not deletable:
        return 0

    if args.dry_run or (not args.yes and not sys.stdin.isatty()):
        print_manual(items)
        if not args.dry_run:
            print("Not a terminal: nothing deleted. Re-run with --yes to delete.")
        return 0
    if not args.yes:
        print_manual(items)
        if input(f"Delete {len(deletable)} safe item(s)? [y/N] ").strip().lower() != "y":
            print("Nothing deleted.")
            return 0
    failed = execute(items, primary)
    print_leftovers(items, failed)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
