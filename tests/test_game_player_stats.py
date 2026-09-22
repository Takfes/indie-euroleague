"""Tests for the game-level player dataset: PIR, usage proxy, minutes parsing and the hard checks."""

from __future__ import annotations

import pandas as pd
import pytest

from build_game_player_stats import (
    CONTRIBUTION_STATS,
    GAME_COLUMNS,
    add_game_number,
    add_row_metrics,
    build_column_guide,
    build_game_stats,
    parse_minutes,
)

STAT_ZERO = {
    "points": 0,
    "two_points_made": 0,
    "two_points_attempted": 0,
    "three_points_made": 0,
    "three_points_attempted": 0,
    "free_throws_made": 0,
    "free_throws_attempted": 0,
    "offensive_rebounds": 0,
    "defensive_rebounds": 0,
    "total_rebounds": 0,
    "assists": 0,
    "steals": 0,
    "turnovers": 0,
    "blocks_favour": 0,
    "blocks_against": 0,
    "fouls_committed": 0,
    "fouls_received": 0,
    "valuation": 0,
    "plus_minus": 0,
}


def _box_row(game_id: str, player_id: str, team: str, minutes: str, dorsal: str = "5", **stats: int) -> dict:
    return {
        "game_player_id": f"{game_id}_{player_id}",
        "game_id": game_id,
        "game": "AAA-BBB",
        "round": 1,
        "phase": "REGULAR SEASON",
        "season_code": "E2025",
        "player_id": player_id,
        "is_starter": 0.0,
        "is_playing": 1.0,
        "team_id": team,
        "dorsal": dorsal,
        "player": f"{player_id}, TEST",
        "minutes": minutes,
        **STAT_ZERO,
        **stats,
    }


def _game_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    """One game: player P1 (PIR 12), a DNP player, and the two team-total rows."""
    p1 = _box_row(
        "E2025_001",
        "P1",
        "AAA",
        "20:30",
        points=10,
        two_points_made=3,
        two_points_attempted=5,
        three_points_made=1,
        three_points_attempted=3,
        free_throws_made=1,
        free_throws_attempted=2,
        total_rebounds=4,
        assists=2,
        steals=1,
        turnovers=1,
        blocks_favour=1,
        blocks_against=1,
        fouls_committed=2,
        fouls_received=3,
        valuation=12,
    )
    # 10 + 4 + 2 + 1 + 1 + 3 - (8 - 4) - (2 - 1) - 1 - 1 - 2 = 12
    dnp = _box_row("E2025_001", "P2", "BBB", "DNP", is_playing=0.0)
    totals = [_box_row("E2025_001", team, team, "200:00", dorsal="TOTAL") for team in ("AAA", "BBB")]
    box = pd.DataFrame([p1, dnp, *totals])
    header = pd.DataFrame([
        {
            "game_id": "E2025_001",
            "date": "2025-10-01",
            "time": "20:00:00",
            "team_id_a": "AAA",
            "team_id_b": "BBB",
            "team_a": "TEAM A",
            "team_b": "TEAM B",
        }
    ])
    return box, header


def test_parse_minutes_decimal_and_dnp() -> None:
    result = parse_minutes(pd.Series(["17:53", "DNP", "00:30", "45:00"]))
    assert result.tolist() == pytest.approx([17 + 53 / 60, 0.0, 0.5, 45.0])


def test_parse_minutes_rejects_unknown_text() -> None:
    with pytest.raises(ValueError, match="Unparseable minutes"):
        parse_minutes(pd.Series(["17:53", "N/A"]))


def test_pir_and_usage_proxy_on_a_row() -> None:
    box, _ = _game_frames()
    row = box.iloc[[0]].copy()
    row["minutes"] = 20.5
    row["played"] = True
    out = add_row_metrics(row).iloc[0]
    assert out["fgm"] == 4
    assert out["fga"] == 8
    assert out["pir"] == 12
    # FGA 8 + 0.44 * FTA 2 + TO 1 + 0.5 * AST 2
    assert out["usage_proxy"] == pytest.approx(8 + 0.88 + 1 + 1)
    assert out["pir_per_min"] == pytest.approx(12 / 20.5)
    assert out["usage_per_min"] == pytest.approx(10.88 / 20.5)
    assert out["fdr_per_min"] == pytest.approx(3 / 20.5)
    assert out["fg_pct"] == pytest.approx(0.5)
    assert out["ft_pct"] == pytest.approx(0.5)
    assert out["ts_pct"] == pytest.approx(10 / (2 * (8 + 0.88)))


def test_contribution_shares_divide_by_row_pir_and_blank_when_pir_not_positive() -> None:
    box, _ = _game_frames()
    row = box.iloc[[0]].copy()  # pir 12, points 10, total_rebounds 4, fouls_received 3
    row["minutes"] = 20.5
    row["played"] = True
    out = add_row_metrics(row).iloc[0]
    assert out["pts_share_of_pir"] == pytest.approx(10 / 12)
    assert out["reb_share_of_pir"] == pytest.approx(4 / 12)
    assert out["fdr_share_of_pir"] == pytest.approx(3 / 12)
    assert out["ast_share_of_pir"] == pytest.approx(2 / 12)
    assert out["stl_share_of_pir"] == pytest.approx(1 / 12)
    assert out["blk_share_of_pir"] == pytest.approx(1 / 12)

    # Zero out every make (keep the attempts): pir drops from 12 to -5, without touching the
    # counting stats (rebounds, assists, steals, blocks, fouls drawn) used above.
    row.loc[row.index[0], ["two_points_made", "three_points_made", "free_throws_made", "points"]] = 0
    zero_pir_out = add_row_metrics(row).iloc[0]
    assert zero_pir_out["pir"] <= 0
    share_columns = [f"{prefix}_share_of_pir" for prefix in CONTRIBUTION_STATS]
    assert zero_pir_out[share_columns].isna().all()


def test_percentages_blank_when_denominator_is_zero_and_dnp_rows_blank() -> None:
    box, _ = _game_frames()
    rows = box.iloc[[1, 1]].copy().reset_index(drop=True)
    rows["minutes"] = [0.0, 10.0]
    rows["played"] = [False, True]
    out = add_row_metrics(rows)
    assert out.loc[0, ["pir", "pir_per_min", "usage_proxy", "fg_pct", "ts_pct"]].isna().all()
    assert out.loc[1, "pir"] == 0
    assert pd.isna(out.loc[1, "fg_pct"])
    assert pd.isna(out.loc[1, "ft_pct"])
    assert pd.isna(out.loc[1, "ts_pct"])


def test_game_number_counts_games_played_chronologically() -> None:
    df = pd.DataFrame({
        "game_id": ["g3", "g1", "g2", "g4"],
        "date": ["2025-10-03", "2025-10-01", "2025-10-02", "2025-10-04"],
        "time": ["20:00:00"] * 4,
        "player_id": ["P1"] * 4,
        "played": [True, True, False, True],
    })
    assert add_game_number(df).tolist() == [2, 1, pd.NA, 3]


def test_build_game_stats_drops_totals_flags_dnp_and_joins_header() -> None:
    box, header = _game_frames()
    df, stats = build_game_stats(box, header, "E2025", ["REGULAR SEASON"])
    assert list(df.columns) == GAME_COLUMNS
    assert stats["team_total_rows_dropped"] == 2
    assert len(df) == 2
    assert df["played"].tolist() == [True, False]
    played, dnp = df.iloc[0], df.iloc[1]
    assert (played["team_name"], played["opponent_id"], played["home_away"]) == ("TEAM A", "BBB", "home")
    assert (dnp["team_name"], dnp["opponent_id"], dnp["home_away"]) == ("TEAM B", "AAA", "away")
    assert played["date"] == "2025-10-01"
    assert played["pir"] == played["valuation"] == 12
    assert pd.isna(dnp["pir"])
    assert dnp["minutes"] == 0


def test_build_game_stats_rejects_pir_mismatch() -> None:
    box, header = _game_frames()
    box.loc[0, "valuation"] = 9
    with pytest.raises(ValueError, match="differs from official valuation"):
        build_game_stats(box, header, "E2025", ["REGULAR SEASON"])


def test_build_game_stats_requires_two_team_total_rows_per_game() -> None:
    box, header = _game_frames()
    with pytest.raises(ValueError, match="team-total rows"):
        build_game_stats(box.iloc[:-1], header, "E2025", ["REGULAR SEASON"])


def test_build_game_stats_rejects_duplicate_player_game() -> None:
    box, header = _game_frames()
    with pytest.raises(ValueError, match="Duplicate"):
        build_game_stats(pd.concat([box, box.iloc[[0]]]), header, "E2025", ["REGULAR SEASON"])


def test_build_game_stats_phase_filter_can_leave_nothing() -> None:
    box, header = _game_frames()
    with pytest.raises(ValueError, match="No player rows"):
        build_game_stats(box, header, "E2025", ["PLAYOFFS"])


def test_column_guide_covers_every_game_stats_column_once() -> None:
    guide = build_column_guide()
    assert guide["Column name"].tolist() == GAME_COLUMNS
    assert not guide["Explanation"].str.len().gt(80).any()
