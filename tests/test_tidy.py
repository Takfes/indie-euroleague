"""Tests for the repo-tidy script, run against real throwaway git repos (no mocks of git)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from eupy.devtools import tidy

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def sh(cwd: Path, *args: str) -> str:
    """Run git in `cwd` with a fixed identity; return stdout."""
    return subprocess.run(  # noqa: S603
        ["git", *args],  # noqa: S607
        cwd=cwd,
        env={**os.environ, **GIT_ENV},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def commit(cwd: Path, name: str) -> str:
    """Add a file and commit it; return the new HEAD sha."""
    (cwd / name).write_text(name)
    sh(cwd, "add", name)
    sh(cwd, "commit", "-m", name)
    return sh(cwd, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A primary checkout on `main` with one commit and a git-ignored `.claude/`."""
    primary = tmp_path / "primary"
    primary.mkdir()
    sh(primary, "init", "-b", "main")
    (primary / ".gitignore").write_text(".claude/\n")
    sh(primary, "add", ".gitignore")
    sh(primary, "commit", "-m", "init")
    return primary


def add_worktree(repo: Path, branch: str, dirname: str | None = None) -> Path:
    """Create a task worktree under .claude/worktrees/ on a new branch off main."""
    path = repo / tidy.WORKTREES_SUBDIR / (dirname or tidy.expected_dir(branch))
    sh(repo, "worktree", "add", "-b", branch, str(path))
    return path


def plan(repo: Path, prs: tidy.PrIndex | None = None, current: Path | None = None) -> dict[tuple[str, str], tidy.Item]:
    """Build the plan keyed by (kind, name)."""
    items = tidy.build_plan(repo, current or repo, prs or {})
    return {(i.kind, i.name): i for i in items}


def merged(repo: Path, *branches: str) -> tidy.PrIndex:
    """PR index saying each branch's PR was merged at its current tip."""
    return {b: [("MERGED", sh(repo, "rev-parse", b))] for b in branches}


def test_fresh_branch_without_commits_is_never_deleted(repo: Path) -> None:
    """A just-created worktree looks like a merged one to git; only a merged PR may authorise it."""
    wt = add_worktree(repo, "feat/fresh")
    items = plan(repo)

    assert items[("worktree", ".claude/worktrees/feat+fresh")].status == "blocked"
    tidy.execute(list(items.values()), repo)
    assert wt.exists()


def test_merged_clean_worktree_and_branch_are_deleted(repo: Path) -> None:
    wt = add_worktree(repo, "feat/done")
    items = plan(repo, merged(repo, "feat/done"))
    assert items[("worktree", ".claude/worktrees/feat+done")].status == "delete"
    assert items[("branch", "feat/done")].status == "delete"

    failed = tidy.execute(list(items.values()), repo)

    assert failed == []
    assert not wt.exists()
    assert "feat/done" not in sh(repo, "branch", "--format=%(refname:short)")


def test_unmerged_branch_is_blocked_and_survives(repo: Path) -> None:
    wt = add_worktree(repo, "feat/wip")
    commit(wt, "work.txt")
    items = plan(repo)

    assert items[("worktree", ".claude/worktrees/feat+wip")].status == "blocked"
    assert ("branch", "feat/wip") not in items  # reported once, via its worktree
    tidy.execute(list(items.values()), repo)
    assert wt.exists()


def test_dirty_worktree_of_merged_branch_is_blocked(repo: Path) -> None:
    wt = add_worktree(repo, "chore/dirty")
    (wt / "scratch.txt").write_text("uncommitted")
    item = plan(repo, merged(repo, "chore/dirty"))[("worktree", ".claude/worktrees/chore+dirty")]

    assert item.status == "blocked"
    assert "scratch.txt" in item.reason


def test_squash_merged_branch_needs_merged_pr_at_same_tip(repo: Path) -> None:
    wt = add_worktree(repo, "feat/squashed")
    tip = commit(wt, "a.txt")

    assert plan(repo)[("worktree", ".claude/worktrees/feat+squashed")].status == "blocked"
    merged = {"feat/squashed": [("MERGED", tip)]}
    assert plan(repo, merged)[("worktree", ".claude/worktrees/feat+squashed")].status == "delete"
    later = {"feat/squashed": [("MERGED", "0" * 40)]}
    blocked = plan(repo, later)[("worktree", ".claude/worktrees/feat+squashed")]
    assert blocked.status == "blocked" and "after it" in blocked.reason


def test_open_pr_blocks_even_when_also_merged_earlier(repo: Path) -> None:
    add_worktree(repo, "feat/open")
    prs = {**merged(repo, "feat/open"), "feat/open": [*merged(repo, "feat/open")["feat/open"], ("OPEN", "x")]}
    assert plan(repo, prs)[("worktree", ".claude/worktrees/feat+open")].status == "blocked"


def test_primary_checkout_branch_is_protected(repo: Path) -> None:
    """If the primary checkout sits on a feature branch, tidy must never plan to delete it."""
    sh(repo, "checkout", "-b", "feat/primary-here")
    elsewhere = repo.parent / "elsewhere"
    sh(repo, "worktree", "add", "-b", "feat/elsewhere", str(elsewhere), "main")
    items = plan(repo, merged(repo, "feat/primary-here"), current=elsewhere)
    assert ("branch", "feat/primary-here") not in items


def test_current_worktree_is_never_a_candidate(repo: Path) -> None:
    wt = add_worktree(repo, "feat/me")
    items = plan(repo, current=wt)
    assert not [k for k in items if k[1] in ("feat/me", ".claude/worktrees/feat+me")]


def test_lock_file_stale_vs_live(repo: Path) -> None:
    (repo / ".claude").mkdir(exist_ok=True)
    lock = repo / tidy.LOCK_FILE
    lock.write_text(json.dumps({"pid": 2**22 + 12345}))  # beyond typical pid range -> dead
    assert plan(repo)[("lockfile", str(tidy.LOCK_FILE))].status == "delete"

    lock.write_text(json.dumps({"pid": os.getpid()}))
    assert plan(repo)[("lockfile", str(tidy.LOCK_FILE))].status == "blocked"

    for garbage in ("not json", "42", json.dumps({"no": "pid"})):
        lock.write_text(garbage)
        assert plan(repo)[("lockfile", str(tidy.LOCK_FILE))].status == "blocked", garbage


def test_stale_worktree_lock_is_unlocked_then_removed(repo: Path) -> None:
    wt = add_worktree(repo, "fix/locked")
    sh(repo, "worktree", "lock", "--reason", "claude session x (pid 4194999 start now)", str(wt))
    item = plan(repo, merged(repo, "fix/locked"))[("worktree", ".claude/worktrees/fix+locked")]

    assert item.status == "delete"
    assert item.commands[0][:3] == ["git", "worktree", "unlock"]
    assert tidy.execute([item], repo) == []
    assert not wt.exists()


def test_manual_worktree_lock_without_pid_is_respected(repo: Path) -> None:
    wt = add_worktree(repo, "fix/held")
    sh(repo, "worktree", "lock", "--reason", "keep me", str(wt))
    item = plan(repo, merged(repo, "fix/held"))[("worktree", ".claude/worktrees/fix+held")]

    assert item.status == "blocked"
    tidy.execute([item], repo)
    assert wt.exists()


def test_missing_worktree_dir_is_pruned_not_a_crash(repo: Path) -> None:
    wt = add_worktree(repo, "feat/vanished")
    for child in wt.iterdir():
        child.unlink()
    wt.rmdir()
    item = plan(repo)[("worktree", ".claude/worktrees/feat+vanished")]

    assert item.status == "delete" and item.commands == [["git", "worktree", "prune"]]
    assert tidy.execute([item], repo) == []
    assert str(wt) not in sh(repo, "worktree", "list")


def test_naming_warnings_suggest_rename(repo: Path) -> None:
    wt = add_worktree(repo, "worktree-chore+bad-name", dirname="whatever")
    commit(wt, "x.txt")  # unmerged so it survives and is judged on naming
    items = plan(repo)

    branch_warn = items[("naming", "worktree-chore+bad-name")]
    assert branch_warn.commands == [["git", "branch", "-m", "worktree-chore+bad-name", "chore/bad-name"]]
    assert ("naming", ".claude/worktrees/whatever") in items


def test_remote_branch_deleted_when_integrated(repo: Path, tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    sh(tmp_path, "init", "--bare", "-b", "main", str(origin))
    sh(repo, "remote", "add", "origin", str(origin))
    sh(repo, "push", "origin", "main")
    sh(repo, "push", "origin", "main:refs/heads/feat/gone")
    sh(repo, "fetch", "origin")

    prs = {"feat/gone": [("MERGED", sh(repo, "rev-parse", "main"))]}
    assert plan(repo)[("remote", "origin/feat/gone")].status == "blocked"  # no PR evidence
    item = plan(repo, prs)[("remote", "origin/feat/gone")]
    assert item.status == "delete"
    assert tidy.execute([item], repo) == []
    assert "feat/gone" not in sh(repo, "ls-remote", "--heads", "origin")
