"""Tests for the team matching and Column Guide check of the team master table."""

from __future__ import annotations

import pandas as pd
import pytest

from build_team_master_table import BN_ID, DVP_ID, build_column_guide, check_column_guide, match_teams
from team_master_column_guide import BN_TEAM, DUNKEST_DVP, TEAM_KPI_COLUMNS


def _teams(*rows: tuple[int, str]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["team_id", "team_name"])


def test_match_teams_one_to_one_with_crosswalk_and_accents() -> None:
    bn = _teams((368, "Real Madrid"), (90, "Virtus Segafredo Bologna"), (60, "FC Barcelóna"))
    dunkest = _teams((47, "Virtus Bologna"), (37, "FC  Barcelona"), (45, "real madrid"))
    matched = match_teams(bn, dunkest)
    assert dict(zip(matched[BN_ID], matched[DVP_ID], strict=True)) == {368: 45, 90: 47, 60: 37}


def test_match_teams_ignores_stale_crosswalk_entry() -> None:
    bn = _teams((90, "Virtus Bologna"))
    dunkest = _teams((47, "Virtus Bologna"))
    assert match_teams(bn, dunkest)[DVP_ID].tolist() == [47]


def test_match_teams_names_unmatched_teams_on_each_side() -> None:
    bn = _teams((1, "Real Madrid"), (2, "Zalgiris Kaunas"))
    dunkest = _teams((10, "Real Madrid"), (11, "Zalgiris Kaunas Sponsor"))
    with pytest.raises(
        ValueError,
        match=r"Only in BN Team Stats: \['Zalgiris Kaunas'\].*Dunkest Defense vs Position: \['Zalgiris Kaunas Sponsor'\]",
    ):
        match_teams(bn, dunkest)


def test_match_teams_rejects_duplicate_match() -> None:
    bn = _teams((1, "Real Madrid"), (2, "Zalgiris Kaunas"))
    dunkest = _teams((10, "Real Madrid"), (11, "real  madrid"))
    with pytest.raises(ValueError, match="not one-to-one"):
        match_teams(bn, dunkest)


def test_column_guide_lists_every_source_column_once() -> None:
    sources = {BN_TEAM: pd.DataFrame(columns=["team_id", "points"]), DUNKEST_DVP: pd.DataFrame(columns=["team_id"])}
    guide = build_column_guide(sources)
    check_column_guide(guide, sources)
    with pytest.raises(ValueError, match="exactly once"):
        check_column_guide(pd.concat([guide, guide.iloc[[0]]]), sources)
    with pytest.raises(ValueError, match="exactly once"):
        check_column_guide(guide.iloc[1:], sources)


def test_team_kpi_block_groups_the_actual_pir_funnel_right_after_the_dunkest_funnel() -> None:
    funnel = [c for c in TEAM_KPI_COLUMNS if c.startswith("funnel_")]
    start = TEAM_KPI_COLUMNS.index(funnel[0])
    assert TEAM_KPI_COLUMNS[start : start + 6] == funnel
    assert [c.rsplit("_", 1)[0] for c in funnel] == ["funnel_ratio"] * 3 + ["funnel_actual_pir"] * 3
