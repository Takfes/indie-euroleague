"""Tests for the read-only PlayerNameCrosswalk consumption class."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from eupy.entity.crosswalk import PlayerNameCrosswalk


def _write_crosswalk(path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = ["name", "boxscore_name", "match_status", "match_score", "matched_by", "notes"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _row(name: str, boxscore_name: str, match_status: str) -> dict[str, str]:
    return {
        "name": name,
        "boxscore_name": boxscore_name,
        "match_status": match_status,
        "match_score": "100.0",
        "matched_by": "exact",
        "notes": "",
    }


def test_load_then_lookup_hits_for_exact_status(tmp_path: Path) -> None:
    path = tmp_path / "crosswalk.csv"
    _write_crosswalk(path, [_row("John Smith", "John SMITH", "exact")])

    crosswalk = PlayerNameCrosswalk.load(path)

    assert crosswalk.boxscore_name_for("John Smith") == "John SMITH"


def test_load_then_lookup_hits_for_confirmed_status(tmp_path: Path) -> None:
    path = tmp_path / "crosswalk.csv"
    _write_crosswalk(path, [_row("Wade Baldwin", "WADE BALDWIN IV", "confirmed")])

    crosswalk = PlayerNameCrosswalk.load(path)

    assert crosswalk.boxscore_name_for("Wade Baldwin") == "WADE BALDWIN IV"


@pytest.mark.parametrize("match_status", ["needs_review", "no_candidate", "no_match", "rejected"])
def test_lookup_misses_for_every_non_resolved_status(tmp_path: Path, match_status: str) -> None:
    path = tmp_path / "crosswalk.csv"
    _write_crosswalk(path, [_row("Some Name", "SOME CANDIDATE", match_status)])

    crosswalk = PlayerNameCrosswalk.load(path)

    assert crosswalk.boxscore_name_for("Some Name") is None


def test_lookup_misses_for_name_not_in_crosswalk(tmp_path: Path) -> None:
    path = tmp_path / "crosswalk.csv"
    _write_crosswalk(path, [_row("John Smith", "John SMITH", "exact")])

    crosswalk = PlayerNameCrosswalk.load(path)

    assert crosswalk.boxscore_name_for("Nobody Here") is None


def test_load_raises_clear_error_when_file_missing(tmp_path: Path) -> None:
    missing_path = tmp_path / "does_not_exist.csv"

    with pytest.raises(FileNotFoundError, match="resolve_player_names.py"):
        PlayerNameCrosswalk.load(missing_path)
