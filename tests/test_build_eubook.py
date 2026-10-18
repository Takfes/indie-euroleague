"""Tests for the eubook workbook builder (the pieces whose logic is not plain pandas)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from eupy.entity.player_names import PlayerNames
from eupy.entity.team_names import TeamNames
from eupy.transform.build_eubook import delivery_path, join_stats, next_round_games, player_kpis, prepare_boxscore

TEAMS = TeamNames([{"name": "Panathinaikos", "variants": ["PAO"]}, {"name": "Real Madrid", "variants": ["MAD"]}])
PLAYERS = PlayerNames(
    [
        {
            "name": "Carlik Jones",
            "variants": ["JONES, CARLIK", "C. Jones", "Carlik Jones"],
            "team": "Panathinaikos",
            "match_status": "exact",
        },
    ],
    TEAMS,
)


def _box(rows: list[tuple]) -> pd.DataFrame:
    cols = ["season_code", "round", "game", "team_id", "dorsal", "player", "minutes", "valuation"]
    return pd.DataFrame(rows, columns=cols)


def test_prepare_boxscore_adds_venue_opponent_minutes_and_drops_unknown_players() -> None:
    box = _box([
        ("E2026", 1, "PAO-MAD", "PAO", "1", "JONES, CARLIK", "15:30", 10),
        ("E2026", 2, "MAD-PAO", "PAO", "1", "JONES, CARLIK", "DNP", 0),
        ("E2026", 1, "PAO-MAD", "PAO", "TOTAL", "Team", "200:00", 80),  # team totals are dropped
        ("E2025", 1, "PAO-MAD", "PAO", "1", "JONES, CARLIK", "10:00", 5),  # before the season
        ("E2026", 1, "PAO-MAD", "MAD", "9", "NOBODY, NEW", "10:00", 5),  # not in the map
    ])
    df = prepare_boxscore(box, 2026, PLAYERS)
    assert df["player_normalized"].tolist() == ["Carlik Jones", "Carlik Jones"]
    assert df["homeaway"].tolist() == ["home", "away"]
    assert df["opponent"].tolist() == ["MAD", "MAD"]
    assert df["minutes_num"].tolist() == [15.5, 0.0]


def _joined_box() -> pd.DataFrame:
    return pd.DataFrame({"round": [1, 2], "player_normalized": ["Carlik Jones"] * 2, "valuation": [10, 20]})


def test_join_stats_per_round_matches_round_and_player() -> None:
    stats = pd.DataFrame({
        "round": [1, 2],
        "name": ["C. Jones"] * 2,
        "team": ["PAO"] * 2,
        "position": ["Guard"] * 2,
        "quotation": [10.0, 12.0],
    })
    df = join_stats(_joined_box(), stats, PLAYERS)
    assert df["quotation"].tolist() == [10.0, 12.0]
    assert set(df["position"]) == {"G"}


def test_join_stats_season_wide_matches_on_player_alone() -> None:
    stats = pd.DataFrame({
        "round": ["all"],
        "name": ["C. Jones"],
        "team": ["PAO"],
        "position": ["Guard"],
        "quotation": [13.0],
    })
    df = join_stats(_joined_box(), stats, PLAYERS)
    assert df["quotation"].tolist() == [13.0, 13.0]  # the current price in every row


def test_join_stats_drops_rows_without_stats() -> None:
    stats = pd.DataFrame({
        "round": [1],
        "name": ["C. Jones"],
        "team": ["PAO"],
        "position": ["Guard"],
        "quotation": [10.0],
    })
    assert join_stats(_joined_box(), stats, PLAYERS)["round"].tolist() == [1]  # round 2 has no stats row


def test_player_kpis_last_lag_and_breakeven() -> None:
    df = pd.DataFrame({
        "player_normalized": ["A"] * 3,
        "team_id": ["PAO"] * 3,
        "position": ["G"] * 3,
        "round": [1, 2, 3],
        "valuation": [10.0, 20.0, 30.0],
        "minutes_num": [20.0, 20.0, 20.0],
        "quotation": [10.0, 11.0, 12.0],
    })
    row = player_kpis(df).iloc[0]
    assert (row.pir_last, row.pir_lag1, row.pir_lag2, row.crd_last) == (30.0, 20.0, 10.0, 12.0)
    assert row.pir_mean == 20.0
    assert row.pir_breakeven == pytest.approx((20.0 - 12.0 * 1.1) / 25)


def test_next_round_games_gives_the_latest_date_turn_2_and_lists_both_venues() -> None:
    schedule = pd.DataFrame({
        "gameday": [7, 7, 8],
        "date": ["Oct 20, 2026", "Oct 21, 2026", "Oct 27, 2026"],
        "hometeam": ["P", "R", "X"],
        "homecode": ["PAN", "MAD", "XXX"],
        "awayteam": ["Q", "S", "Y"],
        "awaycode": ["PRS", "BAR", "YYY"],
    })
    games, per_team = next_round_games(schedule, 7)
    assert games["turn"].tolist() == [1, 2]
    assert sorted(zip(per_team["team"], per_team["venue"], per_team["turn"], strict=True)) == [
        ("BAR", "away", 2),
        ("MAD", "home", 2),
        ("PAN", "home", 1),
        ("PRS", "away", 1),
    ]


def test_turns_follow_the_dates_not_the_text_order() -> None:
    # a round over a month end: "Oct 1" sorts before "Sep 30" as text, but it is the later date -> turn 2;
    # the lone earlier date (Sep 29) and the middle date (Sep 30) are both turn 1
    schedule = pd.DataFrame({
        "gameday": [7, 7, 7],
        "date": ["Sep 29, 2026", "Sep 30, 2026", "Oct 1, 2026"],
        "hometeam": ["A", "B", "C"],
        "homecode": ["AAA", "BBB", "CCC"],
        "awayteam": ["D", "E", "F"],
        "awaycode": ["DDD", "EEE", "FFF"],
    })
    games, _ = next_round_games(schedule, 7)
    assert dict(zip(games["homecode"], games["turn"], strict=True)) == {"AAA": 1, "BBB": 1, "CCC": 2}


def test_a_round_on_a_single_date_is_all_turn_1() -> None:
    schedule = pd.DataFrame({
        "gameday": [7, 7],
        "date": ["Oct 20, 2026", "Oct 20, 2026"],
        "hometeam": ["A", "B"],
        "homecode": ["AAA", "BBB"],
        "awayteam": ["C", "D"],
        "awaycode": ["CCC", "DDD"],
    })
    games, _ = next_round_games(schedule, 7)
    assert games["turn"].tolist() == [1, 1]


def test_delivery_copy_is_round_numbered_in_the_delivery_dir() -> None:
    assert delivery_path(Path("data/stage_03/eubook.xlsx"), Path("data/stage_99"), 7) == Path(
        "data/stage_99/eubook_007.xlsx"
    )
