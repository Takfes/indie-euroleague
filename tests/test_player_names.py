"""Tests for the player matching engine, the map builder and the `PlayerNames` lookup."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from eupy.entity import build_player_name_map
from eupy.entity.build_player_name_map import (
    BoxscorePools,
    abbreviate_name,
    build_entries,
    build_rows,
    load_agent_decisions,
    load_boxscore_pools,
    load_stats_players,
    reorder_boxscore_name,
)
from eupy.entity.matching import AmbiguousNameError, UnknownNameError
from eupy.entity.player_names import PlayerNames
from eupy.entity.team_names import TeamNames

TEAMS = TeamNames([
    {"name": "Panathinaikos", "variants": ["PAN", "PAO"]},
    {"name": "Real Madrid", "variants": ["MAD", "RMB"]},
])
BOX_HEADER = ["season_code", "team_id", "player_id", "player", "dorsal"]
BOX_ROWS = [
    # season, team_id, player_id, player, dorsal
    ("E2024", "PAN", "P1", "JONES, CARLIK", "1"),
    ("E2025", "PAN", "P1", "JONES, CARLIK", "1"),
    ("E2025", "MAD", "P2", "JONES, CHRIS", "9"),
    ("E2025", "MAD", "P3", "CAMPAZZO, FACUNDO", "7"),
    ("E2024", "XXX", "P4", "OLD, ALAN", "3"),  # club not in the team map
    ("E2025", "PAN", "P1", "TOTAL", "TOTAL"),
    ("E2022", "ZZZ", "P5", "BALCEROWSKI, OLEK", "5"),  # outside the recent seasons
]


def _box_csv(path: Path) -> Path:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(BOX_HEADER)
        writer.writerows(BOX_ROWS)
    return path


def _stats_csv(path: Path, pairs: list[tuple[str, str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["round", "player_id", "name", "team"])
        for i, (team, name) in enumerate(pairs):
            writer.writerow(["1", str(i), name, team])
            writer.writerow(["2", str(i), name, team])  # repeated rounds collapse to one pair
    return path


def _pools(tmp_path: Path) -> BoxscorePools:
    return load_boxscore_pools(_box_csv(tmp_path / "box.csv"), TEAMS, recent_seasons=2)


def _rows(tmp_path: Path, pairs: list[tuple[str, str]], decisions: dict[str, dict[str, str]] | None = None):
    players = [{"team": t, "name": n} for t, n in pairs]
    return build_rows(players, _pools(tmp_path), TEAMS, decisions or {})


def test_name_helpers() -> None:
    assert reorder_boxscore_name("JONES, CARLIK") == "CARLIK JONES"
    assert reorder_boxscore_name("Plain Name") == "Plain Name"
    assert abbreviate_name("Marc-Owen Fodzo Dada") == "M. Fodzo Dada"


def test_pools_skip_totals_and_scope_clubs_to_recent_seasons(tmp_path: Path) -> None:
    pools = _pools(tmp_path)
    assert "TOTAL" not in pools.all
    assert set(pools.by_club["Panathinaikos"]) == {"CARLIK JONES"}
    assert "OLEK BALCEROWSKI" in pools.all  # old season: only in the unscoped pool
    assert "OLEK BALCEROWSKI" not in {s for p in pools.by_club.values() for s in p}
    assert "ALAN OLD" in pools.all  # unknown club: only in the unscoped pool
    assert pools.raw["CARLIK JONES"] == {"JONES, CARLIK"}


def test_accent_variants_of_a_name_share_their_raw_spellings(tmp_path: Path) -> None:
    path = tmp_path / "box.csv"
    path.write_text(
        'season_code,team_id,player_id,player,dorsal\nE2025,MAD,P7,"MARI, LUCAS",4\nE2025,MAD,P8,"MAR\u00cd, LUCAS",4\n',
        encoding="utf-8",
    )
    pools = load_boxscore_pools(path, TEAMS)
    assert pools.raw["LUCAS MARI"] == {"MARI, LUCAS", "MAR\u00cd, LUCAS"}


def test_load_stats_players_collapses_rounds(tmp_path: Path) -> None:
    path = _stats_csv(tmp_path / "s.csv", [("PAO", "C. Jones"), ("MAD", "C. Jones")])
    assert load_stats_players(path) == [{"team": "MAD", "name": "C. Jones"}, {"team": "PAO", "name": "C. Jones"}]


def test_exact_match_in_own_club_is_the_only_auto_accept(tmp_path: Path) -> None:
    row = _rows(tmp_path, [("PAO", "C. Jones")])[0]
    assert (row["match_status"], row["resolved_name"], row["matched_by"]) == ("exact", "Carlik Jones", "exact")


def test_same_abbreviation_at_two_clubs_resolves_per_club(tmp_path: Path) -> None:
    rows = _rows(tmp_path, [("PAO", "C. Jones"), ("RMB", "C. Jones")])
    assert [r["resolved_name"] for r in rows] == ["Carlik Jones", "Chris Jones"]


def test_hit_outside_the_club_needs_review(tmp_path: Path) -> None:
    row = _rows(tmp_path, [("PAO", "F. Campazzo")])[0]  # Campazzo plays for Madrid
    assert row["match_status"] == "needs_review"
    assert "Facundo Campazzo" in row["notes"]


def test_player_with_no_box_score_history_has_no_candidate(tmp_path: Path) -> None:
    row = _rows(tmp_path, [("PAO", "Z. Qqqqqqq")])[0]
    assert (row["match_status"], row["resolved_name"]) == ("no_candidate", "")


def test_unknown_team_code_is_noted_not_fatal(tmp_path: Path) -> None:
    row = _rows(tmp_path, [("QQQ", "C. Jones")])[0]
    assert row["club"] == ""
    assert "not in the team map" in row["notes"]
    assert row["match_status"] == "needs_review"  # no club pool, so never an auto-accept


def _decision(status: str, resolved: str = "") -> dict[str, str]:
    return {"match_status": status, "resolved_name": resolved, "notes": "n"}


def test_agent_decision_is_applied_to_an_open_player(tmp_path: Path) -> None:
    decisions = {"PAO|S. Francisco": _decision("confirmed", "Sam Francisco")}
    row = _rows(tmp_path, [("PAO", "S. Francisco")], decisions)[0]
    assert (row["match_status"], row["resolved_name"], row["matched_by"]) == ("confirmed", "Sam Francisco", "agent")


def test_agent_decision_never_replaces_an_exact_match(tmp_path: Path) -> None:
    row = _rows(tmp_path, [("PAO", "C. Jones")], {"PAO|C. Jones": _decision("confirmed", "Someone Else")})[0]
    assert (row["match_status"], row["resolved_name"]) == ("exact", "Carlik Jones")


def _write_map(path: Path, *entries: dict[str, object]) -> Path:
    path.write_text(json.dumps({"players": list(entries)}), encoding="utf-8")
    return path


def _map_entry(status: str, name: str = "Full Name", notes: str = "n", stats_name: str = "F. Campazzo") -> dict:
    return {"stats_team": "PAO", "stats_name": stats_name, "match_status": status, "name": name, "notes": notes}


def test_load_agent_decisions_reads_only_agent_statuses(tmp_path: Path) -> None:
    path = _write_map(
        tmp_path / "m.json",
        _map_entry("confirmed", "Facundo Campazzo"),
        _map_entry("rejected", stats_name="R. Eject"),
        _map_entry("exact", stats_name="C. Jones"),
        _map_entry("needs_review", stats_name="O. Pen"),
    )
    assert load_agent_decisions(path) == {
        "PAO|F. Campazzo": {"match_status": "confirmed", "resolved_name": "Facundo Campazzo", "notes": "n"},
        "PAO|R. Eject": {"match_status": "rejected", "resolved_name": "", "notes": "n"},
    }
    assert load_agent_decisions(tmp_path / "missing.json") == {}


def test_load_agent_decisions_rejects_a_decision_without_notes_or_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="notes"):
        load_agent_decisions(_write_map(tmp_path / "a.json", _map_entry("confirmed", notes="")))
    with pytest.raises(ValueError, match="full player `name`"):
        load_agent_decisions(_write_map(tmp_path / "b.json", _map_entry("confirmed", name="")))


def _entries(tmp_path: Path, pairs: list[tuple[str, str]], decisions=None) -> list[dict[str, object]]:
    return build_entries(_rows(tmp_path, pairs, decisions), _pools(tmp_path))


def test_entries_carry_every_spelling_and_the_club(tmp_path: Path) -> None:
    entry = _entries(tmp_path, [("PAO", "C. Jones")])[0]
    assert entry["name"] == "Carlik Jones"
    assert entry["variants"] == ["C. Jones", "Carlik Jones", "JONES, CARLIK"]
    assert (entry["team"], entry["stats_team"], entry["stats_name"]) == ("Panathinaikos", "PAO", "C. Jones")


def test_lookup_resolves_every_variant_and_is_idempotent(tmp_path: Path) -> None:
    players = PlayerNames(_entries(tmp_path, [("PAO", "C. Jones")]), TEAMS)
    assert {players.normalize(v) for v in ("JONES, CARLIK", "C. Jones", "carlik jones", "Carlik Jones")} == {
        "Carlik Jones"
    }


def test_lookup_needs_team_for_a_shared_spelling(tmp_path: Path) -> None:
    players = PlayerNames(_entries(tmp_path, [("PAO", "C. Jones"), ("RMB", "C. Jones")]), TEAMS)
    with pytest.raises(AmbiguousNameError, match="team="):
        players.normalize("C. Jones")
    assert players.normalize("C. Jones", team="PAO") == "Carlik Jones"
    assert players.normalize("C. Jones", team="MAD") == "Chris Jones"
    assert players.normalize("JONES, CHRIS") == "Chris Jones"  # unique full spelling needs no team


def test_lookup_unknown_name_and_unknown_team(tmp_path: Path) -> None:
    players = PlayerNames(_entries(tmp_path, [("PAO", "C. Jones"), ("RMB", "C. Jones")]), TEAMS)
    with pytest.raises(UnknownNameError, match="Nobody"):
        players.normalize("Nobody")
    with pytest.raises(UnknownNameError, match="QQQ"):
        players.normalize("C. Jones", team="QQQ")


def test_player_at_two_clubs_resolves_without_team(tmp_path: Path) -> None:
    entries = _entries(tmp_path, [("PAO", "C. Jones")])
    entries.append({**entries[0], "team": "Real Madrid", "stats_team": "RMB"})  # same player, mid-season move
    assert PlayerNames(entries, TEAMS).normalize("C. Jones") == "Carlik Jones"


def test_open_entries_are_listed_not_looked_up(tmp_path: Path) -> None:
    players = PlayerNames(_entries(tmp_path, [("PAO", "F. Campazzo")]), TEAMS)
    assert [e["stats_name"] for e in players.unresolved()] == ["F. Campazzo"]
    with pytest.raises(UnknownNameError):
        players.normalize("F. Campazzo")


def test_rejected_entry_keeps_the_stats_spelling(tmp_path: Path) -> None:
    decisions = {"PAO|F. Campazzo": _decision("rejected")}
    players = PlayerNames(_entries(tmp_path, [("PAO", "F. Campazzo")], decisions), TEAMS)
    assert players.normalize("F. Campazzo") == "F. Campazzo"
    assert players.unresolved() == []


def test_builder_writes_stable_json_that_loads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    team_map = tmp_path / "team_map.json"
    team_map.write_text(json.dumps({"teams": TEAMS_JSON}), encoding="utf-8")
    monkeypatch.setattr("eupy.entity.team_names.MAP_PATH", team_map)
    stats = _stats_csv(tmp_path / "stats.csv", [("PAO", "C. Jones")])
    out, csv_out = tmp_path / "players.json", tmp_path / "players.csv"
    args = ["--stats", str(stats), "--boxscore", str(_box_csv(tmp_path / "box.csv")), "--out", str(out)]
    args += ["--csv-out", str(csv_out)]
    build_player_name_map.main(args)
    first = (out.read_bytes(), csv_out.read_bytes())
    build_player_name_map.main(args)
    assert (out.read_bytes(), csv_out.read_bytes()) == first
    assert csv_out.read_text(encoding="utf-8").splitlines() == [
        "stats_team,stats_name,team,name,variants,match_status,matched_by,notes",
        'PAO,C. Jones,Panathinaikos,Carlik Jones,"C. Jones | Carlik Jones | JONES, CARLIK",exact,exact,',
    ]
    assert PlayerNames.load(out, TeamNames.load(team_map)).normalize("JONES, CARLIK") == "Carlik Jones"


def test_agent_decisions_in_the_json_survive_a_rebuild(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    team_map = tmp_path / "team_map.json"
    team_map.write_text(json.dumps({"teams": TEAMS_JSON}), encoding="utf-8")
    monkeypatch.setattr("eupy.entity.team_names.MAP_PATH", team_map)
    stats = _stats_csv(tmp_path / "stats.csv", [("PAO", "C. Jones"), ("PAO", "F. Campazzo")])
    out, csv_out = tmp_path / "players.json", tmp_path / "players.csv"
    args = ["--stats", str(stats), "--boxscore", str(_box_csv(tmp_path / "box.csv")), "--out", str(out)]
    args += ["--csv-out", str(csv_out)]

    build_player_name_map.main(args)  # first run: Campazzo plays for Madrid, so he is open
    data = json.loads(out.read_text(encoding="utf-8"))
    campazzo = next(e for e in data["players"] if e["stats_name"] == "F. Campazzo")
    assert campazzo["match_status"] == "needs_review"
    campazzo |= {"match_status": "confirmed", "name": "Facundo Campazzo", "notes": "Moved to Panathinaikos"}
    data["players"].append(_map_entry("no_match", stats_name="G.One"))  # a player who left the stats data
    out.write_text(json.dumps(data), encoding="utf-8")

    build_player_name_map.main(args)  # the decision is carried forward, the vanished player is dropped
    rebuilt = {e["stats_name"]: e for e in json.loads(out.read_text(encoding="utf-8"))["players"]}
    assert set(rebuilt) == {"C. Jones", "F. Campazzo"}
    assert (rebuilt["F. Campazzo"]["match_status"], rebuilt["F. Campazzo"]["matched_by"]) == ("confirmed", "agent")
    assert PlayerNames.load(out, TeamNames.load(team_map)).normalize("F. Campazzo") == "Facundo Campazzo"
    first = out.read_bytes()
    build_player_name_map.main(args)
    assert out.read_bytes() == first


TEAMS_JSON = [
    {"name": "Panathinaikos", "variants": ["PAN", "PAO"]},
    {"name": "Real Madrid", "variants": ["MAD", "RMB"]},
]


def test_builder_rejects_an_empty_stats_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    team_map = tmp_path / "team_map.json"
    team_map.write_text(json.dumps({"teams": TEAMS_JSON}), encoding="utf-8")
    monkeypatch.setattr("eupy.entity.team_names.MAP_PATH", team_map)
    stats = _stats_csv(tmp_path / "stats.csv", [])
    with pytest.raises(ValueError, match="No players"):
        build_player_name_map.main(["--stats", str(stats), "--boxscore", str(_box_csv(tmp_path / "b.csv"))])


def test_load_missing_map_points_at_the_builder(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="build_player_name_map"):
        PlayerNames.load(tmp_path / "nope.json", TEAMS)
