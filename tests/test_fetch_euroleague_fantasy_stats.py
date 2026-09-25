"""Cover the paging, validation and CSV shaping of the EuroLeague Fantasy stats fetcher (HTTP stubbed)."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

import fetch_euroleague_fantasy_stats as fetcher

COLUMNS = ["rank", "name", "position", "team", "fpt", "plus", "pts", "win_1-10"]


def make_player(player_id: int, name: str = "D. Bacon", pts: str = "24", win: str = "-") -> dict:
    return {"id": player_id, "row": ["1", name, "Forward", "DUB", "34.1", "+0.8", pts, win]}


def make_payload(players: list[dict], page: int, last_page: int, total: int, columns: list[str] = COLUMNS) -> dict:
    return {
        "data": {"columns": columns, "players": players},
        "meta": {"current_page": page, "last_page": last_page, "per_page": 2, "total": total},
    }


def stub_pages(monkeypatch: pytest.MonkeyPatch, pages: list[dict]) -> list[int]:
    requested: list[int] = []

    def fake_fetch_page(competition_id: int, stats_type: str, page: int, token: str) -> dict:
        requested.append(page)
        return pages[page - 1]

    monkeypatch.setattr(fetcher, "fetch_page", fake_fetch_page)
    return requested


def test_get_token_missing_names_the_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(fetcher.TOKEN_ENV_VAR, raising=False)
    with pytest.raises(RuntimeError, match=fetcher.TOKEN_ENV_VAR):
        fetcher.get_token()


def test_get_token_strips_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(fetcher.TOKEN_ENV_VAR, "  123|abc\n")
    assert fetcher.get_token() == "123|abc"


def test_to_records_puts_id_first_and_blanks_the_no_value_marker() -> None:
    [record] = fetcher.to_records(COLUMNS, [make_player(7201)])
    assert list(record)[:2] == ["player_id", "rank"]
    assert record["player_id"] == 7201
    assert record["win_1-10"] == ""
    assert record["plus"] == "+0.8"


def test_to_records_rejects_a_row_that_does_not_match_the_columns() -> None:
    with pytest.raises(ValueError, match="cells for"):
        fetcher.to_records(COLUMNS[:-1], [make_player(1)])


def test_fetch_stats_walks_every_page(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        make_payload([make_player(1), make_player(2)], page=1, last_page=2, total=3),
        make_payload([make_player(3)], page=2, last_page=2, total=3),
    ]
    requested = stub_pages(monkeypatch, pages)
    records = fetcher.fetch_stats(49, "avg", "token")
    assert requested == [1, 2]
    assert [r["player_id"] for r in records] == [1, 2, 3]


def test_fetch_stats_rejects_duplicate_ids_across_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        make_payload([make_player(1), make_player(2)], page=1, last_page=2, total=3),
        make_payload([make_player(2)], page=2, last_page=2, total=3),
    ]
    stub_pages(monkeypatch, pages)
    with pytest.raises(ValueError, match="Duplicate player ids"):
        fetcher.fetch_stats(49, "avg", "token")


def test_fetch_stats_rejects_a_short_result(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_pages(monkeypatch, [make_payload([make_player(1)], page=1, last_page=1, total=2)])
    with pytest.raises(ValueError, match="API reports 2"):
        fetcher.fetch_stats(49, "avg", "token")


def test_fetch_stats_rejects_columns_that_change_between_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = [
        make_payload([make_player(1)], page=1, last_page=2, total=2),
        make_payload([make_player(2)], page=2, last_page=2, total=2, columns=[*COLUMNS[:-1], "renamed"]),
    ]
    stub_pages(monkeypatch, pages)
    with pytest.raises(ValueError, match="Column list changed"):
        fetcher.fetch_stats(49, "avg", "token")


def test_build_params_stays_within_the_api_page_limit() -> None:
    params = dict(fetcher.build_params("tot", 3))
    assert params["stats_type"] == "tot"
    assert params["page"] == "3"
    assert int(params["per_page"]) <= 100


def test_write_csv_round_trip(tmp_path: Path) -> None:
    records = fetcher.to_records(COLUMNS, [make_player(1, name="Š. Žižić"), make_player(2)])
    out_path = tmp_path / "nested" / "player_stats_avg.csv"
    fetcher.write_csv(records, out_path)
    with out_path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [r["player_id"] for r in rows] == ["1", "2"]
    assert rows[0]["name"] == "Š. Žižić"
    assert rows[0]["win_1-10"] == ""


def test_write_csv_refuses_to_overwrite_with_nothing(tmp_path: Path) -> None:
    out_path = tmp_path / "player_stats_avg.csv"
    out_path.write_text("keep me")
    with pytest.raises(ValueError, match="No players returned"):
        fetcher.write_csv([], out_path)
    assert out_path.read_text() == "keep me"
