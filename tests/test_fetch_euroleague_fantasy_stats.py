"""Tests for the EuroLeague Fantasy stats fetcher's parsing and pagination logic."""

from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import patch

import pytest

from eupy.fetchers.fetch_euroleague_fantasy_stats import (
    fetch_round_rows,
    get_auth_token,
    matchday_ids_by_round,
    max_round_to_fetch,
    sanitize_column_name,
    write_csv,
)

CONFIG_FIXTURE = {
    "current_competition_id": 49,
    "previous_matchday": {"id": 1528, "number": 1},
    "current_matchday": {"id": 1529, "number": 2},
    "matchdays": [
        {"id": 1528, "number": 1, "display_name": None},
        {"id": 1529, "number": 2, "display_name": None},
        {"id": 1530, "number": 3, "display_name": None},
    ],
}


def test_sanitize_column_name_replaces_hyphens_with_underscores() -> None:
    assert sanitize_column_name("win_1-10") == "win_1_10"
    assert sanitize_column_name("fpt") == "fpt"


def test_matchday_ids_by_round_maps_round_number_to_matchday_id() -> None:
    assert matchday_ids_by_round(CONFIG_FIXTURE) == {1: 1528, 2: 1529, 3: 1530}


def test_max_round_to_fetch_includes_the_in_progress_round() -> None:
    assert max_round_to_fetch(CONFIG_FIXTURE) == 2


def test_get_auth_token_prefers_environment_variable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("EUROLEAGUE_FANTASY_AUTH_TOKEN", "env-token")
    assert get_auth_token(env_path=tmp_path / "unused.env") == "env-token"


def test_get_auth_token_falls_back_to_env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("EUROLEAGUE_FANTASY_AUTH_TOKEN", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text("# comment\nEUROLEAGUE_FANTASY_AUTH_TOKEN=123|abc\n", encoding="utf-8")
    assert get_auth_token(env_path=env_path) == "123|abc"


def test_get_auth_token_raises_clear_error_when_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("EUROLEAGUE_FANTASY_AUTH_TOKEN", raising=False)
    with pytest.raises(ValueError, match="EUROLEAGUE_FANTASY_AUTH_TOKEN is not set"):
        get_auth_token(env_path=tmp_path / "missing.env")


def _page(players: list[dict], current_page: int, last_page: int) -> dict:
    return {
        "data": {"columns": ["rank", "name", "position", "team", "fpt"], "players": players},
        "meta": {"current_page": current_page, "per_page": 2, "total": last_page * 2, "last_page": last_page},
    }


def test_fetch_round_rows_zips_columns_with_row_and_tags_round() -> None:
    single_page = _page(
        players=[
            {"id": 3791, "row": ["1", "C. Jones", "Guard", "PAR", "36.3"]},
            {"id": 7201, "row": ["2", "D. Bacon", "Forward", "DUB", "34.1"]},
        ],
        current_page=1,
        last_page=1,
    )

    with (
        patch("eupy.fetchers.fetch_euroleague_fantasy_stats.fetch_json", return_value=single_page) as mock_fetch,
        patch("eupy.fetchers.fetch_euroleague_fantasy_stats.time.sleep"),
    ):
        rows = fetch_round_rows(49, 1528, "Guard,Forward,Center", round_number=1, token="tok")  # noqa: S106

    assert rows == [
        {
            "rank": "1",
            "name": "C. Jones",
            "position": "Guard",
            "team": "PAR",
            "fpt": "36.3",
            "round": 1,
            "player_id": 3791,
        },
        {
            "rank": "2",
            "name": "D. Bacon",
            "position": "Forward",
            "team": "DUB",
            "fpt": "34.1",
            "round": 1,
            "player_id": 7201,
        },
    ]
    mock_fetch.assert_called_once()


def test_fetch_round_rows_paginates_until_last_page() -> None:
    page1 = _page(players=[{"id": 1, "row": ["1", "A", "Guard", "PAR", "10"]}], current_page=1, last_page=2)
    page2 = _page(players=[{"id": 2, "row": ["2", "B", "Guard", "PAR", "5"]}], current_page=2, last_page=2)

    with (
        patch("eupy.fetchers.fetch_euroleague_fantasy_stats.fetch_json", side_effect=[page1, page2]) as mock_fetch,
        patch("eupy.fetchers.fetch_euroleague_fantasy_stats.time.sleep"),
    ):
        rows = fetch_round_rows(49, 1528, "Guard,Forward,Center", round_number=1, token="tok")  # noqa: S106

    assert [r["player_id"] for r in rows] == [1, 2]
    assert mock_fetch.call_count == 2


def test_write_csv_writes_header_even_when_rows_empty(tmp_path: Path) -> None:
    out_path = tmp_path / "players.csv"

    write_csv([], out_path)

    with out_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames is not None
        assert "round" in reader.fieldnames
        assert "player_id" in reader.fieldnames
        assert list(reader) == []
