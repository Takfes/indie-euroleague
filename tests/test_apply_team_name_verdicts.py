"""Tests for applying agent verdicts onto the team-name crosswalk."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eupy.entity.apply_team_name_verdicts import apply_verdicts, load_verdicts
from eupy.entity.resolve_team_names import load_existing_crosswalk, write_crosswalk


def _needs_review_row() -> dict[str, str]:
    return {
        "name": "Barcelona",
        "boxscore_team_name": "FC BARCELONA",
        "match_status": "needs_review",
        "match_score": "91.0",
        "matched_by": "fuzzy",
        "notes": "candidates: FC BARCELONA (91.0)",
    }


def _rows_by_key(*rows: dict[str, str]) -> dict[str, dict[str, str]]:
    return {row["name"]: dict(row) for row in rows}


# --- apply_verdicts ------------------------------------------------------------------------


def test_apply_verdicts_confirms_a_candidate() -> None:
    rows_by_key = _rows_by_key(_needs_review_row())
    verdicts = [
        {
            "name": "Barcelona",
            "match_status": "confirmed",
            "boxscore_team_name": "FC BARCELONA",
            "match_score": 91.0,
            "notes": "Fantasy site drops the FC prefix; same club.",
        }
    ]

    apply_verdicts(rows_by_key, verdicts)

    row = rows_by_key["Barcelona"]
    assert row["match_status"] == "confirmed"
    assert row["boxscore_team_name"] == "FC BARCELONA"
    assert row["match_score"] == "91.0"
    assert row["matched_by"] == "agent"
    assert row["notes"] == "Fantasy site drops the FC prefix; same club."


def test_apply_verdicts_rejects_and_clears_boxscore_team_name() -> None:
    rows_by_key = _rows_by_key(_needs_review_row())
    verdicts = [
        {
            "name": "Barcelona",
            "match_status": "rejected",
            "notes": "Candidate is a different club entirely.",
        }
    ]

    apply_verdicts(rows_by_key, verdicts)

    row = rows_by_key["Barcelona"]
    assert row["match_status"] == "rejected"
    assert row["boxscore_team_name"] == ""


def test_apply_verdicts_defaults_matched_by_to_agent() -> None:
    rows_by_key = _rows_by_key(_needs_review_row())
    verdicts = [{"name": "Barcelona", "match_status": "no_match", "notes": "No box-score history found."}]

    apply_verdicts(rows_by_key, verdicts)

    assert rows_by_key["Barcelona"]["matched_by"] == "agent"


def test_apply_verdicts_raises_for_unknown_row() -> None:
    rows_by_key = _rows_by_key(_needs_review_row())
    verdicts = [{"name": "Nobody Here", "match_status": "no_match", "notes": "x"}]

    with pytest.raises(ValueError, match="not in the crosswalk"):
        apply_verdicts(rows_by_key, verdicts)


def test_apply_verdicts_raises_for_non_terminal_status() -> None:
    rows_by_key = _rows_by_key(_needs_review_row())
    verdicts = [{"name": "Barcelona", "match_status": "needs_review", "notes": "x"}]

    with pytest.raises(ValueError, match="match_status"):
        apply_verdicts(rows_by_key, verdicts)


def test_apply_verdicts_raises_when_confirmed_has_no_boxscore_team_name() -> None:
    rows_by_key = _rows_by_key(_needs_review_row())
    verdicts = [{"name": "Barcelona", "match_status": "confirmed", "notes": "x"}]

    with pytest.raises(ValueError, match="no boxscore_team_name"):
        apply_verdicts(rows_by_key, verdicts)


def test_apply_verdicts_raises_when_non_confirmed_has_boxscore_team_name() -> None:
    rows_by_key = _rows_by_key(_needs_review_row())
    verdicts = [{"name": "Barcelona", "match_status": "rejected", "boxscore_team_name": "FC BARCELONA", "notes": "x"}]

    with pytest.raises(ValueError, match="only confirmed rows should"):
        apply_verdicts(rows_by_key, verdicts)


def test_apply_verdicts_raises_when_notes_missing() -> None:
    rows_by_key = _rows_by_key(_needs_review_row())
    verdicts = [{"name": "Barcelona", "match_status": "no_match"}]

    with pytest.raises(ValueError, match="no notes"):
        apply_verdicts(rows_by_key, verdicts)


# --- load_verdicts -------------------------------------------------------------------------


def test_load_verdicts_reads_json_list(tmp_path: Path) -> None:
    path = tmp_path / "verdicts.json"
    payload = [{"name": "Barcelona", "match_status": "no_match", "notes": "x"}]
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
                "name": "Barcelona",
                "match_status": "confirmed",
                "boxscore_team_name": "FC BARCELONA",
                "notes": "Confirmed.",
            }
        ],
    )
    write_crosswalk(list(rows_by_key.values()), crosswalk_path)

    reloaded = load_existing_crosswalk(crosswalk_path)
    assert reloaded["Barcelona"]["match_status"] == "confirmed"
