"""Tests for the team-name crosswalk's loading, row-building, and merge logic."""

from __future__ import annotations

import csv
from pathlib import Path

from eupy.entity.matching import build_normalized_index, normalize_name
from eupy.entity.resolve_team_names import (
    build_crosswalk,
    build_row,
    load_boxscore_team_spellings,
    load_existing_crosswalk,
    load_master_clubs,
    write_crosswalk,
)

# --- loading -------------------------------------------------------------------------


def test_load_master_clubs_dedupes_and_sorts(tmp_path: Path) -> None:
    path = tmp_path / "prices.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["rank", "name", "club", "position", "price", "role"])
        writer.writeheader()
        writer.writerows([
            {"rank": "1", "name": "A", "club": "Zalgiris", "position": "G", "price": "10.0", "role": "player"},
            {"rank": "2", "name": "B", "club": "Baskonia", "position": "F", "price": "9.0", "role": "player"},
            {"rank": "3", "name": "C", "club": "Baskonia", "position": "C", "price": "8.0", "role": "player"},
        ])

    clubs = load_master_clubs(path)

    assert clubs == ["Baskonia", "Zalgiris"]


def test_load_boxscore_team_spellings_pools_both_sides(tmp_path: Path) -> None:
    path = tmp_path / "header.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["team_a", "team_b", "team_id_a", "team_id_b"])
        writer.writeheader()
        writer.writerows([
            {"team_a": "OLYMPIACOS PIRAEUS B.C.", "team_b": "BASKONIA", "team_id_a": "OLY", "team_id_b": "BAS"},
            {"team_a": "BASKONIA", "team_b": "ZALGIRIS KAUNAS", "team_id_a": "BAS", "team_id_b": "ZAL"},
        ])

    spellings = load_boxscore_team_spellings(path)

    assert spellings == {
        "OLYMPIACOS PIRAEUS B.C.": {"OLY"},
        "BASKONIA": {"BAS"},
        "ZALGIRIS KAUNAS": {"ZAL"},
    }


# --- row building ----------------------------------------------------------------------

# "SPONSOR TEAM" is a genuine collision: two different team_ids share one raw spelling.
# "TEAM MARI" / "TEAM MARÍ" is a spelling ambiguity: one normalized value, two raw spellings.
SPELLING_TEAM_IDS = {
    "BASKONIA": {"BAS"},
    "ZALGIRIS KAUNAS": {"ZAL"},
    "SPONSOR TEAM": {"XXX", "YYY"},
    "REAL MADRID": {"MAD"},
    "REAL MADRÍD": {"MA2"},
}


def _index() -> dict[str, set[str]]:
    return build_normalized_index(SPELLING_TEAM_IDS)


def _normalized_spellings() -> dict[str, str]:
    return {spelling: normalize_name(spelling) for spelling in SPELLING_TEAM_IDS}


def test_build_row_exact_match() -> None:
    row = build_row("Baskonia", SPELLING_TEAM_IDS, _index(), _normalized_spellings())

    assert row["match_status"] == "exact"
    assert row["boxscore_team_name"] == "BASKONIA"
    assert row["matched_by"] == "exact"
    assert row["notes"] == ""


def test_build_row_no_candidate_leaves_match_fields_blank() -> None:
    row = build_row("Nonexistent Club", SPELLING_TEAM_IDS, _index(), _normalized_spellings())

    assert row["match_status"] == "no_candidate"
    assert row["boxscore_team_name"] == ""


def test_build_row_collision_records_candidate_in_notes() -> None:
    row = build_row("Sponsor Team", SPELLING_TEAM_IDS, _index(), _normalized_spellings())

    assert row["match_status"] == "needs_review"
    assert row["matched_by"] == "exact_collision"
    assert "SPONSOR TEAM" in row["notes"]


def test_build_row_ambiguous_spelling_records_every_variant_in_notes() -> None:
    row = build_row("Real Madrid", SPELLING_TEAM_IDS, _index(), _normalized_spellings())

    assert row["match_status"] == "needs_review"
    assert row["matched_by"] == "exact_ambiguous_spelling"
    assert "REAL MADRID" in row["notes"]
    assert "REAL MADRÍD" in row["notes"]


# --- crosswalk merge / idempotency -------------------------------------------------------


def test_build_crosswalk_preserves_agent_resolved_rows_byte_identical() -> None:
    clubs = ["Baskonia"]
    existing = {
        "Baskonia": {
            "name": "Baskonia",
            "boxscore_team_name": "TAU CERAMICA",
            "match_status": "confirmed",
            "match_score": "0.0",
            "matched_by": "agent",
            "notes": "Historical sponsor name for the same club.",
        }
    }

    [row] = build_crosswalk(clubs, SPELLING_TEAM_IDS, existing)

    # Not recomputed even though "Baskonia" has a real exact match -- carried over unchanged.
    assert row == existing["Baskonia"]


def test_build_crosswalk_recomputes_non_resolved_statuses() -> None:
    clubs = ["Baskonia"]
    # Stale "exact" row with a wrong boxscore_team_name -- not a resolved status, must be recomputed.
    existing = {
        "Baskonia": {
            "name": "Baskonia",
            "boxscore_team_name": "Wrong Name",
            "match_status": "exact",
            "match_score": "100.0",
            "matched_by": "exact",
            "notes": "",
        }
    }

    [row] = build_crosswalk(clubs, SPELLING_TEAM_IDS, existing)

    assert row["boxscore_team_name"] == "BASKONIA"


def test_build_crosswalk_handles_new_club_not_in_existing_crosswalk() -> None:
    [row] = build_crosswalk(["Baskonia"], SPELLING_TEAM_IDS, existing={})

    assert row["match_status"] == "exact"
    assert row["boxscore_team_name"] == "BASKONIA"


# --- CSV round trip ----------------------------------------------------------------------


def test_write_crosswalk_then_load_existing_crosswalk_round_trips(tmp_path: Path) -> None:
    out_path = tmp_path / "crosswalk.csv"
    rows = [
        {
            "name": "Baskonia",
            "boxscore_team_name": "BASKONIA",
            "match_status": "exact",
            "match_score": "100.0",
            "matched_by": "exact",
            "notes": "",
        }
    ]

    write_crosswalk(rows, out_path)
    loaded = load_existing_crosswalk(out_path)

    assert loaded == {"Baskonia": rows[0]}


def test_write_crosswalk_header_has_no_team_id_or_role(tmp_path: Path) -> None:
    out_path = tmp_path / "crosswalk.csv"
    write_crosswalk(
        [
            {
                "name": "Baskonia",
                "boxscore_team_name": "BASKONIA",
                "match_status": "exact",
                "match_score": "100.0",
                "matched_by": "exact",
                "notes": "",
            }
        ],
        out_path,
    )

    header = out_path.read_text(encoding="utf-8").splitlines()[0]

    assert header == "name,boxscore_team_name,match_status,match_score,matched_by,notes"


def test_load_existing_crosswalk_returns_empty_dict_when_file_missing(tmp_path: Path) -> None:
    assert load_existing_crosswalk(tmp_path / "does_not_exist.csv") == {}
