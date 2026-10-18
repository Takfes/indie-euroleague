"""Tests for the name audit: the three cases between the fantasy stats and the box score."""

from __future__ import annotations

import csv
from pathlib import Path

from eupy.entity.name_audit import (
    BoxOnly,
    audit,
    box_name_as_full_name,
    describe_box_only,
    find_box_only,
    find_stats_only,
)
from eupy.entity.player_names import PlayerNames
from eupy.entity.team_names import TeamNames

TEAMS_DATA = [{"name": "Panathinaikos", "variants": ["PAN", "PAO"]}, {"name": "Real Madrid", "variants": ["MAD"]}]
TEAMS = TeamNames(TEAMS_DATA)


def _entry(team: str, name: str, status: str, full: str = "", club: str = "Panathinaikos") -> dict[str, object]:
    resolved = full or name
    return {
        "name": resolved,
        "variants": [
            name,
            resolved,
            *(["JONES, CARLIK"] if status == "exact" else []),
        ],  # exact = the box spelling is a variant
        "team": club,
        "stats_name": name,
        "stats_team": team,
        "match_status": status,
        "matched_by": "",
        "notes": "",
    }


def test_find_stats_only_returns_the_unresolved_entries() -> None:
    stats = [{"team": "PAO", "name": "C. Jones"}, {"team": "PAO", "name": "R. Rookie"}]
    entries = [_entry("PAO", "C. Jones", "exact", "Carlik Jones"), _entry("PAO", "R. Rookie", "no_candidate")]
    assert [(s["name"], s["status"]) for s in find_stats_only(stats, entries)] == [("R. Rookie", "no_candidate")]


def _box(tmp_path: Path, rows: list[tuple[str, str, str, str, str, int]]) -> Path:
    path = tmp_path / "box.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["game_id", "team_id", "dorsal", "player", "minutes", "round"])
        writer.writerows(rows)
    return path


def test_find_box_only_lists_players_who_played_and_are_not_resolved(tmp_path: Path) -> None:
    players = PlayerNames([_entry("PAO", "C. Jones", "exact", "Carlik Jones")], TEAMS)
    box = _box(
        tmp_path,
        [
            ("G1", "PAO", "1", "JONES, CARLIK", "20:00", 1),  # case 1: resolved
            ("G1", "PAO", "7", "WASHINGTON JR, TYTY", "10:00", 1),  # case 3
            ("G2", "PAO", "7", "WASHINGTON JR, TYTY", "12:00", 2),
            ("G2", "PAO", "9", "BENCH, JOE", "DNP", 2),  # did not play: ignored
            ("G2", "PAO", "TOTAL", "Team", "200:00", 2),  # team row: ignored
        ],
    )
    found = find_box_only(box, players, TEAMS)
    assert [(b.player, b.team, b.club, b.games, b.rounds) for b in found] == [
        ("WASHINGTON JR, TYTY", "PAO", "Panathinaikos", 2, (1, 2))
    ]


def test_hint_names_the_stats_entry_sharing_a_surname_and_the_spelling_to_decide() -> None:
    item = BoxOnly("PAO", "Panathinaikos", "WASHINGTON JR, TYTY", 2, (5, 6))
    unlinked = [
        {"team": "PAO", "name": "T. Washington", "club": "Panathinaikos", "status": "confirmed"},
        {"team": "PAO", "name": "K. Nunn", "club": "Panathinaikos", "status": "no_candidate"},
    ]
    text = describe_box_only(item, unlinked)
    assert "PAO T. Washington (confirmed)" in text and "Nunn" not in text
    assert "'Tyty Washington Jr'" in text
    assert "round(s) 5-6" in text
    assert "fantasy data lacks him" in describe_box_only(item, unlinked[1:])
    assert box_name_as_full_name("MOTLEY, JOHNATHAN") == "Johnathan Motley"


STATS = [{"team": "PAO", "name": "C. Jones"}, {"team": "PAO", "name": "R. Rookie"}]
ENTRIES = [_entry("PAO", "C. Jones", "exact", "Carlik Jones"), _entry("PAO", "R. Rookie", "no_candidate")]
PLAYED = ("G1", "PAO", "1", "JONES, CARLIK", "20:00", 1)
UNLINKED = ("G1", "PAO", "5", "SANZ, LORENZ", "5:00", 1)


def test_stats_only_players_never_fail_the_run(tmp_path: Path, capsys) -> None:
    assert audit(STATS, ENTRIES, _box(tmp_path, [PLAYED]), TEAMS, "pause") == 0  # R. Rookie: reported, never a pause
    assert "R. Rookie" in capsys.readouterr().out


def test_continue_mode_reports_a_played_box_score_player_without_stats_and_goes_on(tmp_path: Path, capsys) -> None:
    assert audit(STATS, ENTRIES, _box(tmp_path, [PLAYED, UNLINKED]), TEAMS, "continue") == 0
    out = capsys.readouterr().out
    assert "WARNING" in out and "SANZ, LORENZ" in out and "stats: not found" in out and "/build-name-maps" in out


def test_pause_mode_stops_on_that_player(tmp_path: Path, capsys) -> None:
    assert audit(STATS, ENTRIES, _box(tmp_path, [PLAYED, UNLINKED]), TEAMS, "pause") == 1
    out = capsys.readouterr().out
    assert "PAUSED" in out and "SANZ, LORENZ" in out
