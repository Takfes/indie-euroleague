"""Tests for the `make tidy` Makefile target, run end to end against real throwaway git repos.

`gh` is replaced by a fake executable (via the `GH` env var) that prints canned PR rows, so no
network is touched. Each test builds a primary checkout with task worktrees under
`.claude/worktrees/` and asserts on `make tidy`'s output and on what survives on disk.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

MAKEFILE = Path(__file__).resolve().parents[1] / "Makefile"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}
WT = ".claude/worktrees"


def sh(cwd: Path, *args: str) -> str:
    """Run a command in `cwd` with a fixed git identity; return stripped stdout."""
    return subprocess.run(  # noqa: S603
        args,
        cwd=cwd,
        env={**os.environ, **GIT_ENV},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def git(cwd: Path, *args: str) -> str:
    """Run git in `cwd`."""
    return sh(cwd, "git", *args)


def commit(cwd: Path, name: str) -> str:
    """Commit a new file; return the new HEAD sha."""
    (cwd / name).write_text(name)
    git(cwd, "add", name)
    git(cwd, "commit", "-m", name)
    return git(cwd, "rev-parse", "HEAD")


def dead_pid() -> int:
    """PID of a process that has already exited."""
    proc = subprocess.Popen(["true"])  # noqa: S603, S607
    proc.wait()
    return proc.pid


def tip(repo: Path, branch: str) -> str:
    """Current tip sha of a branch."""
    return git(repo, "rev-parse", branch)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A primary checkout on `main` with one commit and a git-ignored `.claude/`."""
    primary = (tmp_path / "primary").resolve()
    primary.mkdir()
    git(primary, "init", "-b", "main")
    (primary / ".gitignore").write_text(".claude/\ndata/raw_data/\n.venv/\n")
    git(primary, "add", ".gitignore")
    git(primary, "commit", "-m", "init")
    return primary


def add_worktree(repo: Path, branch: str, dirname: str | None = None) -> Path:
    """Create a task worktree under .claude/worktrees/ on a new branch off main."""
    path = repo / WT / (dirname or branch.replace("/", "+"))
    git(repo, "worktree", "add", "-b", branch, str(path))
    return path


def branches(repo: Path) -> list[str]:
    """Local branch names."""
    return git(repo, "for-each-ref", "--format=%(refname:lstrip=2)", "refs/heads").split()


class Tidy:
    """Runs `make tidy` with a fake `gh` serving the given PR rows."""

    def __init__(self, repo: Path, tmp_path: Path) -> None:
        self.repo = repo
        self.rows = tmp_path / "prs.tsv"
        self.rows.write_text("")
        self.gh = tmp_path / "fake-gh"
        # insists on the exact query tidy relies on, so dropping `--state all` or a field fails loudly
        self.gh.write_text(
            "#!/bin/sh\n"
            'case "$*" in *"pr list"*"--state all"*headRefName*state*headRefOid*baseRefName*) ;;\n'
            '  *) echo "fake gh: unexpected args: $*" >&2; exit 2 ;; esac\n'
            '[ -n "$FAKE_GH_FAIL" ] && exit 1\n'
            'cat "$FAKE_GH_ROWS"\n'
        )
        self.gh.chmod(0o755)

    def prs(self, *rows: tuple[str, ...]) -> None:
        """Set the PR index: (headRefName, state, headRefOid[, baseRefName]) rows; base defaults to main."""
        padded = [r if len(r) == 4 else (*r, "main") for r in rows]
        self.rows.write_text("".join("\t".join(r) + "\n" for r in padded))

    def merged(self, *names: str) -> None:
        """Mark each branch's PR as merged at its current tip."""
        self.prs(*[(n, "MERGED", tip(self.repo, n)) for n in names])

    def run(self, *args: str, cwd: Path | None = None, gh_fails: bool = False) -> subprocess.CompletedProcess[str]:
        """Invoke `make tidy ARGS='<args>'` (no tty, so prompts are never reached)."""
        env = {**os.environ, **GIT_ENV, "GH": str(self.gh), "FAKE_GH_ROWS": str(self.rows)}
        env.pop("FAKE_GH_FAIL", None)
        if gh_fails:
            env["FAKE_GH_FAIL"] = "1"
        return subprocess.run(  # noqa: S603
            ["make", "-s", "-f", str(MAKEFILE), "tidy", f"ARGS={' '.join(args)}"],  # noqa: S607
            cwd=cwd or self.repo,
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
        )


@pytest.fixture
def tidy(repo: Path, tmp_path: Path) -> Tidy:
    return Tidy(repo, tmp_path)


def test_fresh_branch_without_commits_is_never_deleted(repo: Path, tidy: Tidy) -> None:
    """A just-created worktree looks merged to git; only a merged PR may authorise deletion."""
    wt = add_worktree(repo, "feat/fresh")
    out = tidy.run("--yes")

    assert "BLOCKED" in out.stdout and "no merged PR found" in out.stdout
    assert wt.exists() and "feat/fresh" in branches(repo)


def test_dry_run_lists_safe_items_and_manual_commands_but_deletes_nothing(repo: Path, tidy: Tidy) -> None:
    wt = add_worktree(repo, "feat/done")
    tidy.merged("feat/done")
    out = tidy.run("--dry-run")

    assert "SAFE TO DELETE (2)" in out.stdout
    assert f"git worktree remove {wt}" in out.stdout and "git branch -D feat/done" in out.stdout
    assert wt.exists() and "feat/done" in branches(repo)


def test_default_without_a_terminal_deletes_nothing(repo: Path, tidy: Tidy) -> None:
    wt = add_worktree(repo, "feat/done")
    tidy.merged("feat/done")
    out = tidy.run()

    assert "Not a terminal: nothing deleted" in out.stdout
    assert wt.exists()


def test_yes_deletes_merged_worktree_and_branch(repo: Path, tidy: Tidy) -> None:
    wt = add_worktree(repo, "feat/done")
    tidy.merged("feat/done")
    out = tidy.run("--yes")

    assert out.returncode == 0, out.stderr
    assert "deleted [worktree]" in out.stdout and "deleted [branch] feat/done" in out.stdout
    assert not wt.exists() and "feat/done" not in branches(repo)


def test_unmerged_branch_is_blocked_and_reported_once_via_its_worktree(repo: Path, tidy: Tidy) -> None:
    wt = add_worktree(repo, "feat/wip")
    commit(wt, "work.txt")
    out = tidy.run("--yes")

    assert "branch not integrated" in out.stdout and "1 commit(s) not in main" in out.stdout
    assert "[branch] feat/wip" not in out.stdout
    assert wt.exists()


def test_dirty_worktree_of_merged_branch_is_blocked(repo: Path, tidy: Tidy) -> None:
    wt = add_worktree(repo, "chore/dirty")
    (wt / "scratch.txt").write_text("uncommitted")
    tidy.merged("chore/dirty")
    out = tidy.run("--yes")

    assert "uncommitted changes" in out.stdout and "scratch.txt" in out.stdout
    assert wt.exists() and "chore/dirty" in branches(repo)


def test_squash_merge_needs_merged_pr_at_the_same_tip(repo: Path, tidy: Tidy) -> None:
    wt = add_worktree(repo, "feat/squashed")
    sha = commit(wt, "a.txt")

    tidy.prs(("feat/squashed", "MERGED", "0" * 40))  # PR merged, but branch moved on afterwards
    assert "commits after it" in tidy.run("--yes").stdout and wt.exists()

    tidy.prs(("feat/squashed", "MERGED", sha))
    assert tidy.run("--yes").returncode == 0 and not wt.exists()


def test_open_pr_blocks_even_if_an_earlier_pr_was_merged(repo: Path, tidy: Tidy) -> None:
    wt = add_worktree(repo, "feat/open")
    tidy.prs(("feat/open", "MERGED", tip(repo, "feat/open")), ("feat/open", "OPEN", "x"))
    out = tidy.run("--yes")

    assert "open PR" in out.stdout and wt.exists()


def test_current_worktree_is_never_a_candidate(repo: Path, tidy: Tidy) -> None:
    wt = add_worktree(repo, "feat/me")
    tidy.merged("feat/me")
    out = tidy.run("--yes", cwd=wt)

    assert wt.exists() and "feat/me" in branches(repo)
    assert "feat+me" not in out.stdout


def test_primary_checkout_branch_is_protected(repo: Path, tidy: Tidy, tmp_path: Path) -> None:
    git(repo, "checkout", "-b", "feat/primary-here")
    elsewhere = tmp_path / "elsewhere"
    git(repo, "worktree", "add", "-b", "feat/elsewhere", str(elsewhere), "main")
    tidy.merged("feat/primary-here")
    out = tidy.run("--yes", cwd=elsewhere)

    assert out.returncode == 0, out.stdout
    assert "feat/primary-here" in branches(repo) and "feat/primary-here" not in out.stdout


def test_worktree_on_a_protected_branch_is_kept(repo: Path, tidy: Tidy) -> None:
    git(repo, "checkout", "-b", "feat/p")  # primary leaves main, freeing it for a worktree
    wt = repo / WT / "main-copy"
    git(repo, "worktree", "add", str(wt), "main")
    tidy.merged("main")
    out = tidy.run("--yes")

    assert "is protected" in out.stdout and wt.exists()


def test_closed_unmerged_pr_is_not_treated_as_merged(repo: Path, tidy: Tidy) -> None:
    wt = add_worktree(repo, "feat/abandoned")
    tidy.prs(("feat/abandoned", "CLOSED", tip(repo, "feat/abandoned")))
    out = tidy.run("--yes")

    assert "PR closed without merging" in out.stdout and wt.exists()


def test_base_branch_of_an_open_stacked_pr_is_kept(repo: Path, tidy: Tidy) -> None:
    wt = add_worktree(repo, "feat/a")
    tidy.prs(("feat/a", "MERGED", tip(repo, "feat/a")), ("feat/b", "OPEN", "x", "feat/a"))
    out = tidy.run("--yes")

    assert "stacked" in out.stdout and wt.exists()


def test_a_tag_named_like_the_branch_does_not_fool_the_merge_check(repo: Path, tidy: Tidy) -> None:
    main_sha = tip(repo, "main")
    git(repo, "tag", "feat/t", main_sha)  # `git rev-parse feat/t` would resolve to this tag
    wt = add_worktree(repo, "feat/t")
    commit(wt, "unmerged.txt")
    tidy.prs(("feat/t", "MERGED", main_sha))
    out = tidy.run("--yes")

    assert wt.exists() and "feat/t" in branches(repo), out.stdout
    assert "this tip differs" in out.stdout


def test_worktree_dir_named_differently_from_its_branch_is_still_handled(repo: Path, tidy: Tidy) -> None:
    wt = add_worktree(repo, "feat/odd", dirname="odd-dir")
    tidy.merged("feat/odd")
    out = tidy.run("--yes")

    assert out.returncode == 0, out.stdout
    assert not wt.exists() and "feat/odd" not in branches(repo)


def test_hostile_branch_names_cannot_inject_commands(repo: Path, tidy: Tidy) -> None:
    names = ["feat/a;touch${IFS}PWNED1;b", "feat/c$(touch${IFS}PWNED2)d", "feat/e`touch${IFS}PWNED3`f"]
    for n in names:
        add_worktree(repo, n)
    tidy.merged(*names)
    out = tidy.run("--yes")

    assert out.returncode == 0, out.stdout
    assert not list(repo.rglob("PWNED*")), "a branch name was executed as shell"
    assert not [n for n in names if n in branches(repo)]


def test_git_ignored_data_in_a_worktree_blocks_removal_but_rebuildable_files_do_not(repo: Path, tidy: Tidy) -> None:
    data = add_worktree(repo, "feat/has-data")
    (data / "data" / "raw_data").mkdir(parents=True)
    (data / "data" / "raw_data" / "prices.csv").write_text("x")
    clean = add_worktree(repo, "feat/only-venv")
    (clean / ".venv" / "lib").mkdir(parents=True)
    (clean / ".venv" / "lib" / "x.py").write_text("x")
    tidy.merged("feat/has-data", "feat/only-venv")
    out = tidy.run("--yes")

    assert "git-ignored files would be lost" in out.stdout and "data/;" in out.stdout
    assert data.exists() and not clean.exists()


def test_locked_worktree_with_missing_dir_follows_the_lock_rule(repo: Path, tidy: Tidy) -> None:
    stale = add_worktree(repo, "fix/stale-gone")
    live = add_worktree(repo, "fix/live-gone")
    git(repo, "worktree", "lock", "--reason", f"claude session a (pid {dead_pid()} start now)", str(stale))
    git(repo, "worktree", "lock", "--reason", f"claude session b (pid {os.getpid()} start now)", str(live))
    for wt in (stale, live):
        shutil.rmtree(wt)
    out = tidy.run("--yes")
    listing = git(repo, "worktree", "list")

    assert out.returncode == 0, out.stdout
    assert str(stale) not in listing and str(live) in listing


def test_scheduler_lock_file_stale_live_and_garbage(repo: Path, tidy: Tidy) -> None:
    lock = repo / ".claude" / "scheduled_tasks.lock"
    lock.parent.mkdir(exist_ok=True)

    lock.write_text(json.dumps({"pid": os.getpid()}))
    assert "session pid" in tidy.run("--yes").stdout and lock.exists()

    for garbage in ("not json", "42", json.dumps({"no": "pid"})):
        lock.write_text(garbage)
        assert "unreadable lock content" in tidy.run("--yes").stdout and lock.exists(), garbage

    lock.write_text(json.dumps({"pid": dead_pid()}))
    assert tidy.run("--yes").returncode == 0 and not lock.exists()


def test_stale_worktree_lock_is_unlocked_then_removed(repo: Path, tidy: Tidy) -> None:
    wt = add_worktree(repo, "fix/locked")
    git(repo, "worktree", "lock", "--reason", f"claude session x (pid {dead_pid()} start now)", str(wt))
    tidy.merged("fix/locked")
    out = tidy.run("--yes")

    assert out.returncode == 0, out.stdout
    assert not wt.exists()


def test_worktree_lock_without_pid_or_with_live_pid_is_respected(repo: Path, tidy: Tidy) -> None:
    held = add_worktree(repo, "fix/held")
    live = add_worktree(repo, "fix/live")
    git(repo, "worktree", "lock", "--reason", "keep me", str(held))
    git(repo, "worktree", "lock", "--reason", f"claude session y (pid {os.getpid()} start now)", str(live))
    tidy.merged("fix/held", "fix/live")
    out = tidy.run("--yes")

    assert out.stdout.count("locked, not provably stale") == 2
    assert held.exists() and live.exists()


def test_missing_worktree_dir_is_pruned_not_a_crash(repo: Path, tidy: Tidy) -> None:
    wt = add_worktree(repo, "feat/vanished")
    for child in wt.iterdir():
        child.unlink()
    wt.rmdir()
    out = tidy.run("--yes")

    assert out.returncode == 0 and "directory missing" in out.stdout
    assert str(wt) not in git(repo, "worktree", "list")


def test_naming_warnings_suggest_a_rename(repo: Path, tidy: Tidy) -> None:
    wt = add_worktree(repo, "worktree-chore+bad-name", dirname="whatever")
    commit(wt, "x.txt")  # unmerged, so it survives and is judged on naming
    out = tidy.run("--dry-run").stdout

    assert "NAMING WARNINGS" in out
    assert "fix: git branch -m worktree-chore+bad-name chore/bad-name" in out
    assert "worktree dir should be worktree-chore+bad-name" in out


def test_remote_branch_deleted_only_with_merged_pr_evidence(repo: Path, tidy: Tidy, tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "origin", "main")
    git(repo, "push", "origin", "main:refs/heads/feat/gone")

    assert "no merged PR found" in tidy.run("--yes").stdout
    assert "feat/gone" in git(repo, "ls-remote", "--heads", "origin")

    tidy.prs(("feat/gone", "MERGED", tip(repo, "main")))
    assert tidy.run("--yes").returncode == 0
    assert "feat/gone" not in git(repo, "ls-remote", "--heads", "origin")


def test_gh_failure_blocks_everything_with_a_note(repo: Path, tidy: Tidy) -> None:
    wt = add_worktree(repo, "feat/done")
    tidy.merged("feat/done")
    out = tidy.run("--yes", gh_fails=True)

    assert "PR state unavailable" in out.stdout
    assert wt.exists()


def test_yes_deletes_what_is_safe_and_reports_what_was_kept(repo: Path, tidy: Tidy) -> None:
    add_worktree(repo, "feat/kept")  # blocked: fresh, no PR
    done = add_worktree(repo, "feat/done")
    tidy.merged("feat/done")
    out = tidy.run("--yes")

    assert "NOT DELETED" in out.stdout and "[worktree] .claude/worktrees/feat+kept" in out.stdout
    assert out.returncode == 0 and not done.exists()


def test_unknown_argument_is_rejected(tidy: Tidy) -> None:
    out = tidy.run("--nope")
    assert out.returncode != 0 and "unknown argument" in out.stderr
