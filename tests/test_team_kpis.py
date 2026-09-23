"""Tests for the derived team KPIs: pace factor, foul rates and positional funnel ratios."""

from __future__ import annotations

import pandas as pd
import pytest

from build_team_kpis import (
    GAME_STATS,
    GUIDE,
    KPI_COLUMNS,
    PLAYER_MASTER,
    build_column_guide,
    build_team_kpis,
    funnel_actual_pir,
)
from build_team_master_table import BN_TEAM, DUNKEST_DVP

# Two real canonical teams, so the Kaggle codes below resolve through the real crosswalk.
TEAMS = ["FC Barcelona", "Real Madrid"]


def _game_row(game_id: str, team: str, opponent: str, player_id: str, played: bool, pir: float) -> dict:
    return {
        "game_id": game_id,
        "team_id": team,
        "opponent_id": opponent,
        "player_id": player_id,
        "played": played,
        "pir": pir,
    }


def _player_master() -> pd.DataFrame:
    # p1/p2 guards, p3 center, p4 forward; p5 has no resolved position; p6 is not in the master at all.
    positions = {"K1": "G", "K2": "G", "K3": "C", "K4": "F", "K5": None}
    ids = {"p1": "K1", "p2": "K2", "p3": "K3", "p4": "K4", "p5": "K5"}
    return pd.DataFrame({"kag_player_id": list(ids), "position": [positions[k] for k in ids.values()]}).assign(
        kag_player_id=list(ids)
    )


def _game_stats() -> pd.DataFrame:
    return pd.DataFrame([
        _game_row("g1", "BAR", "MAD", "p1", True, 10.0),
        _game_row("g1", "BAR", "MAD", "p3", True, 4.0),
        _game_row("g1", "MAD", "BAR", "p2", True, 6.0),
        _game_row("g1", "MAD", "BAR", "p4", True, 8.0),
        _game_row("g2", "BAR", "MAD", "p1", True, 20.0),
        _game_row("g2", "BAR", "MAD", "p3", False, 0.0),
        _game_row("g2", "MAD", "BAR", "p2", True, 2.0),
        _game_row("g2", "MAD", "BAR", "p4", True, 5.0),
        _game_row("g2", "MAD", "BAR", "p5", False, 0.0),
    ])


def _sources(bn_rows: list[dict], dunkest_rows: list[dict]) -> dict[str, pd.DataFrame]:
    return {
        BN_TEAM: pd.DataFrame(bn_rows),
        DUNKEST_DVP: pd.DataFrame(dunkest_rows),
        GAME_STATS: _game_stats(),
        PLAYER_MASTER: _player_master(),
    }


def _team(team_id: int, name: str, possessions: float, fouls_received: float, fouls: float) -> dict:
    return {
        "team_id": team_id,
        "team_name": name,
        "offense_all_possessions": possessions,
        "offense_all_fouls_received": fouls_received,
        "defense_all_fouls": fouls,
    }


def _dvp(team_id: int, name: str, guards: float, forwards: float, centers: float) -> dict:
    return {
        "team_id": team_id,
        "team_name": name,
        "guards_fantasy_points": guards,
        "forwards_fantasy_points": forwards,
        "centers_fantasy_points": centers,
    }


def test_pace_factor_and_funnel_ratios_are_ratios_to_the_league_average() -> None:
    sources = _sources(
        [_team(1, "FC Barcelona", 70.0, 20.0, 18.0), _team(2, "Real Madrid", 80.0, 22.0, 24.0)],
        [_dvp(10, "FC Barcelona", 30.0, 40.0, 50.0), _dvp(20, "Real Madrid", 50.0, 60.0, 70.0)],
    )
    table = build_team_kpis(sources).set_index("team_name")

    assert table.loc["FC Barcelona", "pace_factor"] == pytest.approx(70.0 / 75.0)
    assert table.loc["Real Madrid", "pace_factor"] == pytest.approx(80.0 / 75.0)
    assert table.loc["FC Barcelona", "funnel_ratio_guards"] == pytest.approx(30.0 / 40.0)
    assert table.loc["Real Madrid", "funnel_ratio_guards"] == pytest.approx(50.0 / 40.0)
    assert table.loc["FC Barcelona", "funnel_ratio_centers"] == pytest.approx(50.0 / 60.0)
    assert table["pace_factor"].mean() == pytest.approx(1.0)
    assert table["funnel_ratio_forwards"].mean() == pytest.approx(1.0)


def test_foul_rate_per40_restates_the_per_game_rate() -> None:
    sources = _sources(
        [_team(1, "FC Barcelona", 70.0, 20.0, 18.0), _team(2, "Real Madrid", 80.0, 22.0, 24.0)],
        [_dvp(10, "FC Barcelona", 30.0, 40.0, 50.0), _dvp(20, "Real Madrid", 50.0, 60.0, 70.0)],
    )
    table = build_team_kpis(sources).set_index("team_name")

    assert table.loc["FC Barcelona", "foul_rate_per40_drawn"] == pytest.approx(20.0)
    assert table.loc["Real Madrid", "foul_rate_per40_drawn"] == pytest.approx(22.0)
    assert table.loc["FC Barcelona", "foul_rate_per40_committed"] == pytest.approx(18.0)
    assert table.loc["Real Madrid", "foul_rate_per40_committed"] == pytest.approx(24.0)


def test_one_row_per_team_sorted_by_name_with_documented_columns() -> None:
    sources = _sources(
        [_team(1, "Real Madrid", 70.0, 20.0, 18.0), _team(2, "FC Barcelona", 80.0, 22.0, 24.0)],
        [_dvp(10, "Real Madrid", 30.0, 40.0, 50.0), _dvp(20, "FC Barcelona", 50.0, 60.0, 70.0)],
    )
    table = build_team_kpis(sources)

    assert table["team_name"].tolist() == ["FC Barcelona", "Real Madrid"]
    assert list(table.columns) == KPI_COLUMNS
    guide = build_column_guide()
    assert guide["Column name"].tolist() == KPI_COLUMNS == list(GUIDE)


def test_funnel_actual_pir_averages_opposing_position_pir_per_game_over_all_games() -> None:
    table = funnel_actual_pir(_game_stats(), _player_master(), TEAMS).set_index("team_name")

    # Real Madrid concede (BAR players): guards 10 and 20 -> 15; centers 4 and 0 (DNP) -> 2; forwards 0.
    # FC Barcelona concede (MAD players): guards 6 and 2 -> 4; forwards 8 and 5 -> 6.5; centers 0.
    assert table.loc["Real Madrid", "funnel_actual_pir_guards"] == pytest.approx(15.0 / 9.5)
    assert table.loc["FC Barcelona", "funnel_actual_pir_guards"] == pytest.approx(4.0 / 9.5)
    assert table.loc["Real Madrid", "funnel_actual_pir_centers"] == pytest.approx(2.0)
    assert table.loc["FC Barcelona", "funnel_actual_pir_centers"] == pytest.approx(0.0)
    assert table.loc["FC Barcelona", "funnel_actual_pir_forwards"] == pytest.approx(2.0)
    assert table.loc["Real Madrid", "funnel_actual_pir_forwards"] == pytest.approx(0.0)
    assert table.filter(like="funnel_actual_pir").mean().tolist() == pytest.approx([1.0, 1.0, 1.0])


def test_funnel_actual_pir_surfaces_an_opponent_that_misses_the_canonical_teams() -> None:
    with pytest.raises(ValueError, match="does not resolve"):
        funnel_actual_pir(_game_stats(), _player_master(), ["FC Barcelona", "Zalgiris Kaunas"])


def test_team_kpis_include_the_actual_pir_funnel_block_after_the_dunkest_funnel_block() -> None:
    sources = _sources(
        [_team(1, "FC Barcelona", 70.0, 20.0, 18.0), _team(2, "Real Madrid", 80.0, 22.0, 24.0)],
        [_dvp(10, "FC Barcelona", 30.0, 40.0, 50.0), _dvp(20, "Real Madrid", 50.0, 60.0, 70.0)],
    )
    table = build_team_kpis(sources)

    assert KPI_COLUMNS[-6:] == [
        "funnel_ratio_guards",
        "funnel_ratio_forwards",
        "funnel_ratio_centers",
        "funnel_actual_pir_guards",
        "funnel_actual_pir_forwards",
        "funnel_actual_pir_centers",
    ]
    assert table["funnel_actual_pir_guards"].notna().all()
