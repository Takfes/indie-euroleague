"""Tests for the increment-2 Kaggle identity-override mechanism (Step 3b adjudication)."""

from __future__ import annotations

import pandas as pd
import pytest

import build_player_master_table as master_build
from build_player_master_table import KAGGLE_PLAYER_IDENTITY_OVERRIDES, apply_kaggle_identity_overrides


def test_override_forces_the_listed_kaggle_row_onto_the_target_name_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(master_build, "KAGGLE_PLAYER_IDENTITY_OVERRIDES", {"P999999": "some existing player"})
    player_id = pd.Series(["P999999", "P000001"])
    resolved = pd.Series(["some new player", "another player"])

    result = apply_kaggle_identity_overrides(player_id, resolved)

    assert result.tolist() == ["some existing player", "another player"]


def test_rows_with_no_override_entry_pass_through_unchanged() -> None:
    player_id = pd.Series(["P007705", "P012185"])
    resolved = pd.Series(["abramo canka", "dominykas daubaris"])

    result = apply_kaggle_identity_overrides(player_id, resolved)

    assert result.tolist() == resolved.tolist()


def test_2026_09_adjudication_records_no_confident_merges() -> None:
    """Increment-2 adjudication (see the build report): the 10 Kaggle rows left unmatched this
    run (0 games played, no fuzzy candidate above threshold on their team roster) are confirmed
    genuinely absent from the other 4 sources, not a matching gap -- so no override is recorded.
    A future entry here is a deliberate captain-reviewed decision, not something the build infers.
    """
    assert KAGGLE_PLAYER_IDENTITY_OVERRIDES == {}
