"""Tests for player_key assignment (after matching completes), the alias table and the identity view."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from player_identity import (
    ALIAS_COLUMNS,
    RECORD_COLUMNS,
    alias_records,
    assign_player_keys,
    build_identity_view,
    load_alias_table,
    slugify_key,
)


def _records(*rows: tuple[str, object, str, str]) -> pd.DataFrame:
    """Records as `alias_records` builds them, from (source, source_id, raw_name, name_key) tuples."""
    return pd.concat(
        [
            alias_records(source, [source_id], [raw_name], [name_key], ["Some Team"], "exact")
            for source, source_id, raw_name, name_key in rows
        ],
        ignore_index=True,
    )


def _persisted(*rows: tuple[str, str, str, str]) -> pd.DataFrame:
    """A previously-persisted alias table from (source, source_id, name_key, player_key) tuples."""
    return pd.DataFrame(
        [
            {
                "source": source,
                "source_id": source_id,
                "raw_name": name_key.title(),
                "normalized_name": name_key,
                "team_name": "Some Team",
                "match_method": "exact",
                "player_key": player_key,
            }
            for source, source_id, name_key, player_key in rows
        ],
        columns=ALIAS_COLUMNS,
    )


EMPTY = pd.DataFrame(columns=ALIAS_COLUMNS)


def _key_of(table: pd.DataFrame, source: str, source_id: str) -> str:
    return table.loc[(table["source"] == source) & (table["source_id"] == source_id), "player_key"].item()


def test_slugify_key_replaces_spaces_with_hyphens() -> None:
    assert slugify_key("wade baldwin") == "wade-baldwin"


def test_load_alias_table_returns_empty_frame_when_missing(tmp_path: Path) -> None:
    table = load_alias_table(tmp_path / "does_not_exist.xlsx")
    assert table.empty
    assert list(table.columns) == ALIAS_COLUMNS


def test_alias_records_stringifies_ids_broadcasts_the_method_and_keeps_a_missing_id_missing() -> None:
    records = alias_records("bnadv", [101, np.nan], ["A B", "C D"], ["a b", "c d"], ["T1", "T2"], "base")
    assert list(records.columns) == RECORD_COLUMNS
    assert records["source_id"].tolist() == ["101", None]
    assert records["match_method"].tolist() == ["base", "base"]
    assert records["source"].tolist() == ["bnadv", "bnadv"]


def test_alias_records_takes_one_method_per_row() -> None:
    records = alias_records("dunk", ["s1", "s2"], ["A B", "C D"], ["a b", "c d"], ["T", "T"], ["exact", "fallback"])
    assert records["match_method"].tolist() == ["exact", "fallback"]


def test_assign_mints_one_slug_key_per_group_shared_by_every_source_row() -> None:
    records = _records(
        ("bnadv", 101, "Wade Baldwin IV", "wade baldwin"), ("dunk", "wbi", "Wade Baldwin", "wade baldwin")
    )
    table = assign_player_keys(records, EMPTY)
    assert list(table.columns) == ALIAS_COLUMNS
    assert table["player_key"].tolist() == ["wade-baldwin", "wade-baldwin"]


def test_assign_keeps_a_persisted_key_found_only_via_a_later_source() -> None:
    """The fixed defect: the earlier-source row has no persisted link, the Kaggle row does."""
    records = _records(
        ("bnadv", 34625, "Kamar Baldwin", "kamar baldwin"), ("kag", "P012597", "Kamar Baldwin", "kamar baldwin")
    )
    table = assign_player_keys(records, _persisted(("kag", "P012597", "kamar baldwin", "kamar-baldwin")))
    assert table["player_key"].tolist() == ["kamar-baldwin", "kamar-baldwin"]


def test_assign_prefers_the_smallest_persisted_key_when_a_group_holds_several() -> None:
    records = _records(
        ("bnadv", 34625, "Kamar Baldwin", "kamar baldwin"), ("kag", "P012597", "Kamar Baldwin", "kamar baldwin")
    )
    persisted = _persisted(
        ("bnadv", "34625", "kamar baldwin", "kamar-baldwin-2"), ("kag", "P012597", "kamar baldwin", "kamar-baldwin")
    )
    table = assign_player_keys(records, persisted)
    assert set(table["player_key"]) == {"kamar-baldwin"}


def test_assign_reuses_a_persisted_key_even_if_the_name_key_drifted() -> None:
    records = _records(("bnadv", 101, "Wade Baldwin", "wade baldwin iv"))
    table = assign_player_keys(records, _persisted(("bnadv", "101", "wade baldwin", "wade-baldwin")))
    assert table["player_key"].tolist() == ["wade-baldwin"]


def test_assign_gives_two_groups_claiming_the_same_persisted_key_distinct_keys() -> None:
    records = _records(
        ("bnadv", 1, "Ann Lee", "ann lee"), ("kag", "P1", "Ann Lee", "ann lee"), ("bnadv", 2, "Bob Ray", "bob ray")
    )
    persisted = _persisted(("bnadv", "1", "ann lee", "shared"), ("bnadv", "2", "bob ray", "shared"))
    table = assign_player_keys(records, persisted)
    assert _key_of(table, "bnadv", "1") == "shared"  # first group in sorted name order keeps it
    assert _key_of(table, "bnadv", "2") == "bob-ray"  # the other falls back to its own slug
    assert table["player_key"].nunique() == 2


def test_assign_never_mints_a_key_another_group_holds_by_persisted_link() -> None:
    records = _records(("bnadv", 1, "A B", "a b"), ("bnadv", 2, "C D", "c d"))
    table = assign_player_keys(records, _persisted(("bnadv", "2", "c d", "a-b")))
    assert _key_of(table, "bnadv", "2") == "a-b"
    assert _key_of(table, "bnadv", "1") == "a-b-2"


def test_assign_is_independent_of_record_order() -> None:
    records = _records(
        ("bnadv", 1, "Kamar Baldwin", "kamar baldwin"),
        ("dunk", "kb", "Kamar Baldwin", "kamar baldwin"),
        ("kag", "P1", "Kamar Baldwin", "kamar baldwin"),
        ("bnadv", 2, "Wade Baldwin IV", "wade baldwin"),
    )
    persisted = _persisted(("kag", "P1", "kamar baldwin", "kamar-baldwin"))
    forward = assign_player_keys(records, persisted)
    shuffled = assign_player_keys(records.iloc[::-1].reset_index(drop=True), persisted)
    pd.testing.assert_frame_equal(forward, shuffled)


def test_assign_never_merges_two_different_players_sharing_a_surname() -> None:
    """Baldwin trio: distinct groups keep distinct player_keys, with or without persisted history."""
    records = _records(
        ("bnadv", 1, "Wade Baldwin IV", "wade baldwin"),
        ("bnadv", 2, "Kamar Baldwin", "kamar baldwin"),
        ("elf", "Patrick Baldwin Jr.", "Patrick Baldwin Jr.", "patrick baldwin"),
    )
    persisted = _persisted(("bnadv", "2", "kamar baldwin", "kamar-baldwin"))
    for existing in (EMPTY, persisted):
        table = assign_player_keys(records, existing)
        assert table["player_key"].nunique() == 3


def test_assign_returns_one_key_per_group_sorted_by_key_then_source() -> None:
    records = _records(("kag", "P1", "B B", "b b"), ("bnadv", 1, "A A", "a a"), ("dunk", "x", "B B", "b b"))
    table = assign_player_keys(records, EMPTY)
    assert table[["player_key", "source"]].values.tolist() == [["a-a", "bnadv"], ["b-b", "dunk"], ["b-b", "kag"]]
    assert table.groupby("normalized_name")["player_key"].nunique().eq(1).all()


def test_assign_handles_an_empty_round() -> None:
    table = assign_player_keys(pd.DataFrame(columns=RECORD_COLUMNS), EMPTY)
    assert table.empty
    assert list(table.columns) == ALIAS_COLUMNS


def test_build_identity_view_is_one_row_per_player_key_with_source_columns_side_by_side() -> None:
    alias_table = _persisted(
        ("bnadv", "101", "wade baldwin", "wade-baldwin"), ("dunk", "wade-baldwin-iv", "wade baldwin", "wade-baldwin")
    )
    alias_table["raw_name"] = ["Wade Baldwin IV", "Wade Baldwin Iv"]
    view = build_identity_view(alias_table)
    assert len(view) == 1
    row = view.iloc[0]
    assert row["player_key"] == "wade-baldwin"
    assert row["bnadv_raw_name"] == "Wade Baldwin IV"
    assert row["dunk_raw_name"] == "Wade Baldwin Iv"
    assert row["bnadv_source_id"] == "101"


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
