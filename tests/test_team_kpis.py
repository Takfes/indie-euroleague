"""Tests for the derived team KPIs: pace factor, foul rates and positional funnel ratios."""

from __future__ import annotations

import pandas as pd
import pytest

from build_team_kpis import GUIDE, KPI_COLUMNS, build_column_guide, build_team_kpis
from build_team_master_table import BN_TEAM, DUNKEST_DVP


def _sources(bn_rows: list[dict], dunkest_rows: list[dict]) -> dict[str, pd.DataFrame]:
    return {BN_TEAM: pd.DataFrame(bn_rows), DUNKEST_DVP: pd.DataFrame(dunkest_rows)}


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
        [_team(1, "Alpha", 70.0, 20.0, 18.0), _team(2, "Beta", 80.0, 22.0, 24.0)],
        [_dvp(10, "Alpha", 30.0, 40.0, 50.0), _dvp(20, "Beta", 50.0, 60.0, 70.0)],
    )
    table = build_team_kpis(sources).set_index("team_name")

    assert table.loc["Alpha", "pace_factor"] == pytest.approx(70.0 / 75.0)
    assert table.loc["Beta", "pace_factor"] == pytest.approx(80.0 / 75.0)
    assert table.loc["Alpha", "funnel_ratio_guards"] == pytest.approx(30.0 / 40.0)
    assert table.loc["Beta", "funnel_ratio_guards"] == pytest.approx(50.0 / 40.0)
    assert table.loc["Alpha", "funnel_ratio_centers"] == pytest.approx(50.0 / 60.0)
    assert table["pace_factor"].mean() == pytest.approx(1.0)
    assert table["funnel_ratio_forwards"].mean() == pytest.approx(1.0)


def test_foul_rate_per40_restates_the_per_game_rate() -> None:
    sources = _sources(
        [_team(1, "Alpha", 70.0, 20.0, 18.0), _team(2, "Beta", 80.0, 22.0, 24.0)],
        [_dvp(10, "Alpha", 30.0, 40.0, 50.0), _dvp(20, "Beta", 50.0, 60.0, 70.0)],
    )
    table = build_team_kpis(sources).set_index("team_name")

    assert table.loc["Alpha", "foul_rate_per40_drawn"] == pytest.approx(20.0)
    assert table.loc["Beta", "foul_rate_per40_drawn"] == pytest.approx(22.0)
    assert table.loc["Alpha", "foul_rate_per40_committed"] == pytest.approx(18.0)
    assert table.loc["Beta", "foul_rate_per40_committed"] == pytest.approx(24.0)


def test_one_row_per_team_sorted_by_name_with_documented_columns() -> None:
    sources = _sources(
        [_team(1, "Zulu", 70.0, 20.0, 18.0), _team(2, "Alpha", 80.0, 22.0, 24.0)],
        [_dvp(10, "Zulu", 30.0, 40.0, 50.0), _dvp(20, "Alpha", 50.0, 60.0, 70.0)],
    )
    table = build_team_kpis(sources)

    assert table["team_name"].tolist() == ["Alpha", "Zulu"]
    assert list(table.columns) == KPI_COLUMNS
    guide = build_column_guide()
    assert guide["Column name"].tolist() == KPI_COLUMNS == list(GUIDE)
