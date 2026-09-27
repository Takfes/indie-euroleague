#!/usr/bin/env python3
"""Resolve basketballsphere fantasy-price names to EuroLeague box-score display names.

`basketballsphere_prices.csv` (current-season fantasy prices) spells names
`"First Last"`; `euroleague_box_score.csv` (historical box scores, E2007-E2025)
spells them `"LAST, First"` with an upper-case surname. This script builds a
left-join **name map** from the former to the latter so downstream feature
work can attach historical box-score stats to current fantasy players. The
map carries no other dataset's attributes (no `club`/`position`/`price` from
the master, no `player_id` from the box score) -- only the two name strings
plus the resolution process's own metadata. Consumers who need `player_id`,
`club`, `price`, etc. read them from their respective source files directly.

Pipeline (see docs/specs/spec-player-name-linking.md for the full design):
1. Exact stage -- normalize both sides (strip accents, upper-case, letters/
   spaces only, reorder box-score names to "First Last") and exact-match the
   normalized string against the distinct box-score display-name spellings.
   Two ambiguity cases are NOT auto-matched, surfaced as `needs_review`
   instead:
   - `exact_collision` -- one raw spelling shared by more than one distinct
     `player_id` (two different real players could share a name string).
   - `exact_ambiguous_spelling` -- more than one distinct raw spelling shares
     one normalized value (e.g. an accented vs. unaccented variant).
2. Fuzzy stage -- for still-unmatched `role=player` rows, score the normalized
   name against every distinct box-score spelling using rapidfuzz. The top
   `FUZZY_LIMIT` candidates at or above `FUZZY_SCORE_CUTOFF` become a
   `needs_review` row (never auto-accepted); nothing above the floor becomes
   `no_candidate`. The cutoff was tuned against this repo's real ~77 misses:
   at 80 it catches clear reformattings (suffixes, nicknames, hyphenation)
   while mostly excluding same-surname noise; below ~80 the candidate quality
   drops sharply (see spec Analysis section).
3. Agent stage (skill) -- out of scope for this script; see
   `apply_player_name_verdicts.py` and the `resolve-player-names` skill.

`role=head_coach` rows pass through untouched as `match_status=not_applicable`
-- the box-score dataset has no coach data, so matching them is impossible.

Idempotency: rows the agent stage already resolved (`match_status` in
`confirmed` / `rejected` / `no_match`) are carried over to the new output
completely unchanged, keyed on `(name, role)`. There is nothing on the master
side (other than `name`/`role`, the key itself) that this artifact carries,
so there is nothing to refresh -- every other row (new, `exact`,
`needs_review`, `no_candidate`) is recomputed fresh.

Usage:
    python src/eupy/entity/resolve_player_names.py [--master PATH] [--boxscore PATH] [--out PATH]

Inputs: data/raw_data/fantasy_prices/basketballsphere_prices.csv,
    data/raw_data/kaggle_data/euroleague_box_score.csv.
Outputs: data/stage_01/player_name_crosswalk.csv (merged in place; existing
    agent-resolved rows are preserved unchanged, see idempotency note above).
Final: true -- feeds downstream feature work; symlinked into data/stage_99/.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path

from eupy.entity.matching import build_normalized_index, find_candidates, format_candidates_note, normalize_name

MASTER_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "raw_data" / "fantasy_prices" / "basketballsphere_prices.csv"
)
BOXSCORE_PATH = Path(__file__).resolve().parents[3] / "data" / "raw_data" / "kaggle_data" / "euroleague_box_score.csv"
CROSSWALK_PATH = Path(__file__).resolve().parents[3] / "data" / "stage_01" / "player_name_crosswalk.csv"

CROSSWALK_FIELDNAMES = [
    "name",
    "role",
    "boxscore_name",
    "match_status",
    "match_score",
    "matched_by",
    "notes",
]

RESOLVED_STATUSES = {"confirmed", "rejected", "no_match"}


def reorder_boxscore_name(name: str) -> str:
    """Reorder a box-score `"LAST, First"` name to `"First Last"`; passes through names with no comma."""
    if "," not in name:
        return name
    last, first = name.split(",", 1)
    return f"{first.strip()} {last.strip()}"


def load_master_rows(path: Path) -> list[dict[str, str]]:
    """Load basketballsphere_prices.csv rows (rank, name, club, position, price, role)."""
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_boxscore_spellings(path: Path) -> dict[str, set[str]]:
    """Load distinct box-score display-name spellings and the player_ids that use each.

    Keyed by the reordered `"First LAST"` spelling, excluding synthetic TOTAL
    rows. A spelling used by more than one distinct `player_id` signals a
    genuine name collision (two different real players, or an upstream
    data-quality duplicate) -- kept here for internal collision detection,
    even though `player_id` is never written to the crosswalk.
    """
    spellings: dict[str, set[str]] = defaultdict(set)
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["dorsal"] == "TOTAL":
                continue
            spellings[reorder_boxscore_name(row["player"])].add(row["player_id"])
    return dict(spellings)


def build_row(
    master_row: dict[str, str],
    spelling_player_ids: dict[str, set[str]],
    normalized_index: dict[str, set[str]],
    normalized_spellings: dict[str, str],
) -> dict[str, str]:
    """Build one fresh crosswalk row from a master row (no prior crosswalk state)."""
    base = {"name": master_row["name"], "role": master_row["role"]}

    if master_row["role"] == "head_coach":
        return {
            **base,
            "boxscore_name": "",
            "match_status": "not_applicable",
            "match_score": "",
            "matched_by": "not_applicable",
            "notes": "",
        }

    status, candidates, matched_by = find_candidates(
        normalize_name(master_row["name"]), spelling_player_ids, normalized_index, normalized_spellings
    )

    if status == "no_candidate":
        return {
            **base,
            "boxscore_name": "",
            "match_status": "no_candidate",
            "match_score": "",
            "matched_by": matched_by,
            "notes": "",
        }

    boxscore_name, score = candidates[0]
    if status == "exact":
        return {
            **base,
            "boxscore_name": boxscore_name,
            "match_status": "exact",
            "match_score": f"{score:.1f}",
            "matched_by": matched_by,
            "notes": "",
        }

    # needs_review: exact_collision, exact_ambiguous_spelling, or genuine fuzzy candidates.
    return {
        **base,
        "boxscore_name": boxscore_name,
        "match_status": "needs_review",
        "match_score": f"{score:.1f}",
        "matched_by": matched_by,
        "notes": f"candidates: {format_candidates_note(candidates)}",
    }


def load_existing_crosswalk(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    """Load an existing crosswalk keyed by `(name, role)`, or an empty dict if it doesn't exist yet."""
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {(row["name"], row["role"]): row for row in csv.DictReader(f)}


def build_crosswalk(
    master_rows: list[dict[str, str]],
    spelling_player_ids: dict[str, set[str]],
    existing: dict[tuple[str, str], dict[str, str]],
) -> list[dict[str, str]]:
    """Build the full crosswalk for the current master snapshot.

    Rows already resolved by the agent stage (`RESOLVED_STATUSES`) are
    carried over completely unchanged. Everything else is recomputed from
    scratch.
    """
    normalized_index = build_normalized_index(spelling_player_ids)
    normalized_spellings = {spelling: normalize_name(spelling) for spelling in spelling_player_ids}

    crosswalk = []
    for master_row in master_rows:
        key = (master_row["name"], master_row["role"])
        prior = existing.get(key)
        if prior is not None and prior["match_status"] in RESOLVED_STATUSES:
            crosswalk.append(dict(prior))
            continue
        crosswalk.append(build_row(master_row, spelling_player_ids, normalized_index, normalized_spellings))
    return crosswalk


def write_crosswalk(rows: list[dict[str, str]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CROSSWALK_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master", type=Path, default=MASTER_PATH, help="basketballsphere_prices.csv path")
    parser.add_argument("--boxscore", type=Path, default=BOXSCORE_PATH, help="euroleague_box_score.csv path")
    parser.add_argument("--out", type=Path, default=CROSSWALK_PATH, help="Crosswalk CSV output path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    master_rows = load_master_rows(args.master)
    spelling_player_ids = load_boxscore_spellings(args.boxscore)
    existing = load_existing_crosswalk(args.out)

    rows = build_crosswalk(master_rows, spelling_player_ids, existing)
    write_crosswalk(rows, args.out)

    counts = Counter(row["match_status"] for row in rows)
    summary = ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
    print(f"Wrote {len(rows)} rows to {args.out}: {summary}")


if __name__ == "__main__":
    main()
