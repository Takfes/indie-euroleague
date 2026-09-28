"""Tests for the Euroleague Advanced API schedule fetcher.

The fixture game is a real row from `GET /Euroleague/schedule?season=2026` (gamecode E2026_7,
PAN vs PRS, 24 Sep 2026), captured live during implementation. No test hits the network; every
call goes through a mocked `urlopen`.
"""

from __future__ import annotations

import json
import sys
import urllib.error
from email.message import Message
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from eupy.fetchers.fetch_euroleague_schedule import fetch_schedule, main, parse_season_year

URLOPEN = "eupy.fetchers.fetch_euroleague_schedule.urllib.request.urlopen"

# "E2026" with Arabic-Indic digits (U+0660 is zero), built by code point so the source stays ASCII.
ARABIC_INDIC_E2026 = "E" + "".join(chr(0x0660 + int(digit)) for digit in "2026")

# Real API field order. Deliberately not alphabetical, so a sorted/dict-reordering bug shows up.
FIELDS = [
    "gameday",
    "round",
    "arenacode",
    "arenaname",
    "arenacapacity",
    "date",
    "startime",
    "endtime",
    "group",
    "game",
    "gamecode",
    "hometeam",
    "homecode",
    "hometv",
    "awayteam",
    "awaycode",
    "awaytv",
    "confirmeddate",
    "confirmedtime",
    "played",
]

GAME: dict[str, Any] = {
    "gameday": 1,
    "round": "RS",
    "arenacode": "AUM",
    "arenaname": "TELEKOM CENTER ATHENS",
    "arenacapacity": "18500",
    "date": "Sep 24, 2026",
    "startime": "20:15",
    "endtime": "23:45",
    "group": "Regular Season",
    "game": "7",
    "gamecode": "E2026_7",
    "hometeam": "PANATHINAIKOS AKTOR ATHENS",
    "homecode": "PAN",
    "hometv": "PAO",
    "awayteam": "PARIS BASKETBALL",
    "awaycode": "PRS",
    "awaytv": "PBB",
    "confirmeddate": "true",
    "confirmedtime": "true",
    "played": "true",
}


def _game(number: int, **overrides: Any) -> dict[str, Any]:
    return {**GAME, "game": str(number), "gamecode": f"E2026_{number}", **overrides}


def _fake_response(body: bytes) -> MagicMock:
    response = MagicMock()
    response.read.return_value = body
    response.__enter__.return_value = response
    return response


def _api_returns(payload: Any) -> MagicMock:
    return _fake_response(json.dumps(payload).encode("utf-8"))


def _run_main(monkeypatch: pytest.MonkeyPatch, out_dir: Path, *extra_args: str) -> None:
    monkeypatch.setattr(sys, "argv", ["fetch_euroleague_schedule.py", "--out-dir", str(out_dir), *extra_args])
    main()


@pytest.mark.parametrize(("code", "year"), [("E2026", 2026), ("E2025", 2025)])
def test_parse_season_year_maps_code_to_api_year(code: str, year: int) -> None:
    assert parse_season_year(code) == year


@pytest.mark.parametrize(
    "code",
    [
        "2026",  # bare year: the API's own format, easy to pass by mistake
        "E26",
        "X2026",
        "e2026",
        "E20266",
        "E2026\n",  # a `$`-anchored regex would accept this
        ARABIC_INDIC_E2026,  # `\d` would accept these digits
        "",
    ],
)
def test_parse_season_year_rejects_malformed_codes(code: str) -> None:
    with pytest.raises(ValueError, match="Invalid season code"):
        parse_season_year(code)


def test_fetch_schedule_rejects_bad_season_before_any_request() -> None:
    with patch(URLOPEN) as mock_urlopen, pytest.raises(ValueError, match="Invalid season code"):
        fetch_schedule("2026")

    mock_urlopen.assert_not_called()


def test_fetch_schedule_requests_the_api_year_with_a_custom_user_agent() -> None:
    """E2026 must query season=2026 -- a wrong mapping would silently fetch another season."""
    with patch(URLOPEN, return_value=_api_returns([GAME])) as mock_urlopen:
        fetch_schedule("E2026")

    sent_request = mock_urlopen.call_args[0][0]
    assert sent_request.full_url == "https://euroleague-advanced-api.eu/Euroleague/schedule?season=2026"
    user_agent = sent_request.get_header("User-agent")
    assert user_agent
    assert "python-urllib" not in user_agent.lower()


def test_main_writes_named_csv_with_api_columns_and_verbatim_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    played = _game(7)
    upcoming = _game(8, gameday=2, date="Sep 25, 2026", played="false", confirmedtime="false")
    with patch(URLOPEN, return_value=_api_returns([played, upcoming])):
        _run_main(monkeypatch, tmp_path)

    lines = (tmp_path / "schedule_E2026.csv").read_text(encoding="utf-8").splitlines()
    assert lines[0] == ",".join(FIELDS)
    # The comma inside the date must be quoted; booleans stay the API's "true"/"false" strings.
    assert lines[1] == (
        '1,RS,AUM,TELEKOM CENTER ATHENS,18500,"Sep 24, 2026",20:15,23:45,Regular Season,7,E2026_7,'
        "PANATHINAIKOS AKTOR ATHENS,PAN,PAO,PARIS BASKETBALL,PRS,PBB,true,true,true"
    )
    assert lines[2].startswith('2,RS,AUM,TELEKOM CENTER ATHENS,18500,"Sep 25, 2026"')
    assert lines[2].endswith(",E2026_8,PANATHINAIKOS AKTOR ATHENS,PAN,PAO,PARIS BASKETBALL,PRS,PBB,true,false,false")
    assert len(lines) == 3


def test_main_creates_missing_output_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # First real run on a fresh checkout: data/raw_data/euroleague_schedule/ doesn't exist yet.
    out_dir = tmp_path / "raw_data" / "euroleague_schedule"
    with patch(URLOPEN, return_value=_api_returns([_game(7)])):
        _run_main(monkeypatch, out_dir)

    assert (out_dir / "schedule_E2026.csv").is_file()


def test_main_rerun_is_byte_identical_and_fully_replaces_a_stale_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_path = tmp_path / "schedule_E2026.csv"
    out_path.write_text("stale,content\n" * 500, encoding="utf-8")  # longer than the fresh file
    payload = [_game(7), _game(8, played="false")]

    with patch(URLOPEN, return_value=_api_returns(payload)):
        _run_main(monkeypatch, tmp_path)
    first = out_path.read_bytes()
    with patch(URLOPEN, return_value=_api_returns(payload)):
        _run_main(monkeypatch, tmp_path)

    assert b"stale" not in first
    assert out_path.read_bytes() == first


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ({"detail": "not a list"}, "JSON array"),
        ([], "zero games"),
        ([_game(7), _game(8, gamecode="E2025_8")], "gamecode 'E2025_8'"),
        ([{**GAME, "gamecode": None}], "gamecode None"),
        ([_game(7), {k: v for k, v in _game(8).items() if k != "played"}], "Row 1"),
        ([_game(7), {**_game(8), "extra": "x"}], "Row 1"),
        ([_game(7), "not an object"], "Row 1"),
    ],
    ids=["non-list", "empty", "wrong-season", "missing-gamecode", "missing-key", "extra-key", "non-object-row"],
)
def test_main_invalid_payload_raises_and_leaves_existing_file_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: Any, match: str
) -> None:
    out_path = tmp_path / "schedule_E2026.csv"
    out_path.write_bytes(b"previous,good\r\nfile,here\r\n")

    with patch(URLOPEN, return_value=_api_returns(payload)), pytest.raises(ValueError, match=match):
        _run_main(monkeypatch, tmp_path)

    assert out_path.read_bytes() == b"previous,good\r\nfile,here\r\n"


def test_main_non_json_body_raises_and_leaves_existing_file_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_path = tmp_path / "schedule_E2026.csv"
    out_path.write_bytes(b"previous,good\r\n")

    with (
        patch(URLOPEN, return_value=_fake_response(b"<html>gateway error</html>")),
        pytest.raises(ValueError, match="not valid UTF-8 JSON"),
    ):
        _run_main(monkeypatch, tmp_path)

    assert out_path.read_bytes() == b"previous,good\r\n"


@pytest.mark.parametrize(
    "error",
    [
        urllib.error.HTTPError("https://example.com", 402, "Payment Required", Message(), None),
        urllib.error.URLError("name resolution failed"),
        TimeoutError("timed out"),
    ],
    ids=["http-error", "network-error", "timeout"],
)
def test_main_request_failure_raises_and_leaves_existing_file_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    out_path = tmp_path / "schedule_E2026.csv"
    out_path.write_bytes(b"previous,good\r\n")

    with patch(URLOPEN, side_effect=error), pytest.raises(RuntimeError, match="Nothing was written"):
        _run_main(monkeypatch, tmp_path)

    assert out_path.read_bytes() == b"previous,good\r\n"


@pytest.mark.parametrize("season", ["2026", "E26", "X2026"])
def test_main_bad_season_exits_with_usage_error_and_makes_no_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], season: str
) -> None:
    with patch(URLOPEN) as mock_urlopen, pytest.raises(SystemExit) as exit_info:
        _run_main(monkeypatch, tmp_path, "--season", season)

    assert exit_info.value.code == 2
    assert "Invalid season code" in capsys.readouterr().err
    mock_urlopen.assert_not_called()
    assert list(tmp_path.iterdir()) == []
