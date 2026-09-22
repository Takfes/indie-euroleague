"""Tests for the position fallback and Basketnews 5-position -> G/F/C bucket normalization."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from build_player_master_table import normalize_bn_positions, resolve_position


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PG", "G"),
        ("SG", "G"),
        ("SF", "F"),
        ("PF", "F"),
        ("C", "C"),
    ],
)
def test_each_raw_bn_code_maps_to_its_bucket(raw: str, expected: str) -> None:
    assert normalize_bn_positions(pd.Series([raw])).tolist() == [expected]


def test_multi_position_combo_takes_the_first_listed_token() -> None:
    result = normalize_bn_positions(pd.Series(["SF,PF", "PG,SG", "SG,PG", "C,PF"]))
    assert result.tolist() == ["F", "G", "G", "C"]


def test_already_bucketed_value_passes_through_unchanged() -> None:
    # "C" is common to both the 5-position and 3-bucket schemes: no real transform needed.
    assert normalize_bn_positions(pd.Series(["C"])).tolist() == ["C"]


def test_blank_or_nan_stays_blank() -> None:
    result = normalize_bn_positions(pd.Series([np.nan, None]))
    assert result.isna().tolist() == [True, True]


def test_unrecognised_code_raises_and_names_the_fix() -> None:
    with pytest.raises(ValueError, match=r"BN_POSITION_TO_BUCKET: \['SC'\]"):
        normalize_bn_positions(pd.Series(["SC"]))


def test_resolve_position_prefers_dunkest_then_basketnews_then_price() -> None:
    dunk = pd.Series(["G", np.nan, np.nan, np.nan])
    bn = pd.Series(["F", "F", np.nan, np.nan])
    price = pd.Series(["C", "C", "C", np.nan])
    result = resolve_position(dunk, bn, price)
    assert result.tolist()[:3] == ["G", "F", "C"]
    assert pd.isna(result.iloc[3])
