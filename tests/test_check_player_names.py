"""Tests for the player name check: the three cases between the fantasy stats and the box score."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from eupy.entity.build_player_name_map import CSV_FIELDS
from eupy.entity.check_player_names import (
    BoxOnly,
    box_name_as_full_name,
    describe_box_only,
    find_box_only,
    find_stats_only,
    main,
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


def test_find_stats_only_returns_unresolved_entries_and_missing_pairs() -> None:
    stats = [
        {"team": "PAO", "name": "C. Jones"},
        {"team": "PAO", "name": "R. Rookie"},
        {"team": "PAO", "name": "N. New"},
    ]
    entries = [_entry("PAO", "C. Jones", "exact", "Carlik Jones"), _entry("PAO", "R. Rookie", "no_candidate")]
    stats_only, missing = find_stats_only(stats, entries)
    assert [(s["name"], s["status"]) for s in stats_only] == [("R. Rookie", "no_candidate")]
    assert missing == [("PAO", "N. New")]


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
    box_only = find_box_only.__annotations__ and None  # placeholder to keep imports honest
    assert box_only is None
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


def _run(
    tmp_path: Path, entries: list[dict[str, object]], box_rows: list, monkeypatch: pytest.MonkeyPatch
) -> list[str]:
    stats = tmp_path / "players.csv"
    with stats.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["round", "player_id", "name", "team"])
        writer.writerow(["all", "1", "C. Jones", "PAO"])
        writer.writerow(["all", "2", "R. Rookie", "PAO"])
    map_path = tmp_path / "map.csv"
    with map_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows({**e, "variants": " | ".join(e["variants"])} for e in entries)  # type: ignore[arg-type]
    team_map = tmp_path / "teams.json"
    team_map.write_text(json.dumps({"teams": TEAMS_DATA}), encoding="utf-8")
    monkeypatch.setattr("eupy.entity.team_names.MAP_PATH", team_map)
    return [
        "--stats", str(stats), "--boxscore", str(_box(tmp_path, box_rows)), "--map", str(map_path),
        "--out", str(tmp_path / "report.csv"),
    ]  # fmt: skip


ENTRIES = [_entry("PAO", "C. Jones", "exact", "Carlik Jones"), _entry("PAO", "R. Rookie", "no_candidate")]


def test_stats_only_players_do_not_fail_the_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    args = _run(tmp_path, ENTRIES, [("G1", "PAO", "1", "JONES, CARLIK", "20:00", 1)], monkeypatch)
    assert main(args) == 0
    assert "R. Rookie" in capsys.readouterr().out
    report = (tmp_path / "report.csv").read_text().splitlines()
    assert report[0] == "case,team,name,detail" and report[1].startswith("stats_only,PAO,R. Rookie,no_candidate")


def _case_three_args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    rows = [("G1", "PAO", "1", "JONES, CARLIK", "20:00", 1), ("G1", "PAO", "5", "SANZ, LORENZ", "5:00", 1)]
    return _run(tmp_path, ENTRIES, rows, monkeypatch)


def test_continue_mode_reports_a_played_box_score_player_without_stats_and_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    args = _case_three_args(tmp_path, monkeypatch)
    assert main(args) == 0  # the default mode is continue
    assert main([*args, "--mode", "continue"]) == 0
    out = capsys.readouterr().out
    assert "WARNING" in out and "SANZ, LORENZ" in out and "stats: not found" in out and "/build-name-maps" in out
    report = (tmp_path / "report.csv").read_text().splitlines()
    assert any(line.startswith('box_only,PAO,"SANZ, LORENZ"') for line in report)


def test_pause_mode_stops_on_that_player_but_not_on_stats_only_players(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    assert main([*_case_three_args(tmp_path, monkeypatch), "--mode", "pause"]) == 1
    out = capsys.readouterr().out
    assert "PAUSED" in out and "SANZ, LORENZ" in out
    clean = _run(tmp_path, ENTRIES, [("G1", "PAO", "1", "JONES, CARLIK", "20:00", 1)], monkeypatch)
    assert main([*clean, "--mode", "pause"]) == 0  # R. Rookie is stats-only: reported, never a pause
