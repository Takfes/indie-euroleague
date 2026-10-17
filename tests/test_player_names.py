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


def _write_decisions(path: Path, *decisions: dict[str, object]) -> Path:
    path.write_text(json.dumps({"decisions": list(decisions)}), encoding="utf-8")
    return path


def _decision_entry(status: str, name: str = "Full Name", notes: str = "n", stats_name: str = "F. Campazzo") -> dict:
    return {"stats_team": "PAO", "stats_name": stats_name, "match_status": status, "name": name, "notes": notes}


def test_load_agent_decisions_reads_the_decisions_file(tmp_path: Path) -> None:
    path = _write_decisions(
        tmp_path / "d.json",
        _decision_entry("confirmed", "Facundo Campazzo"),
        _decision_entry("rejected", stats_name="R. Eject"),
    )
    assert load_agent_decisions(path) == {
        "PAO|F. Campazzo": {"match_status": "confirmed", "resolved_name": "Facundo Campazzo", "notes": "n"},
        "PAO|R. Eject": {"match_status": "rejected", "resolved_name": "", "notes": "n"},
    }
    assert load_agent_decisions(tmp_path / "missing.json") == {}


def test_load_agent_decisions_rejects_an_incomplete_or_unknown_decision(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="notes"):
        load_agent_decisions(_write_decisions(tmp_path / "a.json", _decision_entry("confirmed", notes="")))
    with pytest.raises(ValueError, match="full player `name`"):
        load_agent_decisions(_write_decisions(tmp_path / "b.json", _decision_entry("confirmed", name="")))
    with pytest.raises(ValueError, match="status"):
        load_agent_decisions(_write_decisions(tmp_path / "c.json", _decision_entry("exact")))


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


def _setup_builder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pairs: list[tuple[str, str]]) -> list[str]:
    """Point the team map at a temp file and return the builder args (stats, box score, decisions and output)."""
    schedule = tmp_path / "schedule"
    schedule.mkdir(exist_ok=True)
    (schedule / "schedule_E2026.csv").write_text(
        "hometeam,homecode,hometv\nPanathinaikos,PAN,PAO\nReal Madrid,MAD,RMB\n", encoding="utf-8"
    )
    monkeypatch.setattr("eupy.entity.team_names.SCHEDULE_DIR", schedule)
    return [
        "--stats", str(_stats_csv(tmp_path / "stats.csv", pairs)),
        "--boxscore", str(_box_csv(tmp_path / "box.csv")),
        "--decisions", str(tmp_path / "decisions.json"),
        "--out", str(tmp_path / "players.csv"),
    ]  # fmt: skip


def test_builder_writes_a_stable_csv_that_loads(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    args = _setup_builder(tmp_path, monkeypatch, [("PAO", "C. Jones")])
    out = tmp_path / "players.csv"
    build_player_name_map.main(args)
    first = out.read_bytes()
    build_player_name_map.main(args)
    assert out.read_bytes() == first
    assert out.read_text(encoding="utf-8").splitlines() == [
        "stats_team,stats_name,team,name,variants,match_status,matched_by,notes",
        'PAO,C. Jones,Panathinaikos,Carlik Jones,"C. Jones | Carlik Jones | JONES, CARLIK",exact,exact,',
    ]
    assert PlayerNames.load(out, TEAMS).normalize("JONES, CARLIK") == "Carlik Jones"


def test_recorded_decisions_apply_on_rebuild_and_vanished_players_are_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    args = _setup_builder(tmp_path, monkeypatch, [("PAO", "C. Jones"), ("PAO", "F. Campazzo")])
    out = tmp_path / "players.csv"
    build_player_name_map.main(args)  # first run: Campazzo plays for Madrid, so he is open
    first = {r["stats_name"]: r for r in csv.DictReader(out.open(encoding="utf-8"))}
    assert first["F. Campazzo"]["match_status"] == "needs_review"

    _write_decisions(  # the agent decides Campazzo; a decision for a player who left the stats is ignored
        tmp_path / "decisions.json",
        _decision_entry("confirmed", "Facundo Campazzo", "Moved to Panathinaikos"),
        _decision_entry("no_match", stats_name="G.One"),
    )
    build_player_name_map.main(args)
    rebuilt = {r["stats_name"]: r for r in csv.DictReader(out.open(encoding="utf-8"))}
    assert set(rebuilt) == {"C. Jones", "F. Campazzo"}
    assert (rebuilt["F. Campazzo"]["match_status"], rebuilt["F. Campazzo"]["matched_by"]) == ("confirmed", "agent")
    assert PlayerNames.load(out, TEAMS).normalize("F. Campazzo") == "Facundo Campazzo"


def test_builder_rejects_an_empty_stats_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    args = _setup_builder(tmp_path, monkeypatch, [])
    with pytest.raises(ValueError, match="No players"):
        build_player_name_map.main(args)


def test_load_missing_map_points_at_the_builder(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="build_player_name_map"):
        PlayerNames.load(tmp_path / "nope.csv", TEAMS)


def test_builder_returns_the_number_of_open_entries(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    args = _setup_builder(tmp_path, monkeypatch, [("PAO", "C. Jones")])
    assert build_player_name_map.main(args) == 0
    stats = _stats_csv(tmp_path / "new.csv", [("PAO", "C. Jones"), ("PAO", "Z. Nobody")])
    args[args.index("--stats") + 1] = str(stats)
    assert build_player_name_map.main(args) == 1


def test_suffix_differences_between_stats_and_box_score_still_match(tmp_path: Path) -> None:
    box = tmp_path / "box.csv"
    with box.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(BOX_HEADER)
        writer.writerows([
            ("E2025", "PAN", "P7", "WASHINGTON JR, TYTY", "4"),  # stats drop the suffix
            ("E2025", "PAN", "P8", "BALDWIN, PATRICK", "5"),  # stats carry a suffix the box score lacks
        ])
    pools = load_boxscore_pools(box, TEAMS)
    stats = _stats_csv(tmp_path / "stats.csv", [("PAO", "T. Washington"), ("PAO", "P. Baldwin Jr")])
    rows = build_rows(load_stats_players(stats), pools, TEAMS, {})
    assert [(r["match_status"], r["resolved_name"]) for r in rows] == [
        ("exact", "Patrick Baldwin"),
        ("exact", "Tyty Washington Jr"),
    ]
    entry = build_entries(rows, pools)[1]
    assert "WASHINGTON JR, TYTY" in entry["variants"]  # the box-score spelling now resolves


def test_two_players_differing_only_by_suffix_are_not_auto_matched(tmp_path: Path) -> None:
    box = tmp_path / "box.csv"
    with box.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(BOX_HEADER)
        writer.writerows([
            ("E2025", "PAN", "P7", "SMITH, JOHN", "4"),
            ("E2025", "PAN", "P8", "SMITH JR, JOHN", "5"),
        ])
    stats = _stats_csv(tmp_path / "stats.csv", [("PAO", "J. Smith")])
    row = build_rows(load_stats_players(stats), load_boxscore_pools(box, TEAMS), TEAMS, {})[0]
    assert row["match_status"] == "needs_review"
