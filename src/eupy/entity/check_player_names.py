#!/usr/bin/env python3
"""Check that every fantasy-stats player is resolved in the player name map; writes only a small report.

Run it after a fetch to learn whether the name matching needs to run (and, after the builder, whether it needs an
agent decision). One unit is a distinct `(team, name)` pair of `fantasy_stats/players` (the trusted set, see
`build_player_name_map.py`). A pair is *unmatched* when the map has no entry for it (a new player since the last
build) or its entry is still open (`needs_review` / `no_candidate`). Box-score players that the map cannot resolve
are only counted: the map is built from the stats players, so box-score-only players are expected and are never a
failure.

The unmatched pairs are also written to a report CSV (header only when none), so that a DVC flow can depend on
the check and stop at it: downstream steps (the workbook) list the report as an input.

Exit code 0: nothing unmatched, the matching step can be skipped. Exit code 1: the unmatched pairs are listed with
the next step (rebuild the map; players still open after a rebuild need the `build-name-maps` skill's decisions).

Usage:
    python -m eupy.entity.check_player_names [--stats PATH] [--boxscore PATH] [--map PATH] [--out PATH]

Inputs:
  - fantasy_stats/players: data/raw_data/fantasy_stats/players.csv
  - euroleague_box_score_merged: data/stage_01/euroleague_box_score_merged.csv
  - player_name_map: data/stage_02/player_name_map.csv
Sources: none
Outputs:
  - player_name_check: data/stage_03/player_name_check.csv
Final: false
Impure: false
Notes: Report columns: team, name, reason (header only when everything is matched). Reads the JSON copy of the player map (src/eupy/entity/maps/player_name_map.json, written together with the
  CSV listed as input) because that is what PlayerNames and the agent decisions use.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from eupy.entity.matching import OPEN_STATUSES, UnknownNameError, load_stats_players
from eupy.entity.player_names import MAP_PATH, PlayerNames

REPO_ROOT = Path(__file__).resolve().parents[3]
STATS_PATH = REPO_ROOT / "data" / "raw_data" / "fantasy_stats" / "players.csv"
BOXSCORE_PATH = REPO_ROOT / "data" / "stage_01" / "euroleague_box_score_merged.csv"
OUT_PATH = REPO_ROOT / "data" / "stage_03" / "player_name_check.csv"


def find_unmatched(stats_players: list[dict[str, str]], entries: list[dict[str, object]]) -> list[tuple[str, str, str]]:
    """`(team, name, reason)` for every stats player the map does not resolve.

    The reason is `not in the map` (a new player) or the entry's open `match_status`. Entries are matched on
    `(stats_team, stats_name)`, the builder's row key.
    """
    status = {(str(e["stats_team"]), str(e["stats_name"])): str(e["match_status"]) for e in entries}
    unmatched = []
    for player in stats_players:
        found = status.get((player["team"], player["name"]))
        if found is None:
            unmatched.append((player["team"], player["name"], "not in the map"))
        elif found in OPEN_STATUSES:
            unmatched.append((player["team"], player["name"], found))
    return unmatched


def count_unresolved_boxscore_players(path: Path, players: PlayerNames) -> int:
    """Distinct box-score player spellings (`TOTAL` rows excluded) that the map cannot resolve; informational."""
    with path.open(newline="", encoding="utf-8") as f:
        spellings = {r["player"] for r in csv.DictReader(f) if r["dorsal"] != "TOTAL"}
    unresolved = 0
    for spelling in spellings:
        try:
            players.normalize(spelling)
        except UnknownNameError:
            unresolved += 1
        except LookupError:  # ambiguous without a club: the name is in the map, so it is resolved
            continue
    return unresolved


def write_report(unmatched: list[tuple[str, str, str]], path: Path) -> None:
    """Write the unmatched `(team, name, reason)` rows as CSV (header only when empty)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["team", "name", "reason"])
        writer.writerows(unmatched)


def main(argv: list[str] | None = None) -> int:
    """Print the check result; return the process exit code (0 all matched, 1 unmatched)."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--stats", type=Path, default=STATS_PATH, help="fantasy_stats/players.csv path")
    parser.add_argument("--boxscore", type=Path, default=BOXSCORE_PATH, help="merged box-score CSV path")
    parser.add_argument("--map", type=Path, default=MAP_PATH, help="player name map JSON path")
    parser.add_argument("--out", type=Path, default=OUT_PATH, help="report CSV output path")
    args = parser.parse_args(argv)

    stats_players = load_stats_players(args.stats)
    if not stats_players:
        raise ValueError(f"No players in {args.stats}; refetch with `uv run dvc repro fetch_euroleague_fantasy_stats`")
    entries = json.loads(args.map.read_text(encoding="utf-8"))["players"] if args.map.exists() else []
    unmatched = find_unmatched(stats_players, entries)
    write_report(unmatched, args.out)

    if args.boxscore.exists() and args.map.exists():
        count = count_unresolved_boxscore_players(args.boxscore, PlayerNames.load(args.map))
        print(f"info: {count} box-score player spellings are not in the map (expected: the map follows the stats).")
    if not unmatched:
        print(f"OK: all {len(stats_players)} stats players are resolved; the name matching can be skipped.")
        return 0
    print(f"{len(unmatched)} of {len(stats_players)} stats players are unmatched:")
    for team, name, reason in unmatched:
        print(f"  {team} {name}: {reason}")
    print(
        "Next: `uv run python -m eupy.entity.build_player_name_map` (adds new players); players still open "
        "after that need the `build-name-maps` skill."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
