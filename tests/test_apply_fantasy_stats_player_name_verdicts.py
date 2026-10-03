"""Tests for the fantasy-stats player-name verdict ingest CLI (shared logic: test_verdict_batches.py)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eupy.entity import apply_fantasy_stats_player_name_verdicts as script

VALID = {
    "player_id": "11502",
    "match_status": "confirmed",
    "resolved_name": "Olivier Nkamhoua",
    "notes": "Correct spelling.",
}


def _run(monkeypatch: pytest.MonkeyPatch, *args: object) -> int:
    monkeypatch.setattr("sys.argv", ["prog", *map(str, args)])
    return script.main()


def test_default_batches_dir_is_the_fantasy_verdicts_directory() -> None:
    assert script.VERDICTS_DIR.parts[-3:] == ("data", "curated", "fantasy_stats_player_name_verdicts")


def test_cli_writes_a_batch_and_exits_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "v.json"
    src.write_text(json.dumps([VALID]), encoding="utf-8")
    batches = tmp_path / "batches"

    assert _run(monkeypatch, "--verdicts", src, "--batches-dir", batches, "--label", "round-1") == 0

    batch = json.loads((batches / "0001_round-1.json").read_text(encoding="utf-8"))
    assert batch["verdicts"] == [VALID]


def test_cli_rejects_a_record_keyed_by_name_instead_of_player_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    src = tmp_path / "v.json"
    src.write_text(json.dumps([{"name": "x", "match_status": "no_match", "notes": "n"}]), encoding="utf-8")
    batches = tmp_path / "batches"

    assert _run(monkeypatch, "--verdicts", src, "--batches-dir", batches) == 1

    assert "player_id" in capsys.readouterr().err
    assert not batches.exists()


def test_cli_rejects_a_confirmed_verdict_without_resolved_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = tmp_path / "v.json"
    src.write_text(json.dumps([{**VALID, "resolved_name": ""}]), encoding="utf-8")
    batches = tmp_path / "batches"

    assert _run(monkeypatch, "--verdicts", src, "--batches-dir", batches) == 1
    assert not batches.exists()


def test_cli_leaves_the_crosswalk_csv_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    crosswalk = tmp_path / "fantasy_stats_player_name_crosswalk.csv"
    crosswalk.write_text("player_id,resolved_name\n11502,\n", encoding="utf-8")
    before = crosswalk.read_bytes()
    src = tmp_path / "v.json"
    src.write_text(json.dumps([VALID]), encoding="utf-8")

    _run(monkeypatch, "--verdicts", src, "--batches-dir", tmp_path / "batches")

    assert crosswalk.read_bytes() == before
