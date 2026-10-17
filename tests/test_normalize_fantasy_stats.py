"""Tests for the fantasy-stats normalizer: names via the player map, C/F/G roles, loud failures."""

from __future__ import annotations

import pytest

from eupy.entity.matching import AmbiguousNameError, UnknownNameError
from eupy.entity.player_names import PlayerNames
from eupy.entity.team_names import TeamNames
from eupy.transform.normalize_fantasy_stats import normalize_rows

TEAMS = TeamNames([{"name": "Partizan", "variants": ["PAR"]}, {"name": "Zvezda", "variants": ["CZV"]}])


def _entry(name: str, club: str, code: str, stats_name: str, status: str = "exact") -> dict[str, object]:
    return {
        "name": name,
        "variants": sorted({stats_name, name}),
        "team": club,
        "stats_name": stats_name,
        "stats_team": code,
        "match_status": status,
    }


PLAYERS = PlayerNames(
    [
        _entry("Carlik Jones", "Partizan", "PAR", "C. Jones"),
        _entry("Chris Jones", "Zvezda", "CZV", "C. Jones"),
        _entry("Q. Rookie", "Partizan", "PAR", "Q. Rookie", "no_match"),
        _entry("Open Player", "Partizan", "PAR", "O. Player", "needs_review"),
    ],
    TEAMS,
)


def _row(name: str, team: str, position: str = "Guard", **extra: str) -> dict[str, str]:
    return {"round": "1", "player_id": "1", "name": name, "team": team, "position": position, **extra}


def test_names_come_from_the_map_per_club_and_positions_become_roles() -> None:
    out = normalize_rows([_row("C. Jones", "PAR", pts="20"), _row("C. Jones", "CZV", "Center", pts="9")], PLAYERS)

    assert [(r["name"], r["position"], r["pts"]) for r in out] == [
        ("Carlik Jones", "G", "20"),
        ("Chris Jones", "C", "9"),
    ]


def test_no_match_player_keeps_the_abbreviated_name() -> None:
    assert normalize_rows([_row("Q. Rookie", "PAR")], PLAYERS)[0]["name"] == "Q. Rookie"


def test_player_missing_from_the_map_or_still_open_keeps_the_stats_name() -> None:
    out = normalize_rows([_row("N. Ew", "PAR"), _row("O. Player", "PAR")], PLAYERS)
    assert [r["name"] for r in out] == ["N. Ew", "O. Player"]


def test_shared_name_without_a_usable_club_is_ambiguous() -> None:
    with pytest.raises(AmbiguousNameError):
        PLAYERS.normalize("C. Jones")


def test_unknown_position_fails() -> None:
    with pytest.raises(ValueError, match="position"):
        normalize_rows([_row("C. Jones", "PAR", "Head Coach")], PLAYERS)
