"""Tests for the `found_in` provenance columns and the two Column Guide sheets of the master workbook."""

from __future__ import annotations

import pandas as pd
import pytest

from build_player_master_table import (
    build_column_guide,
    build_source_column_guide,
    check_column_guide,
    check_source_column_guide,
    found_in_columns,
)
from player_master_column_guide import (
    BN_ONOFF,
    DERIVED,
    DUNKEST,
    FOUND_IN_SOURCES,
    GUIDE,
    KAGGLE_KPIS,
    UNDOCUMENTED,
    describe_master_column,
)


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


def test_source_guide_lists_every_source_column_once_and_rejects_drift() -> None:
    sources = {"Dunkest": pd.DataFrame(columns=["id", "gp"])}
    guide = build_source_column_guide(sources)
    check_source_column_guide(guide, sources)
    assert guide["Column name"].tolist() == ["id", "gp"]
    extra = pd.DataFrame([("Dunkest", "bogus", "x")], columns=guide.columns)
    for bad in (guide.iloc[1:], pd.concat([guide, guide.iloc[:1]]), pd.concat([guide, extra])):
        with pytest.raises(ValueError, match="exactly once"):
            check_source_column_guide(bad, sources)


def test_source_guide_lists_every_kaggle_kpi_column_once_under_its_own_label() -> None:
    kpis = pd.DataFrame(columns=list(GUIDE[KAGGLE_KPIS]))
    sources = {KAGGLE_KPIS: kpis}
    guide = build_source_column_guide(sources)
    check_source_column_guide(guide, sources)
    assert guide["Column name"].tolist() == list(kpis.columns)
    assert "(undocumented" not in " ".join(guide["Explanation"])


def test_master_guide_follows_master_order_with_master_names_and_source_labels() -> None:
    master = pd.DataFrame(
        columns=[
            "player_name",
            "kag_pir_sd",
            "dunk_tpa",
            "dunk_fgm_tot",
            "bnoo_offensive_rating_lineup_tot",
            "bnoo_3p_attempted_lineup_off",
            "price",
            "price_rank",
            "season",
            "kag_minutes_pct",
            "mystery_column",
        ]
    )
    guide = build_column_guide(master)
    check_column_guide(guide, master)
    assert guide["Column name"].tolist() == list(master.columns)
    label = dict(zip(guide["Column name"], guide["Source dataset"], strict=True))
    text = dict(zip(guide["Column name"], guide["Explanation"], strict=True))
    assert (label["player_name"], label["kag_minutes_pct"]) == (DERIVED, DERIVED)  # derived wins over the prefix
    assert (label["kag_pir_sd"], label["dunk_tpa"], label["dunk_fgm_tot"]) == (KAGGLE_KPIS, DUNKEST, DUNKEST)
    assert (label["price"], label["price_rank"], label["season"]) == ("Fantasy Prices", "Fantasy Prices", "BN Advanced")
    assert text["price_rank"] == GUIDE["Fantasy Prices"]["rank"]
    assert label["bnoo_offensive_rating_lineup_tot"] == BN_ONOFF
    assert text["bnoo_3p_attempted_lineup_off"].endswith("with player on court (offensive view)")
    assert text["mystery_column"] == UNDOCUMENTED
    # A kept column names the duplicates that were dropped in its favour.
    assert (
        "same stat as bnadv_3p_attempted, dropped from the Master; still on the BN Advanced sheet" in text["dunk_tpa"]
    )
    assert describe_master_column("dunk_fgm_tot") == (DUNKEST, GUIDE[DUNKEST]["fgm_tot"])


def test_master_guide_rejects_a_missing_or_reordered_column() -> None:
    master = pd.DataFrame(columns=["player_name", "found_in", "price"])
    guide = build_column_guide(master)
    for bad in (guide.iloc[1:], guide.iloc[::-1], pd.concat([guide, guide.iloc[:1]])):
        with pytest.raises(ValueError, match="Master column order"):
            check_column_guide(bad, master)
