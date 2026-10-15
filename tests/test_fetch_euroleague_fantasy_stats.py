"""Tests for the EuroLeague Fantasy stats fetcher's parsing and pagination logic."""

from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import patch

import pytest

from eupy.fetchers.fetch_euroleague_fantasy_stats import (
    ALL_ROUNDS,
    HEAD_COACH_CSV_FIELDNAMES,
    PLAYER_CSV_FIELDNAMES,
    REQUEST_DELAY_SECONDS,
    ROUNDS_DELAY_SECONDS,
    drop_duplicate_players,
    fetch_round_rows,
    get_auth_token,
    matchday_ids_by_round,
    max_round_to_fetch,
    parse_rounds,
    run,
    sanitize_column_name,
    write_csv,
)
from eupy.quality import DataQualityError, validate

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

    write_csv([], out_path, PLAYER_CSV_FIELDNAMES)

    with out_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames is not None
        assert "round" in reader.fieldnames
        assert "player_id" in reader.fieldnames
        assert list(reader) == []


def test_write_csv_players_drops_head_coach_only_columns(tmp_path: Path) -> None:
    out_path = tmp_path / "players.csv"
    row = {"round": 1, "player_id": 3791, "name": "C. Jones", "pts": "20", "win_1_10": "-"}

    write_csv([row], out_path, PLAYER_CSV_FIELDNAMES)

    with out_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert "pts" in reader.fieldnames
        assert "win_1_10" not in reader.fieldnames


def test_write_csv_head_coaches_drops_player_only_columns(tmp_path: Path) -> None:
    out_path = tmp_path / "head_coaches.csv"
    row = {"round": 1, "player_id": 99, "name": "Coach X", "win_1_10": "3", "pts": "-"}

    write_csv([row], out_path, HEAD_COACH_CSV_FIELDNAMES)

    with out_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert "win_1_10" in reader.fieldnames
        assert "pts" not in reader.fieldnames


def _player(round_number: int, player_id: str, rank: int, name: str = "A. Kone") -> dict[str, object]:
    return {"round": round_number, "player_id": player_id, "rank": rank, "name": name}


def test_duplicate_players_fail_validation_until_dropped() -> None:
    rows = [_player(1, "12517", 303), _player(1, "7", 5, "B. Other"), _player(1, "12517", 291)]
    with pytest.raises(DataQualityError, match="1 duplicate key"):
        validate(rows, ["round", "player_id"], "fantasy_stats/players")
    validate(drop_duplicate_players(rows), ["round", "player_id"], "fantasy_stats/players")


def test_drop_duplicate_players_keeps_lowest_rank_in_original_order() -> None:
    rows = [
        _player(1, "12517", 303),
        _player(1, "7", 5, "B. Other"),
        _player(1, "12517", 291),
        _player(2, "12517", 278),
    ]
    kept = drop_duplicate_players(rows)
    assert kept == [_player(1, "7", 5, "B. Other"), _player(1, "12517", 291), _player(2, "12517", 278)]
    assert drop_duplicate_players(kept) == kept  # nothing left to drop


def test_run_drops_duplicates_validates_and_writes(tmp_path: Path) -> None:
    config = {
        "current_competition_id": 49,
        "previous_matchday": {"id": 1528, "number": 1},
        "current_matchday": {"id": 1528, "number": 1},
        "matchdays": [{"id": 1528, "number": 1, "display_name": None}],
    }
    player_rows = [
        {**_player(1, "12517", 303), "position": "Guard"},
        {**_player(1, "12517", 291), "position": "Guard"},
    ]
    coach_rows = [{**_player(1, "900", 1, "Some Coach"), "position": "Head Coach"}]
    with (
        patch("eupy.fetchers.fetch_euroleague_fantasy_stats.fetch_config", return_value=config),
        patch(
            "eupy.fetchers.fetch_euroleague_fantasy_stats.fetch_round_rows",
            side_effect=[player_rows, coach_rows],
        ),
    ):
        run(tmp_path, "token")

    written = list(csv.DictReader((tmp_path / "players.csv").open(encoding="utf-8")))
    assert [(r["player_id"], r["rank"]) for r in written] == [("12517", "291")]
    assert (tmp_path / "head_coaches.csv").exists()


def test_parse_rounds_expands_ranges_dedupes_and_sorts() -> None:
    assert parse_rounds("3,1-2,2", max_round=4) == [1, 2, 3]


@pytest.mark.parametrize("spec", ["0", "5", "3-2", "1-9"])
def test_parse_rounds_rejects_rounds_outside_the_played_range(spec: str) -> None:
    with pytest.raises(ValueError, match="outside 1-4"):
        parse_rounds(spec, max_round=4)


def test_parse_rounds_rejects_unreadable_selection() -> None:
    with pytest.raises(ValueError, match="neither a round number"):
        parse_rounds("1,abc", max_round=4)


def test_fetch_round_rows_without_matchday_sends_no_filter_and_uses_the_delay() -> None:
    single_page = _page(players=[{"id": 1, "row": ["1", "A", "Guard", "PAR", "10"]}], current_page=1, last_page=1)
    with (
        patch("eupy.fetchers.fetch_euroleague_fantasy_stats.fetch_json", return_value=single_page) as mock_fetch,
        patch("eupy.fetchers.fetch_euroleague_fantasy_stats.time.sleep") as mock_sleep,
    ):
        rows = fetch_round_rows(49, None, "Guard,Forward,Center", ALL_ROUNDS, "tok", delay=0.7)

    assert rows[0]["round"] == ALL_ROUNDS
    assert "matchdays" not in mock_fetch.call_args.args[0]
    mock_sleep.assert_called_once_with(0.7)


def _run_with_mocked_fetch(tmp_path: Path, **kwargs: object) -> list:
    config = {**CONFIG_FIXTURE, "current_matchday": {"id": 1530, "number": 3}}

    def fake_rows(competition_id, matchday_id, positions, round_number, token, delay, stats_type):
        name = "Some Coach" if positions == "Head Coach" else "A. Kone"
        return [{**_player(round_number, "900" if positions == "Head Coach" else "1", 1, name), "position": positions}]

    with (
        patch("eupy.fetchers.fetch_euroleague_fantasy_stats.fetch_config", return_value=config),
        patch("eupy.fetchers.fetch_euroleague_fantasy_stats.fetch_round_rows", side_effect=fake_rows) as mock_rows,
    ):
        run(tmp_path, "token", **kwargs)
    return mock_rows.call_args_list


def test_run_defaults_to_one_total_fetch_per_role_at_the_minimum_delay(tmp_path: Path) -> None:
    calls = _run_with_mocked_fetch(tmp_path)
    # (competition_id, matchday_id, positions, round_number, token, delay)
    assert [(c.args[1], c.args[3], c.args[5]) for c in calls] == [(None, ALL_ROUNDS, REQUEST_DELAY_SECONDS)] * 2


def test_run_with_selected_rounds_fetches_each_at_the_longer_delay(tmp_path: Path) -> None:
    calls = _run_with_mocked_fetch(tmp_path, rounds="1,3")
    assert [(c.args[1], c.args[3], c.args[5]) for c in calls] == [
        (1528, 1, ROUNDS_DELAY_SECONDS),
        (1528, 1, ROUNDS_DELAY_SECONDS),
        (1530, 3, ROUNDS_DELAY_SECONDS),
        (1530, 3, ROUNDS_DELAY_SECONDS),
    ]


def test_run_delay_override_wins_in_both_modes(tmp_path: Path) -> None:
    assert {c.args[5] for c in _run_with_mocked_fetch(tmp_path, delay=2.5)} == {2.5}
    assert {c.args[5] for c in _run_with_mocked_fetch(tmp_path, rounds="2", delay=0.1)} == {0.1}


def test_fetch_round_rows_sends_the_requested_stats_type() -> None:
    single_page = _page(players=[{"id": 1, "row": ["1", "A", "Guard", "PAR", "10"]}], current_page=1, last_page=1)
    with (
        patch("eupy.fetchers.fetch_euroleague_fantasy_stats.fetch_json", return_value=single_page) as mock_fetch,
        patch("eupy.fetchers.fetch_euroleague_fantasy_stats.time.sleep"),
    ):
        fetch_round_rows(49, None, "Guard,Forward,Center", ALL_ROUNDS, "tok", stats_type="avg")
        fetch_round_rows(49, 1528, "Guard,Forward,Center", 1, "tok")

    assert "stats_type=avg" in mock_fetch.call_args_list[0].args[0]
    assert "stats_type=tot" in mock_fetch.call_args_list[1].args[0]  # default stays the sum


def test_run_all_rounds_defaults_to_the_average_and_tot_can_be_chosen(tmp_path: Path) -> None:
    assert {c.kwargs["stats_type"] for c in _run_with_mocked_fetch(tmp_path)} == {"avg"}
    assert {c.kwargs["stats_type"] for c in _run_with_mocked_fetch(tmp_path, stats_type="tot")} == {"tot"}


def test_run_per_round_fetch_is_always_the_sum(tmp_path: Path) -> None:
    assert {c.kwargs["stats_type"] for c in _run_with_mocked_fetch(tmp_path, rounds="1-2")} == {"tot"}
    assert {c.kwargs["stats_type"] for c in _run_with_mocked_fetch(tmp_path, rounds="1", stats_type="tot")} == {"tot"}


def test_run_rejects_the_average_with_selected_rounds(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="only applies to the all-rounds fetch"):
        _run_with_mocked_fetch(tmp_path, rounds="1-2", stats_type="avg")


def test_run_rejects_an_unknown_stats_type(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown stats type 'median'"):
        _run_with_mocked_fetch(tmp_path, stats_type="median")
