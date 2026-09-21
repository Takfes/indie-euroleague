#!/usr/bin/env python3
"""Fetch EuroLeague player stats from the Dunkest fantasy stats API and write them to CSV.

Dunkest's UI (https://www.dunkest.com/en/euroleague/stats/players/table?...) paginates
the player stats table client-side (15 rows/page), but the JSON API behind it returns
every matching player in a single response -- there is no server-side pagination to loop
over. This script issues one GET request and writes the full result set to CSV.

Usage:
    python src/fetch_dunkest_player_stats.py [--out PATH] [--season-id ID] ...

Re-run this any time to refresh data/dunkest-data/player_stats.csv with current-season
averages (the output file is overwritten each run).
"""

from __future__ import annotations

import argparse
import csv
import json
import urllib.parse
import urllib.request
from pathlib import Path

API_URL = "https://www.dunkest.com/api/stats/table"

# All 20 EuroLeague 2025-26 team ids, as used by the Dunkest stats table filters.
DEFAULT_TEAMS = [32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 56, 60, 75]
# 1=Guard, 2=Forward, 3=Center
DEFAULT_POSITIONS = [1, 2, 3]

DEFAULT_OUT = Path(__file__).resolve().parents[1] / "data" / "dunkest-data" / "player_stats.csv"


def build_params(args: argparse.Namespace) -> list[tuple[str, str]]:
    """Assemble the repeated-key query params the API expects (teams[]=.., positions[]=..)."""
    params: list[tuple[str, str]] = [
        ("season_id", str(args.season_id)),
        ("mode", args.mode),
        ("stats_type", args.stats_type),
        ("date_from", args.date_from),
        ("date_to", args.date_to),
    ]
    params += [("teams[]", str(t)) for t in args.teams]
    params += [("positions[]", str(p)) for p in args.positions]
    params += [
        ("player_search", args.player_search),
        ("min_cr", str(args.min_cr)),
        ("max_cr", str(args.max_cr)),
        ("sort_by", args.sort_by),
        ("sort_order", args.sort_order),
    ]
    return params


def fetch_players(params: list[tuple[str, str]]) -> list[dict]:
    """Call the Dunkest stats API and return the full list of player-stat rows.

    No auth/cookies are required -- the endpoint is public and returns the complete
    result set for the given filters in one JSON array, so no pagination loop is needed.
    """
    url = f"{API_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(  # noqa: S310
        url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        data = json.load(response)
    if not isinstance(data, list):
        raise TypeError(f"Expected a JSON array of players, got {type(data).__name__}")
    return data


def write_csv(players: list[dict], out_path: Path) -> None:
    """Write player rows to CSV, using the key order of the first row as the header."""
    if not players:
        raise ValueError("No players returned -- refusing to overwrite the CSV with an empty file")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(players[0].keys())
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(players)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="CSV output path")
    parser.add_argument("--season-id", default=23, help="Dunkest season id (23 = EuroLeague 2025-26)")
    parser.add_argument("--mode", default="nba", help="Stats mode as used by the Dunkest UI")
    parser.add_argument("--stats-type", default="avg", choices=["avg", "tot"], help="Per-game averages or totals")
    parser.add_argument("--date-from", default="2025-09-30", help="Season start date (YYYY-MM-DD)")
    parser.add_argument("--date-to", default="2026-05-24", help="Season end date (YYYY-MM-DD)")
    parser.add_argument("--teams", type=int, nargs="+", default=DEFAULT_TEAMS, help="Team ids to include")
    parser.add_argument("--positions", type=int, nargs="+", default=DEFAULT_POSITIONS, help="1=G, 2=F, 3=C")
    parser.add_argument("--player-search", default="", help="Filter by player name substring")
    parser.add_argument("--min-cr", default=4, help="Minimum fantasy credit value")
    parser.add_argument("--max-cr", default=35, help="Maximum fantasy credit value")
    parser.add_argument("--sort-by", default="cr", help="API sort field")
    parser.add_argument("--sort-order", default="asc", choices=["asc", "desc"], help="API sort order")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    params = build_params(args)
    players = fetch_players(params)
    write_csv(players, args.out)
    team_count = len({p.get("team_id") for p in players})
    print(f"Wrote {len(players)} players ({team_count} teams) to {args.out}")


if __name__ == "__main__":
    main()
