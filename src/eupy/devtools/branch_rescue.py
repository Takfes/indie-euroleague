"""Diagnose a stuck git situation and draw it: the engine behind the `branch-rescue` skill.

Reads the current repo (read-only, never mutates refs or the work tree) and classifies it into one `state`:
`conflicted`, `in-progress`, `detached`, `no-upstream`, `diverged`, `behind`, `ahead` or `in-sync`. The compared
ref is the upstream of HEAD unless `--against` is given (e.g. `--against origin/main` for a drifted feature
branch); during a merge or rebase it is the other side of that operation.

Usage (repo root):
    uv run python src/eupy/devtools/branch_rescue.py                      # snapshot as JSON on stdout
    uv run python src/eupy/devtools/branch_rescue.py --fetch --html       # refresh remotes, write the page
    uv run python src/eupy/devtools/branch_rescue.py --html out.html --plan plan.json --open

`--html` without a path writes `<git-dir>/branch-rescue/situation.html` (inside `.git`, so never tracked; falls
back to the OS temp dir when that is not writable).
`--plan` is an optional JSON file the page shows beside the diagram:
    {"title": str, "policy": [str], "steps": [{"cmd": str, "does": str, "status": "pending|done|failed"}],
     "prevent": [str]}
The page is one self-contained file (no CDN, works from `file://`); the template is `branch_rescue_page.html`
next to this module and gets the snapshot (plus plan) as JSON at the `__PAYLOAD__` marker.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import webbrowser
from pathlib import Path
from typing import Any

PAGE_TEMPLATE = Path(__file__).with_name("branch_rescue_page.html")
PAYLOAD_MARKER = "__PAYLOAD__"
MAX_LANE = 8  # commits drawn per lane; the rest is summarised as "+N more"
CONFLICT_CODES = frozenset({"DD", "AU", "UD", "UA", "DU", "AA", "UU"})
# (marker inside the git dir, operation name), checked in order
OPERATION_MARKERS = (
    ("rebase-merge", "rebase"),
    ("rebase-apply", "rebase"),
    ("MERGE_HEAD", "merge"),
    ("CHERRY_PICK_HEAD", "cherry-pick"),
    ("REVERT_HEAD", "revert"),
    ("BISECT_LOG", "bisect"),
)
# parents sit mid-record: an empty last field would be eaten by `strip()` on root commits
COMMIT_FORMAT = "%H%x1f%h%x1f%P%x1f%s%x1f%an%x1f%as"


class GitError(RuntimeError):
    """A git command that must succeed failed."""


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=False)  # noqa: S603, S607


def _git(repo: Path, *args: str) -> str:
    proc = _run(repo, *args)
    if proc.returncode:
        raise GitError(f"git {' '.join(args)}: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout.strip()


def _try(repo: Path, *args: str) -> str | None:
    proc = _run(repo, *args)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _commits(repo: Path, *args: str) -> list[dict[str, Any]]:
    """Commits from `git log <args>`, oldest first, as plain dicts."""
    out = _git(repo, "log", "--topo-order", "--reverse", f"--format={COMMIT_FORMAT}", *args)
    commits = []
    for line in filter(None, out.splitlines()):
        sha, short, parents, subject, author, date = line.split("\x1f")
        commits.append({
            "sha": sha,
            "short": short,
            "subject": subject,
            "author": author,
            "date": date,
            "merge": len(parents.split()) > 1,
        })
    return commits


def _refs_by_sha(repo: Path) -> dict[str, list[dict[str, str]]]:
    out = _git(
        repo,
        "for-each-ref",
        "--format=%(objectname) %(refname) %(refname:short)",
        "refs/heads",
        "refs/remotes",
        "refs/tags",
    )
    refs: dict[str, list[dict[str, str]]] = {}
    for line in out.splitlines():
        sha, full, short = line.split(" ", 2)
        if full.endswith("/HEAD"):
            continue
        kind = "local" if full.startswith("refs/heads/") else "remote" if full.startswith("refs/remotes/") else "tag"
        refs.setdefault(sha, []).append({"name": short, "kind": kind})
    return refs


def _operation(git_dir: Path) -> str | None:
    return next((op for marker, op in OPERATION_MARKERS if (git_dir / marker).exists()), None)


def _other_side(git_dir: Path, operation: str | None) -> tuple[str, str] | None:
    """(ref-ish, label) for the other side of an in-progress merge/rebase, if it can be told."""
    if operation == "merge":
        return (git_dir / "MERGE_HEAD").read_text().strip(), "MERGE_HEAD"
    if operation == "rebase":
        onto = git_dir / "rebase-merge" / "onto"
        if onto.exists():
            return onto.read_text().strip(), "onto"
    return None


def _status(repo: Path) -> dict[str, Any]:
    conflicted, staged, unstaged, untracked = [], 0, 0, 0
    # not `_git`: its strip() would eat the leading space of the first " M file" line
    status = _run(repo, "status", "--porcelain=v1")
    if status.returncode:
        raise GitError(f"git status: {status.stderr.strip()}")
    for line in status.stdout.splitlines():
        code, path = line[:2], line[3:]
        if code in CONFLICT_CODES:
            conflicted.append(path)
        elif code == "??":
            untracked += 1
        else:
            staged += code[0] not in " ?"
            unstaged += code[1] != " "
    return {"conflicted": sorted(conflicted), "staged": staged, "unstaged": unstaged, "untracked": untracked}


def _worktrees(repo: Path) -> list[dict[str, Any]]:
    trees, cur = [], {}
    for line in [*_git(repo, "worktree", "list", "--porcelain").splitlines(), ""]:
        if not line:
            if cur:
                trees.append(cur)
            cur = {}
            continue
        key, _, value = line.partition(" ")
        cur[key] = value or True
    return [
        {
            "path": t.get("worktree", ""),
            "branch": str(t.get("branch", "")).removeprefix("refs/heads/") or None,
            "locked": "locked" in t,
        }
        for t in trees
    ]


def _predict_merge(repo: Path, against: str) -> dict[str, Any] | None:
    """Dry-run merge of HEAD and `against` with `git merge-tree` (git >= 2.38); None if unsupported."""
    proc = _run(repo, "merge-tree", "--write-tree", "--name-only", "--no-messages", "HEAD", against)
    if proc.returncode not in (0, 1):
        return None
    files = sorted(set(proc.stdout.splitlines()[1:]) - {""})
    return {"clean": proc.returncode == 0, "conflicts": files}


def _classify(snap: dict[str, Any]) -> tuple[str, str]:
    """(state, one-line headline) from the facts in `snap`."""
    op, status = snap["operation"], snap["status"]
    if op and op != "bisect":
        n = len(status["conflicted"])
        if n:
            return "conflicted", f"{op.capitalize()} stopped: {n} file(s) in conflict"
        return "in-progress", f"{op.capitalize()} in progress, no conflicts left: continue or abort it"
    if snap["head"]["branch"] is None:
        return "detached", f"Detached HEAD at {snap['head']['short']}: commits here belong to no branch"
    if snap["against"] is None:
        if snap["upstream_gone"]:
            return "no-upstream", f"{snap['head']['branch']}: its upstream branch no longer exists on the remote"
        return "no-upstream", f"{snap['head']['branch']} tracks no remote branch"
    ahead, behind = snap["ahead"], snap["behind"]
    ref = snap["against"]["name"]
    if ahead and behind:
        return "diverged", f"Diverged from {ref}: {ahead} local-only, {behind} remote-only commit(s)"
    if behind:
        return "behind", f"Behind {ref} by {behind} commit(s)"
    if ahead:
        return "ahead", f"Ahead of {ref} by {ahead} commit(s), unpushed"
    return "in-sync", f"In sync with {ref}"


def _resolve_against(repo: Path, git_dir: Path, operation: str | None, explicit: str | None) -> tuple[str, str] | None:
    if explicit:
        return explicit, explicit
    if (other := _other_side(git_dir, operation)) is not None:
        return other
    upstream = _try(repo, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}")
    return (upstream, upstream) if upstream else None


def collect(repo: Path, against: str | None = None) -> dict[str, Any]:
    """Snapshot of `repo` as a JSON-serialisable dict: facts, lanes of commits, predicted merge, state."""
    repo = Path(_git(repo, "rev-parse", "--show-toplevel"))
    git_dir = Path(_git(repo, "rev-parse", "--absolute-git-dir"))
    operation = _operation(git_dir)
    branch = _try(repo, "symbolic-ref", "-q", "--short", "HEAD")
    head_sha = _git(repo, "rev-parse", "HEAD")
    snap: dict[str, Any] = {
        "repo": repo.name,
        "head": {"branch": branch, "sha": head_sha, "short": head_sha[:7]},
        "operation": operation,
        "status": _status(repo),
        "worktrees": _worktrees(repo),
        "config": {
            k: _try(repo, "config", "--get", k) for k in ("pull.rebase", "pull.ff", "merge.ff", "rebase.autoStash")
        },
        "against": None,
        "ahead": 0,
        "behind": 0,
        "base": None,
        "local_only": [],
        "remote_only": [],
        "hidden": {"local": 0, "remote": 0},
        "both_touched": [],
        "predicted_merge": None,
    }
    resolved = _resolve_against(repo, git_dir, operation, against)
    # a tracking link is configured but its remote branch is gone (e.g. deleted after a PR merge + fetch --prune)
    snap["upstream_gone"] = bool(
        branch and resolved is None and _try(repo, "config", "--get", f"branch.{branch}.merge")
    )
    if resolved is not None:
        ref, label = resolved
        against_sha = _git(repo, "rev-parse", ref)
        snap["against"] = {"name": label, "sha": against_sha, "short": against_sha[:7]}
        bases = (_try(repo, "merge-base", "--all", "HEAD", ref) or "").splitlines()  # empty: unrelated histories
        snap["criss_cross"] = len(bases) > 1
        base = bases[0] if bases else None
        local = _commits(repo, f"{ref}..HEAD")
        remote = _commits(repo, f"HEAD..{ref}")
        snap["ahead"], snap["behind"] = len(local), len(remote)
        snap["hidden"] = {"local": max(0, len(local) - MAX_LANE), "remote": max(0, len(remote) - MAX_LANE)}
        snap["local_only"], snap["remote_only"] = local[-MAX_LANE:], remote[-MAX_LANE:]
        if base:
            snap["base"] = _commits(repo, "-n", "1", base)[0]
            mine = set(_git(repo, "diff", "--name-only", base, "HEAD").splitlines())
            theirs = set(_git(repo, "diff", "--name-only", base, ref).splitlines())
            snap["both_touched"] = sorted(mine & theirs)
        if local and remote and not operation:
            snap["predicted_merge"] = _predict_merge(repo, ref)
    snap["refs"] = _refs_by_sha(repo)
    snap["state"], snap["headline"] = _classify(snap)
    return snap


def render_html(snap: dict[str, Any], plan: dict[str, Any] | None = None) -> str:
    """The self-contained page for `snap` (and optional `plan`)."""
    payload = json.dumps({"snapshot": snap, "plan": plan}, sort_keys=True).replace("</", "<\\/")
    return PAGE_TEMPLATE.read_text(encoding="utf-8").replace(PAYLOAD_MARKER, payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="repo or worktree to inspect (default: cwd)")
    parser.add_argument(
        "--against", help="ref to compare HEAD with (default: upstream, or the other side of a merge/rebase)"
    )
    parser.add_argument("--fetch", action="store_true", help="run `git fetch` first so remote refs are current")
    parser.add_argument(
        "--html", nargs="?", const="", metavar="PATH", help="write the visual page instead of printing JSON"
    )
    parser.add_argument("--plan", type=Path, help="plan JSON to show beside the diagram (needs --html)")
    parser.add_argument("--open", action="store_true", help="open the written page in the browser")
    args = parser.parse_args(argv)
    if args.plan and args.html is None:
        parser.error("--plan needs --html")

    try:
        if args.fetch:
            try:
                _git(args.repo, "fetch")
            except GitError as exc:  # offline or no remote: diagnose from the refs we have
                print(f"warning: fetch failed, remote refs may be stale ({exc})", file=sys.stderr)
        snap = collect(args.repo, args.against)
    except GitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.html is None:
        print(json.dumps(snap, indent=2, sort_keys=True))
        return 0
    plan = json.loads(args.plan.read_text(encoding="utf-8")) if args.plan else None
    out = (
        Path(args.html)
        if args.html
        else Path(_git(args.repo, "rev-parse", "--absolute-git-dir")) / "branch-rescue" / "situation.html"
    )
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_html(snap, plan), encoding="utf-8")
    except OSError:  # e.g. a sandbox that cannot write into the shared .git dir
        out = Path(tempfile.gettempdir()) / "branch-rescue" / f"{snap['repo']}-situation.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render_html(snap, plan), encoding="utf-8")
    print(f"{snap['state']}: {snap['headline']}\n{out}")
    if args.open:
        webbrowser.open(out.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
