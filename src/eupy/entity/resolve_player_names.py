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

Pipeline (see specs/spec-player-name-linking.md for the full design):
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
3. Verdict stage -- every agent verdict recorded under
   `data/curated/player_name_verdicts/` (batch files, later batch wins on the same `name`; see
   `verdict_batches.py`) is applied on top, setting the row to `confirmed` / `rejected` /
   `no_match`. Producing verdicts is the `resolve-player-names` skill's job.

`role=head_coach` rows are filtered out of the master rows before matching --
the box-score dataset has no coach data, so matching them is structurally
impossible, and they never appear in the crosswalk.

Pure: the crosswalk is a function of the raw inputs + the verdict batches only; the previous
output is never read, so a rebuild from scratch reproduces it exactly.

Usage:
    python src/eupy/entity/resolve_player_names.py [--master PATH] [--boxscore PATH]
        [--verdicts-dir DIR] [--out PATH]

Inputs:
  - basketballsphere_prices: data/raw_data/fantasy_prices/basketballsphere_prices.csv
  - kaggle_data/euroleague_box_score: data/raw_data/kaggle_data/euroleague_box_score.csv
  - player_name_verdicts: data/curated/player_name_verdicts/
Sources: none
Outputs:
  - player_name_crosswalk: data/stage_01/player_name_crosswalk.csv
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
from eupy.entity.player_sources import BOXSCORE_PATH, MASTER_PATH, load_boxscore_spellings, load_master_rows
from eupy.entity.verdict_batches import apply_verdicts, load_batches, merge_verdicts

CROSSWALK_PATH = Path(__file__).resolve().parents[3] / "data" / "stage_01" / "player_name_crosswalk.csv"
VERDICTS_DIR = Path(__file__).resolve().parents[3] / "data" / "curated" / "player_name_verdicts"

CROSSWALK_FIELDNAMES = [
    "name",
    "boxscore_name",
    "match_status",
    "match_score",
    "matched_by",
    "notes",
]


def build_row(
    master_row: dict[str, str],
    spelling_player_ids: dict[str, set[str]],
    normalized_index: dict[str, set[str]],
    normalized_spellings: dict[str, str],
) -> dict[str, str]:
    """Build one fresh crosswalk row from a master row (no prior crosswalk state)."""
    base = {"name": master_row["name"]}

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


def build_crosswalk(
    master_rows: list[dict[str, str]],
    spelling_player_ids: dict[str, set[str]],
    verdicts: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    """Build the full crosswalk for the current master snapshot, then apply verdicts.

    Every row is computed fresh from the raw inputs; `verdicts` (merged by
    `name`, see `verdict_batches.merge_verdicts`) then overwrite the matching
    rows' resolution fields. Verdicts for names not in the snapshot are skipped.
    """
    normalized_index = build_normalized_index(spelling_player_ids)
    normalized_spellings = {spelling: normalize_name(spelling) for spelling in spelling_player_ids}

    crosswalk = [
        build_row(master_row, spelling_player_ids, normalized_index, normalized_spellings) for master_row in master_rows
    ]
    apply_verdicts({row["name"]: row for row in crosswalk}, verdicts, "boxscore_name")
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
    parser.add_argument("--boxscore", type=Path, default=BOXSCORE_PATH, help="euroleague_box_score.csv path")
    parser.add_argument("--verdicts-dir", type=Path, default=VERDICTS_DIR, help="Verdict batch directory")
    parser.add_argument("--out", type=Path, default=CROSSWALK_PATH, help="Crosswalk CSV output path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    master_rows = load_master_rows(args.master)
    spelling_player_ids = load_boxscore_spellings(args.boxscore)
    verdicts = merge_verdicts(load_batches(args.verdicts_dir), "name", "boxscore_name")

    rows = build_crosswalk(master_rows, spelling_player_ids, verdicts)
    write_crosswalk(rows, args.out)

    counts = Counter(row["match_status"] for row in rows)
    summary = ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
    stale = len(set(verdicts) - {row["name"] for row in rows})
    print(f"Wrote {len(rows)} rows to {args.out}: {summary} ({stale} verdict(s) for names not in this snapshot)")


if __name__ == "__main__":
    main()
