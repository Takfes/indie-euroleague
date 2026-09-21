#!/usr/bin/env python3
"""Fetch Dunkest EuroLeague "defense vs position" data for every team into one wide CSV.

The page https://www.dunkest.com/en/euroleague/stats/teams/defense-vs-position loads its table
from a public JSON endpoint, ``GET https://www.dunkest.com/api/stats/defense-vs-position``, which
takes ``season_id``, ``stats_id`` and ``position_id`` and returns one row per team. This script
loops over positions (outer) and stats (inner), pivots the results to one row per team and
writes ``<position>_<stat>`` columns in position-major order.

Usage:
    python src/fetch_dunkest_defense_positions.py [--season-id ID] [--season LABEL] [--out PATH]
        [--window {all,l10,l5,l3}] [--delay SECONDS]

Defaults reproduce data/dunkest-defense-positions/dunkest_defense_vs_position.csv.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

API_URL = "https://www.dunkest.com/api/stats/defense-vs-position"

# Outer loop: (column prefix, Dunkest position_id).
POSITIONS: list[tuple[str, int]] = [("guards", 1), ("forwards", 2), ("centers", 3)]

# Inner loop: (column suffix, Dunkest stats_id), in the order of the page's stat picker.
STATS: list[tuple[str, int]] = [
    ("points", 4),
    ("rebounds", 26),
    ("assists", 5),
    ("steals", 6),
    ("blocks", 7),
    ("turnovers", 20),
    ("3point_field_goals_made", 12),
    ("fantasy_points", 25),
]

DEFAULT_SEASON_ID = 23  # EuroLeague 2025-26
DEFAULT_SEASON = "2025-26"
DEFAULT_OUT = (
    Path(__file__).resolve().parents[1] / "data" / "dunkest-defense-positions" / "dunkest_defense_vs_position.csv"
)
WINDOWS = ["all", "l10", "l5", "l3"]  # whole season / last 10 / 5 / 3 games


def fetch_teams(season_id: int, stats_id: int, position_id: int) -> list[dict[str, str]]:
    """Call the Dunkest API for one (season, stat, position) combination.

    The endpoint is public (no auth or cookies) and returns a JSON array with one object per team:
    ``id``, ``name`` and the conceded amounts ``l3``, ``l5``, ``l10`` and ``all``.

    Args:
        season_id: Dunkest season id (23 = EuroLeague 2025-26).
        stats_id: Dunkest stat id, see ``STATS``.
        position_id: Dunkest position id, see ``POSITIONS``.

    Returns:
        The decoded JSON array.

    Raises:
        TypeError: If the response is not a JSON array.
    """
    query = urllib.parse.urlencode({"season_id": season_id, "stats_id": stats_id, "position_id": position_id})
    request = urllib.request.Request(  # noqa: S310
        f"{API_URL}?{query}", headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        data = json.load(response)
    if not isinstance(data, list):
        raise TypeError(f"Expected a JSON array of teams, got {type(data).__name__}")
    return data


def build_rows(season_id: int, season: str, window: str, delay: float) -> tuple[list[str], list[dict[str, str]]]:
    """Run the positions x stats loop and pivot the results to one row per team.

    Args:
        season_id: Dunkest season id.
        season: Season label written to the ``season`` column.
        window: Which API value to keep per team: ``all``, ``l10``, ``l5`` or ``l3``.
        delay: Seconds to sleep between requests.

    Returns:
        The CSV header and the rows, sorted by ``team_id``.

    Raises:
        ValueError: If a response has a different team set than the first one.
    """
    teams: dict[str, dict[str, str]] = {}
    header = ["team_id", "team_name", "season"]
    first = True
    for position, position_id in POSITIONS:
        for stat, stats_id in STATS:
            if not first:
                time.sleep(delay)
            first = False
            column = f"{position}_{stat}"
            header.append(column)
            data = fetch_teams(season_id, stats_id, position_id)
            if teams and {t["id"] for t in data} != set(teams):
                raise ValueError(f"Team set changed for {column}")
            for team in data:
                row = teams.setdefault(team["id"], {"team_id": team["id"], "team_name": team["name"], "season": season})
                row[column] = team[window]
    rows = sorted(teams.values(), key=lambda r: int(r["team_id"]))
    return header, rows


def write_csv(header: list[str], rows: list[dict[str, str]], out_path: Path) -> None:
    """Write rows to CSV, refusing to overwrite the file with an empty result."""
    if not rows:
        raise ValueError("No teams returned -- refusing to overwrite the CSV with an empty file")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="CSV output path")
    parser.add_argument("--season-id", type=int, default=DEFAULT_SEASON_ID, help="Dunkest season id (23 = 2025-26)")
    parser.add_argument("--season", default=DEFAULT_SEASON, help="Season label for the season column")
    parser.add_argument("--window", choices=WINDOWS, default="all", help="Games window (default: whole season)")
    parser.add_argument("--delay", type=float, default=0.5, help="Seconds between requests")
    return parser.parse_args()


def main() -> None:
    """Fetch every position x stat combination and write the CSV."""
    args = parse_args()
    header, rows = build_rows(args.season_id, args.season, args.window, args.delay)
    write_csv(header, rows, args.out)
    print(f"Wrote {len(rows)} teams x {len(header)} columns to {args.out}")


if __name__ == "__main__":
    main()
