"""Tests for the player KPI aggregation: windows, percentiles, contributions, blanks and identity."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import build_player_kpis as kpi_module
from build_game_player_stats import CONTRIBUTION_STATS
from build_player_kpis import (
    DISTRIBUTION_FAMILIES,
    GUIDE,
    KPI_COLUMNS,
    build_column_guide,
    build_player_kpis,
    player_kpis,
)

ROW_DEFAULTS = {
    "points": 0,
    "total_rebounds": 0,
    "assists": 0,
    "steals": 0,
    "blocks_favour": 0,
    "fouls_received": 0,
    "fg_missed": 0,
    "ft_missed": 0,
    "turnovers": 0,
    "blocks_against": 0,
    "fouls_committed": 0,
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
            "is_starter": row.get("is_starter", 0),
            "game_number": number if played else pd.NA,
            "pir_per_min": row["pir"] / row["minutes"] if played else np.nan,
            **ROW_DEFAULTS,
            "pir": pir,
            **{k: v for k, v in row.items() if k not in ("pir", "team_id")},
        }
        entry["usage_per_min"] = entry["usage_proxy"] / row["minutes"] if played else np.nan
        entry["fdr_per_min"] = entry["fouls_received"] / row["minutes"] if played else np.nan
        for prefix, (sign, stat) in CONTRIBUTION_STATS.items():
            entry[f"{prefix}_share_of_pir"] = sign * entry[stat] / pir if pd.notna(pir) and pir > 0 else np.nan
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


def test_starts_rate_is_starts_over_games_played() -> None:
    rows = [
        {"pir": 5, "minutes": 10.0, "is_starter": 1},
        {"pir": 5, "minutes": 10.0, "is_starter": 0},
        {"pir": 0, "minutes": 0.0, "is_starter": 0},  # DNP: excluded from both starts and games_played
        {"pir": 5, "minutes": 10.0, "is_starter": 1},
    ]
    k = _kpis(rows)
    assert k["starts_rate"] == pytest.approx(2 / 3)


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


def test_contribution_pct_is_the_plain_mean_signed_share_times_100_and_all_eleven_sum_to_100() -> None:
    """Both games have pir > 0 and the components add up to pir: no renormalization is needed."""
    rows = [
        # pir = 8 + 4 + 2 - 2 (turnovers) - 1 (missed FG) - 1 (fouls committed) = 10
        {
            "pir": 10,
            "minutes": 20.0,
            "points": 8,
            "total_rebounds": 4,
            "fouls_received": 2,
            "turnovers": 2,
            "fg_missed": 1,
            "fouls_committed": 1,
        },
        # pir = 4 + 6 + 2 + 1 - 2 (missed FT) - 1 (own shot blocked) = 10
        {
            "pir": 10,
            "minutes": 20.0,
            "points": 4,
            "assists": 6,
            "steals": 2,
            "blocks_favour": 1,
            "ft_missed": 2,
            "blocks_against": 1,
        },
    ]
    k = _kpis(rows)
    row_shares = {
        "pts": [0.8, 0.4],
        "reb": [0.4, 0.0],
        "ast": [0.0, 0.6],
        "stl": [0.0, 0.2],
        "blk": [0.0, 0.1],
        "fdr": [0.2, 0.0],
        "mfg": [-0.1, 0.0],
        "mft": [0.0, -0.2],
        "tov": [-0.2, 0.0],
        "blkag": [0.0, -0.1],
        "pf": [-0.1, 0.0],
    }
    assert list(row_shares) == list(CONTRIBUTION_STATS)
    for prefix, shares in row_shares.items():
        assert k[f"{prefix}_contribution_pct"] == pytest.approx(np.mean(shares) * 100)
        assert k[f"{prefix}_contribution_std"] == pytest.approx(np.std(shares, ddof=1))
    pct_columns = [f"{prefix}_contribution_pct" for prefix in CONTRIBUTION_STATS]
    assert k[pct_columns].sum() == pytest.approx(100.0)


def test_contribution_excludes_non_positive_pir_rows_from_mean_and_std() -> None:
    """A game with pir <= 0 is dropped from that stat's share set, like every other blank-ratio KPI."""
    rows = [
        {"pir": 10, "minutes": 20.0, "points": 5},  # share 0.5
        {"pir": -2, "minutes": 20.0, "points": 9},  # excluded: this row's pir <= 0
        {"pir": 6, "minutes": 20.0, "points": 3},  # share 0.5
    ]
    k = _kpis(rows)
    assert k["pts_contribution_std"] == pytest.approx(0.0)  # only [0.5, 0.5] counted
    assert k["pts_contribution_pct"] == pytest.approx(50.0)  # plain mean of [0.5, 0.5], times 100


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


SPREAD_STATS = ("sd", "cv", "p10", "p50", "p90", "range")


def test_every_distribution_family_and_contribution_exposes_the_standard_set() -> None:
    for base in DISTRIBUTION_FAMILIES:
        for stat in SPREAD_STATS:
            assert f"{base}_{stat}" in KPI_COLUMNS, f"{base}_{stat}"
    assert {"pir_avg", "pir_per_min", "minutes_avg", "usage_proxy_avg", "usage_per_min", "fdr_rate"} <= set(KPI_COLUMNS)
    for prefix in CONTRIBUTION_STATS:
        for stat in ("pct", "std", "cv", "p10", "p50", "p90", "range"):
            assert f"{prefix}_contribution_{stat}" in KPI_COLUMNS, f"{prefix}_contribution_{stat}"
    assert len(KPI_COLUMNS) == len(set(KPI_COLUMNS))


def test_pir_gains_sd_and_cv_and_minutes_gain_percentiles() -> None:
    rows = [
        {"pir": pir, "minutes": minutes} for pir, minutes in ((2, 10.0), (4, 20.0), (6, 30.0), (20, 40.0), (8, 20.0))
    ]
    k = _kpis(rows)
    pir = [2, 4, 6, 20, 8]
    assert k["pir_sd"] == pytest.approx(np.std(pir, ddof=1))
    assert k["pir_cv"] == pytest.approx(np.std(pir, ddof=1) / np.mean(pir))
    minutes = [10.0, 20.0, 30.0, 40.0, 20.0]
    assert k["minutes_p10"] == pytest.approx(np.percentile(minutes, 10))
    assert k["minutes_p50"] == pytest.approx(20.0)
    assert k["minutes_p90"] == pytest.approx(np.percentile(minutes, 90))
    assert k["minutes_range"] == pytest.approx(k["minutes_p90"] - k["minutes_p10"])
    assert k["minutes_sd"] == pytest.approx(np.std(minutes, ddof=1))
    assert k["minutes_cv"] == pytest.approx(np.std(minutes, ddof=1) / np.mean(minutes))


def test_per_minute_and_usage_families_take_their_spread_from_the_per_game_series() -> None:
    """The average of a per-minute KPI is a ratio of season totals; its spread stats use the per-game values."""
    rows = [
        {"pir": 4, "minutes": 10.0, "usage_proxy": 5.0, "fouls_received": 1},
        {"pir": 12, "minutes": 40.0, "usage_proxy": 20.0, "fouls_received": 4},
        {"pir": 8, "minutes": 20.0, "usage_proxy": 15.0, "fouls_received": 2},
    ]
    k = _kpis(rows)
    assert k["pir_per_min_p50"] == pytest.approx(np.median([0.4, 0.3, 0.4]))
    assert k["pir_per_min_range"] == pytest.approx(k["pir_per_min_p90"] - k["pir_per_min_p10"])
    assert k["usage_proxy_avg"] == pytest.approx(np.mean([5.0, 20.0, 15.0]))
    assert k["usage_proxy_p50"] == pytest.approx(15.0)
    assert k["usage_proxy_sd"] == pytest.approx(np.std([5.0, 20.0, 15.0], ddof=1))
    assert k["usage_per_min"] == pytest.approx(40 / 70)  # ratio of totals, not the mean of the series
    per_game_usage = [0.5, 0.5, 0.75]
    assert k["usage_per_min_p50"] == pytest.approx(np.median(per_game_usage))
    assert k["usage_per_min_cv"] == pytest.approx(np.std(per_game_usage, ddof=1) / np.mean(per_game_usage))
    per_game_fdr = [0.1, 0.1, 0.1]
    assert k["fdr_rate"] == pytest.approx(7 / 70)
    assert k["fdr_rate_p50"] == pytest.approx(np.median(per_game_fdr))
    assert k["fdr_rate_sd"] == pytest.approx(0.0)


def test_single_game_keeps_percentiles_but_blanks_sd_and_cv_for_every_family() -> None:
    k = _kpis([{"pir": 7, "minutes": 14.0, "points": 7}])
    for base in DISTRIBUTION_FAMILIES:
        assert pd.isna(k[f"{base}_sd"]) and pd.isna(k[f"{base}_cv"])
        assert k[f"{base}_p10"] == k[f"{base}_p50"] == k[f"{base}_p90"]
        assert k[f"{base}_range"] == 0
    assert k["pts_contribution_p10"] == k["pts_contribution_p50"] == k["pts_contribution_p90"] == pytest.approx(100.0)
    assert pd.isna(k["pts_contribution_std"]) and pd.isna(k["pts_contribution_cv"])


def test_contribution_distribution_is_on_the_pct_scale_and_negative_components_get_a_positive_cv() -> None:
    rows = [
        # pir = 8 + 4 - 2 (turnovers) = 10 ; pir = 6 + 2 - 4 (turnovers) = 4 ; pir = 5 + 5 - 0 = 10
        {"pir": 10, "minutes": 20.0, "points": 8, "total_rebounds": 4, "turnovers": 2},
        {"pir": 4, "minutes": 20.0, "points": 6, "total_rebounds": 2, "turnovers": 4},
        {"pir": 10, "minutes": 20.0, "points": 5, "total_rebounds": 5},
    ]
    k = _kpis(rows)
    tov_shares = [-0.2, -1.0, 0.0]
    pts_shares = [0.8, 1.5, 0.5]
    assert k["tov_contribution_pct"] == pytest.approx(np.mean(tov_shares) * 100)
    assert k["tov_contribution_p50"] == pytest.approx(-20.0)
    assert k["tov_contribution_p10"] == pytest.approx(np.percentile(tov_shares, 10) * 100)
    assert k["tov_contribution_p90"] == pytest.approx(np.percentile(tov_shares, 90) * 100)
    assert k["tov_contribution_range"] == pytest.approx(k["tov_contribution_p90"] - k["tov_contribution_p10"])
    assert k["tov_contribution_std"] == pytest.approx(np.std(tov_shares, ddof=1))  # sd stays unscaled
    assert k["tov_contribution_cv"] == pytest.approx(np.std(tov_shares, ddof=1) / abs(np.mean(tov_shares)))
    assert k["pts_contribution_cv"] == pytest.approx(np.std(pts_shares, ddof=1) / np.mean(pts_shares))
    assert k["pts_contribution_p90"] == pytest.approx(np.percentile(pts_shares, 90) * 100)
    # A component that never appears has a zero mean: no CV.
    assert k["mfg_contribution_pct"] == 0
    assert pd.isna(k["mfg_contribution_cv"])
    assert k["mfg_contribution_p50"] == 0


def test_a_kpi_computed_without_a_guide_entry_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(kpi_module.DISTRIBUTION_FAMILIES, "surprise_metric", "pir")
    played = _games("P1", "SMITH, JOHN", [{"pir": 5, "minutes": 10.0}])
    with pytest.raises(ValueError, match=r"missing from the Column Guide: \['surprise_metric_cv'.*surprise_metric_sd"):
        player_kpis(played)
