"""Tests for the player KPI aggregation: windows, percentiles, contributions, blanks and identity."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from build_game_player_stats import CONTRIBUTION_STATS
from build_player_kpis import GUIDE, KPI_COLUMNS, build_column_guide, build_player_kpis

ROW_DEFAULTS = {
    "points": 0,
    "total_rebounds": 0,
    "assists": 0,
    "steals": 0,
    "blocks_favour": 0,
    "fouls_received": 0,
    "fgm": 0,
    "fga": 0,
    "three_points_made": 0,
    "three_points_attempted": 0,
    "free_throws_made": 0,
    "free_throws_attempted": 0,
    "usage_proxy": 0.0,
}


def _games(player_id: str, name: str, rows: list[dict]) -> pd.DataFrame:
    """Chronological rows for one player; each row needs `pir` and `minutes` (0 minutes = DNP)."""
    frame = []
    number = 0
    for i, row in enumerate(rows, start=1):
        played = row["minutes"] > 0
        number += played
        pir = row["pir"] if played else np.nan
        entry = {
            "game_id": f"E2025_{i:03d}",
            "date": f"2025-10-{i:02d}",
            "time": "20:00:00",
            "player_id": player_id,
            "player": name,
            "team_id": row.get("team_id", "AAA"),
            "played": played,
            "game_number": number if played else pd.NA,
            "pir_per_min": row["pir"] / row["minutes"] if played else np.nan,
            **ROW_DEFAULTS,
            "pir": pir,
            **{k: v for k, v in row.items() if k not in ("pir", "team_id")},
        }
        for prefix, stat in CONTRIBUTION_STATS.items():
            entry[f"{prefix}_share_of_pir"] = entry[stat] / pir if pd.notna(pir) and pir > 0 else np.nan
        frame.append(entry)
    return pd.DataFrame(frame)


def _kpis(rows: list[dict], recent_games: int = 3, name: str = "SMITH, JOHN") -> pd.Series:
    return build_player_kpis(_games("P1", name, rows), recent_games).iloc[0]


def test_recent_window_uses_last_games_played_and_skips_dnp() -> None:
    rows = [
        {"pir": 2, "minutes": 10.0},
        {"pir": 4, "minutes": 20.0},
        {"pir": 0, "minutes": 0.0},  # DNP: not a game in any window
        {"pir": 6, "minutes": 30.0},
        {"pir": 20, "minutes": 40.0},
        {"pir": 10, "minutes": 20.0},
    ]
    k = _kpis(rows, recent_games=3)
    assert (k["games_played"], k["games_dnp"], k["recent_games"]) == (5, 1, 3)
    assert k["dnp_rate"] == pytest.approx(1 / 6)
    assert k["pir_avg"] == pytest.approx(42 / 5)
    assert k["pir_avg_recent"] == pytest.approx(12.0)  # last 3 played: 6, 20, 10
    assert k["pir_median_recent"] == 10
    assert k["minutes_avg"] == pytest.approx(24.0)
    assert k["minutes_trend"] == pytest.approx(30.0 - 24.0)
    assert k["pir_per_min"] == pytest.approx(42 / 120)


def test_player_with_fewer_games_than_window_uses_what_exists() -> None:
    k = _kpis([{"pir": 4, "minutes": 10.0}, {"pir": 8, "minutes": 20.0}], recent_games=5)
    assert k["recent_games"] == 2
    assert k["pir_avg_recent"] == pytest.approx(k["pir_avg"])
    assert k["minutes_trend"] == pytest.approx(0.0)


def test_percentiles_range_and_spread_over_all_games() -> None:
    rows = [{"pir": pir, "minutes": 10.0} for pir in (0, 10, 20, 30, 40)]
    k = _kpis(rows, recent_games=2)
    assert (k["pir_p10"], k["pir_p50"], k["pir_p90"]) == pytest.approx((4.0, 20.0, 36.0))
    assert k["pir_range"] == pytest.approx(32.0)
    assert k["pir_per_min_sd"] == pytest.approx(np.std([0, 1, 2, 3, 4], ddof=1))
    assert k["pir_per_min_cv"] == pytest.approx(np.std([0, 1, 2, 3, 4], ddof=1) / 2)
    assert k["minutes_sd"] == pytest.approx(0.0)
    assert k["minutes_cv"] == pytest.approx(0.0)


def test_single_game_has_blank_spread_and_negative_mean_blank_cv() -> None:
    one = _kpis([{"pir": 7, "minutes": 14.0}])
    assert one["pir_p10"] == one["pir_p50"] == one["pir_p90"] == 7
    assert one["pir_range"] == 0
    assert one[["minutes_sd", "minutes_cv", "pir_per_min_sd", "pir_per_min_cv"]].isna().all()
    negative = _kpis([{"pir": -4, "minutes": 10.0}, {"pir": -2, "minutes": 10.0}])
    assert pd.notna(negative["pir_per_min_sd"])
    assert pd.isna(negative["pir_per_min_cv"])


def test_shooting_and_usage_from_season_totals() -> None:
    rows = [
        {"pir": 10, "minutes": 20.0, "points": 8, "total_rebounds": 4, "fouls_received": 2, "fgm": 3, "fga": 6},
        {
            "pir": 10,
            "minutes": 20.0,
            "points": 4,
            "assists": 6,
            "steals": 2,
            "blocks_favour": 1,
            "fgm": 1,
            "fga": 4,
            "three_points_made": 1,
            "three_points_attempted": 2,
            "free_throws_made": 2,
            "free_throws_attempted": 4,
            "usage_proxy": 8.0,
        },
    ]
    k = _kpis(rows)
    assert k["fg_pct"] == pytest.approx(4 / 10)
    assert k["fg3_pct"] == pytest.approx(1 / 2)
    assert k["ft_pct"] == pytest.approx(2 / 4)
    assert k["ts_pct"] == pytest.approx(12 / (2 * (10 + 0.44 * 4)))
    assert k["fdr_rate"] == pytest.approx(2 / 40)
    assert k["usage_proxy_avg"] == pytest.approx(4.0)
    assert k["usage_per_min"] == pytest.approx(8 / 40)


def test_shooting_blank_without_attempts() -> None:
    k = _kpis([{"pir": -3, "minutes": 10.0, "points": 2}, {"pir": 3, "minutes": 10.0}])
    assert k[["fg_pct", "fg3_pct", "ft_pct", "ts_pct"]].isna().all()


def test_contribution_pct_is_the_row_mean_share_renormalized_to_100() -> None:
    """Both games have pir > 0: shares average, then rescale to sum to 100%; std stays unscaled."""
    rows = [
        {"pir": 10, "minutes": 20.0, "points": 8, "total_rebounds": 4, "fouls_received": 2},
        {"pir": 10, "minutes": 20.0, "points": 4, "assists": 6, "steals": 2, "blocks_favour": 1},
    ]
    k = _kpis(rows)
    # Row shares: pts .8/.4, reb .4/0, ast 0/.6, stl 0/.2, blk 0/.1, fdr .2/0 -> means below.
    raw_means = {"pts": 0.6, "reb": 0.2, "ast": 0.3, "stl": 0.1, "blk": 0.05, "fdr": 0.1}
    mean_total = sum(raw_means.values())
    pct_columns = [f"{prefix}_contribution_pct" for prefix in raw_means]
    for prefix, raw_mean in raw_means.items():
        assert k[f"{prefix}_contribution_pct"] == pytest.approx(raw_mean / mean_total * 100)
    assert k[pct_columns].sum() == pytest.approx(100.0)
    raw_shares = {
        "pts": [0.8, 0.4],
        "reb": [0.4, 0.0],
        "ast": [0.0, 0.6],
        "stl": [0.0, 0.2],
        "blk": [0.0, 0.1],
        "fdr": [0.2, 0.0],
    }
    for prefix, shares in raw_shares.items():
        assert k[f"{prefix}_contribution_std"] == pytest.approx(np.std(shares, ddof=1))


def test_contribution_excludes_non_positive_pir_rows_from_mean_and_std() -> None:
    """A game with pir <= 0 is dropped from that stat's share set, like every other blank-ratio KPI."""
    rows = [
        {"pir": 10, "minutes": 20.0, "points": 5},  # share 0.5
        {"pir": -2, "minutes": 20.0, "points": 9},  # excluded: this row's pir <= 0
        {"pir": 6, "minutes": 20.0, "points": 3},  # share 0.5
    ]
    k = _kpis(rows)
    assert k["pts_contribution_std"] == pytest.approx(0.0)  # only [0.5, 0.5] counted
    assert k["pts_contribution_pct"] == pytest.approx(100.0)  # sole tracked stat with nonzero share


def test_contribution_blank_when_no_row_has_positive_pir() -> None:
    k = _kpis([{"pir": -3, "minutes": 10.0, "points": 2}, {"pir": 0, "minutes": 10.0}])
    contribution_columns = [
        f"{prefix}_contribution_{suffix}" for prefix in CONTRIBUTION_STATS for suffix in ("pct", "std")
    ]
    assert k[contribution_columns].isna().all()


def test_contribution_mean_defined_with_one_valid_row_but_std_stays_blank() -> None:
    k = _kpis([{"pir": 10, "minutes": 20.0, "points": 5, "total_rebounds": 5}, {"pir": -1, "minutes": 10.0}])
    assert k["pts_contribution_pct"] == pytest.approx(50.0)
    assert k["reb_contribution_pct"] == pytest.approx(50.0)
    assert pd.isna(k["pts_contribution_std"])
    assert pd.isna(k["reb_contribution_std"])


def test_dnp_only_player_keeps_a_row_with_blank_kpis() -> None:
    k = _kpis([{"pir": 0, "minutes": 0.0}, {"pir": 0, "minutes": 0.0}])
    assert (k["games_played"], k["games_dnp"], k["recent_games"], k["dnp_rate"]) == (0, 2, 0, 1.0)
    assert k[["pir_avg", "pir_per_min", "pir_p50", "pts_contribution_pct", "usage_per_min"]].isna().all()


def test_identity_uses_latest_team_all_games_and_clean_name() -> None:
    rows = [
        {"pir": 5, "minutes": 10.0, "team_id": "AAA"},
        {"pir": 7, "minutes": 10.0, "team_id": "BBB"},
        {"pir": 0, "minutes": 0.0, "team_id": "BBB"},
    ]
    k = _kpis(rows, name="BALDWIN IV, WADE")
    assert (k["team_id"], k["player_name"], k["player_name_raw"]) == ("BBB", "Wade Baldwin IV", "BALDWIN IV, WADE")
    assert k["pir_avg"] == 6


def test_one_row_per_player_sorted_by_name_with_documented_columns() -> None:
    frames = [
        _games("P2", "ZED, ZACK", [{"pir": 1, "minutes": 5.0}]),
        _games("P1", "ABLE, ABE", [{"pir": 2, "minutes": 6.0}]),
    ]
    table = build_player_kpis(pd.concat(frames, ignore_index=True))
    assert table["player_id"].tolist() == ["P1", "P2"]
    assert list(table.columns) == KPI_COLUMNS
    guide = build_column_guide()
    assert guide["Column name"].tolist() == KPI_COLUMNS == list(GUIDE)
