"""Tests of the Master sheet's columns, built from the committed source data."""

from __future__ import annotations

import pandas as pd
import pytest

from build_player_master_table import ALIAS_TABLE_PATH, build_master_table, read_sources


@pytest.fixture(scope="module")
def master() -> pd.DataFrame:
    """The Master built from the committed sources, as the pipeline runs today."""
    table, _, _ = build_master_table(read_sources(), alias_table_path=ALIAS_TABLE_PATH)
    return table


def test_team_name_current_is_the_price_list_club_and_blank_without_a_price_row(master: pd.DataFrame) -> None:
    in_price_list = master["found_in"].str.contains("elf")
    assert in_price_list.any()
    assert master.loc[in_price_list, "team_name_current"].notna().all()
    assert master.loc[~in_price_list, "team_name_current"].isna().all()
    # Every player has a historical team, price row or not: the fallback chain never leaves it blank.
    assert master["team_name_hist"].notna().all()


def test_canonical_team_name_resolves_from_the_historical_team(master: pd.DataFrame) -> None:
    assert "team_name" not in master.columns
    assert master["canonical_team_name"].notna().any()
