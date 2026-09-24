#!/usr/bin/env python3
"""Build the game-level player dataset (one row per player per game) from the Kaggle box score.

Inputs (git-ignored folder, see .claude/skills/player-game-stats/SKILL.md for how to get it):
  - data/kaggle-euroleague-data/euroleague_box_score.csv (one row per player per game, all seasons)
  - data/kaggle-euroleague-data/euroleague_header.csv (one row per game: date, time, teams, phase)

Output: data/curated/player_game_stats.xlsx with sheets, in order:
  - Column Guide: one row per column of Game Stats (Source dataset, Column name, Explanation);
    texts live in src/kaggle_column_guide.py
  - Game Stats: one row per player per game of the chosen season and phases

Steps: keep the season, drop the two team-total rows per game (dorsal "TOTAL"; they are
not players), keep the chosen phases, parse `minutes` (MM:SS text, or DNP) to a decimal
number, join date/time/teams from the header, then add per-row metrics: `pir`,
`pir_per_min`, `usage_proxy`, `usage_per_min`, `fgm`, `fga`, `fg_pct`, `ft_pct`, `ts_pct`,
`fdr_per_min`, `fg_missed`, `ft_missed`, the per-row PIR contribution shares
(`CONTRIB_SHARE_COLUMNS`: one per component of the PIR formula, signed so that the 11 shares
of a row sum to exactly 1; e.g. `pts_share_of_pir` = points / pir and `tov_share_of_pir` =
-turnovers / pir for that row, blank unless that row's pir > 0; a per-game view, the player KPIs
do not aggregate them) and a per-player chronological `game_number` (games played only).

A player played a game when minutes > 0. DNP rows (did not play) stay in the dataset with
`played` False and blank derived values, so a DNP count is possible; every stat KPI later
ignores them. Hard checks: PIR computed here equals the official `valuation` on every
played row, the 11 signed PIR components add up to PIR on every played row, no duplicate
(game_id, player_id), two team-total rows per game.

Usage:
    python src/build_game_player_stats.py [--season E2025] [--phases PHASE ...] [--out PATH]

Re-run after refreshing the Kaggle csv files.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from kaggle_column_guide import GAME_STATS_GUIDE
from master_workbook import write_workbook

REPO_ROOT = Path(__file__).resolve().parents[1]
BOX_SCORE_PATH = REPO_ROOT / "data/kaggle-euroleague-data/euroleague_box_score.csv"
HEADER_PATH = REPO_ROOT / "data/kaggle-euroleague-data/euroleague_header.csv"
DEFAULT_OUT = REPO_ROOT / "data/curated/player_game_stats.xlsx"

DEFAULT_SEASON = "E2025"
DEFAULT_PHASES = ["REGULAR SEASON", "PLAY-IN", "PLAYOFFS", "FINAL FOUR"]
TEAM_TOTAL_DORSAL = "TOTAL"
TEAM_TOTAL_ROWS_PER_GAME = 2
FT_ATTEMPT_WEIGHT = 0.44
ASSIST_WEIGHT = 0.5

# Prefix -> (sign, Game Stats column) for the 11 components of PIR, positive ones first, in the
# order of the PIR formula below. `sign` is the component's sign in that formula, so PIR is exactly
# the sum of sign x column over all eleven and the signed shares `sign x column / pir` of a played
# row sum to exactly 1 (no renormalization needed). Prefixes: mfg / mft = missed field goals / free
# throws, blkag = blocks against (own shots blocked), pf = personal fouls committed. Shared with
# src/build_player_kpis.py, which averages each signed component per player (`{prefix}_contribution_*`)
# and takes its share of the average PIR; the per-game `{prefix}_share_of_pir` columns are only a
# per-game view (nothing aggregates them: a game with a tiny pir makes a share huge).
CONTRIBUTION_STATS: dict[str, tuple[int, str]] = {
    "pts": (1, "points"),
    "reb": (1, "total_rebounds"),
    "ast": (1, "assists"),
    "stl": (1, "steals"),
    "blk": (1, "blocks_favour"),
    "fdr": (1, "fouls_received"),
    "mfg": (-1, "fg_missed"),
    "mft": (-1, "ft_missed"),
    "tov": (-1, "turnovers"),
    "blkag": (-1, "blocks_against"),
    "pf": (-1, "fouls_committed"),
}
CONTRIB_SHARE_COLUMNS = [f"{prefix}_share_of_pir" for prefix in CONTRIBUTION_STATS]

DERIVED_COLUMNS = [
    "pir",
    "pir_per_min",
    "usage_proxy",
    "usage_per_min",
    "fdr_per_min",
    "fg_pct",
    "ft_pct",
    "ts_pct",
    *CONTRIB_SHARE_COLUMNS,
]
GAME_COLUMNS = [
    "game_id",
    "game",
    "season_code",
    "phase",
    "round",
    "date",
    "time",
    "team_id",
    "team_name",
    "opponent_id",
    "opponent_name",
    "home_away",
    "player_id",
    "player",
    "is_starter",
    "played",
    "game_number",
    "minutes",
    "points",
    "fgm",
    "fga",
    "fg_missed",
    "two_points_made",
    "two_points_attempted",
    "three_points_made",
    "three_points_attempted",
    "free_throws_made",
    "free_throws_attempted",
    "ft_missed",
    "offensive_rebounds",
    "defensive_rebounds",
    "total_rebounds",
    "assists",
    "steals",
    "turnovers",
    "blocks_favour",
    "blocks_against",
    "fouls_committed",
    "fouls_received",
    "valuation",
    "plus_minus",
    *DERIVED_COLUMNS,
]
_MINUTES_PATTERN = r"^\d+:[0-5]\d$"


def safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Divide element-wise, giving NaN (blank) wherever the denominator is 0 or missing."""
    return numerator / denominator.where(denominator > 0)


def parse_minutes(text: pd.Series) -> pd.Series:
    """Convert `MM:SS` text to decimal minutes; `DNP` (did not play) becomes 0.

    Args:
        text: Raw `minutes` column of the box score.

    Returns:
        Float minutes (`"17:53"` -> 17.883...).

    Raises:
        ValueError: If a value is neither `DNP` nor `MM:SS`.
    """
    is_dnp = text == "DNP"
    malformed = ~is_dnp & ~text.str.match(_MINUTES_PATTERN, na=False)
    if malformed.any():
        raise ValueError(f"Unparseable minutes values (expected MM:SS or DNP): {sorted(text[malformed].unique())[:10]}")
    parts = text.where(~is_dnp, "0:00").str.split(":", expand=True).astype(float)
    return parts[0] + parts[1] / 60


def add_row_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Add `fgm`, `fga`, the missed-shot counts and the derived per-row metrics; derived values stay blank on DNP rows.

    Args:
        df: Player rows with the box score stat columns, decimal `minutes` and boolean `played`.

    Returns:
        A copy with `fgm`, `fga`, `fg_missed`, `ft_missed`, `pir`, `pir_per_min`, `usage_proxy`,
        `usage_per_min`, `fdr_per_min`, `fg_pct`, `ft_pct`, `ts_pct` and the `CONTRIB_SHARE_COLUMNS` added.
    """
    out = df.copy()
    out["fgm"] = out["two_points_made"] + out["three_points_made"]
    out["fga"] = out["two_points_attempted"] + out["three_points_attempted"]
    out["fg_missed"] = out["fga"] - out["fgm"]
    out["ft_missed"] = out["free_throws_attempted"] - out["free_throws_made"]
    out["pir"] = (
        out["points"]
        + out["total_rebounds"]
        + out["assists"]
        + out["steals"]
        + out["blocks_favour"]
        + out["fouls_received"]
        - (out["fga"] - out["fgm"])
        - (out["free_throws_attempted"] - out["free_throws_made"])
        - out["turnovers"]
        - out["blocks_against"]
        - out["fouls_committed"]
    )
    out["usage_proxy"] = (
        out["fga"]
        + FT_ATTEMPT_WEIGHT * out["free_throws_attempted"]
        + out["turnovers"]
        + ASSIST_WEIGHT * out["assists"]
    )
    out["pir_per_min"] = safe_divide(out["pir"], out["minutes"])
    out["usage_per_min"] = safe_divide(out["usage_proxy"], out["minutes"])
    out["fdr_per_min"] = safe_divide(out["fouls_received"], out["minutes"])
    out["fg_pct"] = safe_divide(out["fgm"], out["fga"])
    out["ft_pct"] = safe_divide(out["free_throws_made"], out["free_throws_attempted"])
    out["ts_pct"] = safe_divide(out["points"], 2 * (out["fga"] + FT_ATTEMPT_WEIGHT * out["free_throws_attempted"]))
    for prefix, (sign, stat) in CONTRIBUTION_STATS.items():
        out[f"{prefix}_share_of_pir"] = safe_divide(sign * out[stat], out["pir"])
    out[DERIVED_COLUMNS] = out[DERIVED_COLUMNS].where(out["played"])
    return out


def add_game_number(df: pd.DataFrame) -> pd.Series:
    """Per-player 1-based chronological game counter over games played (blank on DNP rows).

    Order is game date, then time, then game_id (a deterministic tie-break for games
    that start at the same time).
    """
    played = df[df["played"]].sort_values(["date", "time", "game_id"])
    numbers = played.groupby("player_id").cumcount() + 1
    return numbers.reindex(df.index).astype("Int64")


def check_pir_matches_valuation(df: pd.DataFrame) -> int:
    """Raise if computed PIR differs from the official `valuation` on any played row.

    Returns:
        The number of played rows checked (all matched).
    """
    played = df[df["played"]]
    bad = played[played["pir"] != played["valuation"]]
    if len(bad):
        sample = bad[["game_id", "player", "pir", "valuation"]].head(10).to_string(index=False)
        raise ValueError(f"Computed PIR differs from official valuation on {len(bad)} played rows, e.g.:\n{sample}")
    return len(played)


def check_contributions_sum_to_pir(df: pd.DataFrame) -> int:
    """Raise if the 11 signed PIR components do not add up to `pir` on any played row.

    This is what makes the per-row `{prefix}_share_of_pir` values sum to exactly 1. `pir` and the
    components are defined independently (`add_row_metrics`), so the check also catches one being
    edited without the other.

    Returns:
        The number of played rows checked (all matched).
    """
    played = df[df["played"]]
    components = sum(sign * played[stat] for sign, stat in CONTRIBUTION_STATS.values())
    bad = played[components != played["pir"]]
    if len(bad):
        sample = bad[["game_id", "player", "pir"]].head(10).to_string(index=False)
        raise ValueError(f"Signed PIR components do not sum to pir on {len(bad)} played rows, e.g.:\n{sample}")
    return len(played)


def build_game_stats(
    box: pd.DataFrame, header: pd.DataFrame, season: str, phases: list[str]
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Build the game-level dataset for one season and set of phases.

    Args:
        box: Raw box score rows (all seasons, including team-total rows).
        header: Raw header rows, one per game.
        season: Season code such as "E2025".
        phases: Phases to keep, e.g. ["REGULAR SEASON", "PLAYOFFS"].

    Returns:
        The `Game Stats` frame in `GAME_COLUMNS` order, and row-count statistics.

    Raises:
        ValueError: If the filters leave no rows, a game does not have exactly two team-total
            rows, header data is missing for a game, (game_id, player_id) repeats, minutes
            cannot be parsed, computed PIR differs from the official valuation, or the signed
            PIR components do not sum to PIR.
    """
    stats = {"box_rows": len(box)}
    box = box[box["season_code"] == season]
    stats["rows_in_season"] = len(box)
    if box.empty:
        raise ValueError(f"No box score rows for season {season!r}")

    is_total = box["dorsal"] == TEAM_TOTAL_DORSAL
    totals_per_game = box[is_total].groupby("game_id").size().reindex(box["game_id"].unique(), fill_value=0)
    wrong = totals_per_game[totals_per_game != TEAM_TOTAL_ROWS_PER_GAME]
    if not wrong.empty:
        raise ValueError(f"Games without exactly {TEAM_TOTAL_ROWS_PER_GAME} team-total rows: {wrong.to_dict()}")
    stats["team_total_rows_dropped"] = int(is_total.sum())
    box = box[~is_total]
    stats["player_rows_in_season"] = len(box)

    box = box[box["phase"].isin(phases)]
    stats["player_rows_after_phase_filter"] = len(box)
    if box.empty:
        raise ValueError(f"No player rows for season {season!r} and phases {phases}")

    if box.duplicated(["game_id", "player_id"]).any():
        raise ValueError("Duplicate (game_id, player_id) rows in the box score")

    games = header[header["game_id"].isin(box["game_id"])]
    if games["game_id"].duplicated().any() or set(box["game_id"]) - set(games["game_id"]):
        raise ValueError("Header must have exactly one row for every game of the box score")
    games = games[["game_id", "date", "time", "team_id_a", "team_id_b", "team_a", "team_b"]]
    df = box.drop(columns=["game_player_id", "dorsal"]).merge(games, on="game_id", validate="many_to_one")
    is_a = df["team_id"] == df["team_id_a"]
    if not (is_a | (df["team_id"] == df["team_id_b"])).all():
        raise ValueError("Box score team_id matches neither team of the header row")
    df["team_name"] = df["team_a"].where(is_a, df["team_b"])
    df["opponent_id"] = df["team_id_b"].where(is_a, df["team_id_a"])
    df["opponent_name"] = df["team_b"].where(is_a, df["team_a"])
    df["home_away"] = np.where(is_a, "home", "away")

    df["minutes"] = parse_minutes(df["minutes"])
    df["played"] = df["minutes"] > 0
    df["is_starter"] = df["is_starter"].astype(int)
    df = add_row_metrics(df)
    df["game_number"] = add_game_number(df)
    stats["games"] = df["game_id"].nunique()
    stats["players"] = df["player_id"].nunique()
    stats["rows_played"] = int(df["played"].sum())
    stats["rows_dnp"] = int((~df["played"]).sum())
    stats["pir_rows_checked_equal_to_valuation"] = check_pir_matches_valuation(df)
    stats["pir_rows_checked_component_sum"] = check_contributions_sum_to_pir(df)

    df = df.sort_values(["date", "time", "game_id", "team_id", "player_id"]).reset_index(drop=True)
    return df[GAME_COLUMNS], stats


def build_column_guide() -> pd.DataFrame:
    """One row per `Game Stats` column: (Source dataset, Column name, Explanation)."""
    rows = [(GAME_STATS_GUIDE[column][0], column, GAME_STATS_GUIDE[column][1]) for column in GAME_COLUMNS]
    return pd.DataFrame(rows, columns=["Source dataset", "Column name", "Explanation"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", default=DEFAULT_SEASON, help="Kaggle season_code (default: %(default)s)")
    parser.add_argument("--phases", nargs="+", default=DEFAULT_PHASES, help="Phases to keep (default: all four)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output xlsx path")
    args = parser.parse_args()

    for path in (BOX_SCORE_PATH, HEADER_PATH):
        if not path.exists():
            raise SystemExit(
                f"Missing {path.relative_to(REPO_ROOT)}. The Kaggle folder is git-ignored: download it with "
                "`kaggle datasets download babissamothrakis/euroleague-datasets` "
                "and unzip it into data/kaggle-euroleague-data/."
            )
    box = pd.read_csv(BOX_SCORE_PATH, dtype={"minutes": str})
    header = pd.read_csv(HEADER_PATH)
    game_stats, stats = build_game_stats(box, header, args.season, args.phases)
    write_workbook(args.out, {"Column Guide": build_column_guide(), "Game Stats": game_stats})
    print(f"Wrote {len(game_stats)} rows x {len(game_stats.columns)} columns to {args.out}")
    print("Row counts:", ", ".join(f"{k}={v}" for k, v in stats.items()))


if __name__ == "__main__":
    main()
