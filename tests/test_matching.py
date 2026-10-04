"""Tests for the shared exact name-matching primitives."""

from __future__ import annotations

from eupy.entity.matching import build_normalized_index, find_candidates, normalize_name

# --- normalization -----------------------------------------------------------------


def test_normalize_name_strips_accents_and_upper_cases() -> None:
    assert normalize_name("Núñez Özil") == "NUNEZ OZIL"


def test_normalize_name_replaces_punctuation_with_space_not_deletion() -> None:
    # Hyphens/apostrophes become spaces so words don't glue into one token.
    assert normalize_name("Nigel Hayes-Davis") == "NIGEL HAYES DAVIS"
    assert normalize_name("D'Angelo Russell") == "D ANGELO RUSSELL"


def test_normalize_name_collapses_whitespace() -> None:
    assert normalize_name("  John   Smith  ") == "JOHN SMITH"


# --- candidate resolution -------------------------------------------------------------

# "Ambi GUOUS" is a genuine collision: two different owner ids share one raw spelling.
# "Lucas MARI" / "Lucas MARÍ" is a spelling ambiguity: one normalized value, two raw spellings.
SPELLING_OWNER_IDS = {
    "John SMITH": {"P001"},
    "Bob JONES": {"P002"},
    "Ambi GUOUS": {"P003", "P004"},
    "Lucas MARI": {"P010754"},
    "Lucas MARÍ": {"P011974"},
}


def _index() -> dict[str, set[str]]:
    return build_normalized_index(SPELLING_OWNER_IDS)


def test_find_candidates_exact_match_is_unique() -> None:
    status, candidates, matched_by = find_candidates(normalize_name("John Smith"), SPELLING_OWNER_IDS, _index())

    assert (status, matched_by) == ("exact", "exact")
    assert candidates == ["John SMITH"]


def test_find_candidates_collision_is_needs_review() -> None:
    status, candidates, matched_by = find_candidates(normalize_name("Ambi Guous"), SPELLING_OWNER_IDS, _index())

    assert (status, matched_by) == ("needs_review", "exact_collision")
    assert candidates == ["Ambi GUOUS"]


def test_find_candidates_ambiguous_spelling_lists_every_variant() -> None:
    status, candidates, matched_by = find_candidates(normalize_name("Lucas Mari"), SPELLING_OWNER_IDS, _index())

    assert (status, matched_by) == ("needs_review", "exact_ambiguous_spelling")
    assert set(candidates) == {"Lucas MARI", "Lucas MARÍ"}


def test_find_candidates_near_miss_is_no_candidate() -> None:
    # "Bob Jonez" is one letter away from "Bob Jones" but there is no approximate matching.
    status, candidates, matched_by = find_candidates(normalize_name("Bob Jonez"), SPELLING_OWNER_IDS, _index())

    assert (status, candidates, matched_by) == ("no_candidate", [], "exact")
