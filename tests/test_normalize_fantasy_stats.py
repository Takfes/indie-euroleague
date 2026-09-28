"""Tests for the fantasy-stats normalizer: name lookup by player_id, C/F/G roles, stale-crosswalk guards."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from eupy.transform.normalize_fantasy_stats import load_name_map, normalize_rows

HEADER = ["player_id", "name", "team", "resolved_name", "match_status", "match_score", "matched_by", "source", "notes"]


def _write_crosswalk(path: Path, rows: list[tuple[str, str, str, str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(HEADER)
        for player_id, name, resolved, status in rows:
            writer.writerow([player_id, name, "T", resolved, status, "", "", "", ""])
    return path


def test_load_name_map_uses_resolved_name_for_usable_rows_and_raw_name_for_no_match(tmp_path: Path) -> None:
    path = _write_crosswalk(
        tmp_path / "cw.csv",
        [
            ("1", "C. Jones", "Carlik Jones", "exact"),
            ("2", "Y. Aksu", "Yigit Aksu", "confirmed"),
            ("3", "Q. Rookie", "", "no_match"),
        ],
    )

    assert load_name_map(path) == {"1": "Carlik Jones", "2": "Yigit Aksu", "3": "Q. Rookie"}


@pytest.mark.parametrize("status", ["needs_review", "no_candidate"])
def test_load_name_map_refuses_open_rows(tmp_path: Path, status: str) -> None:
    path = _write_crosswalk(tmp_path / "cw.csv", [("1", "C. Jones", "Carlik Jones", status)])

    with pytest.raises(ValueError, match="unresolved"):
        load_name_map(path)


def test_normalize_rows_replaces_name_by_player_id_and_maps_positions() -> None:
    rows = [
        {"round": "1", "player_id": "1", "name": "C. Jones", "position": "Guard", "pts": "20"},
        {"round": "1", "player_id": "2", "name": "C. Jones", "position": "Center", "pts": "9"},
    ]

    out = normalize_rows(rows, {"1": "Carlik Jones", "2": "Chris Jones"})

    assert [(r["name"], r["position"], r["pts"]) for r in out] == [
        ("Carlik Jones", "G", "20"),
        ("Chris Jones", "C", "9"),
    ]


def test_normalize_rows_fails_on_player_missing_from_crosswalk() -> None:
    with pytest.raises(ValueError, match="not in the crosswalk"):
        normalize_rows([{"player_id": "9", "name": "N. Ew", "position": "Guard"}], {})


def test_normalize_rows_fails_on_unknown_position() -> None:
    with pytest.raises(ValueError, match="position"):
        normalize_rows([{"player_id": "1", "name": "A. B", "position": "Head Coach"}], {"1": "Al Bo"})
