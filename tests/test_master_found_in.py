"""Tests for the `found_in` provenance columns and Column Guide check of the master table."""

from __future__ import annotations

import pandas as pd
import pytest

from build_player_master_table import build_column_guide, check_column_guide, found_in_columns
from player_master_column_guide import DERIVED, FOUND_IN_SOURCES, GUIDE, KAGGLE_KPIS


def _flags(*rows: tuple[bool, bool, bool, bool, bool]) -> pd.DataFrame:
    return pd.DataFrame(list(rows), columns=list(FOUND_IN_SOURCES))


def test_found_in_labels_and_counts() -> None:
    result = found_in_columns(
        _flags(
            (True, False, False, False, True),
            (True, True, True, True, True),
            (False, False, False, False, True),
            (False, True, False, True, False),
        )
    )
    assert result["found_in"].tolist() == ["dunk, elf", "dunk, bnadv, bnoo, kag, elf", "elf", "bnadv, kag"]
    assert result["found_in_count"].tolist() == [2, 5, 1, 2]
    assert pd.api.types.is_integer_dtype(result["found_in_count"])


@pytest.fixture
def guide_inputs() -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    sources = {"Dunkest": pd.DataFrame(columns=["id", "gp"])}
    master = pd.DataFrame(columns=["found_in", "found_in_count", "pir_per_credit", "pir_per_min_per_credit"])
    return sources, master


def test_guide_accepts_source_columns_plus_derived(guide_inputs: tuple[dict[str, pd.DataFrame], pd.DataFrame]) -> None:
    sources, master = guide_inputs
    guide = build_column_guide(sources)
    check_column_guide(guide, sources, master)
    assert set(guide.loc[guide["Source dataset"] == DERIVED, "Column name"]) == {
        "found_in",
        "found_in_count",
        "pir_per_credit",
        "pir_per_min_per_credit",
    }


def test_guide_rejects_missing_duplicate_or_extra_source_column(
    guide_inputs: tuple[dict[str, pd.DataFrame], pd.DataFrame],
) -> None:
    sources, master = guide_inputs
    guide = build_column_guide(sources)
    extra = pd.DataFrame([("Dunkest", "bogus", "x")], columns=guide.columns)
    for bad in (guide.iloc[1:], pd.concat([guide, guide.iloc[:1]]), pd.concat([guide, extra])):
        with pytest.raises(ValueError, match="exactly once"):
            check_column_guide(bad, sources, master)


def test_guide_rejects_derived_column_missing_from_master(
    guide_inputs: tuple[dict[str, pd.DataFrame], pd.DataFrame],
) -> None:
    sources, _ = guide_inputs
    with pytest.raises(ValueError, match="Master sheet"):
        check_column_guide(build_column_guide(sources), sources, pd.DataFrame(columns=["found_in"]))


def test_guide_lists_every_kaggle_kpi_column_once_under_its_own_label() -> None:
    kpis = pd.DataFrame(columns=list(GUIDE[KAGGLE_KPIS]))
    sources = {KAGGLE_KPIS: kpis}
    guide = build_column_guide(sources)
    master = pd.DataFrame(columns=["found_in", "found_in_count", "pir_per_credit", "pir_per_min_per_credit"])
    check_column_guide(guide, sources, master)
    assert guide.loc[guide["Source dataset"] == KAGGLE_KPIS, "Column name"].tolist() == list(kpis.columns)
    assert "(undocumented" not in " ".join(guide["Explanation"])
