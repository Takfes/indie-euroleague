"""Name-matching primitives shared by the entity module.

Library module (no I/O header, never run directly):

- `normalize_name` reduces a spelling to a comparison key (accents stripped, upper-case, letters and spaces only);
  the `TeamNames` / `PlayerNames` lookups and the engine all compare through it.
- `find_candidates` is the exact-match candidate finder of the player map builder (`build_player_name_map.py`).
  It is generic over a spelling pool shaped `{spelling: {owner, ...}}`, an "owner" being whatever identifies the
  real-world entity behind a spelling.
- `load_stats_players` reads the distinct `(team, name)` pairs of the fantasy stats players file; the builder and
  the check share it.
- `UnknownNameError` / `AmbiguousNameError` are what the lookup classes raise; `OPEN_STATUSES` are the match
  statuses of entries still awaiting a decision.

Candidate finder: normalize both sides and exact-match the normalized string against the distinct pool
spellings. Two ambiguity cases are NOT auto-matched, surfaced as `needs_review` instead:
- `exact_collision` -- one raw spelling shared by more than one distinct owner.
- `exact_ambiguous_spelling` -- more than one distinct raw spelling shares one normalized value (e.g. an
  accented vs. unaccented variant).
No match is `no_candidate`; there is no approximate matching.
"""

from __future__ import annotations

import csv
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

OPEN_STATUSES = frozenset({"needs_review", "no_candidate"})

# (stats name, stats team code) per distinct stats player
StatsPlayer = dict[str, str]


def load_stats_players(path: Path) -> list[StatsPlayer]:
    """One `{name, team}` record per distinct pair in the stats players file, ordered by team then name."""
    with path.open(newline="", encoding="utf-8") as f:
        pairs = {(r["team"], r["name"]) for r in csv.DictReader(f)}
    return [{"team": team, "name": name} for team, name in sorted(pairs)]


class UnknownNameError(LookupError):
    """The spelling is not a variant of any entry in the map."""


class AmbiguousNameError(LookupError):
    """The spelling is a variant of more than one entry; more context (e.g. the club) is needed."""


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
    normalized_name: str, spelling_owner_ids: dict[str, set[str]], normalized_index: dict[str, set[str]]
) -> tuple[str, list[str], str]:
    """Resolve one normalized name to a match status, its candidate spellings, and how it was matched.

    - Exactly one distinct raw spelling, used by exactly one owner id -> `exact`.
    - Exactly one distinct raw spelling, used by more than one owner id -> `needs_review` /
      `exact_collision` (picking one would guess which real-world entity it is).
    - More than one distinct raw spelling sharing the normalized value (e.g. an accented variant) ->
      `needs_review` / `exact_ambiguous_spelling`, every spelling listed.
    - Otherwise `no_candidate`.
    """
    spellings = normalized_index.get(normalized_name, set())
    if len(spellings) == 1:
        spelling = next(iter(spellings))
        if len(spelling_owner_ids[spelling]) > 1:
            return "needs_review", [spelling], "exact_collision"
        return "exact", [spelling], "exact"
    if len(spellings) > 1:
        return "needs_review", sorted(spellings), "exact_ambiguous_spelling"
    return "no_candidate", [], "exact"
