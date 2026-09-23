#!/usr/bin/env python3
"""Build a single player master table workbook joining stats + price data across sources.

Combines five EuroLeague datasets into one row per player:
  - data/dunkest-data/player_stats.csv (Dunkest fantasy stats)
  - data/basketnews-players-stats/basketnews_players_advanced_stats.csv (advanced stats)
  - data/basketnews-onoff-stats/onoff_stats.csv (on/off lineup impact stats)
  - data/curated/player_kpis.xlsx (per-player KPIs derived from the Kaggle box score; must
    exist before this script runs: `uv run python src/build_game_player_stats.py`, then
    `uv run python src/build_player_kpis.py`)
  - data/euroleague-fantasy/basketballsphere_prices.csv (fantasy prices)

Output: data/curated/player_master_table.xlsx with sheets, in order:
  - Column Guide: one row per Master column, in Master order (Source dataset, Column name as in
    the Master, Explanation), the derived Master columns under the label "Master (derived)"; texts
    live in src/player_master_column_guide.py
  - Master: one row per player (layout below)
  - Source Column Guide: one row per column of every source sheet below, named as in that source
  - Dunkest, BN Advanced, BN On-Off, Fantasy Prices, Player KPIs: the source datasets as-is
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
different players (e.g. the three Baldwins) never merge. The Kaggle KPI players (names
converted from `LAST, FIRST`) are matched last, against the rows of the four other
sources: exact name, a spelling used by another source for the same row ("Iffe" vs "Gabriel"
Lundberg), then the same rules as above (no games-played rule; plus a compound-surname rule,
"Nigel Hayes" vs "Nigel Hayes-Davis", on the same team); their team comes from
`KAGGLE_TEAM_CROSSWALK` (Kaggle team code -> master team name), and the build stops on a
code that is not in it.

Master column layout (src/player_master_layout.py holds the order, the dropped duplicates and the
excluded columns; the Column Guide follows the same order):
  1. leading columns, exactly: player_name, team_name_hist, team_name_current, canonical_team_name,
     position, found_in, found_in_count, games_played, kag_minutes_avg, kag_minutes_pct, price,
     kag_pir_avg, kag_pir_sd, kag_pir_per_min, kag_pir_per_min_sd, pir_per_credit,
     pir_per_min_per_credit, expected_pir, breakeven_pir, expected_price_change,
     expected_price_next_round.
     `team_name_hist` = Dunkest, then basketnews, then the price list, then Kaggle team (it can lag
     a player's current team); `team_name_current` = the price list club only, blank without a price
     row; `canonical_team_name` = `team_name_hist` on the 20 team_kpis.xlsx names. `position`:
     Dunkest, then basketnews `positions`, then the price list `position`, so it is never blank for
     players missing from Dunkest. `games_played`: Dunkest, then basketnews. `found_in` (source
     codes, comma-separated, e.g. "dunk, elf") and `found_in_count` (how many) come from the join
     provenance (which source rows the player came from), not from non-null values; codes are
     defined once in `FOUND_IN_SOURCES` (src/player_master_column_guide.py): dunk, bnadv, bnoo, kag,
     elf; the run reports any disagreement with the non-null inference. Players traded mid-season
     keep only their max-games stint in the bnadv/bnoo columns, while the identity columns follow
     Dunkest (current team, season games).
  2. the rest of the price projection (`capital_yield_pct`) and `season` (only in basketnews, so
     blank for players missing there).
  3. raw stats and metrics (Dunkest `dunk_*`, basketnews advanced `bnadv_*`, on/off `bnoo_*`, plus
     the Kaggle KPIs that are not distributions): production and availability, offense, defense, then
     team and lineup level (`dunk_plus_minus`, `bnoo_*`: total `_tot`, offense `_off`, defense
     `_def`, source order inside each group).
  4. the 11 signed PIR contribution shares (`kag_*_contribution_pct`, they sum to 100).
  5. the Kaggle distribution KPIs (`kag_*`: average, sd, cv, p10, p50, p90, range per family).
  A stat that two sources both report appears once (`DUPLICATE_COLUMNS`); `EXCLUDED_COLUMNS` are computed
  but left out of the sheet. The value KPIs pir_per_credit and pir_per_min_per_credit are computed
  after the master exists from kag_pir_avg_recent / kag_pir_per_min and price (blank if the KPI or the
  price is missing, or the price is 0). breakeven_pir, expected_price_change, capital_yield_pct and
  expected_price_next_round (= price + expected_price_change) are an ESTIMATE of the next price move
  from docs/rules.md's community-reverse-engineered formula, treating kag_pir_avg (season-average
  PIR, not the recent-5 window) as the Round score; `expected_pir` is kag_pir_avg_recent. See
  `add_price_projection_kpis` for the formula and caveats.

Usage:
    python src/build_player_master_table.py [--out PATH]

Re-run any time a source is refreshed (rebuild player_kpis.xlsx first when the Kaggle data changed).
"""

from __future__ import annotations

import argparse
from difflib import SequenceMatcher
from pathlib import Path

import pandas as pd

from master_workbook import write_workbook
from player_identity import (
    ALIAS_SHEET,
    IDENTITY_VIEW_SHEET,
    alias_records,
    assign_player_keys,
    build_identity_view,
    load_alias_table,
)
from player_master_column_guide import (
    BN_ADVANCED,
    BN_ONOFF,
    DUNKEST,
    FANTASY_PRICES,
    FOUND_IN_SEPARATOR,
    FOUND_IN_SOURCES,
    GUIDE,
    KAGGLE_KPIS,
    UNDOCUMENTED,
    describe_master_column,
)
from player_master_layout import DUPLICATE_COLUMNS, EXCLUDED_COLUMNS, order_master_columns
from player_name_matching import map_alternate_spellings, name_key, normalize_name, resolve_names, teams_compatible

REPO_ROOT = Path(__file__).resolve().parents[1]
BN_ADV_PATH = REPO_ROOT / "data/basketnews-players-stats/basketnews_players_advanced_stats.csv"
BN_ONOFF_PATH = REPO_ROOT / "data/basketnews-onoff-stats/onoff_stats.csv"
DUNKEST_PATH = REPO_ROOT / "data/dunkest-data/player_stats.csv"
PRICE_PATH = REPO_ROOT / "data/euroleague-fantasy/basketballsphere_prices.csv"
KAGGLE_KPIS_PATH = REPO_ROOT / "data/curated/player_kpis.xlsx"
TEAM_KPIS_PATH = REPO_ROOT / "data/curated/team_kpis.xlsx"
DEFAULT_OUT = REPO_ROOT / "data/curated/player_master_table.xlsx"
ALIAS_TABLE_PATH = REPO_ROOT / "data/curated/player_alias_table.xlsx"

# Tier 3 (manual) of team-name resolution: player-master `team_name` values that Tier 1
# (exact/teams_compatible) and Tier 2 (fuzzy) leave unresolved against the 20 canonical
# team_kpis.xlsx names. Value is the canonical name, or None when verified to have no
# current match (e.g. a club not fielding a team in this season's EuroLeague). Add a line
# here when `resolve_team_name` raises for a new unmatched value.
TEAM_NAME_OVERRIDES: dict[str, str | None] = {
    # Not one of this season's 20 EuroLeague teams: all 10 rows carrying this value come
    # only from the fantasy price list (found_in == "elf"), absent from every other source.
    "Besiktas": None,
}
# Consistent with FULL_NAME_MIN_RATIO in player_name_matching.py (comparing full names).
TEAM_NAME_FUZZY_MIN_RATIO = 0.85

# Basketnews 5-position scheme -> the Dunkest/price-list 3-bucket scheme. A closed,
# deterministic mapping: normalize_bn_positions raises on any code not listed here.
BN_POSITION_TO_BUCKET = {"PG": "G", "SG": "G", "SF": "F", "PF": "F", "C": "C"}

SOURCE_PATHS = {
    DUNKEST: DUNKEST_PATH,
    BN_ADVANCED: BN_ADV_PATH,
    BN_ONOFF: BN_ONOFF_PATH,
    FANTASY_PRICES: PRICE_PATH,
    KAGGLE_KPIS: KAGGLE_KPIS_PATH,
}
# Source label -> workbook sheet name, where they differ.
SHEET_NAMES = {KAGGLE_KPIS: "Player KPIs"}
KAGGLE_KPIS_SHEET = SHEET_NAMES[KAGGLE_KPIS]
# Kaggle team code -> master team name (the Dunkest spelling, which the Master prefers). Verified with
# player overlap: the players whose names match exactly across sources sit on the same team. Add a line
# here when the build reports an unmapped code.
KAGGLE_TEAM_CROSSWALK = {
    "ASV": "LDLC ASVEL Villeurbanne",
    "BAR": "FC Barcelona",
    "BAS": "Baskonia Vitoria-Gasteiz",
    "DUB": "Dubai Basketball",
    "HTA": "Hapoel IBI Tel Aviv",
    "IST": "Anadolu Efes Istanbul",
    "MAD": "Real Madrid",
    "MCO": "AS Monaco",
    "MIL": "EA7 Emporio Armani Milan",
    "MUN": "FC Bayern Munich",
    "OLY": "Olympiacos Piraeus",
    "PAM": "Valencia Basket",
    "PAN": "Panathinaikos AKTOR Athens",
    "PAR": "Partizan Mozzart Bet Belgrade",
    "PRS": "Paris Basketball",
    "RED": "Crvena Zvezda Meridianbet Belgrade",
    "TEL": "Maccabi Rapyd Tel Aviv",
    "ULK": "Fenerbahce Beko Istanbul",
    "VIR": "Virtus Bologna",
    "ZAL": "Zalgiris Kaunas",
}
VALUE_KPIS = {"pir_per_credit": "kag_pir_avg_recent", "pir_per_min_per_credit": "kag_pir_per_min"}
# Unofficial price-projection columns (see add_price_projection_kpis), in output order.
PRICE_PROJECTION_KPIS = ["breakeven_pir", "expected_price_change", "capital_yield_pct", "expected_price_next_round"]
# Minutes of a regulation EuroLeague game (overtime not counted): kag_minutes_pct is minutes / this x 100.
FULL_GAME_MINUTES = 40
# Views in the order their columns/rows appear: total, then offense, then defense.
VIEW_ABBREV = {"total": "tot", "offensive": "off", "defensive": "def"}
FOUND_PREFIX = "_found_"
FOUND_IN_COLUMNS = ["found_in", "found_in_count"]


def read_sources() -> dict[str, pd.DataFrame]:
    """Read the four raw source CSVs and the Kaggle KPI workbook, keyed by source label.

    Raises:
        FileNotFoundError: If `data/curated/player_kpis.xlsx` does not exist yet; the message
            gives the commands that produce it.
    """
    if not KAGGLE_KPIS_PATH.exists():
        raise FileNotFoundError(
            f"Missing {KAGGLE_KPIS_PATH.relative_to(REPO_ROOT)}, which the player master table needs. "
            "Build it first with `uv run python src/build_game_player_stats.py` "
            "(game-level dataset from the git-ignored Kaggle box score), then "
            "`uv run python src/build_player_kpis.py`."
        )
    sources = {
        label: pd.read_excel(path, sheet_name=KAGGLE_KPIS_SHEET) if path.suffix == ".xlsx" else pd.read_csv(path)
        for label, path in SOURCE_PATHS.items()
    }
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


def load_canonical_team_names(path: Path = TEAM_KPIS_PATH) -> list[str]:
    """Read the 20 canonical team names from the `Team KPIs` sheet of team_kpis.xlsx.

    Args:
        path: Path to team_kpis.xlsx.

    Returns:
        The canonical team names, in file order.
    """
    return pd.read_excel(path, sheet_name="Team KPIs")["team_name"].tolist()


def resolve_team_name(team_name: object, canonical_teams: list[str]) -> str | None:
    """Resolve a player-master `team_name` value onto exactly one canonical team name.

    Three tiers, each tried only once the previous one fails to yield exactly one match:

    1. Deterministic: an exact normalised-name match, then `teams_compatible()` (subset-of-
       tokens or shared-first-word-plus-3-shared-tokens; reused from player_name_matching.py).
    2. Fuzzy: a `SequenceMatcher` ratio against every canonical name; the top ratio is used
       only if it clears `TEAM_NAME_FUZZY_MIN_RATIO` and no other candidate ties it (a single
       clear winner, same discipline as `resolve_names`).
    3. Manual: `TEAM_NAME_OVERRIDES`, keyed by the raw `team_name` value.

    Args:
        team_name: Raw `team_name` value from the player master table.
        canonical_teams: The 20 canonical team names (see `load_canonical_team_names`).

    Returns:
        The matched canonical team name, or None if `team_name` is missing or is verified
        (via `TEAM_NAME_OVERRIDES`) to have no current match.

    Raises:
        ValueError: If `team_name` resolves to zero or several candidates at every tier and
            has no entry in `TEAM_NAME_OVERRIDES`.
    """
    if not isinstance(team_name, str):
        return None
    exact = [c for c in canonical_teams if normalize_name(c) == normalize_name(team_name)]
    if len(exact) == 1:
        return exact[0]
    compatible = [c for c in canonical_teams if teams_compatible(team_name, c)]
    if len(compatible) == 1:
        return compatible[0]
    if not compatible:
        ratios = sorted(
            (SequenceMatcher(None, normalize_name(team_name), normalize_name(c)).ratio(), c) for c in canonical_teams
        )
        best_ratio, best_team = ratios[-1]
        runner_up_ratio = ratios[-2][0] if len(ratios) > 1 else 0.0
        if best_ratio >= TEAM_NAME_FUZZY_MIN_RATIO and best_ratio > runner_up_ratio:
            return best_team
    if team_name in TEAM_NAME_OVERRIDES:
        return TEAM_NAME_OVERRIDES[team_name]
    raise ValueError(
        f"team_name {team_name!r} does not resolve to exactly one of the 20 canonical teams in "
        "data/curated/team_kpis.xlsx via exact match, teams_compatible(), or fuzzy match. Add an "
        "entry to TEAM_NAME_OVERRIDES in src/build_player_master_table.py (canonical name, or None "
        "if verified to have no current match)."
    )


def normalize_bn_positions(positions: pd.Series) -> pd.Series:
    """Normalise basketnews `bn_position` values onto the Dunkest/price-list G/F/C bucket scheme.

    Basketnews uses a 5-position scheme (PG/SG/SF/PF/C), including comma-separated
    multi-position combos (e.g. "SF,PF"); a combo takes its first-listed token
    (captain-confirmed tie-break rule) before mapping through `BN_POSITION_TO_BUCKET`.

    Args:
        positions: Raw `bn_position` values; NaN (player missing from basketnews) stays NaN.

    Returns:
        Series of "G"/"F"/"C" (or NaN), aligned to `positions`.

    Raises:
        ValueError: If a non-null value's first-listed token is not one of PG/SG/SF/PF/C.
    """
    first_token = positions.map(lambda p: p.split(",")[0] if isinstance(p, str) else None)
    unmapped = sorted(set(first_token.dropna()) - set(BN_POSITION_TO_BUCKET))
    if unmapped:
        raise ValueError(
            f"bn_position codes not in BN_POSITION_TO_BUCKET: {unmapped}. Add an entry in "
            "src/build_player_master_table.py (Basketnews position code -> G/F/C bucket)."
        )
    return first_token.map(BN_POSITION_TO_BUCKET)


def resolve_position(dunk_position: pd.Series, bn_position: pd.Series, price_position: pd.Series) -> pd.Series:
    """Resolve a player's position via the 3-source fallback: Dunkest, then Basketnews, then price list.

    Args:
        dunk_position: Dunkest `position` column (G/F/C; highest priority).
        bn_position: Basketnews position, already bucketed to G/F/C (see `normalize_bn_positions`).
        price_position: Fantasy price list `position` column (G/F/C; lowest priority).

    Returns:
        Series aligned to the inputs: the first non-null value in priority order, or NaN
        when all three sources are missing.
    """
    return dunk_position.fillna(bn_position).fillna(price_position)


def map_kaggle_teams(codes: pd.Series) -> pd.Series:
    """Map Kaggle team codes to master team names through `KAGGLE_TEAM_CROSSWALK`.

    Args:
        codes: Kaggle three-letter team codes.

    Returns:
        The master team names, aligned to `codes`.

    Raises:
        ValueError: If a code is not in the crosswalk (names the codes and the fix).
    """
    unmapped = sorted(set(codes.dropna()) - set(KAGGLE_TEAM_CROSSWALK))
    if unmapped:
        raise ValueError(
            f"Kaggle team codes not in KAGGLE_TEAM_CROSSWALK: {unmapped}. Add an entry in "
            'src/build_player_master_table.py (Kaggle team code -> master team name, e.g. "MAD": "Real Madrid").'
        )
    return codes.map(KAGGLE_TEAM_CROSSWALK)


def load_kaggle_kpis(df: pd.DataFrame) -> pd.DataFrame:
    """Prefix the Kaggle KPI columns with `kag_` and add a matching `name_key` and team name.

    Args:
        df: The `Player KPIs` sheet of `data/curated/player_kpis.xlsx`.

    Returns:
        One row per player with `name_key`, `kag_team_name` (from the crosswalk) and every
        source column as `kag_<column>`.

    Raises:
        ValueError: If a Kaggle team code is not in `KAGGLE_TEAM_CROSSWALK`.
    """
    out = df.rename(columns={c: f"kag_{c}" for c in df.columns})
    out["name_key"] = out["kag_player_name"].map(name_key)
    out["kag_team_name"] = map_kaggle_teams(out["kag_team_id"])
    return out


# Increment-2 adjudication (Step 3b): `kag_player_id` -> the name_key of the existing master
# row it is the same player as, for Kaggle rows `resolve_names`/`map_alternate_spellings` leave
# unmatched. A one-off manual decision, applied at build time, never re-derived inside the build.
# 2026-09 adjudication: the 10 Kaggle rows currently left unmatched (Abramo Canka, Dominykas
# Daubaris, Jacopo Vogogna, Joseba Querejeta, Lazar Stojkovic, Marvyn Wade, Mate Khatiashvili,
# Mattia Ceccato, Novak Pavlovic, Tamir Gold) all have kag_games_played == 0 and no candidate
# above the fuzzy-match threshold on their team roster (best ratio 0.667, "Tamir Gold" vs "Tamir
# Blatt", a different player) -- confirmed genuinely absent from the other 4 sources, not a
# matching gap. No overrides recorded; see the build report for the full investigation.
KAGGLE_PLAYER_IDENTITY_OVERRIDES: dict[str, str] = {}


def apply_kaggle_identity_overrides(kaggle_player_id: pd.Series, resolved_name_key: pd.Series) -> pd.Series:
    """Force specific Kaggle rows (by `kag_player_id`) onto a manually-adjudicated existing name_key.

    Args:
        kaggle_player_id: `kag_player_id` column.
        resolved_name_key: The Kaggle frame's `name_key` after `resolve_names`.

    Returns:
        `resolved_name_key`, with any `KAGGLE_PLAYER_IDENTITY_OVERRIDES` entries applied.
    """
    overrides = kaggle_player_id.map(KAGGLE_PLAYER_IDENTITY_OVERRIDES)
    return resolved_name_key.where(overrides.isna(), overrides)


def add_value_kpis(master: pd.DataFrame) -> pd.DataFrame:
    """Add the price-dependent KPIs `pir_per_credit` and `pir_per_min_per_credit`.

    Args:
        master: Frame with `price` and the `kag_` KPI columns named in `VALUE_KPIS`.

    Returns:
        A copy with the derived columns appended; blank where the KPI or the price is
        missing, or the price is 0.
    """
    out = master.copy()
    price = out["price"].where(out["price"] > 0)
    for column, kpi in VALUE_KPIS.items():
        out[column] = out[kpi] / price
    return out


def add_kaggle_display_columns(master: pd.DataFrame) -> pd.DataFrame:
    """Add `kag_minutes_pct` and `expected_pir`, two display columns taken straight from Kaggle KPIs.

    `kag_minutes_pct` is `kag_minutes_avg` as a percentage of a full game (`FULL_GAME_MINUTES`),
    on the 0-100 scale of the `_pct` suffix (`_rate` would be 0-1). `expected_pir` is
    `kag_pir_avg_recent`, the recency-weighted PIR and most forward-looking figure in the table;
    the price projections below deliberately keep using `kag_pir_avg`, per docs/rules.md.

    Args:
        master: Frame with `kag_minutes_avg` and `kag_pir_avg_recent`.

    Returns:
        A copy with both columns appended; blank wherever the Kaggle KPI is.
    """
    out = master.copy()
    out["kag_minutes_pct"] = out["kag_minutes_avg"] / FULL_GAME_MINUTES * 100
    out["expected_pir"] = out["kag_pir_avg_recent"]
    return out


def add_price_projection_kpis(master: pd.DataFrame) -> pd.DataFrame:
    """Add the unofficial price-projection KPIs (breakeven, expected price move and level, capital yield).

    ESTIMATE, not the official price mechanism: applies the community-reverse-engineered
    formula from docs/rules.md (`Price change = (Round score - 0.9 x Starting value) / 10`)
    with this pipeline's `kag_pir_avg` (season-average PIR) standing in for the Round score
    and today's `price` standing in for the starting value. docs/rules.md flags two open
    questions this does not resolve: whether the real Round score includes a 10% team-win
    bonus that `kag_pir_avg` does not, and whether the formula itself (reverse-engineered,
    unvalidated against real price outcomes) is accurate at all. There is no round-by-round
    price history in this pipeline, so this is a forward-looking estimate off today's price,
    not a backtest.

    Args:
        master: Frame with `price` and `kag_pir_avg`.

    Returns:
        A copy with `breakeven_pir`, `expected_price_change`, `capital_yield_pct` and
        `expected_price_next_round` (`price + expected_price_change`, the forecast price level
        rather than the delta) appended; blank (not zero) wherever `price` or `kag_pir_avg` is
        missing, or the price is 0.
    """
    out = master.copy()
    price = out["price"].where(out["price"] > 0)
    out["breakeven_pir"] = 0.9 * price
    out["expected_price_change"] = (out["kag_pir_avg"] - out["breakeven_pir"]) / 10
    out["capital_yield_pct"] = out["expected_price_change"] / price * 100
    out["expected_price_next_round"] = out["price"] + out["expected_price_change"]
    return out


def _require_unique_names(df: pd.DataFrame, source: str) -> None:
    """Raise if two rows of one source normalise to the same name (would corrupt the join)."""
    dupes = df.loc[df["name_key"].duplicated(keep=False), "name_key"].unique().tolist()
    if dupes:
        raise ValueError(
            f"{source}: distinct rows share a normalised name: {dupes}. "
            "Two players would be merged by the join; check the source rows and extend the name rules."
        )


def _match_method(before: pd.Series, after: pd.Series, known_before: set[str]) -> list[str]:
    """Classify each row as "fallback" (resolve_names changed its key), "exact" (unchanged, already
    known) or "new" (unchanged, starts a new master row); for the alias table's audit trail."""
    return [
        "fallback" if b != a else ("exact" if b in known_before else "new") for b, a in zip(before, after, strict=True)
    ]


def _kaggle_match_method(
    original: pd.Series, after_aliases: pd.Series, after_fallback: pd.Series, final: pd.Series, known_before: set[str]
) -> list[str]:
    """Classify each Kaggle row by which stage last changed its key: override, fallback,
    alternate_spelling, exact (unchanged, already known) or new (unchanged, starts a new row)."""
    methods = []
    for o, aa, af, f in zip(original, after_aliases, after_fallback, final, strict=True):
        if f != af:
            methods.append("override")
        elif af != aa:
            methods.append("fallback")
        elif aa != o:
            methods.append("alternate_spelling")
        elif o in known_before:
            methods.append("exact")
        else:
            methods.append("new")
    return methods


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


def build_master_table(
    sources: dict[str, pd.DataFrame], alias_table_path: Path = ALIAS_TABLE_PATH
) -> tuple[pd.DataFrame, dict[str, int], pd.DataFrame]:
    """Join the five sources into the Master table and assign each player's stable `player_key`.

    Two phases, in this order. First the matching pass resolves which rows of all five sources
    are the same player: the bnadv -> dunk -> elf -> kag chain of `resolve_names` /
    `map_alternate_spellings` / `name_key` (player_name_matching.py), each source outer-joined
    onto the previous ones by its resolved `name_key`. No `player_key` exists during this pass,
    and it reads nothing persisted. Only once it is complete does `assign_player_keys`
    (player_identity.py) give each final group one key, in a single pass, reusing the key of any
    (source, source_id) link already in the previous alias table. `player_key` therefore never
    depends on which source's row was matched first, and is not a Master output column.

    Args:
        sources: Raw source frames keyed by sheet name (see `read_sources`).
        alias_table_path: Path to the previous run's alias table (empty/bootstrap if missing);
            only its (source, source_id) -> player_key links are read.

    Returns:
        The master table, a dict of join statistics (rows per source, players in all four
        original sources and in all five, fallback name matches, Kaggle matching counts,
        position-fallback players), and this round's alias table (one row per source row with
        its `player_key`) to persist back to `alias_table_path`.
    """
    bn_adv = load_bn_advanced_stats(sources[BN_ADVANCED])
    bn_onoff = load_bn_onoff_stats(sources[BN_ONOFF])
    dunkest = load_dunkest_stats(sources[DUNKEST])
    prices = load_prices(sources[FANTASY_PRICES])
    kaggle = load_kaggle_kpis(sources[KAGGLE_KPIS])
    for df, source in ((bn_adv, BN_ADVANCED), (dunkest, DUNKEST), (prices, FANTASY_PRICES), (kaggle, KAGGLE_KPIS)):
        _require_unique_names(df, source)

    # Provenance: a marker column per source, carried through the joins (NaN where the row has no source row).
    for df, code in ((dunkest, "dunk"), (bn_adv, "bnadv"), (bn_onoff, "bnoo"), (kaggle, "kag"), (prices, "elf")):
        df[f"{FOUND_PREFIX}{code}"] = True

    master = bn_adv.merge(bn_onoff, on="player_id", how="left")

    records = [
        alias_records(
            "bnadv", master["player_id"], master["bn_player_name"], master["name_key"], master["bn_team_name"], "base"
        )
    ]

    exact_dunkest = dunkest["name_key"].copy()
    known_before_dunkest = set(master["name_key"])
    bn_base = master.rename(columns={"bn_team_name": "team_name", "bn_games_played": "games_played"})
    dunkest["name_key"] = resolve_names(bn_base, dunkest, "dunk_team_name", other_games_col="dunk_gp")
    _require_unique_names(dunkest, DUNKEST)
    dunkest_method = _match_method(exact_dunkest, dunkest["name_key"], known_before_dunkest)
    records.append(
        alias_records(
            "dunk",
            dunkest["dunk_slug"],
            dunkest["dunk_player_name"],
            dunkest["name_key"],
            dunkest["dunk_team_name"],
            dunkest_method,
        )
    )
    master = master.merge(dunkest, on="name_key", how="outer")

    # A price row spelled like a Dunkest row that was linked to basketnews under
    # another spelling (e.g. "Sasha" vs "Aleksandr") follows that link.
    aliases = dict(zip(exact_dunkest, dunkest["name_key"], strict=True))
    prices["name_key"] = prices["name_key"].map(lambda key: aliases.get(key, key))
    exact_prices = prices["name_key"].copy()
    known_before_prices = set(master["name_key"])
    known_teams = master.assign(team_name=master["dunk_team_name"].fillna(master["bn_team_name"]))
    prices["name_key"] = resolve_names(known_teams, prices, "price_club")
    _require_unique_names(prices, FANTASY_PRICES)
    prices_method = _match_method(exact_prices, prices["name_key"], known_before_prices)
    records.append(
        alias_records(
            "elf",
            prices["price_name_raw"],
            prices["price_name_raw"],
            prices["name_key"],
            prices["price_club"],
            prices_method,
        )
    )
    master = master.merge(prices, on="name_key", how="outer")

    # Kaggle last: its names are converted from `LAST, FIRST` and it has no position or price, so it
    # only ever links onto (or adds) rows; the four earlier sources are matched exactly as before.
    exact_kaggle = kaggle["name_key"].copy()
    known_teams = master.assign(
        team_name=master["dunk_team_name"].fillna(master["bn_team_name"]).fillna(master["price_club"])
    )
    kaggle["name_key"] = map_alternate_spellings(
        known_teams, kaggle, ["dunk_player_name", "bn_player_name", "price_name_raw"]
    )
    after_aliases = kaggle["name_key"].copy()
    kaggle["name_key"] = resolve_names(known_teams, kaggle, "kag_team_name", extend_surnames=True)
    after_fallback = kaggle["name_key"].copy()
    kaggle["name_key"] = apply_kaggle_identity_overrides(kaggle["kag_player_id"], kaggle["name_key"])
    _require_unique_names(kaggle, KAGGLE_KPIS)
    keys_before_kaggle = set(master["name_key"])
    kaggle_method = _kaggle_match_method(
        exact_kaggle, after_aliases, after_fallback, kaggle["name_key"], keys_before_kaggle
    )
    records.append(
        alias_records(
            "kag",
            kaggle["kag_player_id"],
            kaggle["kag_player_name"],
            kaggle["name_key"],
            kaggle["kag_team_name"],
            kaggle_method,
        )
    )
    master = master.merge(kaggle, on="name_key", how="outer")

    # Matching is complete: every source row now sits in its final group. Only now are keys assigned.
    alias_table = assign_player_keys(pd.concat(records, ignore_index=True), load_alias_table(alias_table_path))

    found = pd.DataFrame({code: master[f"{FOUND_PREFIX}{code}"].notna() for code in FOUND_IN_SOURCES})
    master = master.drop(columns=[f"{FOUND_PREFIX}{code}" for code in FOUND_IN_SOURCES])
    master = master.join(found_in_columns(found))
    in_bn, in_dunkest, in_price, in_onoff = found["bnadv"], found["dunk"], found["elf"], found["bnoo"]
    # Cross-check: provenance versus "has any non-null value" inference, per source.
    inferred = {
        "dunk": master["dunk_slug"].notna(),
        "bnadv": master["player_id"].notna(),
        "bnoo": master.filter(like="bnoo_").notna().any(axis=1),
        "kag": master["kag_player_id"].notna(),
        "elf": master["price_rank"].notna(),
    }
    stats = {
        "players": len(master),
        "in_bn_advanced": int(in_bn.sum()),
        "in_bn_onoff": int(in_onoff.sum()),
        "in_dunkest": int(in_dunkest.sum()),
        "in_prices": int(in_price.sum()),
        "in_all_four": int((in_bn & in_onoff & in_dunkest & in_price).sum()),
        "in_kaggle": int(found["kag"].sum()),
        "in_all_five": int(found.all(axis=1).sum()),
        "kaggle_exact_matches": int(exact_kaggle.isin(keys_before_kaggle).sum()),
        "kaggle_alternate_spelling_matches": int((exact_kaggle != after_aliases).sum()),
        "kaggle_fallback_matches": int((after_aliases != after_fallback).sum()),
        "kaggle_override_matches": int((after_fallback != kaggle["name_key"]).sum()),
        "kaggle_new_rows": int((~kaggle["name_key"].isin(keys_before_kaggle)).sum()),
        "fallback_matches_dunkest": int((exact_dunkest != dunkest["name_key"]).sum()),
        "fallback_matches_prices": int((exact_prices != prices["name_key"]).sum()),
        "position_fallback": int((~in_dunkest).sum()),
        "found_in_null_disagreements": int(sum((found[c] != inferred[c]).sum() for c in FOUND_IN_SOURCES)),
    }

    master["player_name"] = (
        master["dunk_player_name"]
        .fillna(master["bn_player_name"])
        .fillna(master["price_name_raw"])
        .fillna(master["kag_player_name"])
    )
    master["team_name_hist"] = (
        master["dunk_team_name"]
        .fillna(master["bn_team_name"])
        .fillna(master["price_club"])
        .fillna(master["kag_team_name"])
    )
    # The price list is the freshest source of a player's current team; no fallback, so a player
    # without a price row stays blank rather than borrowing the historical team.
    master["team_name_current"] = master["price_club"]
    master["position"] = resolve_position(
        master["dunk_position"], normalize_bn_positions(master["bn_position"]), master["price_position"]
    )
    master["games_played"] = master["dunk_gp"].fillna(master["bn_games_played"])
    canonical_teams = load_canonical_team_names()
    master["canonical_team_name"] = master["team_name_hist"].map(lambda t: resolve_team_name(t, canonical_teams))

    lead_cols = [
        "player_name",
        "team_name_hist",
        "team_name_current",
        "canonical_team_name",
        "position",
        "season",
        "games_played",
        *FOUND_IN_COLUMNS,
    ]
    dunkest_cols = [
        c
        for c in dunkest.columns
        if c.startswith("dunk_") and c not in ("dunk_player_name", "dunk_team_name", "dunk_position", "dunk_gp")
    ]
    bn_cols = ["player_id"] + [c for c in bn_adv.columns if c.startswith("bnadv_")]
    onoff_cols = [c for c in bn_onoff.columns if c.startswith("bnoo_")]
    kaggle_cols = [c for c in kaggle.columns if c.startswith("kag_") and c != "kag_team_name"]
    master = add_kaggle_display_columns(master)
    master = add_value_kpis(master)
    master = add_price_projection_kpis(master)
    master = master[
        lead_cols
        + dunkest_cols
        + bn_cols
        + onoff_cols
        + kaggle_cols
        + ["kag_minutes_pct"]
        + ["price", "price_rank"]
        + list(VALUE_KPIS)
        + ["expected_pir"]
        + PRICE_PROJECTION_KPIS
    ]
    master = master.drop(columns=list(DUPLICATE_COLUMNS))
    master = master[order_master_columns(master.columns)]
    master = master.sort_values("player_name", key=lambda names: names.str.lower()).reset_index(drop=True)
    # Very last step of column assembly: EXCLUDED_COLUMNS (src/player_master_layout.py) only leaves
    # columns out of the sheet, everything above still computed them.
    master = master.drop(columns=EXCLUDED_COLUMNS)
    return master, stats, alias_table


def build_column_guide(master: pd.DataFrame) -> pd.DataFrame:
    """One row per Master column, in Master order: (Source dataset, Column name, Explanation).

    Column names are the Master headers (`dunk_tpa`, not the source's `tpa`). A kept column that a
    dropped duplicate (`DUPLICATE_COLUMNS`) also reported says so in its explanation.
    """
    dropped_for: dict[str, list[str]] = {}
    for dropped, kept in DUPLICATE_COLUMNS.items():
        dropped_for.setdefault(kept, []).append(dropped)
    rows = []
    for column in master.columns:
        label, text = describe_master_column(column)
        if column in dropped_for:
            sheets = sorted({
                SHEET_NAMES.get(describe_master_column(d)[0], describe_master_column(d)[0]) for d in dropped_for[column]
            })
            text += f" (same stat as {', '.join(dropped_for[column])}, dropped from the Master; still on the {' / '.join(sheets)} sheet)"
        rows.append((label, column, text))
    return pd.DataFrame(rows, columns=["Source dataset", "Column name", "Explanation"])


def build_source_column_guide(sources: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per column of every source sheet, in workbook sheet order (names as in that source)."""
    rows = [
        (sheet, column, GUIDE[sheet].get(column, UNDOCUMENTED))
        for sheet, df in sources.items()
        for column in df.columns
    ]
    return pd.DataFrame(rows, columns=["Source dataset", "Column name", "Explanation"])


def check_column_guide(guide: pd.DataFrame, master: pd.DataFrame) -> None:
    """Raise unless the guide lists exactly the Master's columns, once each and in Master order.

    Args:
        guide: The Column Guide frame from `build_column_guide`.
        master: The Master table.
    """
    if guide["Column name"].tolist() != list(master.columns):
        raise ValueError("Column Guide must list every Master column exactly once, in Master column order")


def check_source_column_guide(guide: pd.DataFrame, sources: dict[str, pd.DataFrame]) -> None:
    """Raise if the guide is not: every source sheet column exactly once and nothing else.

    Args:
        guide: The Source Column Guide frame from `build_source_column_guide`.
        sources: Raw source frames keyed by sheet name.
    """
    listed = list(zip(guide["Source dataset"], guide["Column name"], strict=True))
    expected = [(sheet, column) for sheet, df in sources.items() for column in df.columns]
    if sorted(listed) != sorted(expected):
        raise ValueError("Source Column Guide must list every source column exactly once and nothing else")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output xlsx path")
    parser.add_argument("--alias-table", type=Path, default=ALIAS_TABLE_PATH, help="Player alias table xlsx path")
    args = parser.parse_args()

    sources = read_sources()
    master, stats, alias_table = build_master_table(sources, alias_table_path=args.alias_table)
    guide = build_column_guide(master)
    check_column_guide(guide, master)
    source_guide = build_source_column_guide(sources)
    check_source_column_guide(source_guide, sources)
    write_workbook(
        args.out,
        {
            "Column Guide": guide,
            "Master": master,
            "Source Column Guide": source_guide,
            **{SHEET_NAMES.get(label, label): df for label, df in sources.items()},
        },
    )
    write_workbook(
        args.alias_table,
        {ALIAS_SHEET: alias_table, IDENTITY_VIEW_SHEET: build_identity_view(alias_table)},
    )
    print(f"Wrote {len(master)} players x {len(master.columns)} columns to {args.out}")
    print(f"Wrote {len(alias_table)} alias rows ({alias_table['player_key'].nunique()} players) to {args.alias_table}")
    print("Join stats:", ", ".join(f"{k}={v}" for k, v in stats.items()))
    undocumented = int(
        (guide["Explanation"] == UNDOCUMENTED).sum() + (source_guide["Explanation"] == UNDOCUMENTED).sum()
    )
    if undocumented:
        print(f"WARNING: {undocumented} columns have no Column Guide explanation")


if __name__ == "__main__":
    main()
