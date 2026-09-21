"""Tests for the cross-source player name matching used by the master table."""

from __future__ import annotations

import pandas as pd
import pytest

from player_name_matching import name_key, resolve_names, surname_key, teams_compatible


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Wade Baldwin IV", "wade baldwin"),
        ("Wade Baldwin Iv", "wade baldwin"),
        ("Patrick Baldwin Jr.", "patrick baldwin"),
        ("TJ Shorts II", "tj shorts"),
        ("Jimmy Clark III", "jimmy clark"),
        ("Talen Horton-Tucker", "talen horton tucker"),
        ("Talen Horton\u2013Tucker", "talen horton tucker"),
        ("Shaquielle McKissic", "shaquielle mckissic"),
        ("O'Shae Brissett", "oshae brissett"),
        ("Šarūnas Jasikevičius", "sarunas jasikevicius"),
        ("  Kamar   Baldwin ", "kamar baldwin"),
        ("V", "v"),
        (float("nan"), ""),
    ],
)
def test_name_key(raw: object, expected: str) -> None:
    assert name_key(raw) == expected


def test_surname_key_excludes_generational_suffix() -> None:
    assert surname_key(name_key("Wade Baldwin IV")) == "baldwin"
    assert surname_key(name_key("Patrick Baldwin Jr.")) == "baldwin"


def test_teams_compatible() -> None:
    assert teams_compatible("Fenerbahce", "Fenerbahce Beko Istanbul")
    assert teams_compatible("Milano", "EA7 Emporio Armani Milan")
    assert teams_compatible("Maccabi Playtika Tel Aviv", "Maccabi Rapyd Tel Aviv")
    assert not teams_compatible("Hapoel Tel Aviv", "Maccabi Playtika Tel Aviv")
    assert not teams_compatible("FC Barcelona", "FC Bayern Munich")
    assert not teams_compatible(float("nan"), "Real Madrid")


def _frame(rows: list[tuple[str, str]], team_col: str, **extra: list[int]) -> pd.DataFrame:
    df = pd.DataFrame({"name_key": [name_key(n) for n, _ in rows], team_col: [t for _, t in rows]})
    return df.assign(**extra)


BASE_BALDWINS = [("Wade Baldwin IV", "Fenerbahce Beko Istanbul"), ("Kamar Baldwin", "FC Bayern Munich")]
PRICE_BALDWINS = [
    ("Wade Baldwin", "Fenerbahce"),
    ("Patrick Baldwin Jr.", "Crvena Zvezda"),
    ("Kamar Baldwin", "Bayern Munich"),
]


def test_baldwin_trio_stays_three_players() -> None:
    base = _frame(BASE_BALDWINS, "team_name")
    prices = _frame(PRICE_BALDWINS, "club")

    resolved = resolve_names(base, prices, "club")

    assert resolved.tolist() == ["wade baldwin", "patrick baldwin", "kamar baldwin"]
    assert resolved.is_unique


def test_wade_baldwin_matches_without_kamar_in_price_list() -> None:
    """Surname alone must not send Wade to Kamar, nor Patrick to either."""
    base = _frame(BASE_BALDWINS, "team_name")
    prices = _frame([PRICE_BALDWINS[0], PRICE_BALDWINS[1]], "club")

    assert resolve_names(base, prices, "club").tolist() == ["wade baldwin", "patrick baldwin"]


def test_lone_same_surname_different_first_name_is_not_merged() -> None:
    base = _frame([("Kamar Baldwin", "FC Bayern Munich")], "team_name")
    prices = _frame([("Patrick Baldwin Jr.", "Crvena Zvezda")], "club")

    assert resolve_names(base, prices, "club").tolist() == ["patrick baldwin"]


def test_nickname_and_prefix_first_names_match_on_surname() -> None:
    base = _frame(
        [("Aleksandr Vezenkov", "Olympiacos Piraeus"), ("Zac Seljaas", "LDLC ASVEL Villeurbanne")], "team_name"
    )
    other = _frame([("Sasha Vezenkov", "Olympiacos"), ("Zachary Seljaas", "ASVEL")], "club")

    assert resolve_names(base, other, "club").tolist() == ["aleksandr vezenkov", "zac seljaas"]


def test_spelling_variant_on_same_team_matches() -> None:
    base = _frame(
        [("Gur Lavi", "Maccabi Playtika Tel Aviv"), ("Gabriel Lundberg", "Maccabi Playtika Tel Aviv")], "team_name"
    )
    other = _frame([("Gur Lavy", "Maccabi Tel Aviv")], "club")

    assert resolve_names(base, other, "club").tolist() == ["gur lavi"]


def test_unrelated_first_names_need_matching_games_to_merge() -> None:
    """'Iffe' vs 'Gabriel' Lundberg: only identical games played links them."""
    base = _frame([("Gabriel Lundberg", "Maccabi Playtika Tel Aviv")], "team_name", games_played=[16])
    same_games = _frame([("Iffe Lundberg", "Maccabi Rapyd Tel Aviv")], "dunk_team", dunk_gp=[16])
    other_games = same_games.assign(dunk_gp=[10])

    assert resolve_names(base, same_games, "dunk_team").tolist() == ["iffe lundberg"]
    assert resolve_names(base, same_games, "dunk_team", other_games_col="dunk_gp").tolist() == ["gabriel lundberg"]
    assert resolve_names(base, other_games, "dunk_team", other_games_col="dunk_gp").tolist() == ["iffe lundberg"]


def test_colliding_resolutions_are_reverted() -> None:
    base = _frame([("Alan Dokossi", "Paris Basketball")], "team_name")
    other = _frame([("Allan Dokossi", "Paris"), ("Alann Dokossi", "Paris")], "club")

    assert resolve_names(base, other, "club").tolist() == ["allan dokossi", "alann dokossi"]


def test_loose_first_name_on_another_team_is_not_merged() -> None:
    """'Chris' vs 'Christian' Duarte on different clubs are different players."""
    base = _frame([("Christian Duarte", "Real Madrid")], "team_name")

    assert resolve_names(base, _frame([("Chris Duarte", "Baskonia")], "club"), "club").tolist() == ["chris duarte"]
    assert resolve_names(base, _frame([("Chris Duarte", "Madrid")], "club"), "club").tolist() == ["christian duarte"]
