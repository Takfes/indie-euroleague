"""Tests for the read-only player name check."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from eupy.entity.check_player_names import find_unmatched, main

STATS = [{"team": "PAO", "name": "C. Jones"}, {"team": "MAD", "name": "F. Campazzo"}]


def _entry(team: str, name: str, status: str, full: str = "") -> dict[str, object]:
    return {
        "name": full or name,
        "variants": [name, full or name],
        "team": team,
        "stats_name": name,
        "stats_team": team,
        "match_status": status,
        "matched_by": "",
        "notes": "",
    }


def test_find_unmatched_reports_new_and_open_players_only() -> None:
    entries = [_entry("PAO", "C. Jones", "exact", "Carlik Jones"), _entry("MAD", "F. Campazzo", "needs_review")]
    assert find_unmatched(STATS, entries) == [("MAD", "F. Campazzo", "needs_review")]
    assert find_unmatched(STATS, entries[:1]) == [("MAD", "F. Campazzo", "not in the map")]


def test_agent_closed_statuses_count_as_matched() -> None:
    entries = [_entry("PAO", "C. Jones", "confirmed", "Carlik Jones"), _entry("MAD", "F. Campazzo", "no_match")]
    assert find_unmatched(STATS, entries) == []


def _write(tmp_path: Path, entries: list[dict[str, object]]) -> list[str]:
    stats = tmp_path / "players.csv"
    with stats.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["round", "player_id", "name", "team"])
        for i, p in enumerate(STATS):
            writer.writerow(["all", str(i), p["name"], p["team"]])
    map_path = tmp_path / "map.json"
    map_path.write_text(json.dumps({"players": entries}), encoding="utf-8")
    return ["--stats", str(stats), "--boxscore", str(tmp_path / "absent.csv"), "--map", str(map_path)]


def test_main_exits_zero_when_all_resolved_and_one_otherwise(tmp_path: Path, capsys) -> None:
    resolved = [
        _entry("PAO", "C. Jones", "exact", "Carlik Jones"),
        _entry("MAD", "F. Campazzo", "exact", "Facundo Campazzo"),
    ]
    assert main(_write(tmp_path, resolved)) == 0
    assert "can be skipped" in capsys.readouterr().out

    assert main(_write(tmp_path, resolved[:1])) == 1
    out = capsys.readouterr().out
    assert "MAD F. Campazzo: not in the map" in out
    assert "build_player_name_map" in out
