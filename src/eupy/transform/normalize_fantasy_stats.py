#!/usr/bin/env python3
"""Normalize the fantasy stats players table: full player names and C/F/G roles.

Two changes to `fantasy_stats/players.csv`, everything else passes through unchanged (same
columns, same row order):

* `name` -- the abbreviated `"C. Jones"` becomes the full name from the player name map
  (`PlayerNames.normalize(name, team=team)`, so same-abbreviation players at different clubs stay
  distinct). Players the map closed as `rejected` / `no_match`, or still open (typically not played yet, so no
  box-score name to match), keep their abbreviated name; `check_player_names` lists them.
* `position` -- `Guard` / `Forward` / `Center` become `G` / `F` / `C`, the role letters used by
  the optimizer input.

Fails loudly only for an ambiguous name (a spelling shared by players the row's `team` does not tell apart).

Usage:
    python -m eupy.transform.normalize_fantasy_stats [--players PATH] [--out PATH]

Inputs:
  - fantasy_stats/players: data/raw_data/fantasy_stats/players.csv
  - player_name_map: data/stage_02/player_name_map.csv
  - team_name_map: data/stage_01/team_name_map.csv
Sources: none
Outputs:
  - fantasy_stats_players_normalized: data/stage_03/fantasy_stats_players_normalized.csv
Final: true
Impure: false
Notes: Overwritten each run. Output is fantasy_stats/players with the same columns and row order, name
  replaced by the map's full name (the abbreviated name stays for unresolved players) and position mapped
  Guard/Forward/Center -> G/F/C. Names are looked up through PlayerNames, which reads the player map CSV
  listed as input and the team map through TeamNames (the JSON copy in src/eupy/entity/maps/).
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from eupy.entity.matching import UnknownNameError
from eupy.entity.player_names import PlayerNames

REPO_ROOT = Path(__file__).resolve().parents[3]
PLAYERS_PATH = REPO_ROOT / "data" / "raw_data" / "fantasy_stats" / "players.csv"
OUT_PATH = REPO_ROOT / "data" / "stage_03" / "fantasy_stats_players_normalized.csv"

POSITION_ROLES = {"Guard": "G", "Forward": "F", "Center": "C"}


def normalize_rows(rows: list[dict[str, str]], players: PlayerNames) -> list[dict[str, str]]:
    """Return `rows` with `name` replaced by the map's full name and `position` mapped to C/F/G.

    A player the map does not resolve (open, closed as `no_match`, or not in it) keeps the stats name.

    Raises:
        AmbiguousNameError: If a name is shared by players the row's `team` does not tell apart.
        ValueError: If a `position` is not Guard / Forward / Center.
    """
    normalized = []
    for row in rows:
        if row["position"] not in POSITION_ROLES:
            raise ValueError(f"Unexpected position {row['position']!r} for player_id {row['player_id']}")
        try:
            name = players.normalize(row["name"], team=row["team"])
        except UnknownNameError:
            name = row["name"]
        normalized.append({**row, "name": name, "position": POSITION_ROLES[row["position"]]})
    return normalized


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--players", type=Path, default=PLAYERS_PATH, help="fantasy_stats/players.csv path")
    parser.add_argument("--out", type=Path, default=OUT_PATH, help="Normalized CSV output path")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    players = PlayerNames.load()
    with args.players.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = normalize_rows(list(reader), players)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {args.out}")


if __name__ == "__main__":
    main()
