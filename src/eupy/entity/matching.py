"""Shared exact -> fuzzy name-matching engine for entity-resolution pipelines.

Generic over a spelling pool shaped `{spelling: {owner_id, ...}}` -- an
"owner_id" is whatever uniquely identifies the real-world entity behind a
spelling (a `player_id` for the player crosswalk, a `team_id` for the team
crosswalk). Used by both `resolve_player_names.py` and
`resolve_team_names.py`, which differ only in where the pool comes from and
what they call the matched output column.

Pipeline:
1. Exact stage (`find_candidates`'s first branch) -- normalize both sides
   (strip accents, upper-case, letters/spaces only) and exact-match the
   normalized string against the distinct pool spellings. Two ambiguity
   cases are NOT auto-matched, surfaced as `needs_review` instead:
   - `exact_collision` -- one raw spelling shared by more than one distinct
     owner id (two different real-world entities could share a name string).
   - `exact_ambiguous_spelling` -- more than one distinct raw spelling
     shares one normalized value (e.g. an accented vs. unaccented variant).
2. Fuzzy stage -- for still-unmatched names, score the normalized name
   against every distinct pool spelling using rapidfuzz. The top
   `FUZZY_LIMIT` candidates at or above `FUZZY_SCORE_CUTOFF` become a
   `needs_review` row (never auto-accepted); nothing above the floor
   becomes `no_candidate`.
"""

from __future__ import annotations

import re
import unicodedata
from collections import defaultdict

from rapidfuzz import fuzz, process

FUZZY_LIMIT = 3
FUZZY_SCORE_CUTOFF = 80.0

# (spelling, match_score)
Candidate = tuple[str, float]


def normalize_name(name: str) -> str:
    """Normalize a name for matching: strip accents, upper-case, letters/spaces only."""
    decomposed = unicodedata.normalize("NFKD", name)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    letters_and_spaces = re.sub(r"[^A-Za-z ]", " ", without_accents.upper())
    return re.sub(r"\s+", " ", letters_and_spaces).strip()


def build_normalized_index(spelling_owner_ids: dict[str, set[str]]) -> dict[str, set[str]]:
    """Group distinct pool spellings by normalized name, for exact-match lookup."""
    index: dict[str, set[str]] = defaultdict(set)
    for spelling in spelling_owner_ids:
        index[normalize_name(spelling)].add(spelling)
    return dict(index)


def find_candidates(
    normalized_name: str,
    spelling_owner_ids: dict[str, set[str]],
    normalized_index: dict[str, set[str]],
    normalized_spellings: dict[str, str],
    *,
    limit: int = FUZZY_LIMIT,
    score_cutoff: float = FUZZY_SCORE_CUTOFF,
) -> tuple[str, list[Candidate], str]:
    """Resolve one normalized name to a match status, its candidates, and how it was matched.

    Tries an exact normalized-string match first:
    - Exactly one distinct raw spelling, used by exactly one owner id ->
      `exact`.
    - Exactly one distinct raw spelling, used by more than one owner id ->
      `needs_review` / `exact_collision` (picking one would guess which
      real-world entity it is).
    - More than one distinct raw spelling sharing the normalized value (e.g.
      an accented variant) -> `needs_review` / `exact_ambiguous_spelling`,
      every spelling listed.
    Falls back to fuzzy top-`limit` candidates at or above `score_cutoff`
    (`needs_review` / `fuzzy`), or `no_candidate` if none clear it.
    """
    spellings = normalized_index.get(normalized_name, set())
    if len(spellings) == 1:
        spelling = next(iter(spellings))
        if len(spelling_owner_ids[spelling]) > 1:
            return "needs_review", [(spelling, 100.0)], "exact_collision"
        return "exact", [(spelling, 100.0)], "exact"
    if len(spellings) > 1:
        candidates = [(spelling, 100.0) for spelling in sorted(spellings)]
        return "needs_review", candidates, "exact_ambiguous_spelling"

    matches = process.extract(
        normalized_name, normalized_spellings, scorer=fuzz.WRatio, limit=limit, score_cutoff=score_cutoff
    )
    if not matches:
        return "no_candidate", [], "fuzzy"
    candidates = [(spelling, score) for _, score, spelling in matches]
    return "needs_review", candidates, "fuzzy"


def format_candidates_note(candidates: list[Candidate]) -> str:
    """Render candidates as a human-readable note for `needs_review` rows."""
    return "; ".join(f"{name} ({score:.1f})" for name, score in candidates)
