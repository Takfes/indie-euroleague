"""Tests for the team-name verdict ingest CLI (shared ingest logic is tested in test_verdict_batches.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eupy.entity import apply_team_name_verdicts as script

VALID = {
    "name": "Barcelona",
    "match_status": "confirmed",
    "boxscore_team_name": "FC BARCELONA",
    "notes": "Fantasy site drops the FC prefix; same club.",
}


def _run(monkeypatch: pytest.MonkeyPatch, *args: object) -> int:
    monkeypatch.setattr("sys.argv", ["prog", *map(str, args)])
    return script.main()


def test_default_batches_dir_is_the_team_name_verdicts_directory() -> None:
    assert script.VERDICTS_DIR.parts[-3:] == ("data", "curated", "team_name_verdicts")


def test_cli_writes_a_batch_and_exits_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "v.json"
    src.write_text(json.dumps([VALID]), encoding="utf-8")
    batches = tmp_path / "batches"

    assert _run(monkeypatch, "--verdicts", src, "--batches-dir", batches, "--label", "round-1") == 0

    batch = json.loads((batches / "0001_round-1.json").read_text(encoding="utf-8"))
    assert batch["verdicts"] == [VALID]
    assert batch["source"] == "v.json"


def test_cli_rejects_a_confirmed_verdict_without_boxscore_team_name_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "v.json"
    src.write_text(json.dumps([{**VALID, "boxscore_team_name": ""}]), encoding="utf-8")
    batches = tmp_path / "batches"

    assert _run(monkeypatch, "--verdicts", src, "--batches-dir", batches) == 1

    assert "boxscore_team_name" in capsys.readouterr().err
    assert not batches.exists()


def test_cli_leaves_the_crosswalk_csv_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    crosswalk = tmp_path / "team_name_crosswalk.csv"
    crosswalk.write_text("name,boxscore_team_name\nBarcelona,\n", encoding="utf-8")
    before = crosswalk.read_bytes()
    src = tmp_path / "v.json"
    src.write_text(json.dumps([VALID]), encoding="utf-8")

    _run(monkeypatch, "--verdicts", src, "--batches-dir", tmp_path / "batches")

    assert crosswalk.read_bytes() == before


def test_cli_requires_the_verdicts_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch)
    assert exc.value.code == 2
