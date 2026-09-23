"""Stable player identity: `player_key` and the alias table it is written to.

`name_key` (player_name_matching.py) is a normalisation helper recomputed fresh from raw
names on every build; it is not an identity. `player_key` is the identity, and it is assigned
strictly *after* a round's multi-source matching (`resolve_names` / `map_alternate_spellings` /
`name_key` in player_name_matching.py, chained bnadv -> dunk -> elf -> kag) has fully resolved
which source rows are the same player. Only then does `assign_player_keys` give each final
group one key, in a single pass, so no key is minted while a later source's rows are still
unmatched and a player cannot be split by which source happened to be processed first.

The alias table (data/curated/player_alias_table.xlsx) is that pass's output: one row per
source row with its key. The matching never reads it; the only thing read back from the previous
run's table is each (source, source_id) link's key, so a player keeps its key across refreshes
even when a source respells the name.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

import pandas as pd

ALIAS_COLUMNS = ["source", "source_id", "raw_name", "normalized_name", "team_name", "match_method", "player_key"]
# What the matching pass records per source row, before any key exists.
RECORD_COLUMNS = [c for c in ALIAS_COLUMNS if c != "player_key"]
ALIAS_SHEET = "Alias Table"
IDENTITY_VIEW_SHEET = "Identity View"


def slugify_key(key: str) -> str:
    """Turn a `name_key` ("wade baldwin") into a player_key slug ("wade-baldwin")."""
    return re.sub(r"\s+", "-", key.strip())


def load_alias_table(path: Path) -> pd.DataFrame:
    """Read the checked-in alias table, or an empty one (first-ever build) if it doesn't exist yet.

    Args:
        path: Path to data/curated/player_alias_table.xlsx.

    Returns:
        Frame with `ALIAS_COLUMNS`, one row per previously-persisted (source, source_id) link.
    """
    if not path.exists():
        return pd.DataFrame(columns=ALIAS_COLUMNS)
    return pd.read_excel(path, sheet_name=ALIAS_SHEET)


def alias_records(
    source: str,
    source_ids: Sequence[object],
    raw_names: Sequence[object],
    name_keys: Sequence[str],
    team_names: Sequence[object],
    match_methods: str | Sequence[str],
) -> pd.DataFrame:
    """Record one source's rows, as resolved by the matching pass, ready for `assign_player_keys`.

    Args:
        source: Short source code (e.g. "dunk", "bnadv", "elf", "kag").
        source_ids: That source's own identifier per row (NaN/None if a row has none).
        raw_names: The raw, un-normalised name per row as that source spells it.
        name_keys: Each row's final `name_key`, after every matching stage.
        team_names: The raw team/club name per row as that source spells it.
        match_methods: How each name_key was resolved this run ("base", "exact", "fallback",
            "alternate_spelling", "override", "new"), for the audit trail; one value applies to all rows.

    Returns:
        Frame with `RECORD_COLUMNS`, one row per source row; ids stringified (missing stays None).
    """
    return pd.DataFrame({
        "source": source,
        "source_id": [str(i) if pd.notna(i) else None for i in source_ids],
        "raw_name": list(raw_names),
        "normalized_name": list(name_keys),
        "team_name": list(team_names),
        "match_method": match_methods if isinstance(match_methods, str) else list(match_methods),
    })[RECORD_COLUMNS]


def assign_player_keys(records: pd.DataFrame, existing_alias: pd.DataFrame) -> pd.DataFrame:
    """Give every group of matched source rows one `player_key`, once the round's matching is complete.

    A group is all `records` sharing a `normalized_name` (the final `name_key`: every source row
    the matching chain resolved onto one player). Groups are visited in sorted name order, so the
    result never depends on which source's rows were processed first. Two steps:

    1. A group takes the key of a previously persisted (source, source_id) link among its rows, so
       keys survive refreshes even when a source respells a name. If its rows hold several
       persisted keys the smallest wins (the un-suffixed slug sorts before "-2"); a key already
       taken by an earlier group is skipped, so no two groups ever share one.
    2. A group with no usable persisted key gets its `name_key` slug ("wade-baldwin"), suffixed
       "-2", "-3", ... if a key is already taken. This runs only after every persisted key has
       been claimed, so a new group can never take a key another group holds by link.

    Args:
        records: One row per source row, with `RECORD_COLUMNS` (see `alias_records`).
        existing_alias: Previous run's alias table, or an empty frame on a first build. Only its
            (source, source_id) -> player_key links are read.

    Returns:
        This round's alias table: `ALIAS_COLUMNS`, one row per source row, sorted by player_key
        then source.
    """
    persisted = {
        (row.source, str(row.source_id)): row.player_key
        for row in existing_alias.itertuples()
        if pd.notna(row.source_id)
    }
    groups = dict(tuple(records.groupby("normalized_name", sort=True)))
    keys: dict[str, str] = {}
    taken: set[str] = set()
    for name, rows in groups.items():
        held = {persisted[link] for link in zip(rows["source"], rows["source_id"], strict=True) if link in persisted}
        free = sorted(held - taken)
        if free:
            keys[name] = free[0]
            taken.add(free[0])
    for name in groups:
        if name not in keys:
            keys[name] = _mint(name, taken)
    table = records.assign(player_key=records["normalized_name"].map(keys))[ALIAS_COLUMNS]
    return table.sort_values(["player_key", "source"], kind="stable").reset_index(drop=True)


def _mint(name_key: str, taken: set[str]) -> str:
    """Derive a player_key slug from a name_key, suffixing "-2", "-3", ... past keys already taken."""
    base = slugify_key(name_key)
    candidate, suffix = base, 2
    while candidate in taken:
        candidate = f"{base}-{suffix}"
        suffix += 1
    taken.add(candidate)
    return candidate


def build_identity_view(alias_table: pd.DataFrame) -> pd.DataFrame:
    """Build a wide, one-row-per-player view: each source's raw id/name side by side.

    Args:
        alias_table: The long-form alias table (`ALIAS_COLUMNS`).

    Returns:
        One row per `player_key`, with `<source>_source_id` and `<source>_raw_name` column
        pairs for every source that has a row for that player, columns ordered by source.
    """
    sources = list(dict.fromkeys(alias_table["source"]))
    view = pd.DataFrame({"player_key": sorted(alias_table["player_key"].unique())}).set_index("player_key")
    for source in sources:
        rows = alias_table[alias_table["source"] == source].set_index("player_key")
        view[f"{source}_source_id"] = rows["source_id"]
        view[f"{source}_raw_name"] = rows["raw_name"]
    return view.reset_index()
