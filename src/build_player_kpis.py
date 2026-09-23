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
    in minutes), `starts_rate` (starts / games played, season).
  - Distribution KPIs: every per-game series below has an average plus the same six spread
    stats, over all games played: `{base}_sd` (sample std, ddof=1) and `{base}_cv` (sd / mean of
    the series; blank if the mean is not positive) need 2 games, otherwise blank;
    `{base}_p10`, `{base}_p50`, `{base}_p90` (linear-interpolation percentiles; defined from 1
    game) and `{base}_range` = p90 - p10. The series (`DISTRIBUTION_FAMILIES`) are game PIR
    (`pir_avg`), PIR/min (`pir_per_min`, the ratio of season totals; its spread stats come from
    the per-game series), minutes (`minutes_avg`), usage proxy (`usage_proxy_avg`), usage per
    minute (`usage_per_min`, ratio of season totals) and fouls drawn per minute (`fdr_rate`, ratio
    of season totals). Shooting percentages are left out: their per-game value is attempt-weighted
    (0% or 100% on one shot, undefined on a game without attempts, and `fg3_pct` has no per-game
    series), so the season ratio of totals is the figure; `starts_rate` and the recent-window
    figures are single values too.
  - Profile: contribution shares are computed per game in src/build_game_player_stats.py
    for all 11 components of PIR (`{prefix}_share_of_pir` = the component's signed value /
    that row's pir, blank unless the row's pir > 0; missed shots, turnovers, shots blocked
    and fouls committed enter with a minus sign, so the 11 shares of a row sum to exactly 1),
    then aggregated here per player over the rows where the share is defined:
    `{prefix}_contribution_pct` is the plain mean share times 100 - no renormalization, the 11
    values sum to 100 by construction. The same shares carry the distribution set:
    `{prefix}_contribution_std` (sample std, left unscaled), `_cv` (sd / |mean|, so the
    negative components get a positive CV), and `_p10`, `_p50`, `_p90`, `_range` on the same
    0-100 scale as `_pct`. Also shooting percentages from season totals (fractions, 0-1),
    `fdr_rate`, `usage_proxy_avg`, `usage_per_min`.
  Players who only have DNP rows keep a row with blank KPIs and `games_played` 0.

Usage:
    python src/build_player_kpis.py [--games PATH] [--out PATH]

Re-run after src/build_game_player_stats.py.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from build_game_player_stats import CONTRIBUTION_STATS, FT_ATTEMPT_WEIGHT
from build_game_player_stats import DEFAULT_OUT as GAME_STATS_PATH
from kaggle_column_guide import player_kpis_guide
from master_workbook import write_workbook
from player_name_matching import kaggle_display_name

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "data/curated/player_kpis.xlsx"

RECENT_GAMES = 5
MIN_GAMES_FOR_SPREAD = 2
PERCENTILES = {"p10": 0.1, "p50": 0.5, "p90": 0.9}
# KPI name prefix -> per-game series of the Game Stats sheet: each gets the full distribution set
# (`_sd`, `_cv`, `_p10`, `_p50`, `_p90`, `_range`) next to its average. Remove an entry to drop a family.
DISTRIBUTION_FAMILIES = {
    "pir": "pir",
    "pir_per_min": "pir_per_min",
    "minutes": "minutes",
    "usage_proxy": "usage_proxy",
    "usage_per_min": "usage_per_min",
    "fdr_rate": "fdr_per_min",
}
GUIDE = player_kpis_guide(RECENT_GAMES)
KPI_COLUMNS = list(GUIDE)
IDENTITY_COLUMNS = ["player_id", "player_name_raw", "player_name", "team_id", "games_played", "games_dnp", "dnp_rate"]
_SEASON_TOTALS = [
    "minutes",
    "pir",
    "points",
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


def _distribution(series: pd.Series, sign: int = 1, scale: float = 1.0) -> dict[str, float]:
    """The standard spread set of one per-game series: sd, cv, p10, p50, p90 and range.

    Args:
        series: The player's per-game values; NaN games are ignored.
        sign: The sign that makes the series' mean non-negative (-1 for a component that
            reduces PIR), so `cv` = sd / |mean| is defined for it too.
        scale: Factor applied to the percentiles and the range, not to `sd` or `cv`.

    Returns:
        `sd` and `cv` (NaN under `MIN_GAMES_FOR_SPREAD` values, `cv` also NaN if the mean is not
        positive after `sign`), `p10`, `p50`, `p90` (NaN only without any value) and `range`.
    """
    sd = series.std() if series.count() >= MIN_GAMES_FOR_SPREAD else float("nan")
    p10, p50, p90 = (series.quantile(q) * scale for q in PERCENTILES.values())
    return {"sd": sd, "cv": _ratio(sd, sign * series.mean()), "p10": p10, "p50": p50, "p90": p90, "range": p90 - p10}


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
    minutes_avg = games["minutes"].mean()

    out: dict[str, float] = {
        "recent_games": len(recent),
        "pir_avg": games["pir"].mean(),
        "pir_per_min": _ratio(total["pir"], total["minutes"]),
        "pir_avg_recent": recent["pir"].mean(),
        "pir_median_recent": recent["pir"].median(),
        "minutes_avg": minutes_avg,
        "minutes_avg_recent": recent["minutes"].mean(),
        "minutes_trend": recent["minutes"].mean() - minutes_avg,
        "starts_rate": _ratio(games["is_starter"].sum(), n_games),
    }
    for base, column in DISTRIBUTION_FAMILIES.items():
        out |= {f"{base}_{stat}": value for stat, value in _distribution(games[column]).items()}
    for prefix, (sign, _) in CONTRIBUTION_STATS.items():
        shares = games[f"{prefix}_share_of_pir"]
        out[f"{prefix}_contribution_pct"] = shares.mean() * 100
        # `sd` keeps its original `_std` column name here; the other families call it `_sd`.
        distribution = _distribution(shares, sign=sign, scale=100)
        out |= {f"{prefix}_contribution_{'std' if stat == 'sd' else stat}": v for stat, v in distribution.items()}
    out |= {
        "fg_pct": _ratio(total["fgm"], total["fga"]),
        "fg3_pct": _ratio(total["three_points_made"], total["three_points_attempted"]),
        "ft_pct": _ratio(total["free_throws_made"], total["free_throws_attempted"]),
        "ts_pct": _ratio(total["points"], 2 * (total["fga"] + FT_ATTEMPT_WEIGHT * total["free_throws_attempted"])),
        "fdr_rate": _ratio(total["fouls_received"], total["minutes"]),
        "usage_proxy_avg": games["usage_proxy"].mean(),
        "usage_per_min": _ratio(total["usage_proxy"], total["minutes"]),
    }
    undocumented = sorted(set(out) - set(KPI_COLUMNS))
    if undocumented:
        raise ValueError(
            f"KPIs computed but missing from the Column Guide: {undocumented}. "
            "Add them to player_kpis_guide() in src/kaggle_column_guide.py."
        )
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
