#!/usr/bin/env python3
"""Resolve basketballsphere fantasy-price club names to EuroLeague box-score team names.

`basketballsphere_prices.csv` (current-season fantasy prices) carries one
`club` value per player row. `euroleague_box_score.csv` (historical box
scores, E2007-E2025) has no team display-name column at all -- only a stable
3-letter `team_id`. Team display names live in `euroleague_header.csv`
(`team_a`/`team_b` + `team_id_a`/`team_id_b`, one row per game). This script
builds a left-join **name map** from the master's distinct `club` values to a
box-score team display-name spelling, so downstream feature work can attach
historical team-level stats to current fantasy clubs. The map carries no
other attribute from either source (no `team_id`, no `position`/`price`/
`role`) -- only the two name strings plus the resolution process's own
metadata. Consumers who need `team_id` resolve it themselves from
`euroleague_header.csv` (`team_a`/`team_b` <-> `team_id_a`/`team_id_b` are in
the same row, a trivial reverse lookup).

Pipeline (see specs/spec-team-name-linking.md for the full design; the
exact->fuzzy engine itself lives in `matching.py` and is shared with
`resolve_player_names.py`):
1. Exact stage -- normalize both sides and exact-match the normalized
   `club` string against the distinct box-score team-name spellings pooled
   from `euroleague_header.csv`. Two ambiguity cases are NOT auto-matched,
   surfaced as `needs_review` instead:
   - `exact_collision` -- one raw spelling shared by more than one distinct
     `team_id` (not observed in real data today, but still checked).
   - `exact_ambiguous_spelling` -- more than one distinct raw spelling
     shares one normalized value.
2. Fuzzy stage -- for still-unmatched clubs, score the normalized name
   against every distinct box-score team-name spelling using rapidfuzz. The
   top `FUZZY_LIMIT` candidates at or above `FUZZY_SCORE_CUTOFF` become a
   `needs_review` row (never auto-accepted); nothing above the floor becomes
   `no_candidate`. Team names churn heavily by sponsor/season (e.g.
   `TAU CERAMICA` -> `BASKONIA`), so many real misses need agent domain
   knowledge rather than string similarity -- see the `resolve-team-names`
   skill.
3. Verdict stage -- every agent verdict recorded under
   `data/curated/team_name_verdicts/` (batch files, later batch wins on the same `name`; see
   `verdict_batches.py`) is applied on top, setting the row to `confirmed` / `rejected` /
   `no_match`. Producing verdicts is the `resolve-team-names` skill's job.

Pure: the crosswalk is a function of the raw inputs + the verdict batches only; the previous
output is never read, so a rebuild from scratch reproduces it exactly.

Usage:
    python src/eupy/entity/resolve_team_names.py [--master PATH] [--header PATH]
        [--verdicts-dir DIR] [--out PATH]

Inputs:
  - basketballsphere_prices: data/raw_data/fantasy_prices/basketballsphere_prices.csv
  - kaggle_data/euroleague_header: data/raw_data/kaggle_data/euroleague_header.csv
  - team_name_verdicts: data/curated/team_name_verdicts/
Sources: none
Outputs:
  - team_name_crosswalk: data/stage_01/team_name_crosswalk.csv
Final: true
Impure: false
Notes: Verdict batches are applied in file-name order; a later batch wins on the same name.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from eupy.entity.matching import build_normalized_index, find_candidates, format_candidates_note, normalize_name
from eupy.entity.verdict_batches import apply_verdicts, load_batches, merge_verdicts

MASTER_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "raw_data" / "fantasy_prices" / "basketballsphere_prices.csv"
)
HEADER_PATH = Path(__file__).resolve().parents[3] / "data" / "raw_data" / "kaggle_data" / "euroleague_header.csv"
CROSSWALK_PATH = Path(__file__).resolve().parents[3] / "data" / "stage_01" / "team_name_crosswalk.csv"
VERDICTS_DIR = Path(__file__).resolve().parents[3] / "data" / "curated" / "team_name_verdicts"

CROSSWALK_FIELDNAMES = [
    "name",
    "boxscore_team_name",
    "match_status",
    "match_score",
    "matched_by",
    "notes",
]


def load_master_clubs(path: Path) -> list[str]:
    """Load the distinct, sorted `club` values from basketballsphere_prices.csv."""
    with path.open(newline="", encoding="utf-8") as f:
        clubs = {row["club"] for row in csv.DictReader(f)}
    return sorted(clubs)


def load_boxscore_team_spellings(path: Path) -> dict[str, set[str]]:
    """Load distinct box-score team display-name spellings and the team_ids that use each.

    Pooled from both `(team_a, team_id_a)` and `(team_b, team_id_b)` pairs
    across every `euroleague_header.csv` row. A spelling used by more than
    one distinct `team_id` signals a genuine name collision -- kept here for
    internal collision detection, even though `team_id` is never written to
    the crosswalk.
    """
    spellings: dict[str, set[str]] = defaultdict(set)
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            spellings[row["team_a"]].add(row["team_id_a"])
            spellings[row["team_b"]].add(row["team_id_b"])
    return dict(spellings)


def build_row(
    club: str,
    spelling_team_ids: dict[str, set[str]],
    normalized_index: dict[str, set[str]],
    normalized_spellings: dict[str, str],
) -> dict[str, str]:
    """Build one fresh crosswalk row from a master `club` value (no prior crosswalk state)."""
    status, candidates, matched_by = find_candidates(
        normalize_name(club), spelling_team_ids, normalized_index, normalized_spellings
    )

    if status == "no_candidate":
        return {
            "name": club,
            "boxscore_team_name": "",
            "match_status": "no_candidate",
            "match_score": "",
            "matched_by": matched_by,
            "notes": "",
        }

    boxscore_team_name, score = candidates[0]
    if status == "exact":
        return {
            "name": club,
            "boxscore_team_name": boxscore_team_name,
            "match_status": "exact",
            "match_score": f"{score:.1f}",
            "matched_by": matched_by,
            "notes": "",
        }

    # needs_review: exact_collision, exact_ambiguous_spelling, or genuine fuzzy candidates.
    return {
        "name": club,
        "boxscore_team_name": boxscore_team_name,
        "match_status": "needs_review",
        "match_score": f"{score:.1f}",
        "matched_by": matched_by,
        "notes": f"candidates: {format_candidates_note(candidates)}",
    }


def build_crosswalk(
    clubs: list[str],
    spelling_team_ids: dict[str, set[str]],
    verdicts: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    """Build the full crosswalk for the current master snapshot, then apply verdicts.

    Every row is computed fresh from the raw inputs; `verdicts` (merged by
    `name`, see `verdict_batches.merge_verdicts`) then overwrite the matching
    rows' resolution fields. Verdicts for clubs not in the snapshot are skipped.
    """
    normalized_index = build_normalized_index(spelling_team_ids)
    normalized_spellings = {spelling: normalize_name(spelling) for spelling in spelling_team_ids}

    crosswalk = [build_row(club, spelling_team_ids, normalized_index, normalized_spellings) for club in clubs]
    apply_verdicts({row["name"]: row for row in crosswalk}, verdicts, "boxscore_team_name")
    return crosswalk


def write_crosswalk(rows: list[dict[str, str]], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CROSSWALK_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--master", type=Path, default=MASTER_PATH, help="basketballsphere_prices.csv path")
    parser.add_argument("--header", type=Path, default=HEADER_PATH, help="euroleague_header.csv path")
    parser.add_argument("--verdicts-dir", type=Path, default=VERDICTS_DIR, help="Verdict batch directory")
    parser.add_argument("--out", type=Path, default=CROSSWALK_PATH, help="Crosswalk CSV output path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    clubs = load_master_clubs(args.master)
    spelling_team_ids = load_boxscore_team_spellings(args.header)
    verdicts = merge_verdicts(load_batches(args.verdicts_dir), "name", "boxscore_team_name")

    rows = build_crosswalk(clubs, spelling_team_ids, verdicts)
    write_crosswalk(rows, args.out)

    counts = Counter(row["match_status"] for row in rows)
    summary = ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
    stale = len(set(verdicts) - {row["name"] for row in rows})
    print(f"Wrote {len(rows)} rows to {args.out}: {summary} ({stale} verdict(s) for clubs not in this snapshot)")


if __name__ == "__main__":
    main()
