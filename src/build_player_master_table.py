#!/usr/bin/env python3
"""Build a single player master table joining stats + price data across sources.

Combines four EuroLeague datasets into one row per player:
  - data/basketnews-players-stats/basketnews_players_advanced_stats.csv (advanced stats)
  - data/basketnews-onoff-stats/onoff_stats.csv (on/off lineup impact stats)
  - data/dunkest-data/player_stats.csv (Dunkest fantasy stats)
  - data/euroleague-fantasy/basketballsphere_prices.csv (fantasy prices)

Matching strategy: players are joined primarily by normalized full name. When
that has no exact match (e.g. nicknames like "Sasha" vs "Aleksandr" Vezenkov),
a fallback tries normalized last name + team/club overlap; a fallback is only
applied when it resolves to exactly one unambiguous candidate.

Column layout: identity columns (player_name, team_name, position, season,
games_played) are deduplicated to one column each, preferring
basketnews-players-stats > Dunkest > basketballsphere. All other columns keep
a source prefix (bnadv_, bnoo_, dunk_) since they are distinct measurements,
not duplicates. Price columns are appended last, as requested.

Usage:
    python src/build_player_master_table.py [--out PATH]

Re-run any time the four source CSVs are refreshed.
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
BN_ADV_PATH = REPO_ROOT / "data/basketnews-players-stats/basketnews_players_advanced_stats.csv"
BN_ONOFF_PATH = REPO_ROOT / "data/basketnews-onoff-stats/onoff_stats.csv"
DUNKEST_PATH = REPO_ROOT / "data/dunkest-data/player_stats.csv"
PRICE_PATH = REPO_ROOT / "data/euroleague-fantasy/basketballsphere_prices.csv"
DEFAULT_OUT = REPO_ROOT / "data/player-master-table/player_master_table.csv"

VIEW_ABBREV = {"total": "tot", "offensive": "off", "defensive": "def"}


def normalize_name(name: str) -> str:
    """Lowercase, strip accents/punctuation, collapse whitespace for name matching."""
    ascii_name = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    ascii_name = re.sub(r"[^a-z ]", "", ascii_name.lower())
    return re.sub(r"\s+", " ", ascii_name).strip()


def _primary_stint(df: pd.DataFrame, group_cols: list[str], rank_col: str) -> pd.DataFrame:
    """Keep one row per player_id, preferring the team stint with most games
    played (handles the small number of players traded mid-season)."""
    df = df.sort_values(rank_col, ascending=False)
    return df.drop_duplicates("player_id", keep="first")


def load_bn_advanced_stats() -> pd.DataFrame:
    df = pd.read_csv(BN_ADV_PATH)
    # offensive/defensive rows are column-disjoint except identity columns,
    # which are identical within a (player_id, team_id) stint, so first()
    # merges the pair back into one row without losing or conflating data.
    stints = df.groupby(["player_id", "team_id"], as_index=False).first()
    primary = _primary_stint(stints, ["player_id", "team_id"], "games_played").copy()
    primary = primary.rename(columns={"positions": "position"})
    primary["name_key"] = primary["player_name"].map(normalize_name)
    identity = {
        "season", "league_id", "mode", "player_id", "player_name", "player_name_short",
        "team_id", "team_name", "team_short_name", "position", "games_played",
        "minutes_per_game", "minutes_per_game_raw", "name_key",
    }
    metric_cols = [c for c in primary.columns if c not in identity]
    primary = primary.rename(columns={c: f"bnadv_{c}" for c in metric_cols})
    keep = ["player_id", "player_name", "team_name", "position", "season", "games_played", "name_key"]
    keep += [f"bnadv_{c}" for c in metric_cols]
    return primary[keep]


def load_bn_onoff_stats() -> pd.DataFrame:
    df = pd.read_csv(BN_ONOFF_PATH)
    identity_cols = [
        "league", "league_id", "season", "team_id", "team_name", "team_short_name",
        "player_id", "player_name", "position", "games_played",
        "time_played_formatted", "time_played",
    ]
    metric_cols = [c for c in df.columns if c not in identity_cols and c != "view"]
    wide = df.pivot_table(index=["player_id", "team_id"], columns="view", values=metric_cols, aggfunc="first")
    wide.columns = [f"bnoo_{metric}_{VIEW_ABBREV[view]}" for metric, view in wide.columns]
    wide = wide.reset_index()
    games = df.groupby(["player_id", "team_id"], as_index=False)["games_played"].first()
    combined = games.merge(wide, on=["player_id", "team_id"])
    primary = _primary_stint(combined, ["player_id", "team_id"], "games_played")
    return primary.drop(columns=["team_id", "games_played"])


def load_dunkest_stats() -> pd.DataFrame:
    df = pd.read_csv(DUNKEST_PATH)
    df["dunk_player_name"] = df["first_name"] + " " + df["last_name"]
    df["name_key"] = df["dunk_player_name"].map(normalize_name)
    df["last_name_key"] = df["last_name"].map(normalize_name)
    df["dunk_team_name"] = df["team_name"]
    dont_prefix = {
        "id", "first_name", "last_name", "team_id", "team_code", "team_name", "position_id",
        "dunk_player_name", "dunk_team_name", "name_key", "last_name_key",
    }
    metric_cols = [c for c in df.columns if c not in dont_prefix]
    df = df.rename(columns={c: f"dunk_{c}" for c in metric_cols})
    keep = ["name_key", "last_name_key", "dunk_player_name", "dunk_team_name"]
    keep += [f"dunk_{c}" for c in metric_cols]
    return df[keep]


def load_prices() -> pd.DataFrame:
    df = pd.read_csv(PRICE_PATH)
    df = df[df["role"] == "player"].copy()
    df["price_name_raw"] = df["name"]
    df["name_key"] = df["name"].map(normalize_name)
    df["last_name_key"] = df["name"].str.split().str[-1].map(normalize_name)
    df = df.rename(columns={"club": "price_club", "position": "price_position", "rank": "price_rank"})
    return df[["name_key", "last_name_key", "price_name_raw", "price_club", "price_position", "price_rank", "price"]]


def match_by_last_name(base: pd.DataFrame, other: pd.DataFrame, other_team_col: str) -> pd.Series:
    """For rows in `other` with no exact name_key match in `base`, try to
    resolve via last name + team/club overlap. Only applies a fallback when
    it resolves to exactly one unambiguous base candidate; if two `other`
    rows would collide onto the same base candidate, both reassignments are
    reverted to avoid conflating two different players into one row."""
    base_last = base["name_key"].str.split().str[-1]
    base_team_norm = base["team_name"].map(normalize_name)
    result = other["name_key"].copy()
    unmatched = ~other["name_key"].isin(set(base["name_key"]))
    for idx in other.index[unmatched]:
        last = other.at[idx, "last_name_key"]
        team = normalize_name(other.at[idx, other_team_col])
        candidates = base[base_last == last]
        if len(candidates) == 1:
            result.at[idx] = candidates.iloc[0]["name_key"]
        elif len(candidates) > 1:
            team_hits = candidates[base_team_norm.loc[candidates.index].apply(
                lambda t: t in team or team in t)]
            if len(team_hits) == 1:
                result.at[idx] = team_hits.iloc[0]["name_key"]
    # A reassignment can collide either with another reassignment or with a
    # row that already matched `base` exactly; check the full result for
    # duplicates and revert any reassigned row involved, rather than only
    # comparing reassigned rows against each other.
    revert = unmatched & result.duplicated(keep=False)
    result.loc[revert] = other.loc[revert, "name_key"]
    return result


def build_master_table() -> pd.DataFrame:
    bn_adv = load_bn_advanced_stats()
    bn_onoff = load_bn_onoff_stats()
    dunkest = load_dunkest_stats()
    prices = load_prices()

    master = bn_adv.merge(bn_onoff, on="player_id", how="left")

    dunkest["name_key"] = match_by_last_name(master, dunkest, "dunk_team_name")
    master = master.merge(dunkest.drop(columns="last_name_key"), on="name_key", how="outer")

    prices["name_key"] = match_by_last_name(master, prices, "price_club")
    master = master.merge(prices.drop(columns="last_name_key"), on="name_key", how="outer")

    master["player_name"] = master["player_name"].fillna(master["dunk_player_name"]).fillna(master["price_name_raw"])
    master["team_name"] = master["team_name"].fillna(master["dunk_team_name"]).fillna(master["price_club"])
    master["position"] = master["position"].fillna(master["dunk_position"]).fillna(master["price_position"])
    master["games_played"] = master["games_played"].fillna(master["dunk_gp"])

    master = master.drop(columns=[
        "name_key", "dunk_player_name", "dunk_team_name", "dunk_position", "dunk_gp",
        "price_name_raw", "price_club", "price_position",
    ])

    lead_cols = ["player_id", "player_name", "team_name", "position", "season", "games_played"]
    price_cols = ["price", "price_rank"]
    metric_cols = [c for c in master.columns if c not in lead_cols and c not in price_cols]
    master = master[lead_cols + metric_cols + price_cols]
    return master.sort_values("player_name").reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output CSV path")
    args = parser.parse_args()

    master = build_master_table()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    master.to_csv(args.out, index=False)
    print(f"Wrote {len(master)} players x {len(master.columns)} columns to {args.out}")


if __name__ == "__main__":
    main()
