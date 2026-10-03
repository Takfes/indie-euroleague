"""`branch_rescue.collect` classifies real throwaway repos (bare origin + clone) and the page embeds the snapshot."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from eupy.devtools import branch_rescue as br

ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.com",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.com",
}


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, env=ENV, capture_output=True, text=True, check=check)  # noqa: S603, S607


def commit(repo: Path, name: str, text: str = "x\n", msg: str | None = None) -> None:
    (repo / name).write_text(text)
    git(repo, "add", name)
    git(repo, "commit", "-m", msg or f"edit {name}")


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    """A clone of a bare origin, one shared commit, on main tracking origin/main."""
    origin, work = tmp_path / "origin.git", tmp_path / "work"
    git(tmp_path, "init", "--bare", "-b", "main", str(origin))
    git(tmp_path, "clone", str(origin), str(work))
    git(work, "checkout", "-b", "main")
    commit(work, "shared.txt", "base\n", "shared base")
    git(work, "push", "-u", "origin", "main")
    return work


def push_from_second_clone(clone: Path, name: str, text: str = "remote\n") -> None:
    """Land a commit on origin/main from another clone, then fetch it into `clone`."""
    other = clone.parent / "other"
    if not other.exists():
        git(clone.parent, "clone", str(clone.parent / "origin.git"), str(other))
    commit(other, name, text, f"remote {name}")
    git(other, "push", "origin", "HEAD:main")
    git(clone, "fetch")


def test_in_sync(clone: Path) -> None:
    snap = br.collect(clone)
    assert snap["state"] == "in-sync"
    assert (snap["ahead"], snap["behind"]) == (0, 0)


def test_ahead(clone: Path) -> None:
    commit(clone, "mine.txt")
    snap = br.collect(clone)
    assert (snap["state"], snap["ahead"], snap["behind"]) == ("ahead", 1, 0)
    assert [c["subject"] for c in snap["local_only"]] == ["edit mine.txt"]


def test_behind(clone: Path) -> None:
    push_from_second_clone(clone, "theirs.txt")
    snap = br.collect(clone)
    assert (snap["state"], snap["ahead"], snap["behind"]) == ("behind", 0, 1)


def test_diverged_disjoint_files_predicts_clean_merge(clone: Path) -> None:
    commit(clone, "mine.txt")
    push_from_second_clone(clone, "theirs.txt")
    snap = br.collect(clone)
    assert snap["state"] == "diverged"
    assert snap["base"]["subject"] == "shared base"
    assert snap["both_touched"] == []
    assert snap["predicted_merge"] == {"clean": True, "conflicts": []}


def test_diverged_same_file_predicts_conflict(clone: Path) -> None:
    commit(clone, "shared.txt", "mine\n")
    push_from_second_clone(clone, "shared.txt", "theirs\n")
    snap = br.collect(clone)
    assert snap["state"] == "diverged"
    assert snap["both_touched"] == ["shared.txt"]
    assert snap["predicted_merge"] == {"clean": False, "conflicts": ["shared.txt"]}


def test_collect_does_not_touch_the_repo(clone: Path) -> None:
    commit(clone, "shared.txt", "mine\n")
    push_from_second_clone(clone, "shared.txt", "theirs\n")
    before = (git(clone, "rev-parse", "HEAD").stdout, git(clone, "status", "--porcelain").stdout)
    br.collect(clone)
    assert (git(clone, "rev-parse", "HEAD").stdout, git(clone, "status", "--porcelain").stdout) == before


def test_merge_stopped_on_conflict(clone: Path) -> None:
    commit(clone, "shared.txt", "mine\n")
    push_from_second_clone(clone, "shared.txt", "theirs\n")
    assert git(clone, "merge", "origin/main", check=False).returncode != 0
    snap = br.collect(clone)
    assert snap["state"] == "conflicted"
    assert snap["operation"] == "merge"
    assert snap["status"]["conflicted"] == ["shared.txt"]
    assert snap["against"]["name"] == "MERGE_HEAD"


def test_detached_head(clone: Path) -> None:
    git(clone, "checkout", "--detach")
    snap = br.collect(clone)
    assert snap["state"] == "detached"
    assert snap["head"]["branch"] is None


def test_no_upstream(clone: Path) -> None:
    git(clone, "checkout", "-b", "side")
    assert br.collect(clone)["state"] == "no-upstream"


def test_against_compares_feature_branch_with_main(clone: Path) -> None:
    git(clone, "checkout", "-b", "feat/x")
    commit(clone, "feature.txt")
    push_from_second_clone(clone, "theirs.txt")
    snap = br.collect(clone, against="origin/main")
    assert (snap["state"], snap["ahead"], snap["behind"]) == ("diverged", 1, 1)


def test_lane_is_capped_and_hidden_count_reported(clone: Path) -> None:
    for i in range(br.MAX_LANE + 3):
        commit(clone, f"f{i}.txt")
    snap = br.collect(clone)
    assert len(snap["local_only"]) == br.MAX_LANE
    assert snap["hidden"]["local"] == 3
    assert snap["local_only"][-1]["sha"] == snap["head"]["sha"]


def test_render_html_embeds_snapshot_and_plan_safely(clone: Path) -> None:
    commit(clone, "mine.txt", msg="</script><b>x")
    page = br.render_html(br.collect(clone), {"title": "t", "steps": []})
    assert br.PAYLOAD_MARKER not in page
    payload = re.search(r'<script id="payload" type="application/json">(.*?)</script>', page, re.S)
    assert payload, "payload script tag must survive a hostile commit subject"
    data = json.loads(payload.group(1))
    assert data["snapshot"]["local_only"][0]["subject"] == "</script><b>x"
    assert data["plan"]["title"] == "t"


def test_cli_writes_page_inside_git_dir(clone: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert br.main(["--repo", str(clone), "--html"]) == 0
    out = capsys.readouterr().out
    page = clone / ".git" / "branch-rescue" / "situation.html"
    assert page.is_file()
    assert out.startswith("in-sync:")


def test_status_counts_unstaged_first_line_correctly(clone: Path) -> None:
    """Regression: `git status --porcelain` starts with a space for unstaged edits; stripping it misread the code."""
    (clone / "shared.txt").write_text("changed\n")
    status = br.collect(clone)["status"]
    assert (status["staged"], status["unstaged"], status["untracked"]) == (0, 1, 0)


def test_unrelated_histories_do_not_crash(clone: Path) -> None:
    git(clone, "checkout", "--orphan", "island")
    commit(clone, "island.txt", msg="island root")
    snap = br.collect(clone, against="main")
    assert snap["base"] is None
    assert snap["state"] == "diverged"


def test_upstream_deleted_on_remote_is_reported(clone: Path) -> None:
    git(clone, "checkout", "-b", "feat/x")
    git(clone, "push", "-u", "origin", "feat/x")
    git(clone, "push", "origin", "--delete", "feat/x")
    git(clone, "fetch", "--prune")
    snap = br.collect(clone)
    assert snap["state"] == "no-upstream"
    assert snap["upstream_gone"] is True
    assert "no longer exists" in snap["headline"]


def test_plan_without_html_is_rejected(clone: Path, tmp_path: Path) -> None:
    plan = tmp_path / "plan.json"
    plan.write_text("{}")
    with pytest.raises(SystemExit):
        br.main(["--repo", str(clone), "--plan", str(plan)])
