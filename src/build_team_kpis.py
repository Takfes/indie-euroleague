#!/usr/bin/env python3
"""Build the team-level derived KPI table (one row per EuroLeague team) from two raw sources.

Reads the same two raw CSVs `src/build_team_master_table.py` reads directly (no new data):
  - data/basketnews-team-stats/basketnews_team_stats.csv (Basketnews team stats)
  - data/dunkest-defense-positions/dunkest_defense_vs_position.csv (Dunkest defense vs position)
Teams are matched one-to-one with `match_teams()` from `build_team_master_table.py` (same
crosswalk and error behaviour; team matching is not reimplemented here).
The `funnel_actual_pir_*` columns additionally read two curated player-side workbooks
(build them first: player-game-stats skill, then the player master table):
  - data/curated/player_game_stats.xlsx (`Game Stats` sheet: one row per player per game, with PIR)
  - data/curated/player_master_table.xlsx (`Master` sheet: `kag_player_id` -> `position` G/F/C)

Output: data/curated/team_kpis.xlsx with sheets, in order:
  - Column Guide: one row per column of Team KPIs (Source dataset, Column name, Explanation);
    texts live in src/team_master_column_guide.py
  - Team KPIs: one row per team (20 teams), columns in `KPI_COLUMNS` order

Definitions (pure arithmetic on existing team-level columns, no new raw data):
  - `pace_factor`: `offense_all_possessions` (already a per-game pace figure) divided by the
    20-team league average of that column. ~1.0 for a league-average-pace team.
  - `foul_rate_per40_drawn` / `foul_rate_per40_committed`: `offense_all_fouls_received` /
    `defense_all_fouls` expressed per 40 minutes of team play. A EuroLeague game is 40
    regulation minutes (4 x 10-minute quarters) and these raw columns are already per-game
    averages; per-team overtime minutes are not available anywhere in the raw data, so under
    the documented assumption that every game is exactly 40 minutes (overtime not modeled), a
    per-game rate already equals a per-40-minutes rate. These columns restate that per-game
    rate under the explicit assumption, rather than leaving the unit implicit.
  - `funnel_ratio_{guards,forwards,centers}`: `{position}_fantasy_points` conceded per game
    (Dunkest defense vs position) divided by the 20-team league average for that position.
    ~1.0 for a team that concedes a league-average number of fantasy points to that position.
  - `funnel_actual_pir_{guards,forwards,centers}`: the same shape computed from real box-score
    PIR instead of Dunkest's proprietary fantasy points. For each team-as-defender T and each
    of T's games (every game in `Game Stats`, regular season and postseason), sum the `pir` of
    the opposing players who played and whose master-table position is G (F, C); average that
    per-game sum over all of T's games (a game with no such opposing player counts as 0) and
    divide by the 20-team league average of that figure. ~1.0 = league-average PIR conceded.
    The opponent's identity is the Kaggle `opponent_id` code, mapped through
    `KAGGLE_TEAM_CROSSWALK` and `resolve_team_name()` (both from `build_player_master_table.py`)
    onto the 20 canonical team names.
:
    python src/build_team_kpis.py [--out PATH]

Re-run any time the two source CSVs are refreshed.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from build_player_master_table import map_kaggle_teams, resolve_team_name
from build_team_master_table import BN_ID, BN_TEAM, DUNKEST_DVP, DVP_ID, SOURCE_PATHS, match_teams
from master_workbook import write_workbook
from team_master_column_guide import DVP_POSITIONS, team_kpis_guide

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "data/curated/team_kpis.xlsx"

GAME_STATS_PATH = REPO_ROOT / "data/curated/player_game_stats.xlsx"
PLAYER_MASTER_PATH = REPO_ROOT / "data/curated/player_master_table.xlsx"
GAME_STATS = "Game Stats"
PLAYER_MASTER = "Master"
# DVP position name -> the G/F/C bucket in the player master table's `position` column.
POSITION_BUCKET = {"guards": "G", "forwards": "F", "centers": "C"}

GUIDE = team_kpis_guide()
KPI_COLUMNS = list(GUIDE)
BN_COLUMNS = ["team_name", "offense_all_possessions", "offense_all_fouls_received", "defense_all_fouls"]
DVP_COLUMNS = [f"{position}_fantasy_points" for position in DVP_POSITIONS]


def read_sources() -> dict[str, pd.DataFrame]:
    """Read the two raw source CSVs and the two curated player workbooks, keyed by source label.

    Raises:
        FileNotFoundError: If a curated player workbook does not exist yet; the message names
            the skill that builds it.
    """
    for path, skill in ((GAME_STATS_PATH, "player-game-stats"), (PLAYER_MASTER_PATH, "player-master-table")):
        if not path.exists():
            raise FileNotFoundError(
                f"Missing {path.relative_to(REPO_ROOT)}, which the funnel_actual_pir_* KPIs need. "
                f"Build it first (see the {skill} skill)."
            )
    sources = {label: pd.read_csv(SOURCE_PATHS[label]) for label in (BN_TEAM, DUNKEST_DVP)}
    sources[GAME_STATS] = pd.read_excel(GAME_STATS_PATH, sheet_name=GAME_STATS)
    sources[PLAYER_MASTER] = pd.read_excel(PLAYER_MASTER_PATH, sheet_name=PLAYER_MASTER)
    return sources


def funnel_actual_pir(
    game_stats: pd.DataFrame, player_master: pd.DataFrame, canonical_teams: list[str]
) -> pd.DataFrame:
    """Compute the real-PIR positional funnel ratio per defending team.

    For each defending team T and each of T's games, sums the `pir` of the opposing players
    who played and whose position (via `kag_player_id`) is G, F or C; averages that per-game
    sum over all of T's games (0 for a game with no such opposing player) and divides by the
    league average of the per-team averages.

    Args:
        game_stats: `Game Stats` frame with `game_id`, `player_id`, `opponent_id`, `played`, `pir`.
        player_master: Player `Master` frame with `kag_player_id` and `position`.
        canonical_teams: The 20 canonical team names to resolve Kaggle opponents onto.

    Returns:
        One row per team: `team_name` and `funnel_actual_pir_{guards,forwards,centers}`.

    Raises:
        ValueError: If a Kaggle opponent code is unmapped/unresolved, or a canonical team has
            no games.
    """
    codes = pd.Series(sorted(game_stats["opponent_id"].unique()))
    team_by_code = {
        code: resolve_team_name(name, canonical_teams)
        for code, name in zip(codes, map_kaggle_teams(codes), strict=True)
    }
    if len(set(team_by_code.values()) - {None}) != len(canonical_teams) or None in team_by_code.values():
        raise ValueError(f"Kaggle opponent codes do not resolve one-to-one onto the canonical teams: {team_by_code}")

    position = player_master.dropna(subset=["kag_player_id"]).set_index("kag_player_id")["position"]
    rows = game_stats.assign(
        defender=game_stats["opponent_id"].map(team_by_code), position=game_stats["player_id"].map(position)
    )
    games = rows.groupby("defender")["game_id"].nunique()
    played = rows[rows["played"]]

    table = pd.DataFrame({"team_name": sorted(canonical_teams)}).set_index("team_name")
    for name, bucket in POSITION_BUCKET.items():
        per_game = played[played["position"] == bucket].groupby("defender")["pir"].sum()
        conceded = per_game.reindex(games.index, fill_value=0.0) / games
        table[f"funnel_actual_pir_{name}"] = conceded / conceded.mean()
    return table.reset_index()


def build_team_kpis(sources: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Compute the nine derived team KPIs, one row per team.

    Args:
        sources: Source frames keyed by sheet name (see `read_sources`).

    Returns:
        One row per team in `KPI_COLUMNS` order, sorted by `team_name`.
    """
    bn = sources[BN_TEAM]
    dunkest = sources[DUNKEST_DVP]
    ids = match_teams(bn, dunkest)

    bn_part = bn.rename(columns={"team_id": BN_ID})[[BN_ID, *BN_COLUMNS]]
    dunkest_part = dunkest.rename(columns={"team_id": DVP_ID})[[DVP_ID, *DVP_COLUMNS]]
    table = ids.merge(bn_part, on=BN_ID, validate="one_to_one").merge(dunkest_part, on=DVP_ID, validate="one_to_one")

    table["pace_factor"] = table["offense_all_possessions"] / table["offense_all_possessions"].mean()
    table["foul_rate_per40_drawn"] = table["offense_all_fouls_received"]
    table["foul_rate_per40_committed"] = table["defense_all_fouls"]
    for position in DVP_POSITIONS:
        column = f"{position}_fantasy_points"
        table[f"funnel_ratio_{position}"] = table[column] / table[column].mean()

    table = table.merge(
        funnel_actual_pir(sources[GAME_STATS], sources[PLAYER_MASTER], table["team_name"].tolist()),
        on="team_name",
        validate="one_to_one",
    )
    table = table[KPI_COLUMNS].sort_values("team_name", key=lambda names: names.str.lower()).reset_index(drop=True)
    return table


def build_column_guide() -> pd.DataFrame:
    """One row per `Team KPIs` column: (Source dataset, Column name, Explanation)."""
    rows = [(source, column, text) for column, (source, text) in GUIDE.items()]
    return pd.DataFrame(rows, columns=["Source dataset", "Column name", "Explanation"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output xlsx path")
    args = parser.parse_args()

    table = build_team_kpis(read_sources())
    write_workbook(args.out, {"Column Guide": build_column_guide(), "Team KPIs": table})
    print(f"Wrote {len(table)} teams x {len(table.columns)} columns to {args.out}")


if __name__ == "__main__":
    main()
