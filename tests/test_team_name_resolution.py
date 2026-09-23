"""Tests for the player-master team-name -> canonical (current-season, price-list-derived) name resolution."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from build_player_master_table import (
    CURRENT_TEAM_COUNT,
    PRICE_CLUB_TO_CANONICAL,
    PRICE_PATH,
    TEAM_NAME_OVERRIDES,
    load_canonical_team_names,
    resolve_canonical_team_names,
    resolve_team_name,
)

CANONICAL = [
    "Anadolu Efes Istanbul",
    "Besiktas Istanbul",
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


def test_besiktas_is_a_current_team_and_resolves_normally() -> None:
    """Besiktas is new to the league this season: a real canonical team, not an override."""
    assert "Besiktas" not in TEAM_NAME_OVERRIDES
    assert resolve_team_name("Besiktas", CANONICAL) == "Besiktas Istanbul"


def test_as_monaco_is_a_verified_no_match_not_a_forced_fuzzy_guess() -> None:
    """AS Monaco has no team in the current price list; TEAM_NAME_OVERRIDES records that explicitly."""
    assert TEAM_NAME_OVERRIDES["AS Monaco"] is None
    assert resolve_team_name("AS Monaco", CANONICAL) is None


def test_missing_team_name_resolves_to_none() -> None:
    assert resolve_team_name(float("nan"), CANONICAL) is None
    assert resolve_team_name(None, CANONICAL) is None


def test_fuzzy_tier_requires_a_clear_winner_above_threshold() -> None:
    # One character off a canonical name: clears the threshold with no near-tie runner-up.
    assert resolve_team_name("Real Madird", CANONICAL) == "Real Madrid"


def test_unmapped_value_raises_and_names_the_fix() -> None:
    with pytest.raises(ValueError, match=r"Yerevan Titans.*TEAM_NAME_OVERRIDES"):
        resolve_team_name("Yerevan Titans", CANONICAL)


def test_canonical_names_come_from_the_price_list_not_last_seasons_team_kpis() -> None:
    canonical = load_canonical_team_names()
    prices = pd.read_csv(PRICE_PATH)
    clubs = set(prices.loc[prices["role"] == "player", "club"])
    assert len(canonical) == CURRENT_TEAM_COUNT == len(clubs)
    assert set(canonical) == {PRICE_CLUB_TO_CANONICAL[club] for club in clubs}
    assert "Besiktas Istanbul" in canonical
    assert "AS Monaco" not in canonical
    assert canonical == sorted(canonical, key=str.lower)


def test_a_price_list_club_without_a_canonical_spelling_raises_and_names_the_fix(tmp_path: Path) -> None:
    prices = pd.read_csv(PRICE_PATH)
    prices.loc[prices["club"] == "Besiktas", "club"] = "Yerevan Titans"
    path = tmp_path / "prices.csv"
    prices.to_csv(path, index=False)
    with pytest.raises(ValueError, match=r"Yerevan Titans.*PRICE_CLUB_TO_CANONICAL"):
        load_canonical_team_names(path)


def test_a_price_list_with_too_few_clubs_raises(tmp_path: Path) -> None:
    prices = pd.read_csv(PRICE_PATH)
    path = tmp_path / "prices.csv"
    prices[prices["club"] != "Besiktas"].to_csv(path, index=False)
    with pytest.raises(ValueError, match="lists 19 clubs, expected 20"):
        load_canonical_team_names(path)


def test_canonical_team_follows_the_price_list_and_falls_back_to_history_only_without_it() -> None:
    current = pd.Series(["Milano", None, None, "Besiktas"])
    hist = pd.Series(["Real Madrid", "Real Madrid", "AS Monaco", "Fenerbahce Beko Istanbul"])
    resolved = resolve_canonical_team_names(current, hist, CANONICAL)
    # Priced players follow the price list even when history disagrees; the two unpriced ones resolve
    # their historical team, and a historical team with no current match stays blank.
    assert resolved[0] == "EA7 Emporio Armani Milan"
    assert resolved[1] == "Real Madrid"
    assert resolved[2] is None
    assert resolved[3] == "Besiktas Istanbul"


def test_an_unresolvable_historical_team_only_raises_for_players_who_need_the_fallback() -> None:
    current = pd.Series(["Milano", None])
    hist = pd.Series(["Yerevan Titans", "Real Madrid"])
    assert resolve_canonical_team_names(current, hist, CANONICAL).tolist() == [
        "EA7 Emporio Armani Milan",
        "Real Madrid",
    ]


def test_a_priced_club_without_a_canonical_spelling_raises_instead_of_going_blank() -> None:
    with pytest.raises(ValueError, match=r"Yerevan Titans.*PRICE_CLUB_TO_CANONICAL"):
        resolve_canonical_team_names(pd.Series(["Yerevan Titans"]), pd.Series(["Real Madrid"]), CANONICAL)
