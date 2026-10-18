#!/usr/bin/env python3
"""Build the `eubook` Excel workbook: box score, player KPIs and the next round's schedule, in one file.

The pipeline version of the analysis in `apps/dataprep.py` (same sheets, same KPIs). Steps, in order:

1. Box score: rows of the config season on, without the `TOTAL` team rows; adds `year`, `minutes_num` (`DNP` = 0),
   `opponent` and `homeaway` (read from `game`, home team first) and `player_normalized` (`PlayerNames`, scoped
   by `team_id`). Rows whose player the name map does not know are dropped and counted (box-score-only players
   are expected; `build_player_name_map` lists them when it audits the map).
2. Stats join: `position` (`G`/`F`/`C`) and `quotation` (price) from the fantasy stats players. With per-round
   stats the join key is `(round, player)`; with the season-wide stats (`round` = "all") it is the player alone,
   so `quotation` is the current price in every row. Rows without a match are dropped and counted.
3. Player KPIs per `(player, team, position)`: PIR (`valuation`) mean, last, lags, std, slow/fast windows,
   percentiles, minutes statistics, PIR per credit, break-even and trends.
4. Team grades: mean PIR per game by position, conceded (by opponent) and collected (by team), normalized by the
   league mean per position and ranked.
5. Next round (the box score's last `round` + 1): the schedule's games with a `turn` (latest date of the round = 2, earlier dates = 1), the teams' grades
   side by side, and the expected PIR of each player (`pir_mean` x the opponent's normalized PIR conceded).

Sheets: `schedule`, `teams`, `boxscore`, `hist_long_pir_per_pos`, `hist_wide_pir_per_pos`,
`next_round_team_pir_per_pos`, `player_analytical_record`.

Usage:
    python -m eupy.transform.build_eubook [--box-score PATH] [--players PATH] [--schedule PATH] [--out PATH]

Inputs:
  - euroleague_box_score_merged: data/stage_01/euroleague_box_score_merged.csv
  - fantasy_stats/players: data/raw_data/fantasy_stats/players.csv
  - game_schedule/schedule: data/raw_data/game_schedule/
  - player_name_map: data/stage_02/player_name_map.csv
Sources: none
Outputs:
  - eubook: data/stage_03/eubook.xlsx
Final: true
Impure: false
Notes: Overwritten each run. Reads schedule_<season>.csv for the config season.
  Names are looked up through PlayerNames (the player map CSV listed as input; the team lookup through TeamNames,
  from the schedule input). In `pause` mode the map stage fails first, so a flow stops before this step.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from eupy.config import get_config
from eupy.entity.matching import AmbiguousNameError, UnknownNameError
from eupy.entity.player_names import PlayerNames

REPO_ROOT = Path(__file__).resolve().parents[3]
BOX_SCORE_PATH = REPO_ROOT / "data" / "stage_01" / "euroleague_box_score_merged.csv"
PLAYERS_PATH = REPO_ROOT / "data" / "raw_data" / "fantasy_stats" / "players.csv"
SCHEDULE_DIR = REPO_ROOT / "data" / "raw_data" / "game_schedule"
OUT_PATH = REPO_ROOT / "data" / "stage_03" / "eubook.xlsx"

SLOW_WINDOW = 5
FAST_WINDOW = 3
POSITION_LETTERS = {"Guard": "G", "Forward": "F", "Center": "C"}

BOXSCORE_COLUMNS = [
    "season_code",
    "year",
    "game_player_id",
    "game_id",
    "player_id",
    "phase",
    "round",
    "game",
    "team_id",
    "opponent",
    "homeaway",
    "position",
    "dorsal",
    "player",
    "player_normalized",
    "quotation",
    "valuation",
    "is_starter",
    "is_playing",
    "minutes",
    "minutes_num",
    "points",
    "two_points_made",
    "two_points_attempted",
    "three_points_made",
    "three_points_attempted",
    "free_throws_made",
    "free_throws_attempted",
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
    "plus_minus",
]
ANALYTICAL_COLUMNS = [
    "player",
    "team",
    "position",
    "turn",
    "venue",
    "opponent",
    "crd_last",
    "normd_pstn_pir",
    "exp_pir",
    "pir_mean",
    "pir_per_crd",
    "pir_breakeven",
    "pir_per_min",
    "pir_last",
    "pir_lag1",
    "pir_lag2",
    "pir_std",
    "pir_p75",
    "pir_p90",
    "pir_slow",
    "pir_fast",
    "pir_trend",
    "mins_mean",
    "mins_std",
    "mins_slow",
    "mins_fast",
    "mins_trend",
]
SCHEDULE_COLUMNS = ["hometeam", "homecode", "awayteam", "awaycode", "turn", "date", "gameday"]


def safe_normalize(players: PlayerNames, name: str, team: str | None) -> str | None:
    """Normalized full name, or `None` when the map does not know the spelling or it is ambiguous."""
    try:
        return players.normalize(name, team=team)
    except (UnknownNameError, AmbiguousNameError):
        return None


def insert_after(df: pd.DataFrame, after: str, name: str, values: object) -> None:
    """Insert column `name` right after column `after`."""
    df.insert(df.columns.get_loc(after) + 1, name, values)


def prepare_boxscore(box: pd.DataFrame, year: int, players: PlayerNames) -> pd.DataFrame:
    """Box-score rows from `year` on without team totals, plus year, minutes, opponent, venue and normalized player."""
    df = box.copy()
    insert_after(df, "season_code", "year", df["season_code"].str.replace("E", "").astype(int))
    df = df[(df["year"] >= year) & (df["dorsal"] != "TOTAL")].reset_index(drop=True).copy()

    df["minutes"] = df["minutes"].replace("DNP", "0:00")
    insert_after(df, "minutes", "minutes_num", df["minutes"].str.split(":").apply(lambda x: int(x[0]) + int(x[1]) / 60))

    home = df["game"].str.split("-").str[0]
    away = df["game"].str.split("-").str[1]
    is_home = df["team_id"] == home
    insert_after(df, "team_id", "homeaway", is_home.map({True: "home", False: "away"}))
    insert_after(df, "team_id", "opponent", away.where(is_home, home))

    normalized = [safe_normalize(players, p, t) for p, t in zip(df["player"], df["team_id"], strict=True)]
    insert_after(df, "player", "player_normalized", normalized)
    missing = df["player_normalized"].isna()
    if missing.any():
        print(f"dropped {missing.sum()} box-score rows of {df.loc[missing, 'player'].nunique()} players not in the map")
    return df.dropna(subset=["player_normalized"]).reset_index(drop=True)


def join_stats(df: pd.DataFrame, stats: pd.DataFrame, players: PlayerNames) -> pd.DataFrame:
    """Add `position` and `quotation` from the fantasy stats (per round, or per player for season-wide stats)."""
    st = stats.copy()
    st["player_normalized"] = [safe_normalize(players, n, t) for n, t in zip(st["name"], st["team"], strict=True)]
    st["position"] = st["position"].map(POSITION_LETTERS)
    columns = ["player_normalized", "position", "quotation", "round"]
    if st["round"].astype(str).eq("all").all():  # season-wide stats: one row per player, no round to join on
        pls = st[columns].drop(columns="round").drop_duplicates("player_normalized")
        joined = df.merge(pls, on="player_normalized", how="left")
    else:
        joined = df.merge(
            st[columns].astype({"round": df["round"].dtype}), on=["round", "player_normalized"], how="left"
        )
    unmatched = joined["quotation"].isna() | joined["position"].isna()
    if unmatched.any():
        print(f"dropped {unmatched.sum()} box-score rows without stats (position, quotation)")
    return joined.dropna(subset=["quotation", "position"]).reset_index(drop=True)


def player_kpis(df: pd.DataFrame) -> pd.DataFrame:
    """One row per `(player, team, position)` with the PIR and minutes statistics."""
    return (
        df.sort_values("round", ascending=True)
        .groupby(["player_normalized", "team_id", "position"], as_index=False)
        .agg(
            crd_last=("quotation", "last"),
            pir_mean=("valuation", "mean"),
            pir_last=("valuation", "last"),
            pir_lag1=("valuation", lambda x: x.shift(1).iloc[-1]),
            pir_lag2=("valuation", lambda x: x.shift(2).iloc[-1]),
            pir_std=("valuation", "std"),
            pir_p75=("valuation", lambda x: x.head(SLOW_WINDOW).quantile(0.75)),
            pir_p90=("valuation", lambda x: x.head(SLOW_WINDOW).quantile(0.90)),
            pir_slow=("valuation", lambda x: x.head(SLOW_WINDOW).mean()),
            pir_fast=("valuation", lambda x: x.head(FAST_WINDOW).mean()),
            mins_mean=("minutes_num", "mean"),
            mins_std=("minutes_num", "std"),
            mins_slow=("minutes_num", lambda x: x.head(SLOW_WINDOW).mean()),
            mins_fast=("minutes_num", lambda x: x.head(FAST_WINDOW).mean()),
        )
        .assign(
            pir_per_crd=lambda x: x.pir_mean / x.crd_last,
            pir_breakeven=lambda x: (x.pir_mean - x.crd_last * 1.1) / 25,
            pir_per_min=lambda x: x.pir_mean / x.mins_mean,
            pir_trend=lambda x: x.pir_fast / x.pir_slow,
            mins_trend=lambda x: x.mins_fast / x.mins_slow,
        )
        .reset_index(drop=True)
    )


def _grade_pivot(grades: pd.DataFrame, index: str, suffix: str) -> pd.DataFrame:
    """Rank grades pivoted to one row per team and one `<position>_<suffix>` column per position."""
    return (
        grades.pivot(index=index, columns="position", values="grade")
        .fillna(0)
        .round(2)
        .sort_index()
        .sort_index(axis=1)
        .rename(columns=lambda x: x + suffix)
        .reset_index()
        .rename_axis(columns=None)
        .rename(columns={index: "team"})
    )


def _normalize_by_position(grades: pd.DataFrame, ascending: bool) -> pd.DataFrame:
    """Add the league mean, the value relative to it and its rank within each position."""
    grades["league_average"] = grades.groupby("position")["valuation"].transform("mean")
    grades["normd_pstn_pir"] = grades["valuation"] / grades["league_average"]
    grades["grade"] = grades.groupby("position")["normd_pstn_pir"].rank(ascending=ascending, method="min")
    return grades


def team_grades(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """PIR per game by position: per game-team (`g1`), conceded by opponent (`g2`), collected by team (`g3`) and the
    two grade pivots (`g2b`, `g3b`)."""
    g1 = df.groupby(["game_id", "team_id", "opponent", "position"]).agg({"valuation": "mean"}).reset_index()
    g2 = _normalize_by_position(g1.groupby(["opponent", "position"], as_index=False).agg({"valuation": "mean"}), False)
    g3 = _normalize_by_position(g1.groupby(["team_id", "position"], as_index=False).agg({"valuation": "mean"}), True)
    return {
        "g1": g1,
        "g2": g2,
        "g3": g3,
        "g2b": _grade_pivot(g2, "opponent", "_conceded_pir"),
        "g3b": _grade_pivot(g3, "team_id", "_collected_pir"),
    }


def next_round_games(schedule: pd.DataFrame, next_round: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The next round's games with a `turn`, and the same games as one row per team (`venue` home/away).

    The round's latest date is turn 2 and every earlier date turn 1 (a round on a single date is all turn 1). Dates
    are parsed (`Sep 24, 2026`) before comparing, so a round spanning two months still orders correctly.
    """
    games = schedule.query("gameday==@next_round")[["gameday", "date", "hometeam", "homecode", "awayteam", "awaycode"]]
    dates = pd.to_datetime(games["date"], format="%b %d, %Y")
    games = games.assign(turn=((dates == dates.max()) & (dates != dates.min())).astype(int) + 1).sort_values([
        "turn",
        "gameday",
    ])
    home = (
        games[["homecode", "awaycode", "turn"]]
        .rename(columns={"homecode": "team", "awaycode": "opponent"})
        .assign(venue="home")
    )
    away = (
        games[["awaycode", "homecode", "turn"]]
        .rename(columns={"awaycode": "team", "homecode": "opponent"})
        .assign(venue="away")
    )
    return games, pd.concat([home, away], ignore_index=True)


def build_sheets(box: pd.DataFrame, stats: pd.DataFrame, schedule: pd.DataFrame, year: int, players: PlayerNames):
    """All worksheets of the workbook, keyed by sheet name, and the next round number."""
    next_round = int(box["round"].max()) + 1
    df = join_stats(prepare_boxscore(box, year, players), stats, players)
    kpis = player_kpis(df)
    grades = team_grades(df)
    g1, g2, g3 = grades["g1"], grades["g2"], grades["g3"]
    games, all_games = next_round_games(schedule, next_round)

    teams = (
        schedule[["hometeam", "homecode", "hometv"]]
        .drop_duplicates()
        .rename(columns=lambda x: x.replace("home", ""))
        .sort_values(by="team")
    )
    long = g1.assign(
        game_id_num=lambda x: x.game_id.str.split("_").str[-1].astype(str),
        game_no=lambda x: x.groupby(["team_id", "position"])["game_id_num"]
        .rank(method="min")
        .astype(int)
        .astype(str)
        .str.zfill(3),
        game_no_opponent=lambda x: x.game_no + "_" + x.opponent,
    )
    conceded = g2.rename(
        columns={
            "opponent": "team",
            "valuation": "pir_conceded_per_position",
            "league_average": "pir_conceded_per_position_league_average",
            "normd_pstn_pir": "normalized_pir_conceded_per_position_league_average",
            "grade": "grade_pir_conceded_per_position",
        }
    )
    collected = g3.rename(
        columns={
            "team_id": "team",
            "valuation": "pir_collected_per_position",
            "league_average": "pir_collected_per_position_league_average",
            "normd_pstn_pir": "normalized_pir_collected_per_position_league_average",
            "grade": "grade_pir_collected_per_position",
        }
    )
    g2b, g3b = grades["g2b"], grades["g3b"]
    schedule_grades = (
        games[SCHEDULE_COLUMNS]
        .merge(g2b.rename(columns=lambda x: "home_" + x), how="left", left_on="homecode", right_on="home_team")
        .drop(columns=["home_team"])
        .merge(g2b.rename(columns=lambda x: "away_" + x), how="left", left_on="awaycode", right_on="away_team")
        .drop(columns=["away_team"])
        .merge(g3b.rename(columns=lambda x: "home_" + x), how="left", left_on="homecode", right_on="home_team")
        .drop(columns=["home_team"])
        .merge(g3b.rename(columns=lambda x: "away_" + x), how="left", left_on="awaycode", right_on="away_team")
        .drop(columns=["away_team"])
    )
    for a, b in (("collected", "conceded"), ("conceded", "collected")):
        for pos in ("C", "F", "G"):
            schedule_grades[f"{pos}_home_{a}_away_{b}"] = (
                schedule_grades[f"home_{pos}_{a}_pir"] - schedule_grades[f"away_{pos}_{b}_pir"]
            )
    record = (
        kpis.merge(all_games, left_on=["team_id"], right_on=["team"], how="left")
        .drop(columns=["team"])
        .merge(g2[["opponent", "position", "normd_pstn_pir"]], how="left", on=["opponent", "position"])
        .assign(exp_pir=lambda x: x.pir_mean * x.normd_pstn_pir)
        .rename(columns={"player_normalized": "player", "team_id": "team"})
    )[ANALYTICAL_COLUMNS]
    sheets = {
        "schedule": schedule,
        "teams": teams,
        "boxscore": df[BOXSCORE_COLUMNS].copy(),
        "hist_long_pir_per_pos": long,
        "hist_wide_pir_per_pos": conceded.merge(collected, how="left", on=["team", "position"]),
        "next_round_team_pir_per_pos": schedule_grades,
        "player_analytical_record": record,
    }
    return sheets, next_round


def write_workbook(sheets: dict[str, pd.DataFrame], path: Path) -> None:
    """Write the dataframes to an Excel workbook, one sheet per key."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path) as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, sheet_name=name, index=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--box-score", type=Path, default=BOX_SCORE_PATH, help="merged box-score CSV")
    parser.add_argument("--players", type=Path, default=PLAYERS_PATH, help="fantasy_stats/players.csv")
    parser.add_argument("--schedule", type=Path, default=None, help="schedule CSV (default: schedule_<season>.csv)")
    parser.add_argument("--out", type=Path, default=OUT_PATH, help="workbook path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    season = get_config().acquisition.season
    schedule_path = args.schedule or SCHEDULE_DIR / f"schedule_{season}.csv"
    sheets, next_round = build_sheets(
        pd.read_csv(args.box_score),
        pd.read_csv(args.players),
        pd.read_csv(schedule_path),
        int(season.removeprefix("E")),
        PlayerNames.load(),
    )
    write_workbook(sheets, args.out)
    print(f"Wrote {len(sheets)} sheets for round {next_round} to {args.out}")


if __name__ == "__main__":
    main()
