#!/usr/bin/env python3
"""Normalize the fantasy stats players table: full player names and C/F/G roles.

Two changes to `fantasy_stats/players.csv`, everything else passes through unchanged (same
columns, same row order):

* `name` -- the abbreviated `"C. Jones"` is replaced by the full name from
  `fantasy_stats_player_name_crosswalk` (looked up by `player_id`, so same-abbreviation players at
  different clubs stay distinct). Rows the crosswalk closed as `no_match` / `rejected` keep the
  original abbreviated name.
* `position` -- `Guard` / `Forward` / `Center` become `G` / `F` / `C`, the role letters used by
  `basketballsphere_prices` and the optimizer input.

Fails loudly if any `player_id` is missing from the crosswalk or still open (`needs_review` /
`no_candidate`): run `resolve_fantasy_stats_player_names.py` (and record verdicts) first, so a stale
crosswalk can never leak abbreviated names into the normalized dataset.

Usage:
    python src/eupy/transform/normalize_fantasy_stats.py [--players PATH] [--crosswalk PATH] [--out PATH]

Inputs: fantasy_stats/players -- data/raw_data/fantasy_stats/players.csv;
    fantasy_stats_player_name_crosswalk -- data/stage_01/fantasy_stats_player_name_crosswalk.csv.
Outputs: fantasy_stats_players_normalized -- data/stage_02/fantasy_stats_players_normalized.csv
    (overwritten each run).
Final: true -- exposed under data/stage_99/ as a relative symlink.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
PLAYERS_PATH = REPO_ROOT / "data" / "raw_data" / "fantasy_stats" / "players.csv"
CROSSWALK_PATH = REPO_ROOT / "data" / "stage_01" / "fantasy_stats_player_name_crosswalk.csv"
OUT_PATH = REPO_ROOT / "data" / "stage_02" / "fantasy_stats_players_normalized.csv"

POSITION_ROLES = {"Guard": "G", "Forward": "F", "Center": "C"}
USABLE_STATUSES = {"exact", "confirmed"}
KEEP_RAW_STATUSES = {"no_match", "rejected"}


def load_name_map(path: Path) -> dict[str, str]:
    """Map `player_id` -> normalized name, from a crosswalk with no open rows.

    Raises:
        ValueError: If any row is still open (`needs_review` / `no_candidate`).
    """
    with path.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    open_rows = [r for r in rows if r["match_status"] not in USABLE_STATUSES | KEEP_RAW_STATUSES]
    if open_rows:
        listing = ", ".join(f"{r['player_id']} ({r['name']})" for r in open_rows[:10])
        raise ValueError(f"{len(open_rows)} unresolved crosswalk row(s), e.g. {listing} -- resolve them first")
    return {r["player_id"]: r["resolved_name"] if r["match_status"] in USABLE_STATUSES else r["name"] for r in rows}


def normalize_rows(rows: list[dict[str, str]], name_map: dict[str, str]) -> list[dict[str, str]]:
    """Return `rows` with `name` replaced via `name_map` and `position` mapped to C/F/G.

    Raises:
        ValueError: If a `player_id` is missing from `name_map`, or a `position` is not
            Guard / Forward / Center.
    """
    normalized = []
    for row in rows:
        player_id = row["player_id"]
        if player_id not in name_map:
            raise ValueError(f"player_id {player_id} ({row['name']}) is not in the crosswalk -- rerun the resolver")
        if row["position"] not in POSITION_ROLES:
            raise ValueError(f"Unexpected position {row['position']!r} for player_id {player_id}")
        normalized.append({**row, "name": name_map[player_id], "position": POSITION_ROLES[row["position"]]})
    return normalized


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--players", type=Path, default=PLAYERS_PATH, help="fantasy_stats/players.csv path")
    parser.add_argument("--crosswalk", type=Path, default=CROSSWALK_PATH, help="fantasy stats crosswalk path")
    parser.add_argument("--out", type=Path, default=OUT_PATH, help="Normalized CSV output path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    name_map = load_name_map(args.crosswalk)
    with args.players.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = normalize_rows(list(reader), name_map)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {args.out}")


if __name__ == "__main__":
    main()
