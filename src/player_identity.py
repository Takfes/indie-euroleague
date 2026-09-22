"""Persisted, stable player identity: `player_key` and the checked-in alias table.

`name_key` (player_name_matching.py) is a normalisation helper recomputed fresh from raw
names on every build; it is not an identity. `player_key` is the identity: minted once per
real player and looked up (never regenerated) on every later build, from the checked-in
alias table (data/curated/player_alias_table.xlsx). The underlying matching decisions
(which source rows are the same player) still come entirely from `resolve_names` /
`map_alternate_spellings` / `name_key` in player_name_matching.py; this module only persists
and looks up the result of those decisions, and provides the join key the builder merges on.
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

ALIAS_COLUMNS = ["source", "source_id", "raw_name", "normalized_name", "team_name", "match_method", "player_key"]
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


class PlayerKeyRegistry:
    """Mints and looks up `player_key` values for one build run, seeded from a persisted alias table.

    A source row's identity is looked up, in order, by (source, source_id) already on file,
    then by this run's `name_key` (so every source row that `player_name_matching.py` has
    already resolved onto the same name_key shares one player_key), minting a brand new key
    only when neither is known yet.
    """

    def __init__(self, existing_alias: pd.DataFrame) -> None:
        """Seed the registry from a previously-persisted alias table (empty on first build).

        Args:
            existing_alias: Frame with `ALIAS_COLUMNS` (see `load_alias_table`).
        """
        self._by_source_id: dict[tuple[str, str], str] = {
            (row.source, str(row.source_id)): row.player_key
            for row in existing_alias.itertuples()
            if pd.notna(row.source_id)
        }
        self._by_name_key: dict[str, str] = {}
        self._used_keys: set[str] = set(existing_alias["player_key"])
        self.rows: list[dict[str, object]] = []

    def resolve(
        self,
        source: str,
        source_id: object,
        raw_name: object,
        name_key: str,
        team_name: object,
        match_method: str,
    ) -> str:
        """Return the player_key for one source row, minting a new one if never seen before.

        Also records an alias-table row for this (source, source_id) link in `self.rows`.

        Args:
            source: Short source code (e.g. "dunk", "bnadv", "elf", "kag").
            source_id: That source's own identifier for the row (NaN/None if it has none).
            raw_name: The raw, un-normalised name as that source spells it.
            name_key: This row's fully-resolved `name_key` (after `resolve_names` etc.).
            team_name: The raw team/club name as that source spells it.
            match_method: How this row's name_key was resolved this run (e.g. "base", "exact",
                "fallback", "alternate_spelling", "override", "new"), for the audit trail.

        Returns:
            The player_key, reused from the persisted alias table or this run's earlier rows
            where possible, otherwise newly minted.
        """
        source_id_key = str(source_id) if pd.notna(source_id) else None
        if source_id_key is not None and (source, source_id_key) in self._by_source_id:
            key = self._by_source_id[(source, source_id_key)]
        elif name_key in self._by_name_key:
            key = self._by_name_key[name_key]
        else:
            key = self._mint(name_key)
        self._by_name_key[name_key] = key
        if source_id_key is not None:
            self._by_source_id[(source, source_id_key)] = key
        self.rows.append({
            "source": source,
            "source_id": source_id_key,
            "raw_name": raw_name,
            "normalized_name": name_key,
            "team_name": team_name,
            "match_method": match_method,
            "player_key": key,
        })
        return key

    def _mint(self, name_key: str) -> str:
        """Mint a new player_key slug for a name_key never seen in this registry, disambiguating collisions."""
        base = slugify_key(name_key)
        candidate = base
        suffix = 2
        while candidate in self._used_keys:
            candidate = f"{base}-{suffix}"
            suffix += 1
        self._used_keys.add(candidate)
        return candidate


def build_alias_table(existing_alias: pd.DataFrame, new_rows: list[dict[str, object]]) -> pd.DataFrame:
    """Merge this run's newly-recorded links into the persisted alias table.

    Existing (source, source_id) rows are left untouched (in case their raw_name/team_name
    drifted upstream but the row is otherwise unchanged, the persisted version is kept, since
    it is what future builds already rely on); a new link is appended.

    Args:
        existing_alias: The previously persisted alias table.
        new_rows: Rows recorded by `PlayerKeyRegistry.resolve` during this build.

    Returns:
        The updated alias table, `ALIAS_COLUMNS` only, sorted by player_key then source.
    """
    new = pd.DataFrame(new_rows, columns=ALIAS_COLUMNS)
    if existing_alias.empty:
        combined = new
    else:
        known = set(zip(existing_alias["source"], existing_alias["source_id"].astype(str), strict=True))
        genuinely_new = new[~new.apply(lambda r: (r["source"], str(r["source_id"])) in known, axis=1)]
        combined = pd.concat([existing_alias[ALIAS_COLUMNS], genuinely_new], ignore_index=True)
    return combined.sort_values(["player_key", "source"]).reset_index(drop=True)


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


def merge_on_player_key(base: pd.DataFrame, other: pd.DataFrame, how: str) -> pd.DataFrame:
    """Merge two frames on `player_key`, reconciling the `name_key` column both carry.

    Both frames keep a `name_key` column alongside `player_key` (the matching helpers in
    player_name_matching.py operate on `name_key`); merging on `player_key` would otherwise
    leave two suffixed copies. This coalesces them back into one `name_key` column, preferring
    `base`'s value and falling back to `other`'s for rows the outer join adds from `other` alone.

    Args:
        base: Left frame, with `player_key` and `name_key` columns.
        other: Right frame, with `player_key` and `name_key` columns.
        how: Merge type, passed through to `pd.DataFrame.merge`.

    Returns:
        The merged frame with a single `name_key` column.
    """
    merged = base.merge(other, on="player_key", how=how, suffixes=("", "_other"))
    merged["name_key"] = merged["name_key"].fillna(merged["name_key_other"])
    return merged.drop(columns="name_key_other")
