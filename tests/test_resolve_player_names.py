"""Tests for the player-name crosswalk's loading, row-building, and merge logic."""

from __future__ import annotations

import csv
from pathlib import Path

from eupy.entity.matching import build_normalized_index, normalize_name
from eupy.entity.resolve_player_names import (
    build_crosswalk,
    build_row,
    load_boxscore_spellings,
    load_existing_crosswalk,
    load_master_rows,
    reorder_boxscore_name,
    write_crosswalk,
)


def _master_row(name: str, role: str = "player") -> dict[str, str]:
    return {"rank": "1", "name": name, "club": "C", "position": "G", "price": "10.0", "role": role}


def test_reorder_boxscore_name_swaps_last_comma_first() -> None:
    assert reorder_boxscore_name("SMITH, John") == "John SMITH"


def test_reorder_boxscore_name_passes_through_when_no_comma() -> None:
    assert reorder_boxscore_name("John Smith") == "John Smith"


# --- loading -------------------------------------------------------------------------


def test_load_boxscore_spellings_excludes_total_rows_and_dedupes_by_spelling(tmp_path: Path) -> None:
    path = tmp_path / "box_score.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["player_id", "player", "dorsal"])
        writer.writeheader()
        writer.writerows([
            {"player_id": "P001", "player": "SMITH, John", "dorsal": "4"},
            {"player_id": "P001", "player": "SMITH, John", "dorsal": "4"},  # second event row, same player
            {"player_id": "ZAL", "player": "TEAM TOTAL", "dorsal": "TOTAL"},
        ])

    spellings = load_boxscore_spellings(path)

    assert spellings == {"John SMITH": {"P001"}}


def test_load_boxscore_spellings_groups_distinct_player_ids_sharing_one_spelling(tmp_path: Path) -> None:
    path = tmp_path / "box_score.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["player_id", "player", "dorsal"])
        writer.writeheader()
        writer.writerows([
            {"player_id": "P003", "player": "GUOUS, Ambi", "dorsal": "4"},
            {"player_id": "P004", "player": "GUOUS, Ambi", "dorsal": "5"},
        ])

    spellings = load_boxscore_spellings(path)

    assert spellings == {"Ambi GUOUS": {"P003", "P004"}}


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


# --- row building ----------------------------------------------------------------------

# "Ambi GUOUS" is a genuine collision: two different player_ids share one raw spelling.
# "Lucas MARI" / "Lucas MARÍ" is a spelling ambiguity: one normalized value, two raw spellings.
SPELLING_PLAYER_IDS = {
    "John SMITH": {"P001"},
    "Bob JONES": {"P002"},
    "Ambi GUOUS": {"P003", "P004"},
    "Lucas MARI": {"P010754"},
    "Lucas MARÍ": {"P011974"},
}


def _index() -> dict[str, set[str]]:
    return build_normalized_index(SPELLING_PLAYER_IDS)


def _normalized_spellings() -> dict[str, str]:
    return {spelling: normalize_name(spelling) for spelling in SPELLING_PLAYER_IDS}


def test_build_row_head_coach_is_not_applicable() -> None:
    row = build_row(
        _master_row("Some Coach", role="head_coach"), SPELLING_PLAYER_IDS, _index(), _normalized_spellings()
    )

    assert row["match_status"] == "not_applicable"
    assert row["matched_by"] == "not_applicable"
    assert row["boxscore_name"] == ""


def test_build_row_exact_match() -> None:
    row = build_row(_master_row("John Smith"), SPELLING_PLAYER_IDS, _index(), _normalized_spellings())

    assert row["match_status"] == "exact"
    assert row["boxscore_name"] == "John SMITH"
    assert row["matched_by"] == "exact"
    assert row["notes"] == ""


def test_build_row_no_candidate_leaves_match_fields_blank() -> None:
    row = build_row(_master_row("Nobody Realname"), SPELLING_PLAYER_IDS, _index(), _normalized_spellings())

    assert row["match_status"] == "no_candidate"
    assert row["boxscore_name"] == ""


def test_build_row_collision_records_candidate_in_notes() -> None:
    row = build_row(_master_row("Ambi Guous"), SPELLING_PLAYER_IDS, _index(), _normalized_spellings())

    assert row["match_status"] == "needs_review"
    assert row["matched_by"] == "exact_collision"
    assert "Ambi GUOUS" in row["notes"]


def test_build_row_ambiguous_spelling_records_every_variant_in_notes() -> None:
    row = build_row(_master_row("Lucas Mari"), SPELLING_PLAYER_IDS, _index(), _normalized_spellings())

    assert row["match_status"] == "needs_review"
    assert row["matched_by"] == "exact_ambiguous_spelling"
    assert "Lucas MARI" in row["notes"]
    assert "Lucas MARÍ" in row["notes"]


# --- crosswalk merge / idempotency -------------------------------------------------------


def test_build_crosswalk_preserves_agent_resolved_rows_byte_identical() -> None:
    master_rows = [_master_row("John Smith")]
    existing = {
        ("John Smith", "player"): {
            "name": "John Smith",
            "role": "player",
            "boxscore_name": "Someone Else",
            "match_status": "confirmed",
            "match_score": "77.0",
            "matched_by": "agent",
            "notes": "manually confirmed despite low fuzzy score",
        }
    }

    [row] = build_crosswalk(master_rows, SPELLING_PLAYER_IDS, existing)

    # Not recomputed even though "John Smith" has a real exact match -- carried over unchanged.
    assert row == existing[("John Smith", "player")]


def test_build_crosswalk_recomputes_non_resolved_statuses() -> None:
    master_rows = [_master_row("John Smith")]
    # Stale "exact" row with a wrong boxscore_name -- not a resolved status, so it must be recomputed.
    existing = {
        ("John Smith", "player"): {
            "name": "John Smith",
            "role": "player",
            "boxscore_name": "Wrong Name",
            "match_status": "exact",
            "match_score": "100.0",
            "matched_by": "exact",
            "notes": "",
        }
    }

    [row] = build_crosswalk(master_rows, SPELLING_PLAYER_IDS, existing)

    assert row["boxscore_name"] == "John SMITH"


def test_build_crosswalk_handles_new_player_not_in_existing_crosswalk() -> None:
    [row] = build_crosswalk([_master_row("John Smith")], SPELLING_PLAYER_IDS, existing={})

    assert row["match_status"] == "exact"
    assert row["boxscore_name"] == "John SMITH"


# --- CSV round trip ----------------------------------------------------------------------


def test_write_crosswalk_then_load_existing_crosswalk_round_trips(tmp_path: Path) -> None:
    out_path = tmp_path / "crosswalk.csv"
    rows = [
        {
            "name": "John Smith",
            "role": "player",
            "boxscore_name": "John SMITH",
            "match_status": "exact",
            "match_score": "100.0",
            "matched_by": "exact",
            "notes": "",
        }
    ]

    write_crosswalk(rows, out_path)
    loaded = load_existing_crosswalk(out_path)

    assert loaded == {("John Smith", "player"): rows[0]}


def test_write_crosswalk_header_has_no_player_id_club_position_or_price(tmp_path: Path) -> None:
    out_path = tmp_path / "crosswalk.csv"
    write_crosswalk(
        [
            {
                "name": "John Smith",
                "role": "player",
                "boxscore_name": "John SMITH",
                "match_status": "exact",
                "match_score": "100.0",
                "matched_by": "exact",
                "notes": "",
            }
        ],
        out_path,
    )

    header = out_path.read_text(encoding="utf-8").splitlines()[0]

    assert header == "name,role,boxscore_name,match_status,match_score,matched_by,notes"


def test_load_existing_crosswalk_returns_empty_dict_when_file_missing(tmp_path: Path) -> None:
    assert load_existing_crosswalk(tmp_path / "does_not_exist.csv") == {}
