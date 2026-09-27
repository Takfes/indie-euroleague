"""Tests for the shared exact -> fuzzy name-matching engine."""

from __future__ import annotations

from eupy.entity.matching import build_normalized_index, find_candidates, format_candidates_note, normalize_name

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


def _normalized_spellings() -> dict[str, str]:
    return {spelling: normalize_name(spelling) for spelling in SPELLING_OWNER_IDS}


def test_find_candidates_exact_match_is_unique() -> None:
    status, candidates, matched_by = find_candidates(
        normalize_name("John Smith"), SPELLING_OWNER_IDS, _index(), _normalized_spellings()
    )

    assert (status, matched_by) == ("exact", "exact")
    assert candidates == [("John SMITH", 100.0)]


def test_find_candidates_collision_is_needs_review() -> None:
    status, candidates, matched_by = find_candidates(
        normalize_name("Ambi Guous"), SPELLING_OWNER_IDS, _index(), _normalized_spellings()
    )

    assert (status, matched_by) == ("needs_review", "exact_collision")
    assert candidates == [("Ambi GUOUS", 100.0)]


def test_find_candidates_ambiguous_spelling_lists_every_variant() -> None:
    status, candidates, matched_by = find_candidates(
        normalize_name("Lucas Mari"), SPELLING_OWNER_IDS, _index(), _normalized_spellings()
    )

    assert (status, matched_by) == ("needs_review", "exact_ambiguous_spelling")
    assert {name for name, _ in candidates} == {"Lucas MARI", "Lucas MARÍ"}
    assert all(score == 100.0 for _, score in candidates)


def test_find_candidates_fuzzy_match_above_cutoff_is_needs_review() -> None:
    # "Bob Jonez" is a one-letter edit away from pool spelling "Bob Jones".
    status, candidates, matched_by = find_candidates(
        normalize_name("Bob Jonez"), SPELLING_OWNER_IDS, _index(), _normalized_spellings(), score_cutoff=50.0
    )

    assert (status, matched_by) == ("needs_review", "fuzzy")
    assert candidates[0][0] == "Bob JONES"


def test_find_candidates_below_cutoff_is_no_candidate() -> None:
    # Same near-match as above, but a cutoff no real-world score would clear.
    status, candidates, matched_by = find_candidates(
        normalize_name("Bob Jonez"), SPELLING_OWNER_IDS, _index(), _normalized_spellings(), score_cutoff=99.9
    )

    assert (status, matched_by) == ("no_candidate", "fuzzy")
    assert candidates == []


def test_find_candidates_respects_limit() -> None:
    spelling_owner_ids = {"Bob Jones": {"P001"}, "Bob Jonas": {"P002"}, "Bob Jonis": {"P003"}, "Bob Jonus": {"P004"}}
    normalized_spellings = {s: normalize_name(s) for s in spelling_owner_ids}
    index = build_normalized_index(spelling_owner_ids)

    status, candidates, matched_by = find_candidates(
        normalize_name("Bob Jonez"), spelling_owner_ids, index, normalized_spellings, limit=2, score_cutoff=0.0
    )

    assert (status, matched_by) == ("needs_review", "fuzzy")
    assert len(candidates) == 2


# --- candidate formatting --------------------------------------------------------------


def test_format_candidates_note_joins_name_and_score() -> None:
    assert format_candidates_note([("Bob JONES", 91.0), ("Bob JONAS", 80.5)]) == "Bob JONES (91.0); Bob JONAS (80.5)"
