#!/usr/bin/env python3
"""Build a single player master table workbook joining stats + price data across sources.

Combines four EuroLeague datasets into one row per player:
  - data/dunkest-data/player_stats.csv (Dunkest fantasy stats)
  - data/basketnews-players-stats/basketnews_players_advanced_stats.csv (advanced stats)
  - data/basketnews-onoff-stats/onoff_stats.csv (on/off lineup impact stats)
  - data/euroleague-fantasy/basketballsphere_prices.csv (fantasy prices)

Output: data/curated/player_master_table.xlsx with sheets, in order:
  - Column Guide: one row per column of every source dataset (Source dataset,
    Column name, Explanation), plus the derived Master columns under the label
    "Master (derived)"; texts live in src/player_master_column_guide.py
  - Master: one row per player (layout below)
  - Dunkest, BN Advanced, BN On-Off, Fantasy Prices: the raw source datasets as-is
    (BN On-Off stays long format, rows ordered total, offensive, defensive; Fantasy
    Prices keeps the head coaches, which the Master table excludes)

Matching strategy: players are joined primarily by normalised name (accents,
casing, hyphens, apostrophes and generational suffixes jr/sr/ii/iii/iv/v are
ignored, so "Wade Baldwin IV" == "Wade Baldwin"). Rows with no exact match are
resolved by src/player_name_matching.py: same surname (suffix excluded) + an
equivalent/compatible first name, with team as tie-breaker; or same team +
near-identical full name; or (Dunkest vs basketnews only) same surname + team +
identical games played, for nicknames such as Iffe/Gabriel Lundberg. A fallback
is only applied when it resolves to exactly one unambiguous candidate, so
different players (e.g. the three Baldwins) never merge.

Master column layout:
  1. identity: player_name, team_name, position, season, games_played - Dunkest is
     preferred, then basketnews, then the price list (position: Dunkest, then
     basketnews `positions`, then the price list `position`, so it is never blank
     for players missing from Dunkest). `season` only exists in basketnews, so it is
     blank for players missing there, as is `games_played` when Dunkest is also missing.
     Players traded mid-season keep only their max-games stint in the bnadv/bnoo
     columns, while the identity columns follow Dunkest (current team, season games).
  2. provenance: found_in (source codes, comma-separated, e.g. "dunk, elf") and
     found_in_count (how many); codes are defined once in
     `FOUND_IN_SOURCES` (src/player_master_column_guide.py): dunk, bnadv, bnoo, elf.
     Computed from the join provenance (which source rows the player came from),
     not from non-null values; the run reports any disagreement with that inference.
  3. Dunkest columns (dunk_*)
  4. basketnews advanced: player_id, then bnadv_* columns
  5. basketnews on/off (bnoo_*): total (_tot), then offensive (_off), then
     defensive (_def) columns, each in source CSV order
  6. fantasy price columns: price, price_rank

Usage:
    python src/build_player_master_table.py [--out PATH]

Re-run any time the four source CSVs are refreshed.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from player_master_column_guide import (
    BN_ADVANCED,
    BN_ONOFF,
    DERIVED,
    DUNKEST,
    FANTASY_PRICES,
    FOUND_IN_SEPARATOR,
    FOUND_IN_SOURCES,
    GUIDE,
    UNDOCUMENTED,
)
from player_name_matching import name_key, resolve_names

REPO_ROOT = Path(__file__).resolve().parents[1]
BN_ADV_PATH = REPO_ROOT / "data/basketnews-players-stats/basketnews_players_advanced_stats.csv"
BN_ONOFF_PATH = REPO_ROOT / "data/basketnews-onoff-stats/onoff_stats.csv"
DUNKEST_PATH = REPO_ROOT / "data/dunkest-data/player_stats.csv"
PRICE_PATH = REPO_ROOT / "data/euroleague-fantasy/basketballsphere_prices.csv"
DEFAULT_OUT = REPO_ROOT / "data/curated/player_master_table.xlsx"

SOURCE_PATHS = {
    DUNKEST: DUNKEST_PATH,
    BN_ADVANCED: BN_ADV_PATH,
    BN_ONOFF: BN_ONOFF_PATH,
    FANTASY_PRICES: PRICE_PATH,
}
# Views in the order their columns/rows appear: total, then offense, then defense.
VIEW_ABBREV = {"total": "tot", "offensive": "off", "defensive": "def"}
MAX_COLUMN_WIDTH = 45
FOUND_PREFIX = "_found_"
FOUND_IN_COLUMNS = ["found_in", "found_in_count"]


def read_sources() -> dict[str, pd.DataFrame]:
    """Read the four raw source CSVs, keyed by their workbook sheet name."""
    sources = {sheet: pd.read_csv(path) for sheet, path in SOURCE_PATHS.items()}
    # Long format, ordered total -> offensive -> defensive (stable within each view).
    onoff = sources[BN_ONOFF]
    order = onoff["view"].map({view: i for i, view in enumerate(VIEW_ABBREV)})
    sources[BN_ONOFF] = onoff.iloc[order.argsort(kind="stable")].reset_index(drop=True)
    return sources


def _primary_stint(df: pd.DataFrame, group_cols: list[str], rank_col: str) -> pd.DataFrame:
    """Keep one row per player_id, preferring the team stint with most games
    played (handles the small number of players traded mid-season)."""
    df = df.sort_values(rank_col, ascending=False)
    return df.drop_duplicates("player_id", keep="first")


def load_bn_advanced_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse basketnews advanced stats to one row per player with `bnadv_*` metrics.

    Args:
        df: Raw advanced-stats rows (one per player stint and offensive/defensive mode).

    Returns:
        One row per player: `player_id`, `bn_*` identity columns, `season`, `name_key`, `bnadv_*`.
    """
    # offensive/defensive rows are column-disjoint except identity columns,
    # which are identical within a (player_id, team_id) stint, so first()
    # merges the pair back into one row without losing or conflating data.
    stints = df.groupby(["player_id", "team_id"], as_index=False).first()
    primary = _primary_stint(stints, ["player_id", "team_id"], "games_played").copy()
    primary["name_key"] = primary["player_name"].map(name_key)
    identity = {
        "season",
        "league_id",
        "mode",
        "player_id",
        "player_name",
        "player_name_short",
        "team_id",
        "team_name",
        "team_short_name",
        "positions",
        "games_played",
        "minutes_per_game",
        "minutes_per_game_raw",
        "name_key",
    }
    metric_cols = [c for c in primary.columns if c not in identity]
    primary = primary.rename(
        columns={
            "player_name": "bn_player_name",
            "team_name": "bn_team_name",
            "positions": "bn_position",
            "games_played": "bn_games_played",
        }
        | {c: f"bnadv_{c}" for c in metric_cols}
    )
    keep = ["player_id", "bn_player_name", "bn_team_name", "bn_position", "season", "bn_games_played", "name_key"]
    keep += [f"bnadv_{c}" for c in metric_cols]
    return primary[keep]


def load_bn_onoff_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Pivot long on/off rows to one row per player with `bnoo_*_{tot,off,def}` columns.

    Args:
        df: Raw on/off rows in long format (one per player stint and view).

    Returns:
        One row per `player_id`; metric columns ordered total, offense, defense.
    """
    identity_cols = [
        "league",
        "league_id",
        "season",
        "team_id",
        "team_name",
        "team_short_name",
        "player_id",
        "player_name",
        "position",
        "games_played",
        "time_played_formatted",
        "time_played",
    ]
    metric_cols = [c for c in df.columns if c not in identity_cols and c != "view"]
    wide = df.pivot_table(index=["player_id", "team_id"], columns="view", values=metric_cols, aggfunc="first")
    # Total columns first, then offense, then defense; source CSV order inside each group.
    ordered = [(m, v) for v in VIEW_ABBREV for m in metric_cols if (m, v) in wide.columns]
    wide = wide[ordered]
    wide.columns = [f"bnoo_{metric}_{VIEW_ABBREV[view]}" for metric, view in wide.columns]
    wide = wide.reset_index()
    games = df.groupby(["player_id", "team_id"], as_index=False)["games_played"].first()
    combined = games.merge(wide, on=["player_id", "team_id"])
    primary = _primary_stint(combined, ["player_id", "team_id"], "games_played")
    return primary.drop(columns=["team_id", "games_played"])


def load_dunkest_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Prefix Dunkest columns with `dunk_` and add a matching `name_key`.

    Args:
        df: Raw Dunkest player stats.

    Returns:
        One row per player with `name_key`, `dunk_player_name`, `dunk_team_name`, `dunk_*`.
    """
    df = df.copy()
    df["dunk_player_name"] = df["first_name"] + " " + df["last_name"]
    df["name_key"] = df["dunk_player_name"].map(name_key)
    df["dunk_team_name"] = df["team_name"]
    dont_prefix = {
        "id",
        "first_name",
        "last_name",
        "team_id",
        "team_code",
        "team_name",
        "position_id",
        "dunk_player_name",
        "dunk_team_name",
        "name_key",
    }
    metric_cols = [c for c in df.columns if c not in dont_prefix]
    df = df.rename(columns={c: f"dunk_{c}" for c in metric_cols})
    keep = ["name_key", "dunk_player_name", "dunk_team_name"]
    keep += [f"dunk_{c}" for c in metric_cols]
    return df[keep]


def load_prices(df: pd.DataFrame) -> pd.DataFrame:
    """Keep player rows (no head coaches) of the price list with a matching `name_key`.

    Args:
        df: Raw basketballsphere price rows.

    Returns:
        One row per player with `name_key`, `price_*` identity columns, `price`.
    """
    df = df[df["role"] == "player"].copy()
    df["price_name_raw"] = df["name"]
    df["name_key"] = df["name"].map(name_key)
    df = df.rename(columns={"club": "price_club", "position": "price_position", "rank": "price_rank"})
    return df[["name_key", "price_name_raw", "price_club", "price_position", "price_rank", "price"]]


def _require_unique_names(df: pd.DataFrame, source: str) -> None:
    """Raise if two rows of one source normalise to the same name (would corrupt the join)."""
    dupes = df.loc[df["name_key"].duplicated(keep=False), "name_key"].unique().tolist()
    if dupes:
        raise ValueError(
            f"{source}: distinct rows share a normalised name: {dupes}. "
            "Two players would be merged by the join; check the source rows and extend the name rules."
        )


def found_in_columns(found: pd.DataFrame) -> pd.DataFrame:
    """Turn per-source provenance flags into the `found_in` and `found_in_count` columns.

    Args:
        found: Boolean frame, one column per code of `FOUND_IN_SOURCES`, one row per player.

    Returns:
        Frame with `found_in` (codes joined in `FOUND_IN_SOURCES` order) and integer `found_in_count`.
    """
    codes = list(FOUND_IN_SOURCES)
    labels = found[codes].apply(lambda row: FOUND_IN_SEPARATOR.join(c for c in codes if row[c]), axis=1)
    return pd.DataFrame({"found_in": labels, "found_in_count": found[codes].sum(axis=1).astype(int)})


def build_master_table(sources: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, dict[str, int]]:
    """Join the four sources into the Master table.

    Args:
        sources: Raw source frames keyed by sheet name (see `read_sources`).

    Returns:
        The master table and a dict of join statistics (rows per source, players
        in all four sources, fallback name matches, position-fallback players).
    """
    bn_adv = load_bn_advanced_stats(sources[BN_ADVANCED])
    bn_onoff = load_bn_onoff_stats(sources[BN_ONOFF])
    dunkest = load_dunkest_stats(sources[DUNKEST])
    prices = load_prices(sources[FANTASY_PRICES])
    for df, source in ((bn_adv, BN_ADVANCED), (dunkest, DUNKEST), (prices, FANTASY_PRICES)):
        _require_unique_names(df, source)

    # Provenance: a marker column per source, carried through the joins (NaN where the row has no source row).
    for df, code in ((dunkest, "dunk"), (bn_adv, "bnadv"), (bn_onoff, "bnoo"), (prices, "elf")):
        df[f"{FOUND_PREFIX}{code}"] = True

    master = bn_adv.merge(bn_onoff, on="player_id", how="left")

    exact_dunkest = dunkest["name_key"].copy()
    bn_base = master.rename(columns={"bn_team_name": "team_name", "bn_games_played": "games_played"})
    dunkest["name_key"] = resolve_names(bn_base, dunkest, "dunk_team_name", other_games_col="dunk_gp")
    _require_unique_names(dunkest, DUNKEST)
    master = master.merge(dunkest, on="name_key", how="outer")

    # A price row spelled like a Dunkest row that was linked to basketnews under
    # another spelling (e.g. "Sasha" vs "Aleksandr") follows that link.
    aliases = dict(zip(exact_dunkest, dunkest["name_key"], strict=True))
    prices["name_key"] = prices["name_key"].map(lambda key: aliases.get(key, key))
    exact_prices = prices["name_key"].copy()
    known_teams = master.assign(team_name=master["dunk_team_name"].fillna(master["bn_team_name"]))
    prices["name_key"] = resolve_names(known_teams, prices, "price_club")
    _require_unique_names(prices, FANTASY_PRICES)
    master = master.merge(prices, on="name_key", how="outer")

    found = pd.DataFrame({code: master[f"{FOUND_PREFIX}{code}"].notna() for code in FOUND_IN_SOURCES})
    master = master.drop(columns=[f"{FOUND_PREFIX}{code}" for code in FOUND_IN_SOURCES])
    master = master.join(found_in_columns(found))
    in_bn, in_dunkest, in_price, in_onoff = found["bnadv"], found["dunk"], found["elf"], found["bnoo"]
    # Cross-check: provenance versus "has any non-null value" inference, per source.
    inferred = {
        "dunk": master["dunk_slug"].notna(),
        "bnadv": master["player_id"].notna(),
        "bnoo": master.filter(like="bnoo_").notna().any(axis=1),
        "elf": master["price_rank"].notna(),
    }
    stats = {
        "players": len(master),
        "in_bn_advanced": int(in_bn.sum()),
        "in_bn_onoff": int(in_onoff.sum()),
        "in_dunkest": int(in_dunkest.sum()),
        "in_prices": int(in_price.sum()),
        "in_all_four": int((in_bn & in_onoff & in_dunkest & in_price).sum()),
        "fallback_matches_dunkest": int((exact_dunkest != dunkest["name_key"]).sum()),
        "fallback_matches_prices": int((exact_prices != prices["name_key"]).sum()),
        "position_fallback": int((~in_dunkest).sum()),
        "found_in_null_disagreements": int(sum((found[c] != inferred[c]).sum() for c in FOUND_IN_SOURCES)),
    }

    master["player_name"] = master["dunk_player_name"].fillna(master["bn_player_name"]).fillna(master["price_name_raw"])
    master["team_name"] = master["dunk_team_name"].fillna(master["bn_team_name"]).fillna(master["price_club"])
    master["position"] = master["dunk_position"].fillna(master["bn_position"]).fillna(master["price_position"])
    master["games_played"] = master["dunk_gp"].fillna(master["bn_games_played"])

    lead_cols = ["player_name", "team_name", "position", "season", "games_played", *FOUND_IN_COLUMNS]
    dunkest_cols = [
        c
        for c in dunkest.columns
        if c.startswith("dunk_") and c not in ("dunk_player_name", "dunk_team_name", "dunk_position", "dunk_gp")
    ]
    bn_cols = ["player_id"] + [c for c in bn_adv.columns if c.startswith("bnadv_")]
    onoff_cols = [c for c in bn_onoff.columns if c.startswith("bnoo_")]
    master = master[lead_cols + dunkest_cols + bn_cols + onoff_cols + ["price", "price_rank"]]
    return master.sort_values("player_name", key=lambda names: names.str.lower()).reset_index(drop=True), stats


def build_column_guide(sources: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per source column in workbook sheet order, then the derived Master columns."""
    rows = [
        (sheet, column, GUIDE[sheet].get(column, UNDOCUMENTED))
        for sheet, df in sources.items()
        for column in df.columns
    ]
    rows += [(DERIVED, column, text) for column, text in GUIDE[DERIVED].items()]
    return pd.DataFrame(rows, columns=["Source dataset", "Column name", "Explanation"])


def check_column_guide(guide: pd.DataFrame, sources: dict[str, pd.DataFrame], master: pd.DataFrame) -> None:
    """Raise if the guide is not: every source column exactly once, plus derived Master columns only.

    Args:
        guide: The Column Guide frame from `build_column_guide`.
        sources: Raw source frames keyed by sheet name.
        master: The Master table; derived columns must exist in it.
    """
    listed = list(zip(guide["Source dataset"], guide["Column name"], strict=True))
    expected = [(sheet, column) for sheet, df in sources.items() for column in df.columns]
    derived = [(label, column) for label, column in listed if label == DERIVED]
    if sorted(item for item in listed if item[0] != DERIVED) != sorted(expected):
        raise ValueError("Column Guide must list every source column exactly once and nothing else")
    if len(set(derived)) != len(derived) or any(column not in master.columns for _, column in derived):
        raise ValueError(f"Column Guide rows under '{DERIVED}' must be unique columns of the Master sheet")


def _style_sheet(ws: Worksheet, freeze_cell: str) -> None:
    """Bold header, freeze panes, autofilter and content-based column widths."""
    for cell in ws[1]:
        cell.font = Font(bold=True)
    ws.freeze_panes = freeze_cell
    ws.auto_filter.ref = ws.dimensions
    for column_cells in ws.iter_cols():
        longest = max(len(str(c.value)) for c in column_cells if c.value is not None)
        ws.column_dimensions[get_column_letter(column_cells[0].column)].width = min(longest + 2, MAX_COLUMN_WIDTH)


def write_workbook(out: Path, guide: pd.DataFrame, master: pd.DataFrame, sources: dict[str, pd.DataFrame]) -> None:
    """Write the workbook: Column Guide, Master, then the raw source sheets."""
    sheets = {"Column Guide": guide, "Master": master, **sources}
    out.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(out, engine="openpyxl") as writer:
        for name, df in sheets.items():
            df.to_excel(writer, sheet_name=name, index=False)
            # Master keeps the player name column visible while scrolling right.
            _style_sheet(writer.sheets[name], "B2" if name == "Master" else "A2")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output xlsx path")
    args = parser.parse_args()

    sources = read_sources()
    master, stats = build_master_table(sources)
    guide = build_column_guide(sources)
    check_column_guide(guide, sources, master)
    write_workbook(args.out, guide, master, sources)
    print(f"Wrote {len(master)} players x {len(master.columns)} columns to {args.out}")
    print("Join stats:", ", ".join(f"{k}={v}" for k, v in stats.items()))
    undocumented = int((guide["Explanation"] == UNDOCUMENTED).sum())
    if undocumented:
        print(f"WARNING: {undocumented} source columns have no Column Guide explanation")


if __name__ == "__main__":
    main()
