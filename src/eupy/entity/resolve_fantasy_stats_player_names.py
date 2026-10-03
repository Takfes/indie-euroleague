#!/usr/bin/env python3
"""Resolve fantasy stats player names (`"C. Jones"` + club code) to full names.

`fantasy_stats/players` abbreviates every player and uses the fantasy app's own club codes; names
collide across clubs, so the crosswalk is keyed by the fantasy `player_id`. `resolved_name` is the
full `"First Last"` spelling of `basketballsphere_prices`, which joins onto `player_name_crosswalk`.

Pipeline (engine shared with `resolve_player_names.py`, see `matching.py`):
1. Prices stage -- pool = basketballsphere players at the player's own club (`FANTASY_TEAM_CLUBS`),
   abbreviated the same way. A unique exact match is the only auto-accept (`exact`).
2. Box-score stage -- for the rest, pool = every box-score spelling (no club filter, so a hit can be
   an older namesake). Never auto-accepted: candidates become `needs_review` (title-cased).
3. Verdict stage -- every verdict batch under `data/curated/fantasy_stats_player_name_verdicts/`
   (records `{player_id, match_status, resolved_name, notes}`, status `confirmed` / `rejected` /
   `no_match`, `resolved_name` only on `confirmed`; see `verdict_batches.py`) is applied on top,
   a later batch winning on the same `player_id`.

Pure: the crosswalk is a function of the raw inputs + the verdict batches only; the previous output is
never read, so a rebuild from scratch reproduces it exactly.

Usage:
    python src/eupy/entity/resolve_fantasy_stats_player_names.py [--verdicts-dir DIR] [--out PATH]

Inputs:
  - fantasy_stats/players: data/raw_data/fantasy_stats/players.csv
  - basketballsphere_prices: data/raw_data/fantasy_prices/basketballsphere_prices.csv
  - kaggle_data/euroleague_box_score: data/raw_data/kaggle_data/euroleague_box_score.csv
  - fantasy_stats_player_name_verdicts: data/curated/fantasy_stats_player_name_verdicts/
Sources: none
Outputs:
  - fantasy_stats_player_name_crosswalk: data/stage_01/fantasy_stats_player_name_crosswalk.csv
Final: true
Impure: false
Notes: Verdict batches are applied in file-name order; a later batch wins on the same player_id.
  Column notes. One row per fantasy player_id: player_id, name (abbreviated, e.g. C. Jones),
  team (fantasy club code), resolved_name (full name), match_status (exact / confirmed / no_match /
  rejected; needs_review / no_candidate are transient), match_score, matched_by, notes. Auto-accepts
  only an exact initial + surname match within the player's own club in basketballsphere_prices;
  every box-score-derived or fuzzy match goes through a verdict.
"""

from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from eupy.entity.matching import build_normalized_index, find_candidates, format_candidates_note, normalize_name
from eupy.entity.resolve_player_names import BOXSCORE_PATH, MASTER_PATH, load_boxscore_spellings, load_master_rows
from eupy.entity.verdict_batches import apply_verdicts, load_batches, merge_verdicts

REPO_ROOT = Path(__file__).resolve().parents[3]
PLAYERS_PATH = REPO_ROOT / "data" / "raw_data" / "fantasy_stats" / "players.csv"
CROSSWALK_PATH = REPO_ROOT / "data" / "stage_01" / "fantasy_stats_player_name_crosswalk.csv"
VERDICTS_DIR = REPO_ROOT / "data" / "curated" / "fantasy_stats_player_name_verdicts"
CROSSWALK_FIELDNAMES = [
    "player_id",
    "name",
    "team",
    "resolved_name",
    "match_status",
    "match_score",
    "matched_by",
    "notes",
]

# The app's club codes differ from the schedule's (PAR = Partizan, PBB = Paris), so they are mapped
# by hand. Checked against every initial+surname match: each code votes for exactly one club.
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
    """`"First Last"` -> `"F. Last"`; splits on whitespace so `"Marc-Owen Fodzo Dada"` -> `"M. Fodzo Dada"`."""
    first, _, rest = name.strip().partition(" ")
    return f"{first[0]}. {rest.strip()}" if rest else name.strip()


def abbreviated_pool(full_names: list[str]) -> dict[str, set[str]]:
    """Matching pool `{abbreviated spelling: {full names}}`."""
    pool: dict[str, set[str]] = defaultdict(set)
    for full in full_names:
        pool[abbreviate_name(full)].add(full)
    return dict(pool)


def _match(name: str, pool: dict[str, set[str]]) -> tuple[str, list[tuple[str, float]], str]:
    """Shared exact -> fuzzy engine over `pool`, with candidates expanded to full names."""
    status, candidates, matched_by = find_candidates(
        normalize_name(name), pool, build_normalized_index(pool), {s: normalize_name(s) for s in pool}
    )
    return status, [(full, score) for abbrev, score in candidates for full in sorted(pool[abbrev])], matched_by


def build_row(
    player: dict[str, str], club_pools: dict[str, dict[str, set[str]]], boxscore_pool: dict[str, set[str]]
) -> dict[str, str]:
    """Build one fresh crosswalk row (no prior verdict state)."""
    status, candidates, matched_by = _match(player["name"], club_pools.get(FANTASY_TEAM_CLUBS.get(player["team"]), {}))
    if status == "no_candidate":
        status, candidates, matched_by = _match(player["name"], boxscore_pool)
        candidates = [(full.title(), score) for full, score in candidates]
        if status == "exact":
            status = "needs_review"  # no club filter: never auto-accept a box-score hit

    row = {field: "" for field in CROSSWALK_FIELDNAMES} | {
        "player_id": player["player_id"],
        "name": player["name"],
        "team": player["team"],
        "match_status": status,
        "matched_by": matched_by,
    }
    if candidates:
        row |= {"resolved_name": candidates[0][0], "match_score": f"{candidates[0][1]:.1f}"}
        if status == "needs_review":
            row["notes"] = f"candidates: {format_candidates_note(candidates)}"
    return row


def load_fantasy_players(path: Path) -> list[dict[str, str]]:
    """One `{player_id, name, team}` record per distinct `player_id`, ordered by id."""
    with path.open(newline="", encoding="utf-8") as f:
        players = {r["player_id"]: {k: r[k] for k in ("player_id", "name", "team")} for r in csv.DictReader(f)}
    return sorted(players.values(), key=lambda p: int(p["player_id"]))


def build_crosswalk(
    players: list[dict[str, str]],
    prices_rows: list[dict[str, str]],
    boxscore_spellings: dict[str, set[str]],
    verdicts: dict[str, dict[str, Any]],
) -> list[dict[str, str]]:
    """Full crosswalk: every row computed fresh, then `verdicts` (merged by `player_id`) applied on top.

    Verdicts for a `player_id` not in the snapshot are skipped.
    """
    club_names: dict[str, list[str]] = defaultdict(list)
    for row in prices_rows:
        club_names[row["club"]].append(row["name"])
    club_pools = {club: abbreviated_pool(names) for club, names in club_names.items()}
    boxscore_pool = abbreviated_pool(list(boxscore_spellings))

    crosswalk = [build_row(player, club_pools, boxscore_pool) for player in players]
    apply_verdicts({row["player_id"]: row for row in crosswalk}, verdicts, "resolved_name")
    return crosswalk


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--players", type=Path, default=PLAYERS_PATH, help="fantasy_stats/players.csv path")
    parser.add_argument("--prices", type=Path, default=MASTER_PATH, help="basketballsphere_prices.csv path")
    parser.add_argument("--boxscore", type=Path, default=BOXSCORE_PATH, help="euroleague_box_score.csv path")
    parser.add_argument("--verdicts-dir", type=Path, default=VERDICTS_DIR, help="Verdict batch directory")
    parser.add_argument("--out", type=Path, default=CROSSWALK_PATH, help="Crosswalk CSV output path")
    args = parser.parse_args(argv)

    verdicts = merge_verdicts(load_batches(args.verdicts_dir), "player_id", "resolved_name")
    rows = build_crosswalk(
        load_fantasy_players(args.players),
        load_master_rows(args.prices),
        load_boxscore_spellings(args.boxscore),
        verdicts,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CROSSWALK_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)
    summary = ", ".join(f"{s}={n}" for s, n in sorted(Counter(r["match_status"] for r in rows).items()))
    stale = len(set(verdicts) - {r["player_id"] for r in rows})
    print(f"Wrote {len(rows)} rows to {args.out}: {summary} ({stale} verdict(s) for ids not in this snapshot)")


if __name__ == "__main__":
    main()
