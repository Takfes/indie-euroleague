#!/usr/bin/env python3
"""Build the player-level KPI table (one row per Kaggle player) from the game-level dataset.

Input: data/curated/player_game_stats.xlsx, sheet "Game Stats" (built by
src/build_game_player_stats.py, which must run first). The raw Kaggle csv is not read here.

Output: data/curated/player_kpis.xlsx with sheets, in order:
  - Column Guide: one row per column of Player KPIs (Source dataset, Column name,
    Explanation); texts live in src/kaggle_column_guide.py
  - Player KPIs: one row per player id with at least one row in the game-level dataset

Definitions (games = games played, i.e. minutes > 0; DNP rows only feed `games_dnp`):
  - Recent window: the last `RECENT_GAMES` games played, in date/time order (fewer if the
    player has fewer; `recent_games` says how many were used). "Season" = all games played.
  - Production: `pir_avg` (season mean), `pir_per_min` (total PIR / total minutes),
    `pir_avg_recent`, `pir_median_recent`.
  - Opportunity: `minutes_avg`, `minutes_avg_recent`, `minutes_trend` (recent - season mean,
    in minutes), `minutes_sd` and `minutes_cv`.
  - Stability, over all games played: SD and CV of per-game PIR/min (CV = SD / mean of the
    per-game series), PIR P10/P50/P90 (linear interpolation) and range P90 - P10. SD and CV
    need 2 games, otherwise blank.
  - Profile: contributions (season total of a component / season total PIR, blank when the
    total PIR is not positive; they can sum above 1 because PIR subtracts negatives),
    shooting percentages from season totals, `fdr_rate`, `usage_proxy_avg`, `usage_per_min`.
  Players who only have DNP rows keep a row with blank KPIs and `games_played` 0.

Usage:
    python src/build_player_kpis.py [--games PATH] [--out PATH]

Re-run after src/build_game_player_stats.py.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from build_game_player_stats import DEFAULT_OUT as GAME_STATS_PATH
from build_game_player_stats import FT_ATTEMPT_WEIGHT
from kaggle_column_guide import player_kpis_guide
from master_workbook import write_workbook
from player_name_matching import kaggle_display_name

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "data/curated/player_kpis.xlsx"

RECENT_GAMES = 5
MIN_GAMES_FOR_SPREAD = 2
PERCENTILES = {"pir_p10": 0.1, "pir_p50": 0.5, "pir_p90": 0.9}
CONTRIBUTIONS = {
    "pts_contrib": "points",
    "reb_contrib": "total_rebounds",
    "ast_contrib": "assists",
    "stl_contrib": "steals",
    "blk_contrib": "blocks_favour",
    "fdr_contrib": "fouls_received",
}
GUIDE = player_kpis_guide(RECENT_GAMES)
KPI_COLUMNS = list(GUIDE)
IDENTITY_COLUMNS = ["player_id", "player_name_raw", "player_name", "team_id", "games_played", "games_dnp", "dnp_rate"]
_SEASON_TOTALS = [
    "minutes",
    "pir",
    "points",
    "total_rebounds",
    "assists",
    "steals",
    "blocks_favour",
    "fouls_received",
    "fgm",
    "fga",
    "three_points_made",
    "three_points_attempted",
    "free_throws_made",
    "free_throws_attempted",
    "usage_proxy",
]


def _ratio(numerator: float, denominator: float) -> float:
    """Divide two scalars, NaN if the denominator is not positive."""
    return numerator / denominator if denominator > 0 else float("nan")


def player_kpis(played: pd.DataFrame, recent_games: int = RECENT_GAMES) -> dict[str, float]:
    """Compute the KPIs of one player from their games played.

    Args:
        played: The player's played rows with a `game_number` column (chronological).
        recent_games: Size of the recent window.

    Returns:
        KPI name -> value for every KPI column after the identity columns.
    """
    games = played.sort_values("game_number")
    recent = games.tail(recent_games)
    n_games = len(games)
    total = games[_SEASON_TOTALS].sum()
    pir_per_min_series = games["pir_per_min"]
    can_spread = n_games >= MIN_GAMES_FOR_SPREAD
    nan = float("nan")

    p10, p50, p90 = (games["pir"].quantile(q) for q in PERCENTILES.values())
    pir_per_min_mean = pir_per_min_series.mean()
    minutes_avg = games["minutes"].mean()
    minutes_sd = games["minutes"].std() if can_spread else nan
    pir_per_min_sd = pir_per_min_series.std() if can_spread else nan

    out: dict[str, float] = {
        "recent_games": len(recent),
        "pir_avg": games["pir"].mean(),
        "pir_per_min": _ratio(total["pir"], total["minutes"]),
        "pir_avg_recent": recent["pir"].mean(),
        "pir_median_recent": recent["pir"].median(),
        "minutes_avg": minutes_avg,
        "minutes_avg_recent": recent["minutes"].mean(),
        "minutes_trend": recent["minutes"].mean() - minutes_avg,
        "minutes_sd": minutes_sd,
        "minutes_cv": _ratio(minutes_sd, minutes_avg),
        "pir_per_min_sd": pir_per_min_sd,
        "pir_per_min_cv": _ratio(pir_per_min_sd, pir_per_min_mean),
        "pir_p10": p10,
        "pir_p50": p50,
        "pir_p90": p90,
        "pir_range": p90 - p10,
    }
    for column, component in CONTRIBUTIONS.items():
        out[column] = _ratio(total[component], total["pir"])
    out |= {
        "fg_pct": _ratio(total["fgm"], total["fga"]),
        "fg3_pct": _ratio(total["three_points_made"], total["three_points_attempted"]),
        "ft_pct": _ratio(total["free_throws_made"], total["free_throws_attempted"]),
        "ts_pct": _ratio(total["points"], 2 * (total["fga"] + FT_ATTEMPT_WEIGHT * total["free_throws_attempted"])),
        "fdr_rate": _ratio(total["fouls_received"], total["minutes"]),
        "usage_proxy_avg": games["usage_proxy"].mean(),
        "usage_per_min": _ratio(total["usage_proxy"], total["minutes"]),
    }
    return {column: float(out[column]) for column in KPI_COLUMNS[len(IDENTITY_COLUMNS) :]}


def build_player_kpis(games: pd.DataFrame, recent_games: int = RECENT_GAMES) -> pd.DataFrame:
    """Aggregate the game-level dataset to one KPI row per player.

    Args:
        games: The `Game Stats` frame (played and DNP rows).
        recent_games: Size of the recent window (games played).

    Returns:
        One row per `player_id` in `KPI_COLUMNS` order, sorted by cleaned name.
    """
    ordered = games.sort_values(["date", "time", "game_id"])
    latest = ordered.groupby("player_id").tail(1).set_index("player_id")
    counts = ordered.groupby("player_id")["played"].agg(games_played="sum", rows="size")
    identity = pd.DataFrame({
        "player_name_raw": latest["player"],
        "player_name": latest["player"].map(kaggle_display_name),
        "team_id": latest["team_id"],
        "games_played": counts["games_played"].astype(int),
        "games_dnp": (counts["rows"] - counts["games_played"]).astype(int),
    })
    identity["dnp_rate"] = identity["games_dnp"] / counts["rows"]

    kpis = {
        player_id: player_kpis(rows, recent_games)
        for player_id, rows in ordered[ordered["played"]].groupby("player_id")
    }
    blank = dict.fromkeys(KPI_COLUMNS[len(IDENTITY_COLUMNS) :], float("nan")) | {"recent_games": 0.0}
    kpi_rows = pd.DataFrame([kpis.get(player_id, blank) for player_id in identity.index], index=identity.index)
    table = identity.join(kpi_rows).reset_index()
    table["recent_games"] = table["recent_games"].astype(int)
    table = table[KPI_COLUMNS].sort_values("player_name", key=lambda names: names.str.lower(), kind="stable")
    return table.reset_index(drop=True)


def build_column_guide() -> pd.DataFrame:
    """One row per `Player KPIs` column: (Source dataset, Column name, Explanation)."""
    rows = [(source, column, text) for column, (source, text) in GUIDE.items()]
    return pd.DataFrame(rows, columns=["Source dataset", "Column name", "Explanation"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--games", type=Path, default=GAME_STATS_PATH, help="Game-level workbook (input)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output xlsx path")
    args = parser.parse_args()

    if not args.games.exists():
        raise SystemExit(
            f"Missing {args.games}. Build it first: `uv run python src/build_game_player_stats.py` "
            "(needs the git-ignored Kaggle csv files)."
        )
    games = pd.read_excel(args.games, sheet_name="Game Stats")
    table = build_player_kpis(games)
    write_workbook(args.out, {"Column Guide": build_column_guide(), "Player KPIs": table})
    played = int((table["games_played"] > 0).sum())
    print(f"Wrote {len(table)} players x {len(table.columns)} columns to {args.out}")
    print(f"Players with at least one game played: {played}; DNP-only players: {len(table) - played}")


if __name__ == "__main__":
    main()
