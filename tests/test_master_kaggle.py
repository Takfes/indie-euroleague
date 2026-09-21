"""Tests for the Kaggle KPI part of the player master build: team crosswalk, value KPIs, missing input."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import build_player_master_table as master_build
from build_player_master_table import (
    KAGGLE_TEAM_CROSSWALK,
    add_value_kpis,
    load_kaggle_kpis,
    map_kaggle_teams,
    read_sources,
)


def test_map_kaggle_teams_maps_codes_to_master_names() -> None:
    result = map_kaggle_teams(pd.Series(["MAD", "HTA", "MAD"]))
    assert result.tolist() == ["Real Madrid", "Hapoel IBI Tel Aviv", "Real Madrid"]


def test_map_kaggle_teams_stops_on_unmapped_code_and_says_how_to_fix() -> None:
    with pytest.raises(ValueError, match=r"not in KAGGLE_TEAM_CROSSWALK: \['XXX', 'YYY'\].*Add an entry"):
        map_kaggle_teams(pd.Series(["MAD", "YYY", "XXX"]))


def test_crosswalk_has_the_twenty_season_codes() -> None:
    assert len(KAGGLE_TEAM_CROSSWALK) == 20
    assert len(set(KAGGLE_TEAM_CROSSWALK.values())) == 20


def test_load_kaggle_kpis_prefixes_columns_and_adds_match_helpers() -> None:
    kpis = pd.DataFrame({
        "player_id": ["P1"],
        "player_name": ["Wade Baldwin IV"],
        "team_id": ["ULK"],
        "pir_avg": [16.4],
    })
    out = load_kaggle_kpis(kpis)
    assert list(out.columns) == [
        "kag_player_id",
        "kag_player_name",
        "kag_team_id",
        "kag_pir_avg",
        "name_key",
        "kag_team_name",
    ]
    assert out.loc[0, "name_key"] == "wade baldwin"
    assert out.loc[0, "kag_team_name"] == "Fenerbahce Beko Istanbul"


def test_load_kaggle_kpis_fails_on_unmapped_team_code() -> None:
    kpis = pd.DataFrame({"player_id": ["P1"], "player_name": ["A B"], "team_id": ["QQQ"]})
    with pytest.raises(ValueError, match="QQQ"):
        load_kaggle_kpis(kpis)


def test_value_kpis_divide_by_price_and_stay_blank_without_one() -> None:
    frame = pd.DataFrame({
        "price": [10.0, 0.0, np.nan, 8.0, 5.0],
        "kag_pir_avg_recent": [15.0, 15.0, 15.0, np.nan, 12.0],
        "kag_pir_per_min": [0.6, 0.6, 0.6, 0.5, np.nan],
    })
    out = add_value_kpis(frame)
    assert out["pir_per_credit"].tolist()[:3] == [
        1.5,
        pytest.approx(float("nan"), nan_ok=True),
        pytest.approx(float("nan"), nan_ok=True),
    ]
    assert out["pir_per_credit"].isna().tolist() == [False, True, True, True, False]
    assert out["pir_per_min_per_credit"].isna().tolist() == [False, True, True, False, True]
    assert out.loc[0, "pir_per_min_per_credit"] == pytest.approx(0.06)
    assert out.loc[3, "pir_per_min_per_credit"] == pytest.approx(0.0625)
    assert out.loc[4, "pir_per_credit"] == pytest.approx(2.4)
    assert "pir_per_credit" not in frame.columns  # input untouched


def test_read_sources_stops_with_the_commands_that_build_the_kpi_workbook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(master_build, "KAGGLE_KPIS_PATH", tmp_path / "data/curated/player_kpis.xlsx")
    monkeypatch.setattr(master_build, "REPO_ROOT", tmp_path)
    with pytest.raises(FileNotFoundError) as error:
        read_sources()
    message = str(error.value)
    assert "data/curated/player_kpis.xlsx" in message
    assert "uv run python src/build_player_kpis.py" in message
    assert "uv run python src/build_game_player_stats.py" in message
