"""Tests for the box-score enrichment: year filter, minutes, names, positions join, opponent and home/away."""

from __future__ import annotations

import pandas as pd
import pytest

from eupy.entity.player_names import PlayerNames
from eupy.entity.team_names import TeamNames
from eupy.transform.build_boxscore_kpis import (
    add_minutes_num,
    add_opponent_home_away,
    add_year_and_filter,
    build,
    stats_positions,
)

TEAMS = TeamNames([{"name": "Red Star", "variants": ["RED", "CZV"]}, {"name": "Zalgiris", "variants": ["ZAL"]}])


def _entry(name: str, club: str, stats_name: str) -> dict[str, object]:
    return {
        "name": name,
        "variants": sorted({stats_name, name, name.upper()}),
        "team": club,
        "stats_name": stats_name,
        "stats_team": "RED",
        "match_status": "exact",
    }


PLAYERS = PlayerNames([_entry("Ann Lee", "Red Star", "A. Lee"), _entry("Bo Day", "Zalgiris", "B. Day")], TEAMS)


def _box(**overrides: str) -> dict[str, str]:
    row = {
        "game_player_id": "g1_p1",
        "season_code": "E2025",
        "game": "RED-ZAL",
        "team_id": "RED",
        "dorsal": "4",
        "player": "ANN LEE",
        "minutes": "15:30",
    }
    return {**row, **overrides}


def _stats(name: str, team: str, position: str) -> dict[str, str]:
    return {"name": name, "team": team, "position": position}


def test_year_inserted_after_season_code_and_old_and_total_rows_dropped() -> None:
    df = pd.DataFrame([
        _box(),
        _box(season_code="E2024", game_player_id="old"),
        _box(dorsal="TOTAL", game_player_id="tot"),
    ])
    out = add_year_and_filter(df, 2025)
    assert out["game_player_id"].tolist() == ["g1_p1"]
    cols = list(out.columns)
    assert cols[cols.index("season_code") + 1] == "year" and out["year"].tolist() == [2025]


def test_minutes_num_converts_clock_and_dnp() -> None:
    out = add_minutes_num(pd.DataFrame([_box(), _box(minutes="DNP")]))
    assert out["minutes_num"].tolist() == [15.5, 0.0]
    cols = list(out.columns)
    assert cols[cols.index("minutes") + 1] == "minutes_num"


def test_opponent_and_home_away_follow_game_order() -> None:
    out = add_opponent_home_away(pd.DataFrame([_box(), _box(team_id="ZAL")]))
    assert out[["opponent", "home_away"]].values.tolist() == [["ZAL", "Home"], ["RED", "Away"]]


def test_team_id_outside_game_raises() -> None:
    with pytest.raises(ValueError, match="neither side"):
        add_opponent_home_away(pd.DataFrame([_box(team_id="XXX")]))


def test_inner_join_keeps_only_stats_players_and_adds_position() -> None:
    box = pd.DataFrame([
        _box(),
        _box(game_player_id="g1_p2", player="NOBODY, X"),
        _box(game_player_id="g1_p3", player="BO DAY", team_id="ZAL"),
    ])
    stats = pd.DataFrame([_stats("A. Lee", "RED", "Guard")])
    out = build(box, stats, PLAYERS, 2025)
    assert out[["game_player_id", "player_name", "position"]].values.tolist() == [["g1_p1", "Ann Lee", "G"]]


def test_two_positions_for_one_player_raise() -> None:
    stats = pd.DataFrame([_stats("A. Lee", "RED", "Guard"), _stats("Ann Lee", "RED", "Forward")])
    with pytest.raises(ValueError):
        stats_positions(stats, PLAYERS)


def test_unknown_position_raises() -> None:
    with pytest.raises(ValueError, match="Unexpected positions"):
        stats_positions(pd.DataFrame([_stats("A. Lee", "RED", "Wing")]), PLAYERS)
