#!/usr/bin/env python3
"""Build the team-level derived KPI table (one row per EuroLeague team) from two raw sources.

Reads the same two raw CSVs `src/build_team_master_table.py` reads directly (no new data):
  - data/basketnews-team-stats/basketnews_team_stats.csv (Basketnews team stats)
  - data/dunkest-defense-positions/dunkest_defense_vs_position.csv (Dunkest defense vs position)
Teams are matched one-to-one with `match_teams()` from `build_team_master_table.py` (same
crosswalk and error behaviour; team matching is not reimplemented here).

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

Usage:
    python src/build_team_kpis.py [--out PATH]

Re-run any time the two source CSVs are refreshed.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from build_team_master_table import BN_ID, BN_TEAM, DUNKEST_DVP, DVP_ID, SOURCE_PATHS, match_teams
from master_workbook import write_workbook
from team_master_column_guide import DVP_POSITIONS, team_kpis_guide

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "data/curated/team_kpis.xlsx"

GUIDE = team_kpis_guide()
KPI_COLUMNS = list(GUIDE)
BN_COLUMNS = ["team_name", "offense_all_possessions", "offense_all_fouls_received", "defense_all_fouls"]
DVP_COLUMNS = [f"{position}_fantasy_points" for position in DVP_POSITIONS]


def read_sources() -> dict[str, pd.DataFrame]:
    """Read the two raw source CSVs `build_team_master_table.py` also reads, keyed by source label."""
    return {label: pd.read_csv(SOURCE_PATHS[label]) for label in (BN_TEAM, DUNKEST_DVP)}


def build_team_kpis(sources: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Compute the six derived team KPIs, one row per team.

    Args:
        sources: Raw source frames keyed by sheet name (see `read_sources`).

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
