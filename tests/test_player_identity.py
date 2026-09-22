"""Tests for the persisted player_key registry, alias table and player_key-based merge."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from player_identity import (
    ALIAS_COLUMNS,
    PlayerKeyRegistry,
    build_alias_table,
    build_identity_view,
    load_alias_table,
    merge_on_player_key,
    slugify_key,
)


def test_slugify_key_replaces_spaces_with_hyphens() -> None:
    assert slugify_key("wade baldwin") == "wade-baldwin"


def test_load_alias_table_returns_empty_frame_when_missing(tmp_path: Path) -> None:
    table = load_alias_table(tmp_path / "does_not_exist.xlsx")
    assert table.empty
    assert list(table.columns) == ALIAS_COLUMNS


def test_registry_mints_a_new_key_on_first_sight() -> None:
    registry = PlayerKeyRegistry(pd.DataFrame(columns=ALIAS_COLUMNS))
    key = registry.resolve("bnadv", 101, "Wade Baldwin IV", "wade baldwin", "Fenerbahce", "base")
    assert key == "wade-baldwin"
    assert registry.rows[0]["player_key"] == "wade-baldwin"
    assert registry.rows[0]["source"] == "bnadv"


def test_registry_reuses_key_within_a_run_for_the_same_name_key() -> None:
    registry = PlayerKeyRegistry(pd.DataFrame(columns=ALIAS_COLUMNS))
    key1 = registry.resolve("bnadv", 101, "Wade Baldwin IV", "wade baldwin", "Fenerbahce", "base")
    key2 = registry.resolve("dunk", "wade-baldwin-iv", "Wade Baldwin Iv", "wade baldwin", "Fenerbahce", "exact")
    assert key1 == key2


def test_registry_disambiguates_a_slug_collision() -> None:
    registry = PlayerKeyRegistry(pd.DataFrame(columns=ALIAS_COLUMNS))
    key1 = registry.resolve("bnadv", 1, "Kamar Baldwin", "kamar baldwin", "Team A", "base")
    key2 = registry.resolve("bnadv", 2, "Patrick Baldwin", "patrick baldwin", "Team B", "base")
    assert key1 != key2


def test_registry_looks_up_an_existing_source_id_even_if_name_key_drifted() -> None:
    existing = pd.DataFrame([
        {
            "source": "bnadv",
            "source_id": "101",
            "raw_name": "Wade Baldwin IV",
            "normalized_name": "wade baldwin",
            "team_name": "Fenerbahce",
            "match_method": "base",
            "player_key": "wade-baldwin",
        }
    ])
    registry = PlayerKeyRegistry(existing)
    # Same source_id, but upstream spelling drifted this run.
    key = registry.resolve("bnadv", 101, "Wade Baldwin", "wade baldwin iv", "Fenerbahce", "base")
    assert key == "wade-baldwin"


def test_build_alias_table_keeps_existing_rows_and_appends_new_links() -> None:
    existing = pd.DataFrame([
        {
            "source": "bnadv",
            "source_id": "101",
            "raw_name": "Wade Baldwin IV",
            "normalized_name": "wade baldwin",
            "team_name": "Fenerbahce",
            "match_method": "base",
            "player_key": "wade-baldwin",
        }
    ])
    new_rows = [
        {
            "source": "bnadv",
            "source_id": "101",
            "raw_name": "Wade Baldwin IV",
            "normalized_name": "wade baldwin",
            "team_name": "Fenerbahce",
            "match_method": "base",
            "player_key": "wade-baldwin",
        },
        {
            "source": "dunk",
            "source_id": "wade-baldwin-iv",
            "raw_name": "Wade Baldwin Iv",
            "normalized_name": "wade baldwin",
            "team_name": "Fenerbahce Beko Istanbul",
            "match_method": "exact",
            "player_key": "wade-baldwin",
        },
    ]
    combined = build_alias_table(existing, new_rows)
    assert len(combined) == 2
    assert set(combined["source"]) == {"bnadv", "dunk"}


def test_build_identity_view_is_one_row_per_player_key_with_source_columns_side_by_side() -> None:
    alias_table = pd.DataFrame([
        {
            "source": "bnadv",
            "source_id": "101",
            "raw_name": "Wade Baldwin IV",
            "normalized_name": "wade baldwin",
            "team_name": "Fenerbahce",
            "match_method": "base",
            "player_key": "wade-baldwin",
        },
        {
            "source": "dunk",
            "source_id": "wade-baldwin-iv",
            "raw_name": "Wade Baldwin Iv",
            "normalized_name": "wade baldwin",
            "team_name": "Fenerbahce Beko Istanbul",
            "match_method": "exact",
            "player_key": "wade-baldwin",
        },
    ])
    view = build_identity_view(alias_table)
    assert len(view) == 1
    row = view.iloc[0]
    assert row["player_key"] == "wade-baldwin"
    assert row["bnadv_raw_name"] == "Wade Baldwin IV"
    assert row["dunk_raw_name"] == "Wade Baldwin Iv"
    assert row["bnadv_source_id"] == "101"


def test_merge_on_player_key_coalesces_name_key_and_preserves_outer_rows() -> None:
    base = pd.DataFrame({"player_key": ["a", "b"], "name_key": ["alice x", "bob y"], "val": [1, 2]})
    other = pd.DataFrame({"player_key": ["b", "c"], "name_key": ["bob y", "carl z"], "extra": [20, 30]})
    merged = merge_on_player_key(base, other, how="outer")
    assert "name_key_other" not in merged.columns
    assert merged.set_index("player_key")["name_key"].to_dict() == {"a": "alice x", "b": "bob y", "c": "carl z"}


def test_registry_never_merges_two_different_players_sharing_a_surname() -> None:
    """Baldwin trio: distinct source rows for different first names must keep distinct player_keys."""
    registry = PlayerKeyRegistry(pd.DataFrame(columns=ALIAS_COLUMNS))
    keys = {
        registry.resolve("bnadv", 1, "Wade Baldwin IV", "wade baldwin", "Fenerbahce Beko Istanbul", "base"),
        registry.resolve("bnadv", 2, "Kamar Baldwin", "kamar baldwin", "FC Bayern Munich", "base"),
        registry.resolve("bnadv", 3, "Patrick Baldwin Jr.", "patrick baldwin", "Crvena Zvezda", "base"),
    }
    assert len(keys) == 3


def test_alias_columns_are_stable() -> None:
    # Guards against accidental column renames breaking the checked-in file's schema.
    assert ALIAS_COLUMNS == [
        "source",
        "source_id",
        "raw_name",
        "normalized_name",
        "team_name",
        "match_method",
        "player_key",
    ]
