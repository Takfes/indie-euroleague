"""Tests for the player-master `team_name` -> canonical team_kpis.xlsx name resolution."""

from __future__ import annotations

import pytest

from build_player_master_table import TEAM_NAME_OVERRIDES, resolve_team_name

CANONICAL = [
    "AS Monaco",
    "Anadolu Efes Istanbul",
    "Crvena Zvezda Meridianbet Belgrade",
    "Dubai Basketball",
    "EA7 Emporio Armani Milan",
    "FC Barcelona",
    "FC Bayern Munich",
    "Fenerbahce Beko Istanbul",
    "Hapoel Shlomo Tel Aviv",
    "Kosner Baskonia Vitoria-Gasteiz",
    "LDLC ASVEL Villeurbanne",
    "Maccabi Playtika Tel Aviv",
    "Olympiacos Piraeus",
    "Panathinaikos AKTOR Athens",
    "Paris Basketball",
    "Partizan Mozzart Bet Belgrade",
    "Real Madrid",
    "Valencia Basket",
    "Virtus Segafredo Bologna",
    "Zalgiris Kaunas",
]


def test_exact_match_is_identity() -> None:
    assert resolve_team_name("Real Madrid", CANONICAL) == "Real Madrid"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Virtus Bologna", "Virtus Segafredo Bologna"),
        ("Baskonia Vitoria-Gasteiz", "Kosner Baskonia Vitoria-Gasteiz"),
        ("Hapoel IBI Tel Aviv", "Hapoel Shlomo Tel Aviv"),
        ("Maccabi Rapyd Tel Aviv", "Maccabi Playtika Tel Aviv"),
        ("Baskonia", "Kosner Baskonia Vitoria-Gasteiz"),
        ("Barcelona", "FC Barcelona"),
        ("Bayern Munich", "FC Bayern Munich"),
        ("Milano", "EA7 Emporio Armani Milan"),
        ("ASVEL", "LDLC ASVEL Villeurbanne"),
        ("Maccabi Tel Aviv", "Maccabi Playtika Tel Aviv"),
        ("Dubai", "Dubai Basketball"),
        ("Paris", "Paris Basketball"),
    ],
)
def test_tier1_teams_compatible_resolves_sponsor_and_shortened_names(raw: str, expected: str) -> None:
    assert resolve_team_name(raw, CANONICAL) == expected


def test_besiktas_is_a_verified_no_match_not_a_forced_fuzzy_guess() -> None:
    """Besiktas is not one of this season's 20 teams; TEAM_NAME_OVERRIDES records that explicitly."""
    assert "Besiktas" in TEAM_NAME_OVERRIDES
    assert TEAM_NAME_OVERRIDES["Besiktas"] is None
    assert resolve_team_name("Besiktas", CANONICAL) is None


def test_missing_team_name_resolves_to_none() -> None:
    assert resolve_team_name(float("nan"), CANONICAL) is None
    assert resolve_team_name(None, CANONICAL) is None


def test_fuzzy_tier_requires_a_clear_winner_above_threshold() -> None:
    # One character off a canonical name: clears the threshold with no near-tie runner-up.
    assert resolve_team_name("Real Madird", CANONICAL) == "Real Madrid"


def test_unmapped_value_raises_and_names_the_fix() -> None:
    with pytest.raises(ValueError, match=r"Yerevan Titans.*TEAM_NAME_OVERRIDES"):
        resolve_team_name("Yerevan Titans", CANONICAL)
