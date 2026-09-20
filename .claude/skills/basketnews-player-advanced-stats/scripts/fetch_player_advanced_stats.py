#!/usr/bin/env python3
"""Fetch EuroLeague player advanced stats (offense + defense, all players,
all positions, all stages) from basketnews.com and write a combined
long-format CSV.

Usage (from the repo root):
    python .claude/skills/basketnews-player-advanced-stats/scripts/fetch_player_advanced_stats.py
    python .claude/skills/basketnews-player-advanced-stats/scripts/fetch_player_advanced_stats.py \
        --league-id 25 --season 2025 --out data/basketnews-players-stats/basketnews_players_advanced_stats.csv

How this was reverse-engineered
--------------------------------
The public leaderboard page is:
    https://basketnews.com/advanced-stats/leaders/players/25-euroleague/2025
        ?leaders_mode=offensive&leaders_sort=offensive_rating_lineup
        &leaders_sort_desc=0&leaders_min_gp=5

It looks paginated and toggle-able (OFFENSE/DEFENSE buttons, page 1/2/3.../12),
but that's all client-side. The page is an Alpine.js app (x-data="advancedStatsData()")
that, on load, makes ONE POST call that returns the full season's per-player stat
line (every offensive AND defensive metric together). The offense/defense toggle
just switches which columns are displayed, and pagination just slices the
already-loaded array — sorted/filtered in the browser. So there is no need to
click through pages or toggle modes: one API call returns everything.

    POST https://basketnews.com/advanced-stats/team-profile/players.json
    Content-Type: application/x-www-form-urlencoded
    body: ajax=true&league_id=<league_id>&season=<season>

    Optional extra params (all omitted by default = "all stages" / full season,
    which is what the target URL above implicitly requests since it sets no
    stage filter):
        stage_id=<id>          # restrict to one stage (see data['stages'] in the response)
        period_start=<ts>      # unix timestamp range filters
        period_end=<ts>
        sequence_from=<n>      # "last N games" style filters (by game sequence number)
        sequence_to=<n>

Response shape (relevant parts):
    data.stats     -> list of per player+team stat lines (one row per player
                       per team they played for that season -- most players
                       have exactly one, traded players have more)
    data.players   -> {id, full_name, full_name_short, positions: [position_id,...]}
    data.teams     -> {id, name_en, short_name_en, ...}
    data.positions -> {id, short_name} e.g. {1: "PG", ..., 5: "C"}
    data.extra     -> {stage_id, total_games, ...} echoes the filters applied

Each stat field in data.stats[i] is itself an object like
    {"value": 19, "pct": 100, "rank": 1}
(and time_played additionally has a "formatted" mm:ss string). We only pull
"value" (and time_played's "formatted") since that's what's shown on the page.

Important: the UI applies a "min games played" filter (defaults to 20, or 1 if
the season has fewer than 20 total games) and a position filter, PURELY
client-side, for the *leaderboard ranking view*. This script deliberately does
NOT apply any such filter -- it dumps every player/team stat line returned by
the API, so the output covers ALL players and ALL positions regardless of
games played, which is a superset of what page 1..N of the UI would ever show
you (the UI leaderboard filters out low-minutes players and low-rank ties by
design, and different stat orderings would produce different page contents to
crawl).

Output
------
One combined long-format CSV: one row per (player, team-stint, mode) where
mode is "offensive" or "defensive". Columns for the *other* mode are left
blank on a given row -- this mirrors toggling OFFENSE/DEFENSE on the page,
just without needing to actually toggle anything or paginate.
"""

from __future__ import annotations

import argparse
import csv
import json
import urllib.request
from pathlib import Path

API_URL = "https://basketnews.com/advanced-stats/team-profile/players.json"

# Exactly the column sets the site itself uses for each leaderboard mode
# (lifted from the page's inline `leadersPlayersStats` Alpine data).
OFFENSIVE_STATS = [
    "offensive_rating_lineup", "offensive_rating_ind", "points",
    "3p_percentage", "2p_percentage", "ft_percentage", "ts_percentage",
    "3p_attempted", "2p_attempted", "ft_attempted", "3p_attempted_rate",
    "created_points", "assists", "assist_percentage", "turnovers",
    "turnover_percentage", "offensive_rebounds", "offensive_rebound_percentage",
    "fouls_received", "blocks_received", "usage_percentage",
]

DEFENSIVE_STATS = [
    "defensive_rating_lineup", "defensive_rating_ind", "stops", "stop_percentage",
    "defensive_rebounds", "defensive_rebound_percentage", "steals",
    "steal_percentage", "blocks", "block_percentage", "foul_stops",
]

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)


def fetch_payload(league_id: int, season: int, stage_id: int | None = None) -> dict:
    params = {"ajax": "true", "league_id": league_id, "season": season}
    if stage_id is not None:
        params["stage_id"] = stage_id
    body = "&".join(f"{k}={v}" for k, v in params.items()).encode()

    req = urllib.request.Request(
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
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.load(resp)
    if not payload.get("success"):
        raise RuntimeError(f"basketnews API call failed: {payload}")
    return payload["data"]


def stat_value(stat_dict: dict, key: str):
    v = stat_dict.get(key)
    if v is None:
        return ""
    return v.get("value", "")


def build_rows(data: dict, league_id: int, season: int) -> list[dict]:
    players_by_id = {p["id"]: p for p in data["players"]}
    teams_by_id = {t["id"]: t for t in data["teams"]}
    positions_by_id = {p["id"]: p["short_name"] for p in data["positions"]}

    rows = []
    for stat in data["stats"]:
        player = players_by_id.get(stat["player_id"], {})
        team = teams_by_id.get(stat["team_id"], {})
        positions = ",".join(
            positions_by_id.get(pid, str(pid)) for pid in player.get("positions", [])
        )
        time_played = stat.get("time_played") or {}

        common = {
            "season": season,
            "league_id": league_id,
            "player_id": stat["player_id"],
            "player_name": player.get("full_name", ""),
            "player_name_short": player.get("full_name_short", ""),
            "team_id": stat["team_id"],
            "team_name": team.get("name_en", team.get("name", "")),
            "team_short_name": team.get("short_name_en", team.get("short_name", "")),
            "positions": positions,
            "games_played": stat.get("games_played", ""),
            "minutes_per_game": time_played.get("formatted", ""),
            "minutes_per_game_raw": time_played.get("value", ""),
        }

        for mode, stat_keys in (("offensive", OFFENSIVE_STATS), ("defensive", DEFENSIVE_STATS)):
            row = dict(common)
            row["mode"] = mode
            for key in OFFENSIVE_STATS:
                row[key] = stat_value(stat, key) if mode == "offensive" else ""
            for key in DEFENSIVE_STATS:
                row[key] = stat_value(stat, key) if mode == "defensive" else ""
            rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--league-id", type=int, default=25, help="basketnews league id (25 = EuroLeague)")
    parser.add_argument("--season", type=int, default=2025, help="season start year, e.g. 2025 for 2025/2026")
    parser.add_argument("--stage-id", type=int, default=None, help="restrict to one stage id; omit for all stages")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("data/basketnews-players-stats/basketnews_players_advanced_stats.csv"),
        help="output CSV path (default: data/basketnews-players-stats/basketnews_players_advanced_stats.csv, relative to CWD -- run this from the repo root)",
    )
    args = parser.parse_args()

    data = fetch_payload(args.league_id, args.season, args.stage_id)
    rows = build_rows(data, args.league_id, args.season)

    fieldnames = [
        "season", "league_id", "mode", "player_id", "player_name",
        "player_name_short", "team_id", "team_name", "team_short_name",
        "positions", "games_played", "minutes_per_game", "minutes_per_game_raw",
        *OFFENSIVE_STATS, *DEFENSIVE_STATS,
    ]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows, {len(fieldnames)} columns -> {args.out}")
    print(f"extra: {data.get('extra')}")
    print(
        f"stat lines: {len(data['stats'])}, players: {len(data['players'])}, "
        f"teams: {len(data['teams'])}, stages: {len(data['stages'])}"
    )


if __name__ == "__main__":
    main()
