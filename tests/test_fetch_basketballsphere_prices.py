"""Tests for the basketballsphere price fetcher's parsing and writing logic."""

from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from eupy.fetchers.fetch_basketballsphere_prices import fetch_html, parse_rows, write_csv

PLAYER_ROW = (
    '<tr data-pos="G" data-club="PAN" data-price="12.5">'
    '<td class="n r">1</td>'
    '<td class="nm"><a href="/player/1">Kostas Sloukas</a></td>'
    '<td class="c">Panathinaikos</td>'
    '<td class="ps">Guard</td>'
    '<td class="n p">12.5</td>'
    "</tr>"
)
HEAD_COACH_ROW = (
    '<tr data-pos="HC" data-club="OLY" data-price="8.0">'
    '<td class="n r">2</td>'
    '<td class="nm">Georgios Bartzokas</td>'
    '<td class="c">Olympiacos</td>'
    '<td class="ps">Head Coach</td>'
    '<td class="n p">8.0</td>'
    "</tr>"
)


def test_parse_rows_extracts_player_and_head_coach_rows() -> None:
    rows = parse_rows(PLAYER_ROW + HEAD_COACH_ROW)

    assert rows == [
        {
            "rank": "1",
            "name": "Kostas Sloukas",
            "club": "Panathinaikos",
            "position": "G",
            "price": "12.5",
            "role": "player",
        },
        {
            "rank": "2",
            "name": "Georgios Bartzokas",
            "club": "Olympiacos",
            "position": "HC",
            "price": "8.0",
            "role": "head_coach",
        },
    ]


def test_parse_rows_returns_empty_list_when_no_rows_match() -> None:
    assert parse_rows("<html><body>no table here</body></html>") == []


def test_parse_rows_unescapes_html_entities_in_name_and_club() -> None:
    row = (
        '<tr data-pos="F" data-club="RMB" data-price="10.0">'
        '<td class="n r">3</td>'
        '<td class="nm">D&#39;Angelo Russell</td>'
        '<td class="c">Real Madrid &amp; Co</td>'
        '<td class="ps">Forward</td>'
        '<td class="n p">10.0</td>'
        "</tr>"
    )

    [parsed] = parse_rows(row)

    assert parsed["name"] == "D'Angelo Russell"
    assert parsed["club"] == "Real Madrid & Co"


def test_write_csv_raises_on_empty_rows(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="No rows parsed"):
        write_csv([], tmp_path / "out.csv")


def test_write_csv_creates_parent_dirs_and_writes_expected_rows(tmp_path: Path) -> None:
    out_path = tmp_path / "nested" / "prices.csv"
    rows = [
        {
            "rank": "1",
            "name": "Kostas Sloukas",
            "club": "Panathinaikos",
            "position": "G",
            "price": "12.5",
            "role": "player",
        }
    ]

    write_csv(rows, out_path)

    with out_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == ["rank", "name", "club", "position", "price", "role"]
        assert list(reader) == rows


def test_fetch_html_sends_desktop_user_agent() -> None:
    """The site 403s bare urllib user agents, so the request must spoof a browser."""
    fake_response = Mock()
    fake_response.read.return_value = b"<html></html>"
    fake_response.__enter__ = Mock(return_value=fake_response)
    fake_response.__exit__ = Mock(return_value=False)

    with patch(
        "eupy.fetchers.fetch_basketballsphere_prices.urllib.request.urlopen", return_value=fake_response
    ) as mock_urlopen:
        fetch_html("https://example.com")

    sent_request = mock_urlopen.call_args[0][0]
    assert "Mozilla" in sent_request.get_header("User-agent")
