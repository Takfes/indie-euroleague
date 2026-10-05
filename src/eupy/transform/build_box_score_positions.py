#!/usr/bin/env python3
"""Add playing positions, minutes, opponent and home/away to the merged box score, for the players in the fantasy stats.

The box score is transactional (one row per player per game); the fantasy stats are the trusted list of
relevant players and carry each player's position. Steps, in order:

1. `year` (from `season_code`, e.g. `E2025` -> 2025) is inserted after `season_code`. Rows before
   `--from-year` (default 2025) and the team-total rows (`dorsal == "TOTAL"`) are dropped.
2. `minutes_num` (`"15:19"` -> 15.32, `"DNP"` -> 0.0) is inserted after `minutes`.
3. `player_name` is the normalized full name of the box-score `player` (`PlayerNames.normalize`, scoped by
   `team_id`). A player the map does not know is outside the stats and is left unnamed.
4. Inner join with the distinct `(player_name, position)` pairs of the fantasy stats: only box-score rows of
   players that appear in the stats stay, and each gains `position` as `G` / `F` / `C`. Two positions for one
   player in the stats raise.
5. `opponent` and `home_away` are read from `game` (`"RED-ZAL"`, home team first): for `team_id` `RED` the
   opponent is `ZAL` and `home_away` is `Home`; for `ZAL` it is `RED` and `Away`.

Usage:
    python -m eupy.transform.build_box_score_positions [--from-year 2025] [--box-score PATH] [--players PATH] [--out PATH]

Inputs:
  - euroleague_box_score_merged: data/stage_01/euroleague_box_score_merged.csv
  - fantasy_stats/players: data/raw_data/fantasy_stats/players.csv
  - player_name_map: data/stage_02/player_name_map.csv
  - team_name_map: data/stage_01/team_name_map.csv
Sources: none
Outputs:
  - euroleague_box_score_positions: data/stage_03/euroleague_box_score_positions.csv
Final: false
Impure: false
Notes: Overwritten each run. Box-score columns keep their order; year, minutes_num, player_name, position, opponent
  and home_away are added. Names are looked up through PlayerNames, which reads the JSON copies of the two
  maps in src/eupy/entity/maps/ (written together with the CSVs listed as inputs).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from eupy.entity.matching import UnknownNameError
from eupy.entity.player_names import PlayerNames
from eupy.quality import validate

REPO_ROOT = Path(__file__).resolve().parents[3]
BOX_SCORE_PATH = REPO_ROOT / "data" / "stage_01" / "euroleague_box_score_merged.csv"
PLAYERS_PATH = REPO_ROOT / "data" / "raw_data" / "fantasy_stats" / "players.csv"
OUT_PATH = REPO_ROOT / "data" / "stage_03" / "euroleague_box_score_positions.csv"

FROM_YEAR = 2025
POSITION_ROLES = {"Guard": "G", "Forward": "F", "Center": "C"}


def add_year_and_filter(box_score: pd.DataFrame, from_year: int) -> pd.DataFrame:
    """Insert `year` after `season_code`; keep rows from `from_year` on, without the `TOTAL` team rows."""
    df = box_score.copy()
    df.insert(df.columns.get_loc("season_code") + 1, "year", df["season_code"].str.replace("E", "").astype(int))
    return df[(df["year"] >= from_year) & (df["dorsal"] != "TOTAL")].reset_index(drop=True)


def add_minutes_num(box_score: pd.DataFrame) -> pd.DataFrame:
    """Insert `minutes_num` (decimal minutes, `DNP` = 0) after `minutes`."""
    df = box_score.copy()
    parts = df["minutes"].replace("DNP", "0:00").str.split(":", expand=True).astype(int)
    df.insert(df.columns.get_loc("minutes") + 1, "minutes_num", parts[0] + parts[1] / 60)
    return df


def add_player_name(box_score: pd.DataFrame, players: PlayerNames) -> pd.DataFrame:
    """Add `player_name`, the normalized full name; unknown players get an empty value.

    Raises:
        AmbiguousNameError: If a spelling is shared by players that `team_id` does not tell apart.
    """

    def lookup(player: str, team: str) -> str | None:
        try:
            return players.normalize(player, team=team)
        except UnknownNameError:
            return None

    names = [lookup(p, t) for p, t in zip(box_score["player"], box_score["team_id"], strict=True)]
    return box_score.assign(player_name=names)


def stats_positions(stats: pd.DataFrame, players: PlayerNames) -> pd.DataFrame:
    """Distinct `(player_name, position)` of the stats, positions as G / F / C.

    Raises:
        ValueError: If a position is not Guard / Forward / Center, or a player has two positions.
    """
    unknown = sorted(set(stats["position"]) - set(POSITION_ROLES))
    if unknown:
        raise ValueError(f"Unexpected positions in the fantasy stats: {unknown}")
    named = pd.DataFrame({
        "player_name": [players.normalize(n, team=t) for n, t in zip(stats["name"], stats["team"], strict=True)],
        "position": stats["position"].map(POSITION_ROLES),
    }).drop_duplicates()
    validate(named.to_dict("records"), "player_name", "box_score_positions: stats positions")
    return named


def add_opponent_home_away(box_score: pd.DataFrame) -> pd.DataFrame:
    """Add `opponent` and `home_away` from `game` (`HOME-AWAY`) and the row's `team_id`.

    Raises:
        ValueError: If `game` is not two codes or `team_id` is neither of them.
    """
    sides = box_score["game"].str.split("-", expand=True)
    if sides.shape[1] != 2 or sides.isna().any().any():
        raise ValueError("`game` must look like 'HOME-AWAY' on every row")
    home, away = sides[0], sides[1]
    is_home = box_score["team_id"] == home
    is_away = box_score["team_id"] == away
    if not (is_home | is_away).all():
        bad = box_score.loc[~(is_home | is_away), ["game", "team_id"]].drop_duplicates()
        raise ValueError(f"team_id is neither side of game: {bad.values.tolist()}")
    return box_score.assign(
        opponent=away.where(is_home, home),
        home_away=pd.Series("Away", index=box_score.index).mask(is_home, "Home"),
    )


def build(box_score: pd.DataFrame, stats: pd.DataFrame, players: PlayerNames, from_year: int) -> pd.DataFrame:
    """Run the steps in order and return the enriched box score."""
    df = add_year_and_filter(box_score, from_year)
    df = add_minutes_num(df)
    df = add_player_name(df, players)
    df = df.merge(stats_positions(stats, players), on="player_name", how="inner")
    df = add_opponent_home_away(df)
    return df.sort_values("game_player_id", kind="stable").reset_index(drop=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument(
        "--from-year", type=int, default=FROM_YEAR, help="First season year kept (default: %(default)s)"
    )
    parser.add_argument("--box-score", type=Path, default=BOX_SCORE_PATH)
    parser.add_argument("--players", type=Path, default=PLAYERS_PATH, help="fantasy_stats/players.csv path")
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    box_score = pd.read_csv(args.box_score, dtype=str, keep_default_na=False)
    stats = pd.read_csv(args.players, dtype=str, keep_default_na=False)
    out = build(box_score, stats, PlayerNames.load(), args.from_year)
    validate(out.to_dict("records"), "game_player_id", "euroleague_box_score_positions")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(args.out, index=False)
    print(f"Wrote {len(out)} rows to {args.out}")


if __name__ == "__main__":
    main()
