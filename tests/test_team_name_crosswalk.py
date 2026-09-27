"""Tests for the read-only TeamNameCrosswalk consumption class."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from eupy.entity.team_crosswalk import TeamNameCrosswalk


def _write_crosswalk(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = ["name", "boxscore_team_name", "match_status", "match_score", "matched_by", "notes"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _row(name: str, boxscore_team_name: str, match_status: str) -> dict[str, str]:
    return {
        "name": name,
        "boxscore_team_name": boxscore_team_name,
        "match_status": match_status,
        "match_score": "100.0",
        "matched_by": "exact",
        "notes": "",
    }


def test_load_then_lookup_hits_for_exact_status(tmp_path: Path) -> None:
    path = tmp_path / "crosswalk.csv"
    _write_crosswalk(path, [_row("Baskonia", "BASKONIA", "exact")])

    crosswalk = TeamNameCrosswalk.load(path)

    assert crosswalk.boxscore_team_name_for("Baskonia") == "BASKONIA"


def test_load_then_lookup_hits_for_confirmed_status(tmp_path: Path) -> None:
    path = tmp_path / "crosswalk.csv"
    _write_crosswalk(path, [_row("Barcelona", "FC BARCELONA", "confirmed")])

    crosswalk = TeamNameCrosswalk.load(path)

    assert crosswalk.boxscore_team_name_for("Barcelona") == "FC BARCELONA"


@pytest.mark.parametrize("match_status", ["needs_review", "no_candidate", "no_match", "rejected"])
def test_lookup_misses_for_every_non_resolved_status(tmp_path: Path, match_status: str) -> None:
    path = tmp_path / "crosswalk.csv"
    _write_crosswalk(path, [_row("Some Club", "SOME CANDIDATE", match_status)])

    crosswalk = TeamNameCrosswalk.load(path)

    assert crosswalk.boxscore_team_name_for("Some Club") is None


def test_lookup_misses_for_name_not_in_crosswalk(tmp_path: Path) -> None:
    path = tmp_path / "crosswalk.csv"
    _write_crosswalk(path, [_row("Baskonia", "BASKONIA", "exact")])

    crosswalk = TeamNameCrosswalk.load(path)

    assert crosswalk.boxscore_team_name_for("Nobody Here") is None


def test_load_raises_clear_error_when_file_missing(tmp_path: Path) -> None:
    missing_path = tmp_path / "does_not_exist.csv"

    with pytest.raises(FileNotFoundError, match="resolve_team_names.py"):
        TeamNameCrosswalk.load(missing_path)
