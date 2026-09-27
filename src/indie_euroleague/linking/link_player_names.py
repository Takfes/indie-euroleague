#!/usr/bin/env python3
"""Link basketballsphere fantasy prices to EuroLeague box-score player identities.

`basketballsphere_prices.csv` (current-season fantasy prices) spells names
`"First Last"`; `euroleague_box_score.csv` (historical box scores, E2007-E2025)
spells them `"LAST, First"` with an upper-case surname. This script builds a
left-join crosswalk from the former to the latter so downstream feature work
can attach historical box-score stats to current fantasy players.

Pipeline (see docs/specs/spec-player-name-linking.md for the full design):
1. Exact stage -- normalize both sides (strip accents, upper-case, letters/
   spaces only, reorder box-score names to "First Last") and exact-match the
   normalized string. A normalized name shared by more than one distinct
   `player_id` in the box-score data (rare -- two cases in the current data)
   is NOT auto-matched; it is surfaced as `needs_review` with every candidate
   listed, since picking one would be a guess.
2. Fuzzy stage -- for still-unmatched `role=player` rows, score the normalized
   name against every distinct box-score player using rapidfuzz. The top
   `FUZZY_LIMIT` candidates at or above `FUZZY_SCORE_CUTOFF` become a
   `needs_review` row (never auto-accepted); nothing above the floor becomes
   `no_candidate`. The cutoff was tuned against this repo's real ~77 misses:
   at 80 it catches clear reformattings (suffixes, nicknames, hyphenation)
   while mostly excluding same-surname noise; below ~80 the candidate quality
   drops sharply (see spec Analysis section).
3. Agent stage (skill) -- out of scope for this script; see
   `apply_link_verdicts.py` and the `link-player-names` skill.

`role=head_coach` rows pass through untouched as `match_status=not_applicable`
-- the box-score dataset has no coach data, so matching them is impossible.

Idempotency: rows the agent stage already resolved (`match_status` in
`confirmed` / `rejected` / `no_match`) are carried over unchanged except for
`club` / `position` / `price`, which are refreshed from the current master
snapshot every run (prices change every round). Every other row -- new,
`exact`, `needs_review`, or `no_candidate` -- is recomputed fresh. Rows are
matched between runs by the `(name, role)` key.

Usage:
    python src/indie_euroleague/linking/link_player_names.py [--master PATH] [--boxscore PATH] [--out PATH]

Inputs: data/raw_data/fantasy_prices/basketballsphere_prices.csv,
    data/raw_data/kaggle_data/euroleague_box_score.csv.
Outputs: data/stage_01/player_name_crosswalk.csv (merged in place; existing
    agent-resolved rows are preserved, see idempotency note above).
Final: true -- feeds downstream feature work; symlinked into data/stage_99/.
"""

from __future__ import annotations

import argparse
import csv
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from rapidfuzz import fuzz, process

MASTER_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "raw_data" / "fantasy_prices" / "basketballsphere_prices.csv"
)
BOXSCORE_PATH = Path(__file__).resolve().parents[3] / "data" / "raw_data" / "kaggle_data" / "euroleague_box_score.csv"
CROSSWALK_PATH = Path(__file__).resolve().parents[3] / "data" / "stage_01" / "player_name_crosswalk.csv"

CROSSWALK_FIELDNAMES = [
    "name",
    "role",
    "club",
    "position",
    "price",
    "match_status",
    "player_id",
    "boxscore_name",
    "match_score",
    "matched_by",
    "notes",
]

RESOLVED_STATUSES = {"confirmed", "rejected", "no_match"}

FUZZY_LIMIT = 3
FUZZY_SCORE_CUTOFF = 80.0

# (player_id, boxscore_name, match_score)
Candidate = tuple[str, str, float]


def normalize_name(name: str) -> str:
    """Normalize a name for matching: strip accents, upper-case, letters/spaces only."""
    decomposed = unicodedata.normalize("NFKD", name)
    without_accents = "".join(c for c in decomposed if not unicodedata.combining(c))
    letters_and_spaces = re.sub(r"[^A-Za-z ]", " ", without_accents.upper())
    return re.sub(r"\s+", " ", letters_and_spaces).strip()


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


def load_boxscore_roster(path: Path) -> dict[str, str]:
    """Load one canonical `"First Last"` name per `player_id`, excluding synthetic TOTAL rows.

    Every `player_id` in this dataset maps to exactly one name spelling
    (verified against the current data), so this dedupe is safe.
    """
    roster: dict[str, str] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["dorsal"] == "TOTAL":
                continue
            roster[row["player_id"]] = reorder_boxscore_name(row["player"])
    return roster


def build_normalized_index(roster: dict[str, str]) -> dict[str, list[tuple[str, str]]]:
    """Group box-score `(player_id, name)` pairs by normalized name, for exact-match lookup."""
    index: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for player_id, name in roster.items():
        index[normalize_name(name)].append((player_id, name))
    return index


def find_candidates(
    normalized_name: str,
    roster: dict[str, str],
    normalized_roster: dict[str, str],
    normalized_index: dict[str, list[tuple[str, str]]],
    *,
    limit: int = FUZZY_LIMIT,
    score_cutoff: float = FUZZY_SCORE_CUTOFF,
) -> tuple[str, list[Candidate]]:
    """Resolve one normalized master name to a match status and its candidates.

    Tries an exact normalized-string match first: `exact` if exactly one
    distinct `player_id` shares that name, `needs_review` (all of them listed)
    if more than one does. Falls back to fuzzy top-`limit` candidates at or
    above `score_cutoff` (`needs_review`), or `no_candidate` if none clear it.
    """
    exact_hits = normalized_index.get(normalized_name, [])
    if len(exact_hits) == 1:
        player_id, boxscore_name = exact_hits[0]
        return "exact", [(player_id, boxscore_name, 100.0)]
    if len(exact_hits) > 1:
        return "needs_review", [(player_id, name, 100.0) for player_id, name in exact_hits]

    matches = process.extract(
        normalized_name, normalized_roster, scorer=fuzz.WRatio, limit=limit, score_cutoff=score_cutoff
    )
    if not matches:
        return "no_candidate", []
    return "needs_review", [(player_id, roster[player_id], score) for _, score, player_id in matches]


def format_candidates_note(candidates: list[Candidate]) -> str:
    """Render candidates as a human-readable note for `needs_review` rows."""
    return "; ".join(f"{name} ({player_id}, {score:.1f})" for player_id, name, score in candidates)


def build_row(
    master_row: dict[str, str],
    roster: dict[str, str],
    normalized_roster: dict[str, str],
    normalized_index: dict[str, list[tuple[str, str]]],
) -> dict[str, str]:
    """Build one fresh crosswalk row from a master row (no prior crosswalk state)."""
    base = {
        "name": master_row["name"],
        "role": master_row["role"],
        "club": master_row["club"],
        "position": master_row["position"],
        "price": master_row["price"],
    }

    if master_row["role"] == "head_coach":
        return {
            **base,
            "match_status": "not_applicable",
            "player_id": "",
            "boxscore_name": "",
            "match_score": "",
            "matched_by": "not_applicable",
            "notes": "",
        }

    status, candidates = find_candidates(
        normalize_name(master_row["name"]), roster, normalized_roster, normalized_index
    )

    if status == "no_candidate":
        return {
            **base,
            "match_status": "no_candidate",
            "player_id": "",
            "boxscore_name": "",
            "match_score": "",
            "matched_by": "fuzzy",
            "notes": "",
        }

    player_id, boxscore_name, score = candidates[0]
    if status == "exact":
        return {
            **base,
            "match_status": "exact",
            "player_id": player_id,
            "boxscore_name": boxscore_name,
            "match_score": f"{score:.1f}",
            "matched_by": "exact",
            "notes": "",
        }

    # needs_review: either an ambiguous exact match or genuine fuzzy candidates.
    matched_by = "exact_ambiguous" if score == 100.0 and len(candidates) > 1 else "fuzzy"
    return {
        **base,
        "match_status": "needs_review",
        "player_id": player_id,
        "boxscore_name": boxscore_name,
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
    roster: dict[str, str],
    existing: dict[tuple[str, str], dict[str, str]],
) -> list[dict[str, str]]:
    """Build the full crosswalk for the current master snapshot.

    Rows already resolved by the agent stage (`RESOLVED_STATUSES`) are carried
    over unchanged except for `club` / `position` / `price`, refreshed from
    the current snapshot. Everything else is recomputed from scratch.
    """
    normalized_roster = {player_id: normalize_name(name) for player_id, name in roster.items()}
    normalized_index = build_normalized_index(roster)

    crosswalk = []
    for master_row in master_rows:
        key = (master_row["name"], master_row["role"])
        prior = existing.get(key)
        if prior is not None and prior["match_status"] in RESOLVED_STATUSES:
            refreshed = dict(prior)
            refreshed["club"] = master_row["club"]
            refreshed["position"] = master_row["position"]
            refreshed["price"] = master_row["price"]
            crosswalk.append(refreshed)
            continue
        crosswalk.append(build_row(master_row, roster, normalized_roster, normalized_index))
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
    roster = load_boxscore_roster(args.boxscore)
    existing = load_existing_crosswalk(args.out)

    rows = build_crosswalk(master_rows, roster, existing)
    write_crosswalk(rows, args.out)

    counts = Counter(row["match_status"] for row in rows)
    summary = ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
    print(f"Wrote {len(rows)} rows to {args.out}: {summary}")


if __name__ == "__main__":
    main()
