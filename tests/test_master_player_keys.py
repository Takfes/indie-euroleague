"""End-to-end regression: the Master table must not depend on what the persisted alias table holds.

Latent defect (fixed): `player_key` used to be assigned incrementally as each source was walked, so
a player originally captured only via a later-processed source (Kaggle) got a second key, and a
second Master row, the first time an earlier-processed source (Basketnews) gained a row for them.
Keys are now assigned only after the round's full multi-source matching is complete.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from build_player_master_table import ALIAS_TABLE_PATH, build_master_table, read_sources
from player_identity import ALIAS_COLUMNS, ALIAS_SHEET, load_alias_table

# Sources processed before Kaggle in the matching chain (bnadv -> dunk -> elf -> kag).
EARLIER_SOURCES = {"bnadv", "dunk", "elf"}


@pytest.fixture(scope="module")
def committed_alias() -> pd.DataFrame:
    return load_alias_table(ALIAS_TABLE_PATH)


@pytest.fixture(scope="module")
def baseline() -> pd.DataFrame:
    """Master built against the committed alias table, as the pipeline runs today."""
    master, _, _ = build_master_table(read_sources(), alias_table_path=ALIAS_TABLE_PATH)
    return master


def _trimmed_alias(alias: pd.DataFrame, player_keys: set[str]) -> pd.DataFrame:
    """Copy of `alias` without the earlier-source rows of the given players (their Kaggle row stays)."""
    drop = alias["player_key"].isin(player_keys) & alias["source"].isin(EARLIER_SOURCES)
    return alias[~drop].reset_index(drop=True)


def _players_with_both_earlier_and_kaggle_rows(alias: pd.DataFrame) -> list[str]:
    sources = alias.groupby("player_key")["source"].agg(set)
    return sorted(key for key, found in sources.items() if "kag" in found and found & EARLIER_SOURCES)


def _rebuild_against(alias: pd.DataFrame, tmp_path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    path = tmp_path / "alias.xlsx"
    with pd.ExcelWriter(path) as writer:
        alias.to_excel(writer, sheet_name=ALIAS_SHEET, index=False)
    master, _, alias_out = build_master_table(read_sources(), alias_table_path=path)
    return master, alias_out


def test_master_has_one_row_when_only_the_kaggle_link_of_one_player_is_persisted(
    committed_alias: pd.DataFrame, baseline: pd.DataFrame, tmp_path: Path
) -> None:
    """The scout's scenario: a player's earlier-source rows have no persisted link, their Kaggle row does."""
    victim = _players_with_both_earlier_and_kaggle_rows(committed_alias)[0]
    master, alias_out = _rebuild_against(_trimmed_alias(committed_alias, {victim}), tmp_path)

    assert len(master) == len(baseline)
    pd.testing.assert_frame_equal(master, baseline)
    assert (alias_out["player_key"] == victim).sum() == (committed_alias["player_key"] == victim).sum()
    assert alias_out["player_key"].nunique() == len(master)


def test_master_has_one_row_per_player_when_every_earlier_source_link_is_missing(
    committed_alias: pd.DataFrame, baseline: pd.DataFrame, tmp_path: Path
) -> None:
    everyone = set(_players_with_both_earlier_and_kaggle_rows(committed_alias))
    master, alias_out = _rebuild_against(_trimmed_alias(committed_alias, everyone), tmp_path)

    pd.testing.assert_frame_equal(master, baseline)
    assert alias_out["player_key"].nunique() == len(master)


def test_master_is_identical_on_a_first_ever_build_with_no_alias_table(baseline: pd.DataFrame, tmp_path: Path) -> None:
    master, _ = _rebuild_against(pd.DataFrame(columns=ALIAS_COLUMNS), tmp_path)
    pd.testing.assert_frame_equal(master, baseline)
