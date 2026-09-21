#!/usr/bin/env python3
"""Build the team master table workbook joining Basketnews team stats + Dunkest defense-vs-position.

Combines two EuroLeague datasets (one row per team, 20 teams) into one row per team:
  - data/basketnews-team-stats/basketnews_team_stats.csv (offense/defense KPIs, all/home/away)
  - data/dunkest-defense-positions/dunkest_defense_vs_position.csv (stats conceded to guards/forwards/centers)

Output: data/curated/team_master_table.xlsx with sheets, in order:
  - Column Guide: every column of both source datasets exactly once (Source dataset,
    Column name, Explanation); texts live in src/team_master_column_guide.py
  - Master: one row per team, sorted by team_name (layout below)
  - BN Team Stats, Dunkest Defense vs Position: the raw source datasets as-is

Matching strategy: the sources have different team ids and spell some names differently.
Teams are matched by normalised name (accents, casing, spacing ignored) after applying
`TEAM_NAME_CROSSWALK`, an explicit Dunkest-name -> Basketnews-name mapping for the
names that differ. The match must be strictly one-to-one; otherwise the build stops
and names the unmatched/duplicated teams on each side. There is no fuzzy matching.

Master column layout (every source column is prefixed, see `SOURCE_PREFIXES`; identity
columns that both sources repeat are kept once, unprefixed):
  1. identity: team_name, team_short_name, season (Basketnews), bnteam_team_id,
     dunkdvp_team_id, bnteam_games_played_all/home/away
  2. remaining Basketnews columns in source order: bnteam_league_id, then the blocks
     offense_all, offense_home, offense_away, defense_all, defense_home, defense_away
  3. Dunkest columns in source order: guards_*, forwards_*, centers_* (dunkdvp_*)
There are no found_in columns: every team is in both sources (the build refuses otherwise).

Usage:
    python src/build_team_master_table.py [--out PATH]

Re-run any time the two source CSVs are refreshed.
"""

from __future__ import annotations

import argparse
import unicodedata
from pathlib import Path

import pandas as pd

from master_workbook import write_workbook
from team_master_column_guide import BN_TEAM, DUNKEST_DVP, GUIDE, SOURCE_PREFIXES, UNDOCUMENTED

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATHS = {
    BN_TEAM: REPO_ROOT / "data/basketnews-team-stats/basketnews_team_stats.csv",
    DUNKEST_DVP: REPO_ROOT / "data/dunkest-defense-positions/dunkest_defense_vs_position.csv",
}
DEFAULT_OUT = REPO_ROOT / "data/curated/team_master_table.xlsx"

# Dunkest team name -> Basketnews team name, only where the two spell a team differently
# (sponsor changes). Add a line here when the build reports an unmatched team pair.
TEAM_NAME_CROSSWALK = {
    "Hapoel IBI Tel Aviv": "Hapoel Shlomo Tel Aviv",
    "Maccabi Rapyd Tel Aviv": "Maccabi Playtika Tel Aviv",
    "Baskonia Vitoria-Gasteiz": "Kosner Baskonia Vitoria-Gasteiz",
    "Virtus Bologna": "Virtus Segafredo Bologna",
}

BN_IDENTITY = ["team_name", "team_short_name", "season"]
BN_GAMES = ["games_played_all", "games_played_home", "games_played_away"]
BN_ID = "bnteam_team_id"
DVP_ID = "dunkdvp_team_id"
# Dunkest columns dropped from Master because the identity columns already carry them.
DVP_DUPLICATED = ["team_id", "team_name", "season"]


def read_sources() -> dict[str, pd.DataFrame]:
    """Read the two raw source CSVs, keyed by their workbook sheet name."""
    return {sheet: pd.read_csv(path) for sheet, path in SOURCE_PATHS.items()}


def team_key(name: str) -> str:
    """Normalise a team name for matching: accents stripped, lowercased, whitespace collapsed."""
    plain = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return " ".join(plain.casefold().split())


def match_teams(bn: pd.DataFrame, dunkest: pd.DataFrame) -> pd.DataFrame:
    """Match Basketnews teams to Dunkest teams strictly one-to-one.

    Args:
        bn: Basketnews team rows with `team_id` and `team_name`.
        dunkest: Dunkest team rows with `team_id` and `team_name`.

    Returns:
        One row per team with the two ids, `bnteam_team_id` and `dunkdvp_team_id`.

    Raises:
        ValueError: If a name repeats within a source after normalisation/crosswalk, or a
            team has no partner on the other side. The message names the teams and the fix.
    """
    crosswalk = {team_key(dunk): team_key(bn_name) for dunk, bn_name in TEAM_NAME_CROSSWALK.items()}
    bn_keys = bn["team_name"].map(team_key)
    dunkest_keys = dunkest["team_name"].map(team_key).map(lambda key: crosswalk.get(key, key))
    fix = (
        "Add or correct an entry in TEAM_NAME_CROSSWALK in src/build_team_master_table.py "
        '(Dunkest name -> Basketnews name, e.g. "Virtus Bologna": "Virtus Segafredo Bologna").'
    )

    for source, names, keys in ((BN_TEAM, bn["team_name"], bn_keys), (DUNKEST_DVP, dunkest["team_name"], dunkest_keys)):
        repeated = sorted(names[keys.duplicated(keep=False)])
        if repeated:
            raise ValueError(
                f"Team matching is not one-to-one: {source} has several teams matching as one: {repeated}. {fix}"
            )

    only_bn = sorted(bn["team_name"][~bn_keys.isin(dunkest_keys)])
    only_dunkest = sorted(dunkest["team_name"][~dunkest_keys.isin(bn_keys)])
    if only_bn or only_dunkest:
        raise ValueError(
            f"Team matching failed. Only in {BN_TEAM}: {only_bn}. Only in {DUNKEST_DVP}: {only_dunkest}. {fix}"
        )

    left = pd.DataFrame({BN_ID: bn["team_id"], "key": bn_keys})
    right = pd.DataFrame({DVP_ID: dunkest["team_id"], "key": dunkest_keys})
    return left.merge(right, on="key", validate="one_to_one").drop(columns="key")


def build_master_table(sources: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Join the two sources into the Master table, one row per team sorted by `team_name`.

    Args:
        sources: Raw source frames keyed by sheet name (see `read_sources`).

    Returns:
        The master table: identity columns, `bnteam_*` columns, then `dunkdvp_*` columns.

    Raises:
        ValueError: If the teams do not match one-to-one or any Master cell is blank.
    """
    bn = sources[BN_TEAM]
    dunkest = sources[DUNKEST_DVP]
    ids = match_teams(bn, dunkest)

    bn_prefix, dvp_prefix = SOURCE_PREFIXES[BN_TEAM], SOURCE_PREFIXES[DUNKEST_DVP]
    bn_cols = [c for c in bn.columns if c not in BN_IDENTITY and c not in BN_GAMES and c != "team_id"]
    bn_part = bn.rename(columns={c: f"{bn_prefix}{c}" for c in [*BN_GAMES, *bn_cols, "team_id"]})
    dvp_cols = [c for c in dunkest.columns if c not in DVP_DUPLICATED]
    dvp_part = dunkest.rename(columns={c: f"{dvp_prefix}{c}" for c in [*dvp_cols, "team_id"]})[
        [DVP_ID] + [f"{dvp_prefix}{c}" for c in dvp_cols]
    ]

    master = bn_part.merge(ids, on=BN_ID, validate="one_to_one").merge(dvp_part, on=DVP_ID, validate="one_to_one")
    lead = [*BN_IDENTITY, BN_ID, DVP_ID, *[f"{bn_prefix}{c}" for c in BN_GAMES]]
    master = master[lead + [f"{bn_prefix}{c}" for c in bn_cols] + [f"{dvp_prefix}{c}" for c in dvp_cols]]
    master = master.sort_values("team_name", key=lambda names: names.str.lower()).reset_index(drop=True)
    if master.isna().any().any() or master["team_name"].duplicated().any():
        raise ValueError("Team master table must have one row per team and no blank cells; check the source CSVs.")
    return master


def build_column_guide(sources: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per source column, in workbook sheet order."""
    rows = [
        (sheet, column, GUIDE[sheet].get(column, UNDOCUMENTED))
        for sheet, df in sources.items()
        for column in df.columns
    ]
    return pd.DataFrame(rows, columns=["Source dataset", "Column name", "Explanation"])


def check_column_guide(guide: pd.DataFrame, sources: dict[str, pd.DataFrame]) -> None:
    """Raise if the guide does not list every source column exactly once and nothing else.

    Args:
        guide: The Column Guide frame from `build_column_guide`.
        sources: Raw source frames keyed by sheet name.
    """
    listed = list(zip(guide["Source dataset"], guide["Column name"], strict=True))
    expected = [(sheet, column) for sheet, df in sources.items() for column in df.columns]
    if sorted(listed) != sorted(expected):
        raise ValueError("Column Guide must list every source column exactly once and nothing else")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output xlsx path")
    args = parser.parse_args()

    sources = read_sources()
    master = build_master_table(sources)
    guide = build_column_guide(sources)
    check_column_guide(guide, sources)
    write_workbook(args.out, {"Column Guide": guide, "Master": master, **sources})
    print(f"Wrote {len(master)} teams x {len(master.columns)} columns to {args.out}")
    undocumented = int((guide["Explanation"] == UNDOCUMENTED).sum())
    if undocumented:
        print(f"WARNING: {undocumented} source columns have no Column Guide explanation")


if __name__ == "__main__":
    main()
