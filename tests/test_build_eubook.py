"""Tests for the eubook workbook builder (the pieces whose logic is not plain pandas)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from eupy.entity.player_names import PlayerNames
from eupy.entity.team_names import TeamNames
from eupy.optimize.optimize_squad import COLUMNS, RISK_COLUMNS
from eupy.transform.build_eubook import (
    ANALYTICAL_COLUMNS,
    delivery_path,
    fill_empty,
    join_stats,
    next_round_games,
    optimizer_input,
    player_kpis,
    prepare_boxscore,
)

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


def test_player_kpis_percentiles_are_ordered_and_a_single_game_has_no_std() -> None:
    df = pd.DataFrame({
        "player_normalized": ["A"] * 5 + ["B"],
        "team_id": ["PAO"] * 6,
        "position": ["G"] * 6,
        "round": [1, 2, 3, 4, 5, 1],
        "valuation": [0.0, 10.0, 20.0, 30.0, 40.0, 7.0],
        "minutes_num": [20.0] * 6,
        "quotation": [10.0] * 6,
    })
    rows = player_kpis(df).set_index("player_normalized")
    a = rows.loc["A"]
    assert (a.pir_p10, a.pir_p50, a.pir_p75, a.pir_p90) == pytest.approx((4.0, 20.0, 30.0, 36.0))
    assert rows.loc["B", "pir_p10"] == rows.loc["B", "pir_p90"] == 7.0  # one game: every percentile is that game
    assert pd.isna(rows.loc["B", "pir_std"])  # the gap fill_empty closes
    cols = ANALYTICAL_COLUMNS
    assert (
        cols.index("pir_std")
        < cols.index("pir_p10")
        < cols.index("pir_p50")
        < cols.index("pir_p75")
        < cols.index("pir_p90")
    )


def test_windows_and_percentiles_use_the_latest_games_whatever_the_row_order() -> None:
    rounds = [4, 1, 7, 2, 6, 3, 5]  # shuffled on purpose: recency must come from `round`, not row order
    df = pd.DataFrame({
        "player_normalized": ["A"] * 7,
        "team_id": ["PAO"] * 7,
        "position": ["G"] * 7,
        "round": rounds,
        "valuation": [float(r * 10) for r in rounds],  # round r scored r * 10
        "minutes_num": [float(r) for r in rounds],
        "quotation": [10.0] * 7,
    })
    row = player_kpis(df).iloc[0]
    assert row.pir_fast == pytest.approx(60.0)  # rounds 5, 6, 7
    assert row.pir_slow == pytest.approx(50.0)  # rounds 3..7
    assert row.mins_fast == pytest.approx(6.0) and row.mins_slow == pytest.approx(5.0)
    assert (row.pir_p10, row.pir_p50, row.pir_p90) == pytest.approx((34.0, 50.0, 66.0))  # percentiles of 30..70


def test_fill_empty_uses_defaults_for_missing_history_and_leaves_other_columns_alone() -> None:
    nan, inf = float("nan"), float("inf")
    record = pd.DataFrame({
        "pir_mean": [12.0, 5.0],
        "pir_lag1": [nan, 3.0],
        "pir_lag2": [nan, nan],
        "pir_std": [nan, 2.0],
        "mins_std": [nan, 1.0],
        "pir_per_min": [nan, 0.5],
        "pir_trend": [inf, 1.4],
        "mins_trend": [nan, 0.9],
        "exp_pir": [nan, 4.0],  # no fixture next round: not ours to invent
    })
    out = fill_empty(record)
    first, second = out.iloc[0], out.iloc[1]
    assert (first.pir_std, first.mins_std, first.pir_per_min) == (0.0, 0.0, 0.0)
    assert (first.pir_trend, first.mins_trend) == (1.0, 1.0)  # inf and NaN both become "no change"
    assert (first.pir_lag1, first.pir_lag2, second.pir_lag2) == (12.0, 12.0, 5.0)  # own mean
    assert (second.pir_std, second.pir_lag1, second.pir_trend) == (2.0, 3.0, 1.4)  # real values stay
    assert pd.isna(first.exp_pir)
    assert pd.isna(record.loc[0, "pir_std"])  # input untouched


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


def test_optimizer_input_is_the_record_plus_a_zeroed_in_prev_roster_at_the_right_end() -> None:
    record = pd.DataFrame({"player": ["A", "B"], "pir_mean": [10.0, 12.0]})
    out = optimizer_input(record)
    assert out.columns.tolist() == ["player", "pir_mean", "in_prev_roster"]
    assert out["in_prev_roster"].tolist() == [0, 0]
    assert "in_prev_roster" not in record.columns  # input untouched


def test_optimizer_input_carries_every_column_the_optimizer_reads() -> None:
    # producer/consumer contract: the optimizer maps these workbook columns, so the build must emit them
    produced = {*ANALYTICAL_COLUMNS, "in_prev_roster"}
    needed = {*COLUMNS.values(), *RISK_COLUMNS.values(), "exp_pir"}
    assert needed <= produced, needed - produced
