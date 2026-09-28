#!/usr/bin/env python3
"""Resolve EuroLeague Fantasy stats-table player names to full names.

The fantasy stats table (`fantasy_stats/players.csv`, from
`fetch_euroleague_fantasy_stats.py`) abbreviates every player to
`"<initial>. <surname>"` (e.g. `"C. Jones"`) and tags them with the app's own
3-letter club code. Names collide across clubs (`"C. Jones"` PAR vs CZV), so the
crosswalk is keyed by the stable fantasy `player_id`, one row per player, and
carries the club code as context. It maps each player to a full `"First Last"`
name -- the spelling used by `basketballsphere_prices.csv`, so it joins straight
onto `player_name_crosswalk` for box-score history.

Pipeline (matching engine shared with `resolve_player_names.py`, see `matching.py`):
1. Prices stage -- pool = basketballsphere `role=player` names **at the player's
   own club** (`FANTASY_TEAM_CLUBS`), each abbreviated the same way
   (`"Carlik Jones"` -> `"C. Jones"`). Exact normalized match on a unique
   spelling -> `exact`, `source=prices`. This is the only auto-accept: initial +
   surname + club is a strong key.
2. Box-score stage -- only for players the prices stage did not settle. Pool =
   every distinct box-score spelling (E2007-E2025, no club filter, so the hit
   could be an older namesake), abbreviated the same way. Never auto-accepted:
   the top candidates (exact or fuzzy) become `needs_review`, `source=boxscore`.
   Box-score spellings are upper-case and are title-cased in the crosswalk so
   `resolved_name` reads like the prices spelling.
3. Agent stage -- `--verdicts PATH` applies a JSON list of
   `confirmed` / `rejected` / `no_match` verdicts keyed by `player_id`
   (`confirmed` needs `resolved_name`; every verdict needs `notes`). Same
   semantics as `apply_player_name_verdicts.py`, folded into this script because
   the crosswalk is small and has no other writer.

Idempotency: rows already resolved by a verdict (`confirmed` / `rejected` /
`no_match`) are carried over unchanged, keyed on `player_id`; everything else is
recomputed. Only round-agnostic identity (`player_id`, `name`, `team`) is read
from the stats table, so new rounds never change existing rows.

Usage:
    python src/eupy/entity/resolve_fantasy_stats_names.py [--players PATH] [--prices PATH]
        [--boxscore PATH] [--out PATH] [--verdicts PATH]

Inputs: data/raw_data/fantasy_stats/players.csv,
    data/raw_data/fantasy_prices/basketballsphere_prices.csv,
    data/raw_data/kaggle_data/euroleague_box_score.csv; optional verdicts JSON (`--verdicts`).
Outputs: data/stage_01/fantasy_stats_player_crosswalk.csv (merged in place; existing
    verdict-resolved rows are preserved unchanged).
Final: true -- feeds `normalize_fantasy_stats.py`; symlinked into data/stage_99/.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from eupy.entity.matching import build_normalized_index, find_candidates, format_candidates_note, normalize_name
from eupy.entity.resolve_player_names import BOXSCORE_PATH, MASTER_PATH, load_boxscore_spellings, load_master_rows

REPO_ROOT = Path(__file__).resolve().parents[3]
PLAYERS_PATH = REPO_ROOT / "data" / "raw_data" / "fantasy_stats" / "players.csv"
CROSSWALK_PATH = REPO_ROOT / "data" / "stage_01" / "fantasy_stats_player_crosswalk.csv"

CROSSWALK_FIELDNAMES = [
    "player_id",
    "name",
    "team",
    "resolved_name",
    "match_status",
    "match_score",
    "matched_by",
    "source",
    "notes",
]

RESOLVED_STATUSES = {"confirmed", "rejected", "no_match"}

# The fantasy app's club codes differ from the schedule's (PAR = Partizan, PBB = Paris; the schedule
# has PAR/PRS the other way round), so they are mapped to basketballsphere club names by hand.
# Verified against every player matched by initial + surname: 20/20 codes vote for one club, no conflicts.
FANTASY_TEAM_CLUBS = {
    "ASV": "ASVEL",
    "BAR": "Barcelona",
    "BAY": "Bayern Munich",
    "BJK": "Besiktas",
    "CZV": "Crvena Zvezda",
    "DUB": "Dubai",
    "EFS": "Anadolu Efes",
    "FBT": "Fenerbahce",
    "HTA": "Hapoel Tel Aviv",
    "KBA": "Baskonia",
    "MIL": "Milano",
    "MTA": "Maccabi Tel Aviv",
    "OLY": "Olympiacos",
    "PAO": "Panathinaikos",
    "PAR": "Partizan",
    "PBB": "Paris",
    "RMB": "Real Madrid",
    "VBC": "Valencia",
    "VIR": "Virtus Bologna",
    "ZAL": "Zalgiris",
}


def abbreviate_name(name: str) -> str:
    """Abbreviate `"First Last"` to `"F. Last"`: first whitespace token -> its initial, rest kept.

    Splitting on whitespace (not hyphens) keeps compound first names whole, so
    `"Marc-Owen Fodzo Dada"` -> `"M. Fodzo Dada"`. Already-abbreviated names pass through.
    """
    first, _, rest = name.strip().partition(" ")
    if not rest:
        return name.strip()
    return f"{first[0]}. {rest.strip()}"


def load_fantasy_players(path: Path) -> list[dict[str, str]]:
    """Load one `{player_id, name, team}` record per distinct fantasy `player_id`, ordered by id."""
    seen: dict[str, dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            seen.setdefault(row["player_id"], {"player_id": row["player_id"], "name": row["name"], "team": row["team"]})
    return sorted(seen.values(), key=lambda r: int(r["player_id"]))


def abbreviated_pool(full_names: list[str]) -> dict[str, set[str]]:
    """Build a matching pool `{abbreviated spelling: {full names}}` from full names."""
    pool: dict[str, set[str]] = defaultdict(set)
    for full in full_names:
        pool[abbreviate_name(full)].add(full)
    return dict(pool)


def _resolve_in_pool(name: str, pool: dict[str, set[str]]) -> tuple[str, list[tuple[str, float]], str]:
    """Run the shared exact -> fuzzy engine over `pool`; candidates are returned as full names."""
    normalized_index = build_normalized_index(pool)
    normalized_spellings = {spelling: normalize_name(spelling) for spelling in pool}
    status, candidates, matched_by = find_candidates(normalize_name(name), pool, normalized_index, normalized_spellings)
    full_candidates = [(full, score) for abbrev, score in candidates for full in sorted(pool[abbrev])]
    return status, full_candidates, matched_by


def build_row(
    player: dict[str, str],
    club_pools: dict[str, dict[str, set[str]]],
    boxscore_pool: dict[str, set[str]],
) -> dict[str, str]:
    """Build one fresh crosswalk row for a fantasy player (no prior verdict state)."""
    base = {"player_id": player["player_id"], "name": player["name"], "team": player["team"]}
    open_row = {"resolved_name": "", "match_score": "", "matched_by": "", "source": "", "notes": ""}

    club = FANTASY_TEAM_CLUBS.get(player["team"])
    prices_status, prices_candidates, prices_by = ("no_candidate", [], "fuzzy")
    if club is not None and club in club_pools:
        prices_status, prices_candidates, prices_by = _resolve_in_pool(player["name"], club_pools[club])

    if prices_status == "exact":
        full, score = prices_candidates[0]
        return {
            **base,
            **open_row,
            "resolved_name": full,
            "match_status": "exact",
            "match_score": f"{score:.1f}",
            "matched_by": prices_by,
            "source": "prices",
        }

    if prices_status == "needs_review":
        full, score = prices_candidates[0]
        return {
            **base,
            **open_row,
            "resolved_name": full,
            "match_status": "needs_review",
            "match_score": f"{score:.1f}",
            "matched_by": prices_by,
            "source": "prices",
            "notes": f"candidates: {format_candidates_note(prices_candidates)}",
        }

    box_status, box_candidates, box_by = _resolve_in_pool(player["name"], boxscore_pool)
    if box_status == "no_candidate":
        return {**base, **open_row, "match_status": "no_candidate", "matched_by": box_by}

    # Box-score hits are never auto-accepted: no club filter, so even an exact hit can be an older namesake.
    box_candidates = [(full.title(), score) for full, score in box_candidates]
    full, score = box_candidates[0]
    return {
        **base,
        **open_row,
        "resolved_name": full,
        "match_status": "needs_review",
        "match_score": f"{score:.1f}",
        "matched_by": box_by,
        "source": "boxscore",
        "notes": f"candidates: {format_candidates_note(box_candidates)}",
    }


def load_existing_crosswalk(path: Path) -> dict[str, dict[str, str]]:
    """Load an existing crosswalk keyed by `player_id`, or an empty dict if it doesn't exist yet."""
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {row["player_id"]: row for row in csv.DictReader(f)}


def build_crosswalk(
    players: list[dict[str, str]],
    prices_rows: list[dict[str, str]],
    boxscore_spellings: dict[str, set[str]],
    existing: dict[str, dict[str, str]],
) -> list[dict[str, str]]:
    """Build the full crosswalk; verdict-resolved rows are carried over unchanged, the rest recomputed."""
    club_names: dict[str, list[str]] = defaultdict(list)
    for row in prices_rows:
        club_names[row["club"]].append(row["name"])
    club_pools = {club: abbreviated_pool(names) for club, names in club_names.items()}
    boxscore_pool = abbreviated_pool(list(boxscore_spellings))

    crosswalk = []
    for player in players:
        prior = existing.get(player["player_id"])
        if prior is not None and prior["match_status"] in RESOLVED_STATUSES:
            crosswalk.append({field: prior.get(field, "") for field in CROSSWALK_FIELDNAMES})
            continue
        crosswalk.append(build_row(player, club_pools, boxscore_pool))
    return crosswalk


def apply_verdicts(rows_by_key: dict[str, dict[str, str]], verdicts: list[dict[str, Any]]) -> None:
    """Apply agent verdicts onto crosswalk rows in place, keyed by `player_id`.

    Raises:
        ValueError: If a verdict targets an unknown `player_id`, uses a status other than
            `confirmed` / `rejected` / `no_match`, is `confirmed` without a `resolved_name`
            (or non-confirmed with one), or has no `notes` rationale.
    """
    for verdict in verdicts:
        key = str(verdict["player_id"])
        if key not in rows_by_key:
            raise ValueError(f"Verdict targets a player_id not in the crosswalk: {key!r}")

        status = verdict["match_status"]
        if status not in RESOLVED_STATUSES:
            raise ValueError(f"match_status must be one of {sorted(RESOLVED_STATUSES)}, got {status!r}")

        resolved_name = verdict.get("resolved_name", "")
        if status == "confirmed" and not resolved_name:
            raise ValueError(f"Verdict for {key!r} is 'confirmed' but has no resolved_name")
        if status != "confirmed" and resolved_name:
            raise ValueError(f"Verdict for {key!r} is {status!r} but carries a resolved_name")
        if not verdict.get("notes"):
            raise ValueError(f"Verdict for {key!r} has no notes -- every agent verdict needs a brief rationale")

        match_score = verdict.get("match_score", "")
        row = rows_by_key[key]
        row["match_status"] = status
        row["resolved_name"] = resolved_name
        row["match_score"] = f"{match_score:.1f}" if isinstance(match_score, int | float) else str(match_score)
        row["matched_by"] = verdict.get("matched_by", "agent")
        row["source"] = verdict.get("source", "agent")
        row["notes"] = verdict["notes"]


def write_crosswalk(rows: list[dict[str, str]], out_path: Path) -> None:
    """Write the crosswalk CSV (creating the parent directory)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CROSSWALK_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--players", type=Path, default=PLAYERS_PATH, help="fantasy_stats/players.csv path")
    parser.add_argument("--prices", type=Path, default=MASTER_PATH, help="basketballsphere_prices.csv path")
    parser.add_argument("--boxscore", type=Path, default=BOXSCORE_PATH, help="euroleague_box_score.csv path")
    parser.add_argument("--out", type=Path, default=CROSSWALK_PATH, help="Crosswalk CSV output path")
    parser.add_argument("--verdicts", type=Path, help="JSON list of agent verdicts to apply after resolving")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    existing = load_existing_crosswalk(args.out)
    rows = build_crosswalk(
        load_fantasy_players(args.players),
        load_master_rows(args.prices),
        load_boxscore_spellings(args.boxscore),
        existing,
    )
    if args.verdicts:
        apply_verdicts({row["player_id"]: row for row in rows}, json.loads(args.verdicts.read_text(encoding="utf-8")))
    write_crosswalk(rows, args.out)

    counts = Counter(row["match_status"] for row in rows)
    summary = ", ".join(f"{status}={count}" for status, count in sorted(counts.items()))
    print(f"Wrote {len(rows)} rows to {args.out}: {summary}")


if __name__ == "__main__":
    main()
