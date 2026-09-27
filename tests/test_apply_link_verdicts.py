"""Tests for applying agent verdicts onto the player-name crosswalk."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from indie_euroleague.linking.apply_link_verdicts import apply_verdicts, load_verdicts
from indie_euroleague.linking.link_player_names import load_existing_crosswalk, write_crosswalk


def _needs_review_row() -> dict[str, str]:
    return {
        "name": "Bob Jonez",
        "role": "player",
        "club": "C",
        "position": "G",
        "price": "10.0",
        "match_status": "needs_review",
        "player_id": "P002",
        "boxscore_name": "Bob JONES",
        "match_score": "91.0",
        "matched_by": "fuzzy",
        "notes": "candidates: Bob JONES (P002, 91.0)",
    }


def _rows_by_key(*rows: dict[str, str]) -> dict[tuple[str, str], dict[str, str]]:
    return {(row["name"], row["role"]): dict(row) for row in rows}


# --- apply_verdicts ------------------------------------------------------------------------


def test_apply_verdicts_confirms_a_candidate() -> None:
    rows_by_key = _rows_by_key(_needs_review_row())
    verdicts = [
        {
            "name": "Bob Jonez",
            "role": "player",
            "match_status": "confirmed",
            "player_id": "P002",
            "boxscore_name": "Bob JONES",
            "match_score": 91.0,
            "notes": "Single-letter spelling drift, same player.",
        }
    ]

    apply_verdicts(rows_by_key, verdicts)

    row = rows_by_key[("Bob Jonez", "player")]
    assert row["match_status"] == "confirmed"
    assert row["player_id"] == "P002"
    assert row["match_score"] == "91.0"
    assert row["matched_by"] == "agent"
    assert row["notes"] == "Single-letter spelling drift, same player."


def test_apply_verdicts_rejects_and_clears_match_fields() -> None:
    rows_by_key = _rows_by_key(_needs_review_row())
    verdicts = [
        {
            "name": "Bob Jonez",
            "role": "player",
            "match_status": "rejected",
            "notes": "Candidate plays a different position and era; not the same person.",
        }
    ]

    apply_verdicts(rows_by_key, verdicts)

    row = rows_by_key[("Bob Jonez", "player")]
    assert row["match_status"] == "rejected"
    assert row["player_id"] == ""
    assert row["boxscore_name"] == ""


def test_apply_verdicts_defaults_matched_by_to_agent() -> None:
    rows_by_key = _rows_by_key(_needs_review_row())
    verdicts = [
        {"name": "Bob Jonez", "role": "player", "match_status": "no_match", "notes": "Genuine rookie, no history."}
    ]

    apply_verdicts(rows_by_key, verdicts)

    assert rows_by_key[("Bob Jonez", "player")]["matched_by"] == "agent"


def test_apply_verdicts_raises_for_unknown_row() -> None:
    rows_by_key = _rows_by_key(_needs_review_row())
    verdicts = [{"name": "Nobody Here", "role": "player", "match_status": "no_match", "notes": "x"}]

    with pytest.raises(ValueError, match="not in the crosswalk"):
        apply_verdicts(rows_by_key, verdicts)


def test_apply_verdicts_raises_for_non_terminal_status() -> None:
    rows_by_key = _rows_by_key(_needs_review_row())
    verdicts = [{"name": "Bob Jonez", "role": "player", "match_status": "needs_review", "notes": "x"}]

    with pytest.raises(ValueError, match="match_status"):
        apply_verdicts(rows_by_key, verdicts)


def test_apply_verdicts_raises_when_confirmed_has_no_player_id() -> None:
    rows_by_key = _rows_by_key(_needs_review_row())
    verdicts = [{"name": "Bob Jonez", "role": "player", "match_status": "confirmed", "notes": "x"}]

    with pytest.raises(ValueError, match="no player_id"):
        apply_verdicts(rows_by_key, verdicts)


def test_apply_verdicts_raises_when_non_confirmed_has_player_id() -> None:
    rows_by_key = _rows_by_key(_needs_review_row())
    verdicts = [{"name": "Bob Jonez", "role": "player", "match_status": "rejected", "player_id": "P002", "notes": "x"}]

    with pytest.raises(ValueError, match="only confirmed rows should"):
        apply_verdicts(rows_by_key, verdicts)


def test_apply_verdicts_raises_when_notes_missing() -> None:
    rows_by_key = _rows_by_key(_needs_review_row())
    verdicts = [{"name": "Bob Jonez", "role": "player", "match_status": "no_match"}]

    with pytest.raises(ValueError, match="no notes"):
        apply_verdicts(rows_by_key, verdicts)


# --- load_verdicts -------------------------------------------------------------------------


def test_load_verdicts_reads_json_list(tmp_path: Path) -> None:
    path = tmp_path / "verdicts.json"
    payload = [{"name": "Bob Jonez", "role": "player", "match_status": "no_match", "notes": "x"}]
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert load_verdicts(path) == payload


# --- crosswalk CSV round trip through apply_verdicts ----------------------------------------


def test_apply_verdicts_then_write_crosswalk_round_trips_through_csv(tmp_path: Path) -> None:
    crosswalk_path = tmp_path / "crosswalk.csv"
    write_crosswalk([_needs_review_row()], crosswalk_path)

    rows_by_key = load_existing_crosswalk(crosswalk_path)
    apply_verdicts(
        rows_by_key,
        [
            {
                "name": "Bob Jonez",
                "role": "player",
                "match_status": "confirmed",
                "player_id": "P002",
                "notes": "Confirmed.",
            }
        ],
    )
    write_crosswalk(list(rows_by_key.values()), crosswalk_path)

    reloaded = load_existing_crosswalk(crosswalk_path)
    assert reloaded[("Bob Jonez", "player")]["match_status"] == "confirmed"
