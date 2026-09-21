#!/usr/bin/env python3
"""Fetch EuroLeague team advanced stats (offense/defense x all/home/away) from basketnews.com.

The team overview page
(https://basketnews.com/advanced-stats/overview/25-euroleague/2025/368-real-madrid) is an
Alpine.js app: its team selector, OFFENSE/DEFENSE tabs and Total/Home/Away columns are all
client-side views over ONE JSON payload loaded on page load:

    POST https://basketnews.com/advanced-stats/team-profile/overview.json
    Content-Type: application/x-www-form-urlencoded
    body: ajax=true&league_id=<league_id>&season=<season>

No auth or cookies are needed. The payload holds every team's stat line, each KPI shaped as
``{"home": {"value", "rank"}, "away": {...}, "total": {...}}``.

Output: one row per team. After the identity columns come six blocks, in this order:
offense_all, offense_home, offense_away, defense_all, defense_home, defense_away. Columns are
named ``<block>_<kpi>`` and the KPI order inside each block is the order the page shows.

Usage (from the repo root):
    python src/fetch_basketnews_team_stats.py
    python src/fetch_basketnews_team_stats.py --season 2024 --league-id 25 --out /tmp/team_stats_2024.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "data/basketnews-team-stats/basketnews_team_stats.csv"
API_URL = "https://basketnews.com/advanced-stats/team-profile/overview.json"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# KPI keys and order, lifted from the page's inline `overviewStats` Alpine data. The page lists
# `steals_opponent` (steals the team's opponents make) under Offense; that is kept as-is.
OFFENSE_KPIS = [
    "offensive_rating", "points", "3p_percentage", "2p_percentage",
    "ft_percentage", "ts_percentage", "3p_attempted", "2p_attempted",
    "ft_attempted", "3p_attempted_rate", "assists", "assist_percentage",
    "turnovers", "turnover_percentage", "steals_opponent", "offensive_rebounds",
    "offensive_rebound_percentage", "fouls_received", "blocks_received", "possessions",
]  # fmt: skip
DEFENSE_KPIS = [
    "defensive_rating", "points_opponent", "3p_percentage_opponent", "2p_percentage_opponent",
    "ft_percentage_opponent", "ts_percentage_opponent", "3p_attempted_opponent", "2p_attempted_opponent",
    "ft_attempted_opponent", "3p_attempted_rate_opponent", "assists_opponent", "assist_percentage_opponent",
    "turnovers_opponent", "turnover_percentage_opponent", "steals", "defensive_rebounds",
    "defensive_rebound_percentage", "fouls", "blocks", "possessions_opponent",
]  # fmt: skip

# (block name, KPI list, API split key). The API calls "all games" `total`.
BLOCKS = [
    ("offense_all", OFFENSE_KPIS, "total"),
    ("offense_home", OFFENSE_KPIS, "home"),
    ("offense_away", OFFENSE_KPIS, "away"),
    ("defense_all", DEFENSE_KPIS, "total"),
    ("defense_home", DEFENSE_KPIS, "home"),
    ("defense_away", DEFENSE_KPIS, "away"),
]
IDENTITY_COLUMNS = [
    "team_id", "team_name", "team_short_name", "season", "league_id",
    "games_played_all", "games_played_home", "games_played_away",
]  # fmt: skip


def fetch_payload(league_id: int, season: int) -> dict[str, Any]:
    """POST to the team overview endpoint and return its ``data`` object.

    Args:
        league_id: basketnews league id (25 = EuroLeague).
        season: Season start year, e.g. 2025 for 2025/26.

    Returns:
        The ``data`` dict with ``stats`` (per team) and ``teams`` (team lookup).

    Raises:
        RuntimeError: If the API reports ``success`` as false.
    """
    body = f"ajax=true&league_id={league_id}&season={season}".encode()
    request = urllib.request.Request(  # noqa: S310 - fixed https URL
        API_URL,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        payload = json.load(response)
    if not payload.get("success"):
        raise RuntimeError(f"basketnews API call failed: {payload}")
    return payload["data"]


def _value(stat: dict[str, Any], kpi: str, split: str) -> Any:
    """Return the displayed value of ``kpi`` for ``split``, or "" when the API has none."""
    value = ((stat.get(kpi) or {}).get(split) or {}).get("value")
    return "" if value is None else value


def build_rows(data: dict[str, Any], league_id: int, season: int) -> list[dict[str, Any]]:
    """Flatten the payload into one wide row per team, sorted by team name."""
    teams = {team["id"]: team for team in data["teams"]}
    rows = []
    for stat in data["stats"]:
        team = teams[stat["team_id"]]
        row: dict[str, Any] = {
            "team_id": stat["team_id"],
            "team_name": team["name_en"],
            "team_short_name": team["short_name_en"],
            "season": season,
            "league_id": league_id,
            "games_played_all": _value(stat, "games_played", "total"),
            "games_played_home": _value(stat, "games_played", "home"),
            "games_played_away": _value(stat, "games_played", "away"),
        }
        for block, kpis, split in BLOCKS:
            for kpi in kpis:
                row[f"{block}_{kpi}"] = _value(stat, kpi, split)
        rows.append(row)
    return sorted(rows, key=lambda r: r["team_name"])


def main() -> None:
    """Parse CLI args, fetch the payload and write the CSV."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--league-id", type=int, default=25, help="basketnews league id (25 = EuroLeague)")
    parser.add_argument("--season", type=int, default=2025, help="season start year, e.g. 2025 for 2025/26")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"output CSV path (default: {DEFAULT_OUT})")
    args = parser.parse_args()

    data = fetch_payload(args.league_id, args.season)
    rows = build_rows(data, args.league_id, args.season)
    fieldnames = IDENTITY_COLUMNS + [f"{block}_{kpi}" for block, kpis, _ in BLOCKS for kpi in kpis]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows, {len(fieldnames)} columns -> {args.out}")
    print(f"extra: {data.get('extra')}")


if __name__ == "__main__":
    main()
