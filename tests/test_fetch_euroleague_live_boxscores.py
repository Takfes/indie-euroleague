"""Tests for the EuroLeague live-API box score fetcher's mapping and state logic.

Fixtures below are trimmed from real `Header`/`BoxScore` responses for
gamecode=1, seasoncode=E2026 (RED vs ZAL, 24/09/2026), captured live during
implementation. No test hits the network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock, patch

import pytest

from eupy.fetchers.fetch_euroleague_live_boxscores import (
    _assert_season_matches,
    build_game_rows,
    is_game_complete,
)
from eupy.fetchers.live_euroleague import fetch_json, load_state, save_state

HEADER: dict[str, Any] = {
    "Live": False,
    "Round": "1",
    "Date": "24/09/2026",
    "Phase": "REGULAR SEASON",
    "CodeTeamA": "RED",
    "CodeTeamB": "ZAL",
    "pcom": "E2026     ",
}

RED_PLAYER: dict[str, Any] = {
    "Player_ID": "P011157   ",
    "IsStarter": 1,
    "IsPlaying": 0,
    "Team": "RED",
    "Dorsal": "0",
    "Player": "MOTLEY, JOHNATHAN",
    "Minutes": "14:08",
    "Points": 2,
    "FieldGoalsMade2": 1,
    "FieldGoalsAttempted2": 5,
    "FieldGoalsMade3": 0,
    "FieldGoalsAttempted3": 2,
    "FreeThrowsMade": 0,
    "FreeThrowsAttempted": 0,
    "OffensiveRebounds": 0,
    "DefensiveRebounds": 0,
    "TotalRebounds": 0,
    "Assistances": 2,
    "Steals": 0,
    "Turnovers": 6,
    "BlocksFavour": 0,
    "BlocksAgainst": 0,
    "FoulsCommited": 5,
    "FoulsReceived": 0,
    "Valuation": -13,
    "Plusminus": -19,
}

ZAL_PLAYER: dict[str, Any] = {
    "Player_ID": "P010123   ",
    "IsStarter": 0,
    "IsPlaying": 1,
    "Team": "ZAL",
    "Dorsal": "5",
    "Player": "BROWN, MARCUS",
    "Minutes": "23:13",
    "Points": 9,
    "FieldGoalsMade2": 3,
    "FieldGoalsAttempted2": 6,
    "FieldGoalsMade3": 1,
    "FieldGoalsAttempted3": 2,
    "FreeThrowsMade": 2,
    "FreeThrowsAttempted": 2,
    "OffensiveRebounds": 1,
    "DefensiveRebounds": 1,
    "TotalRebounds": 2,
    "Assistances": 3,
    "Steals": 1,
    "Turnovers": 0,
    "BlocksFavour": 0,
    "BlocksAgainst": 0,
    "FoulsCommited": 1,
    "FoulsReceived": 1,
    "Valuation": 15,
    "Plusminus": 3,
}

RED_TOTR: dict[str, Any] = {
    "Minutes": "200:00",
    "Points": 77,
    "FieldGoalsMade2": 17,
    "FieldGoalsAttempted2": 44,
    "FieldGoalsMade3": 10,
    "FieldGoalsAttempted3": 29,
    "FreeThrowsMade": 13,
    "FreeThrowsAttempted": 19,
    "OffensiveRebounds": 15,
    "DefensiveRebounds": 24,
    "TotalRebounds": 39,
    "Assistances": 21,
    "Steals": 7,
    "Turnovers": 14,
    "BlocksFavour": 3,
    "BlocksAgainst": 6,
    "FoulsCommited": 26,
    "FoulsReceived": 22,
    "Valuation": 71,
}

ZAL_TOTR: dict[str, Any] = {**RED_TOTR, "Points": 83, "Valuation": 104}

BOXSCORE: dict[str, Any] = {
    "Live": False,
    "Stats": [
        {
            "Team": "CRVENA ZVEZDA MERIDIANBET BELGRADE",
            "Coach": "NAVARRO, IBON",
            "PlayersStats": [RED_PLAYER],
            "totr": RED_TOTR,
        },
        {
            "Team": "ZALGIRIS KAUNAS",
            "Coach": "MASIULIS, TOMAS",
            "PlayersStats": [ZAL_PLAYER],
            "totr": ZAL_TOTR,
        },
    ],
}


def test_is_game_complete_true_for_finished_game() -> None:
    assert is_game_complete(HEADER, BOXSCORE) is True


def test_is_game_complete_false_when_header_missing() -> None:
    assert is_game_complete(None, BOXSCORE) is False


def test_is_game_complete_false_when_boxscore_missing() -> None:
    assert is_game_complete(HEADER, None) is False


def test_is_game_complete_false_when_game_is_live() -> None:
    live_header = {**HEADER, "Live": True}
    assert is_game_complete(live_header, BOXSCORE) is False


def test_is_game_complete_false_when_a_teams_players_stats_is_empty() -> None:
    """A scheduled-but-not-played game is expected to look like this (unverified live --
    see spec's "Could not verify empirically" note), so the check must reject it."""
    incomplete_boxscore = {
        "Live": False,
        "Stats": [
            {"Team": "CRVENA ZVEZDA MERIDIANBET BELGRADE", "PlayersStats": [], "totr": RED_TOTR},
            {"Team": "ZALGIRIS KAUNAS", "PlayersStats": [ZAL_PLAYER], "totr": ZAL_TOTR},
        ],
    }
    assert is_game_complete(HEADER, incomplete_boxscore) is False


def test_build_game_rows_maps_player_fields() -> None:
    rows = build_game_rows(HEADER, BOXSCORE, season="E2026", gamecode=1)

    red_row = rows[0]
    assert red_row["game_player_id"] == "E2026_1_P011157"
    assert red_row["game_id"] == "E2026_1"
    assert red_row["game"] == "RED-ZAL"
    assert red_row["round"] == "1"
    assert red_row["phase"] == "REGULAR SEASON"
    assert red_row["season_code"] == "E2026"
    assert red_row["player_id"] == "P011157"
    assert red_row["team_id"] == "RED"
    assert red_row["dorsal"] == "0"
    assert red_row["player"] == "MOTLEY, JOHNATHAN"
    assert red_row["minutes"] == "14:08"
    assert red_row["points"] == 2
    assert red_row["two_points_made"] == 1
    assert red_row["two_points_attempted"] == 5
    assert red_row["three_points_made"] == 0
    assert red_row["blocks_favour"] == 0
    assert red_row["fouls_committed"] == 5
    assert red_row["valuation"] == -13
    assert red_row["plus_minus"] == -19


def test_build_game_rows_synthesizes_team_total_row_per_team() -> None:
    rows = build_game_rows(HEADER, BOXSCORE, season="E2026", gamecode=1)

    # 1 player + 1 TOTAL row for RED, then 1 player + 1 TOTAL row for ZAL.
    assert len(rows) == 4
    red_total, zal_total = rows[1], rows[3]

    assert red_total["dorsal"] == "TOTAL"
    assert red_total["player"] == "CRVENA ZVEZDA MERIDIANBET BELGRADE"
    assert red_total["player_id"] == "RED"
    assert red_total["team_id"] == "RED"
    assert red_total["game_player_id"] == "E2026_1_RED"
    assert red_total["is_starter"] == 0
    assert red_total["is_playing"] == 1
    assert red_total["plus_minus"] == 0
    assert red_total["points"] == 77
    assert red_total["total_rebounds"] == 39

    assert zal_total["player_id"] == "ZAL"
    assert zal_total["points"] == 83


def test_build_game_rows_row_order_matches_historical_csv() -> None:
    """Team A's players+TOTAL, then team B's players+TOTAL."""
    rows = build_game_rows(HEADER, BOXSCORE, season="E2026", gamecode=1)
    team_ids = [row["team_id"] for row in rows]
    dorsals = [row["dorsal"] for row in rows]
    assert team_ids == ["RED", "RED", "ZAL", "ZAL"]
    assert dorsals == ["0", "TOTAL", "5", "TOTAL"]


def test_fetch_json_returns_none_for_empty_body() -> None:
    fake_response = Mock()
    fake_response.read.return_value = b""
    fake_response.__enter__ = Mock(return_value=fake_response)
    fake_response.__exit__ = Mock(return_value=False)

    with patch("eupy.fetchers.live_euroleague.urllib.request.urlopen", return_value=fake_response):
        assert fetch_json("https://example.com") is None


def test_fetch_json_sends_non_default_user_agent() -> None:
    """live.euroleague.net 403s urllib's default User-Agent -- verified live during
    implementation -- so every request must send a custom one."""
    fake_response = Mock()
    fake_response.read.return_value = b'{"ok": true}'
    fake_response.__enter__ = Mock(return_value=fake_response)
    fake_response.__exit__ = Mock(return_value=False)

    with patch("eupy.fetchers.live_euroleague.urllib.request.urlopen", return_value=fake_response) as mock_urlopen:
        fetch_json("https://example.com")

    sent_request = mock_urlopen.call_args[0][0]
    user_agent = sent_request.get_header("User-agent")
    assert user_agent
    assert "python-urllib" not in user_agent.lower()


def test_load_state_returns_empty_dict_when_file_missing(tmp_path: Path) -> None:
    assert load_state(tmp_path / "_state.json") == {}


def test_save_state_then_load_state_round_trip(tmp_path: Path) -> None:
    state_path = tmp_path / "nested" / "_state.json"
    state = {"E2026": {"last_ingested_gamecode": 10, "last_checked_at": "2026-09-27T12:00:00Z"}}

    save_state(state, state_path)

    assert load_state(state_path) == state
    # Written as plain, human-inspectable JSON (not a single-line blob).
    assert "\n" in state_path.read_text(encoding="utf-8")


def test_save_state_overwrites_previous_content(tmp_path: Path) -> None:
    state_path = tmp_path / "_state.json"
    save_state({"E2025": {"last_ingested_gamecode": 300}}, state_path)

    save_state({"E2026": {"last_ingested_gamecode": 1}}, state_path)

    assert json.loads(state_path.read_text(encoding="utf-8")) == {"E2026": {"last_ingested_gamecode": 1}}


def test_assert_season_matches_raises_on_mismatch() -> None:
    """Guards against a mixed-up season mapping upstream (e.g. the API silently
    redirecting a stale/renamed season code to a different one)."""
    wrong_season_header = {**HEADER, "pcom": "E2025     "}
    with pytest.raises(ValueError, match="E2025"):
        _assert_season_matches(wrong_season_header, season="E2026", gamecode=1)


def test_assert_season_matches_strips_padding_and_passes() -> None:
    _assert_season_matches(HEADER, season="E2026", gamecode=1)  # no raise


def test_build_game_rows_raises_on_mismatched_stats_boxscore_shape() -> None:
    """zip(..., strict=True) should surface a malformed BoxScore rather than
    silently dropping a team -- Stats must have exactly one entry per team_code."""
    lopsided_boxscore = {**BOXSCORE, "Stats": [BOXSCORE["Stats"][0]]}
    with pytest.raises(ValueError, match="zip"):
        build_game_rows(HEADER, lopsided_boxscore, season="E2026", gamecode=1)
