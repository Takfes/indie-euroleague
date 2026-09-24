"""Tests of the Master sheet's columns, built from the committed source data."""

from __future__ import annotations

import pandas as pd
import pytest

import build_player_master_table as master_build
from build_game_player_stats import CONTRIBUTION_STATS
from build_player_kpis import DISTRIBUTION_FAMILIES
from build_player_master_table import (
    ALIAS_TABLE_PATH,
    build_column_guide,
    build_master_table,
    build_source_column_guide,
    load_canonical_team_names,
    read_sources,
    resolve_team_name,
)
from player_master_layout import DUPLICATE_COLUMNS, EXCLUDED_COLUMNS, LEADING_COLUMNS, order_master_columns


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


def test_canonical_team_name_follows_the_price_list_then_the_historical_team(master: pd.DataFrame) -> None:
    assert "team_name" not in master.columns
    canonical = load_canonical_team_names()
    priced = master["team_name_current"].notna()
    assert master.loc[priced, "canonical_team_name"].tolist() == (
        master.loc[priced, "team_name_current"].map(master_build.PRICE_CLUB_TO_CANONICAL).tolist()
    )
    unpriced = master[~priced]
    expected = unpriced["team_name_hist"].map(lambda team: resolve_team_name(team, canonical))
    pd.testing.assert_series_equal(unpriced["canonical_team_name"], expected, check_names=False)


def test_canonical_team_names_are_this_seasons_twenty_teams(master: pd.DataFrame) -> None:
    assert sorted(master["canonical_team_name"].dropna().unique()) == sorted(load_canonical_team_names())
    besiktas = master["team_name_current"] == "Besiktas"
    assert besiktas.any()
    assert (master.loc[besiktas, "canonical_team_name"] == "Besiktas Istanbul").all()
    assert not master["canonical_team_name"].eq("AS Monaco").any()


def test_leading_columns_come_first_in_the_requested_order(master: pd.DataFrame) -> None:
    assert list(master.columns[: len(LEADING_COLUMNS)]) == LEADING_COLUMNS
    # The captain's list, with canonical_team_name slotted in after the two team-name columns.
    requested = "player_name, team_name_hist, team_name_current, position, found_in, found_in_count, games_played"
    requested += ", kag_minutes_avg, kag_minutes_pct, price, kag_pir_avg, kag_pir_sd, kag_pir_per_min"
    requested += ", kag_pir_per_min_sd, pir_per_credit, pir_per_min_per_credit, expected_pir, breakeven_pir"
    requested += ", expected_price_change, expected_price_next_round"
    assert [c for c in LEADING_COLUMNS if c != "canonical_team_name"] == requested.split(", ")


def test_columns_are_unique_and_excluded_or_duplicate_ones_are_gone(master: pd.DataFrame) -> None:
    assert master.columns.is_unique
    assert not set(EXCLUDED_COLUMNS) & set(master.columns)
    assert not set(DUPLICATE_COLUMNS) & set(master.columns)
    assert set(DUPLICATE_COLUMNS.values()) <= set(master.columns) | set(EXCLUDED_COLUMNS)


def test_the_master_drops_exactly_the_requested_columns_and_keeps_the_kaggle_id_the_team_pipeline_reads(
    master: pd.DataFrame,
) -> None:
    """Names are spelled out here, not derived from `EXCLUDED_COLUMNS`, so editing that list breaks this test."""
    requested = {
        "price_rank",
        "dunk_cr",
        "dunk_min",
        "dunk_slug",
        "player_id",
        "bnadv_points",
        "kag_player_name_raw",
        "kag_player_name",
        "kag_team_id",
        "kag_games_played",
        "kag_recent_games",
    }
    assert set(EXCLUDED_COLUMNS) == requested
    assert not requested & set(master.columns)
    # build_team_kpis.funnel_actual_pir maps Kaggle players to positions through the Master's `kag_player_id`
    # (`dropna(subset=["kag_player_id"]).set_index("kag_player_id")["position"]`); excluding it broke that build.
    assert {"kag_player_id", "position"} <= set(master.columns)
    assert master["kag_player_id"].dropna().is_unique


def test_removing_an_exclusion_brings_the_column_back(monkeypatch: pytest.MonkeyPatch) -> None:
    """The layout places every excluded column, so the exclusion is a pure last-step filter."""
    monkeypatch.setattr(master_build, "EXCLUDED_COLUMNS", [])
    table, _, _ = build_master_table(read_sources(), alias_table_path=ALIAS_TABLE_PATH)
    assert set(EXCLUDED_COLUMNS) <= set(table.columns)
    assert table["kag_games_played"].notna().sum() == 351  # still computed


def test_the_eleven_contribution_columns_sit_together_and_sum_to_100(master: pd.DataFrame) -> None:
    pct = [f"kag_{prefix}_contribution_pct" for prefix in CONTRIBUTION_STATS]
    start = list(master.columns).index(pct[0])
    assert list(master.columns[start : start + len(pct)]) == pct
    complete = master.dropna(subset=pct)
    assert len(complete) > 300
    assert complete[pct].sum(axis=1).sub(100).abs().max() < 1e-9


def test_every_kaggle_distribution_family_is_complete(master: pd.DataFrame) -> None:
    for family in DISTRIBUTION_FAMILIES:
        stats = [f"kag_{family}_{stat}" for stat in ("sd", "cv", "p10", "p50", "p90", "range")]
        assert set(stats) <= set(master.columns), family
    for prefix in CONTRIBUTION_STATS:
        stats = [f"kag_{prefix}_contribution_{stat}" for stat in ("pct", "std", "cv", "p10", "p50", "p90", "range")]
        assert set(stats) <= set(master.columns), prefix


def test_shooting_families_follow_attempted_made_percentage(master: pd.DataFrame) -> None:
    position = {column: i for i, column in enumerate(master.columns)}
    for attempted, made, rate in (
        ("dunk_fga", "dunk_fgm", "kag_fg_pct"),
        ("dunk_tpa", "dunk_tpm", "kag_fg3_pct"),
        ("dunk_fta", "dunk_ftm", "kag_ft_pct"),
    ):
        assert position[attempted] < position[f"{attempted}_tot"] < position[made] < position[f"{made}_tot"]
        assert position[made] < position[rate]


def test_layout_rejects_columns_it_does_not_place_and_names_them() -> None:
    columns = [*LEADING_COLUMNS, "brand_new_metric"]
    with pytest.raises(
        ValueError, match=r"not in the Master: .*\bkag_pir_avg_recent\b.*not in the layout: \['brand_new_metric'\]"
    ):
        order_master_columns(columns)


def test_both_column_guides_document_every_column_of_the_real_workbook(master: pd.DataFrame) -> None:
    guide = build_column_guide(master)
    assert guide["Column name"].tolist() == list(master.columns)
    assert not guide["Explanation"].str.contains("undocumented").any(), guide.loc[
        guide["Explanation"].str.contains("undocumented"), "Column name"
    ].tolist()
    sources = read_sources()
    source_guide = build_source_column_guide(sources)
    assert not source_guide["Explanation"].str.contains("undocumented").any()
    assert len(source_guide) == sum(len(df.columns) for df in sources.values())
