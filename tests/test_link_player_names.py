"""Tests for the player-name crosswalk's normalization, matching, and merge logic."""

from __future__ import annotations

import csv
from pathlib import Path

from indie_euroleague.linking.link_player_names import (
    build_crosswalk,
    build_normalized_index,
    build_row,
    find_candidates,
    load_boxscore_roster,
    load_existing_crosswalk,
    load_master_rows,
    normalize_name,
    reorder_boxscore_name,
    write_crosswalk,
)


def _master_row(
    name: str, role: str = "player", club: str = "C", position: str = "G", price: str = "10.0"
) -> dict[str, str]:
    return {"rank": "1", "name": name, "club": club, "position": position, "price": price, "role": role}


# --- normalization -----------------------------------------------------------------


def test_normalize_name_strips_accents_and_upper_cases() -> None:
    assert normalize_name("Núñez Özil") == "NUNEZ OZIL"


def test_normalize_name_replaces_punctuation_with_space_not_deletion() -> None:
    # Hyphens/apostrophes become spaces so words don't glue into one token.
    assert normalize_name("Nigel Hayes-Davis") == "NIGEL HAYES DAVIS"
    assert normalize_name("D'Angelo Russell") == "D ANGELO RUSSELL"


def test_normalize_name_collapses_whitespace() -> None:
    assert normalize_name("  John   Smith  ") == "JOHN SMITH"


def test_reorder_boxscore_name_swaps_last_comma_first() -> None:
    assert reorder_boxscore_name("SMITH, John") == "John SMITH"


def test_reorder_boxscore_name_passes_through_when_no_comma() -> None:
    assert reorder_boxscore_name("John Smith") == "John Smith"


# --- loading -------------------------------------------------------------------------


def test_load_boxscore_roster_excludes_total_rows_and_dedupes_by_player_id(tmp_path: Path) -> None:
    path = tmp_path / "box_score.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["player_id", "player", "dorsal"])
        writer.writeheader()
        writer.writerows([
            {"player_id": "P001", "player": "SMITH, John", "dorsal": "4"},
            {"player_id": "P001", "player": "SMITH, John", "dorsal": "4"},  # second event row, same player
            {"player_id": "ZAL", "player": "TEAM TOTAL", "dorsal": "TOTAL"},
        ])

    roster = load_boxscore_roster(path)

    assert roster == {"P001": "John SMITH"}


def test_load_master_rows_reads_all_columns(tmp_path: Path) -> None:
    path = tmp_path / "prices.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["rank", "name", "club", "position", "price", "role"])
        writer.writeheader()
        writer.writerow({
            "rank": "1",
            "name": "John Smith",
            "club": "C",
            "position": "G",
            "price": "10.0",
            "role": "player",
        })

    rows = load_master_rows(path)

    assert rows == [
        {"rank": "1", "name": "John Smith", "club": "C", "position": "G", "price": "10.0", "role": "player"}
    ]


# --- candidate resolution -------------------------------------------------------------

ROSTER = {
    "P001": "John SMITH",
    "P002": "Bob JONES",
    "P003": "Ambi GUOUS",
    "P004": "Ambi GUOUS",  # namesake: different player_id, identical normalized name
}


def _index() -> dict[str, list[tuple[str, str]]]:
    return build_normalized_index(ROSTER)


def _normalized_roster() -> dict[str, str]:
    return {player_id: normalize_name(name) for player_id, name in ROSTER.items()}


def test_find_candidates_exact_match_is_unique() -> None:
    status, candidates = find_candidates(normalize_name("John Smith"), ROSTER, _normalized_roster(), _index())

    assert status == "exact"
    assert candidates == [("P001", "John SMITH", 100.0)]


def test_find_candidates_ambiguous_exact_match_lists_every_namesake() -> None:
    status, candidates = find_candidates(normalize_name("Ambi Guous"), ROSTER, _normalized_roster(), _index())

    assert status == "needs_review"
    assert {c[0] for c in candidates} == {"P003", "P004"}
    assert all(score == 100.0 for _, _, score in candidates)


def test_find_candidates_fuzzy_match_above_cutoff_is_needs_review() -> None:
    # "Bob Jonez" is a one-letter edit away from box-score "Bob Jones".
    status, candidates = find_candidates(
        normalize_name("Bob Jonez"), ROSTER, _normalized_roster(), _index(), score_cutoff=50.0
    )

    assert status == "needs_review"
    assert candidates[0][0] == "P002"


def test_find_candidates_below_cutoff_is_no_candidate() -> None:
    # Same near-match as above, but a cutoff no real-world score would clear.
    status, candidates = find_candidates(
        normalize_name("Bob Jonez"), ROSTER, _normalized_roster(), _index(), score_cutoff=99.9
    )

    assert status == "no_candidate"
    assert candidates == []


def test_find_candidates_respects_limit() -> None:
    roster = {"P001": "Bob Jones", "P002": "Bob Jonas", "P003": "Bob Jonis", "P004": "Bob Jonus"}
    normalized_roster = {pid: normalize_name(n) for pid, n in roster.items()}
    index = build_normalized_index(roster)

    status, candidates = find_candidates(
        normalize_name("Bob Jonez"), roster, normalized_roster, index, limit=2, score_cutoff=0.0
    )

    assert status == "needs_review"
    assert len(candidates) == 2


# --- row building ----------------------------------------------------------------------


def test_build_row_head_coach_is_not_applicable() -> None:
    row = build_row(_master_row("Some Coach", role="head_coach"), ROSTER, _normalized_roster(), _index())

    assert row["match_status"] == "not_applicable"
    assert row["matched_by"] == "not_applicable"
    assert row["player_id"] == ""


def test_build_row_exact_match() -> None:
    row = build_row(_master_row("John Smith"), ROSTER, _normalized_roster(), _index())

    assert row["match_status"] == "exact"
    assert row["player_id"] == "P001"
    assert row["boxscore_name"] == "John SMITH"
    assert row["matched_by"] == "exact"
    assert row["notes"] == ""


def test_build_row_no_candidate_leaves_match_fields_blank() -> None:
    row = build_row(_master_row("Nobody Realname"), ROSTER, _normalized_roster(), _index())

    assert row["match_status"] == "no_candidate"
    assert row["player_id"] == ""
    assert row["boxscore_name"] == ""


def test_build_row_needs_review_records_candidates_in_notes() -> None:
    row = build_row(_master_row("Ambi Guous"), ROSTER, _normalized_roster(), _index())

    assert row["match_status"] == "needs_review"
    assert row["matched_by"] == "exact_ambiguous"
    assert "P003" in row["notes"]
    assert "P004" in row["notes"]


# --- crosswalk merge / idempotency -------------------------------------------------------


def test_build_crosswalk_preserves_agent_resolved_rows_but_refreshes_price() -> None:
    master_rows = [_master_row("John Smith", club="NEW_CLUB", position="F", price="99.0")]
    existing = {
        ("John Smith", "player"): {
            "name": "John Smith",
            "role": "player",
            "club": "OLD_CLUB",
            "position": "G",
            "price": "10.0",
            "match_status": "confirmed",
            "player_id": "P999",
            "boxscore_name": "Someone Else",
            "match_score": "77.0",
            "matched_by": "agent",
            "notes": "manually confirmed despite low fuzzy score",
        }
    }

    [row] = build_crosswalk(master_rows, ROSTER, existing)

    # Match decision untouched -- not recomputed even though "John Smith" has a real exact match.
    assert row["match_status"] == "confirmed"
    assert row["player_id"] == "P999"
    assert row["notes"] == "manually confirmed despite low fuzzy score"
    # Snapshot-derived fields refreshed to the new master row.
    assert row["club"] == "NEW_CLUB"
    assert row["position"] == "F"
    assert row["price"] == "99.0"


def test_build_crosswalk_recomputes_non_resolved_statuses() -> None:
    master_rows = [_master_row("John Smith")]
    # Stale "exact" row with a wrong player_id -- not a resolved status, so it must be recomputed.
    existing = {
        ("John Smith", "player"): {
            "name": "John Smith",
            "role": "player",
            "club": "C",
            "position": "G",
            "price": "10.0",
            "match_status": "exact",
            "player_id": "WRONG_ID",
            "boxscore_name": "Wrong Name",
            "match_score": "100.0",
            "matched_by": "exact",
            "notes": "",
        }
    }

    [row] = build_crosswalk(master_rows, ROSTER, existing)

    assert row["player_id"] == "P001"


def test_build_crosswalk_handles_new_player_not_in_existing_crosswalk() -> None:
    [row] = build_crosswalk([_master_row("John Smith")], ROSTER, existing={})

    assert row["match_status"] == "exact"
    assert row["player_id"] == "P001"


# --- CSV round trip ----------------------------------------------------------------------


def test_write_crosswalk_then_load_existing_crosswalk_round_trips(tmp_path: Path) -> None:
    out_path = tmp_path / "crosswalk.csv"
    rows = [
        {
            "name": "John Smith",
            "role": "player",
            "club": "C",
            "position": "G",
            "price": "10.0",
            "match_status": "exact",
            "player_id": "P001",
            "boxscore_name": "John SMITH",
            "match_score": "100.0",
            "matched_by": "exact",
            "notes": "",
        }
    ]

    write_crosswalk(rows, out_path)
    loaded = load_existing_crosswalk(out_path)

    assert loaded == {("John Smith", "player"): rows[0]}


def test_load_existing_crosswalk_returns_empty_dict_when_file_missing(tmp_path: Path) -> None:
    assert load_existing_crosswalk(tmp_path / "does_not_exist.csv") == {}
