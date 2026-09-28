"""Tests for the fantasy-stats player-name resolver: pools, row building, verdict carry-over."""

from __future__ import annotations

import pytest

from eupy.entity.resolve_fantasy_stats_names import (
    FANTASY_TEAM_CLUBS,
    abbreviate_name,
    abbreviated_pool,
    apply_verdicts,
    build_crosswalk,
    build_row,
)


def _player(player_id: str, name: str, team: str) -> dict[str, str]:
    return {"player_id": player_id, "name": name, "team": team}


def _prices(name: str, club: str) -> dict[str, str]:
    return {"name": name, "club": club, "role": "player"}


def test_abbreviate_name_keeps_compound_first_names_whole() -> None:
    assert abbreviate_name("Marc-Owen Fodzo Dada") == "M. Fodzo Dada"
    assert abbreviate_name("Carlik Jones") == "C. Jones"
    assert abbreviate_name("C. Jones") == "C. Jones"
    assert abbreviate_name("Madonna") == "Madonna"


def test_team_codes_map_to_distinct_clubs() -> None:
    assert len(set(FANTASY_TEAM_CLUBS.values())) == len(FANTASY_TEAM_CLUBS) == 20


def test_same_abbreviation_at_different_clubs_resolves_by_club() -> None:
    prices = [_prices("Carlik Jones", "Partizan"), _prices("Chris Jones", "Crvena Zvezda")]
    crosswalk = build_crosswalk(
        [_player("1", "C. Jones", "PAR"), _player("2", "C. Jones", "CZV")], prices, {}, existing={}
    )

    assert [(r["resolved_name"], r["match_status"], r["source"]) for r in crosswalk] == [
        ("Carlik Jones", "exact", "prices"),
        ("Chris Jones", "exact", "prices"),
    ]


def test_same_abbreviation_within_one_club_is_flagged_not_guessed() -> None:
    pools = {"Partizan": abbreviated_pool(["Carlik Jones", "Chris Jones"])}

    row = build_row(_player("1", "C. Jones", "PAR"), pools, {})

    assert row["match_status"] == "needs_review"
    assert row["matched_by"] == "exact_collision"


def test_boxscore_hit_is_never_auto_accepted_and_is_title_cased() -> None:
    boxscore = abbreviated_pool(["BRUNO CABOCLO"])

    row = build_row(_player("1", "B. Caboclo", "HTA"), {}, boxscore)

    assert (row["match_status"], row["source"], row["resolved_name"]) == ("needs_review", "boxscore", "Bruno Caboclo")


def test_unknown_player_ends_as_no_candidate() -> None:
    row = build_row(_player("1", "Z. Nobody", "XXX"), {}, abbreviated_pool(["BRUNO CABOCLO"]))

    assert row["match_status"] == "no_candidate"
    assert row["resolved_name"] == ""


def test_verdict_resolved_rows_are_carried_over_unchanged() -> None:
    existing = {
        "1": {
            "player_id": "1",
            "name": "Y. Aksu",
            "team": "BJK",
            "resolved_name": "Yigit Aksu",
            "match_status": "confirmed",
            "match_score": "",
            "matched_by": "agent",
            "source": "web",
            "notes": "ok",
        }
    }

    crosswalk = build_crosswalk([_player("1", "Y. Aksu", "BJK")], [], {}, existing)

    assert crosswalk == [existing["1"]]


def _open_rows() -> dict[str, dict[str, str]]:
    return {
        "1": {
            "player_id": "1",
            "name": "X. Y",
            "team": "T",
            "resolved_name": "",
            "match_status": "no_candidate",
            "match_score": "",
            "matched_by": "fuzzy",
            "source": "",
            "notes": "",
        }
    }


def test_apply_verdicts_confirms_with_name_and_rationale() -> None:
    rows = _open_rows()

    apply_verdicts(rows, [{"player_id": 1, "match_status": "confirmed", "resolved_name": "Xavi Yang", "notes": "n"}])

    assert (rows["1"]["match_status"], rows["1"]["resolved_name"], rows["1"]["matched_by"]) == (
        "confirmed",
        "Xavi Yang",
        "agent",
    )


@pytest.mark.parametrize(
    "verdict",
    [
        {"player_id": 9, "match_status": "no_match", "notes": "n"},  # unknown id
        {"player_id": 1, "match_status": "needs_review", "notes": "n"},  # non-terminal status
        {"player_id": 1, "match_status": "confirmed", "notes": "n"},  # confirmed without a name
        {"player_id": 1, "match_status": "no_match", "resolved_name": "A B", "notes": "n"},  # name on non-confirmed
        {"player_id": 1, "match_status": "no_match"},  # no rationale
    ],
)
def test_apply_verdicts_rejects_malformed_verdicts(verdict: dict) -> None:
    with pytest.raises(ValueError):
        apply_verdicts(_open_rows(), [verdict])
