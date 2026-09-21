#!/usr/bin/env python3
"""Refresh the EuroLeague team on/off-court stats dataset from basketnews.com.

Source page (UI): https://basketnews.com/advanced-stats/on-off/25-euroleague/2025/33-panathinaikos-aktor-athens
The page has a team-logo selector across the top (each team is a different
URL, e.g. .../2025/60-fc-barcelona) and a TOTAL / OFFENSE / DEFENSE / COMBO
toggle that changes which columns are shown for the currently selected team.

Underlying data API (found via network + inline-<script> inspection of that
page - see the `onoffStats` / `loadStats()` / `types` objects in the page's
own JS):

    POST https://basketnews.com/advanced-stats/team-profile/on-off.json
    form data: ajax=true, league_id=25, season=<year>
    headers:  X-Requested-With: XMLHttpRequest
              Referer: <the UI page URL>
              Accept: application/json, text/javascript, */*; q=0.01

This ONE call returns on/off stats for every player on every EuroLeague team
for the full season - the team selector and the total/offense/defense toggle
are both pure client-side filters over this single payload, so there is no
need to loop over teams or toggle states to hit the API. (Optional params
stage_id / period_start / period_end / sequence_from / sequence_to exist for
narrower slices - e.g. a specific round range or quarter - but are omitted
here to get full-season totals, matching the page's default view.)

Response shape: {"success": true, "data": {"stats": [...], "teams": [...],
"players": [...], "positions": [...], "stages": [...], "extra": {...}}}
Each entry in `stats` is one (player, team) pair for the season, with most
numeric fields shaped as {"value": ..., "pct": ...} (pct is only used by the
page for cell-background heat-coloring, not shown as text, so this script
keeps just the value).

Output: a single long-format CSV at data/basketnews-onoff-stats/onoff_stats.csv
with one row per (team, player, view) where view in {total, offensive,
defensive} - matching the three toggle states named in the task (the page's
fourth "COMBO" tab covers arbitrary multi-player lineup combos, a different
shape of data, and is out of scope here). The KPI columns are the union of
all three views' fields (per the page's own `onoffStats` config below); a
column is left blank on rows for a view it doesn't apply to.
"""

from __future__ import annotations

import csv
import subprocess
import sys
from pathlib import Path

import requests

API_URL = "https://basketnews.com/advanced-stats/team-profile/on-off.json"
REFERER = "https://basketnews.com/advanced-stats/on-off/25-euroleague/2025/33-panathinaikos-aktor-athens"
LEAGUE_ID = 25
LEAGUE_NAME = "euroleague"
SEASON = 2025  # start year of the season, e.g. 2025 = 2025-2026 EuroLeague season

OUTPUT_RELATIVE_PATH = Path("data/basketnews-onoff-stats/onoff_stats.csv")

# Column sets per view, copied verbatim from the page's own `onoffStats`
# inline-JS config, which maps each toggle state to the API fields it
# displays. If basketnews changes the on/off page's columns, re-extract this
# object from the page source (search for "onoffStats:") and update below.
ONOFF_STATS: dict[str, list[str]] = {
    "total": [
        "time_played",
        "offensive_rating_lineup",
        "offensive_rating_lineup_diff",
        "defensive_rating_lineup",
        "defensive_rating_lineup_diff",
        "net_rating_lineup",
        "net_rating_lineup_diff",
        "possessions_lineup",
        "possession_percentage_lineup",
        "points_lineup",
        "points_opponent_lineup",
        "net_points_lineup",
        "rebound_percentage_lineup",
        "rebound_percentage_lineup_diff",
    ],
    "offensive": [
        "time_played",
        "offensive_rating_lineup",
        "offensive_rating_lineup_wo",
        "offensive_rating_lineup_diff",
        "possession_percentage_lineup",
        "offensive_rebound_percentage_lineup",
        "offensive_rebound_percentage_lineup_diff",
        "2p_attempted_lineup",
        "2p_percentage_lineup",
        "2p_percentage_lineup_diff",
        "3p_attempted_lineup",
        "3p_attempted_rate_lineup",
        "3p_attempted_rate_lineup_diff",
        "3p_percentage_lineup",
        "3p_percentage_lineup_diff",
        "fouls_received_rate_lineup",
        "fouls_received_rate_lineup_diff",
        "assist_percentage_lineup",
        "assist_percentage_lineup_diff",
        "turnover_percentage_lineup",
        "turnover_percentage_lineup_diff",
    ],
    "defensive": [
        "time_played",
        "defensive_rating_lineup",
        "defensive_rating_lineup_wo",
        "defensive_rating_lineup_diff",
        "possession_percentage_opponent_lineup",
        "defensive_rebound_percentage_lineup",
        "defensive_rebound_percentage_lineup_diff",
        "2p_attempted_opponent_lineup",
        "2p_percentage_opponent_lineup",
        "2p_percentage_opponent_lineup_diff",
        "3p_attempted_opponent_lineup",
        "3p_attempted_rate_opponent_lineup",
        "3p_attempted_rate_opponent_lineup_diff",
        "3p_percentage_opponent_lineup",
        "3p_percentage_opponent_lineup_diff",
        "fouls_rate_lineup",
        "fouls_rate_lineup_diff",
        "assist_percentage_opponent_lineup",
        "assist_percentage_opponent_lineup_diff",
        "turnover_percentage_opponent_lineup",
        "turnover_percentage_opponent_lineup_diff",
    ],
}

BASE_COLUMNS = [
    "league",
    "league_id",
    "season",
    "view",
    "team_id",
    "team_name",
    "team_short_name",
    "player_id",
    "player_name",
    "position",
    "games_played",
    "time_played_formatted",
]


def find_repo_root() -> Path:
    """Locate the repo root so the CSV always lands at <repo>/data/... no
    matter where this script is invoked from."""
    try:
        out = subprocess.run(  # noqa: S603
            ["git", "rev-parse", "--show-toplevel"],  # noqa: S607
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(out.stdout.strip())
    except Exception:
        # Fallback: src/fetch_basketnews_onoff_stats.py -> src -> repo root
        return Path(__file__).resolve().parents[1]


def fetch_payload(season: int = SEASON, league_id: int = LEAGUE_ID) -> dict:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        ),
        "X-Requested-With": "XMLHttpRequest",
        "Referer": REFERER,
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }
    data = {"ajax": "true", "league_id": league_id, "season": season}
    resp = requests.post(API_URL, headers=headers, data=data, timeout=30)
    resp.raise_for_status()
    payload = resp.json()
    if not payload.get("success"):
        raise RuntimeError(f"basketnews on-off API returned success=false: {payload}")
    return payload["data"]


def build_rows(data: dict) -> list[dict]:
    teams = {t["id"]: t for t in data["teams"]}
    players = {p["id"]: p for p in data["players"]}
    positions = {p["id"]: p["short_name"] for p in data["positions"]}

    rows = []
    for s in data["stats"]:
        team = teams.get(s["team_id"], {})
        player = players.get(s["player_id"], {})
        pos = "/".join(positions.get(pid, str(pid)) for pid in player.get("positions", []))
        time_played = s.get("time_played") or {}

        base = {
            "league": LEAGUE_NAME,
            "league_id": LEAGUE_ID,
            "season": SEASON,
            "team_id": s["team_id"],
            "team_name": team.get("name_en", ""),
            "team_short_name": team.get("short_name_en", ""),
            "player_id": s["player_id"],
            "player_name": player.get("full_name", ""),
            "position": pos,
            "games_played": s.get("games_played"),
            "time_played_formatted": time_played.get("formatted", ""),
        }

        for view, fields in ONOFF_STATS.items():
            row = dict(base)
            row["view"] = view
            for field in fields:
                val = s.get(field)
                row[field] = val.get("value") if isinstance(val, dict) else val
            rows.append(row)

    return rows


def main() -> None:
    data = fetch_payload()

    # Union of all KPI field names, in first-seen order across the three views.
    kpi_columns: list[str] = []
    seen: set[str] = set()
    for fields in ONOFF_STATS.values():
        for f in fields:
            if f not in seen:
                seen.add(f)
                kpi_columns.append(f)

    columns = BASE_COLUMNS + kpi_columns
    rows = build_rows(data)

    output_path = find_repo_root() / OUTPUT_RELATIVE_PATH
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, restval="")
        writer.writeheader()
        writer.writerows(rows)

    n_teams = len({r["team_id"] for r in rows})
    n_player_team_pairs = len(rows) // 3
    print(
        f"Wrote {len(rows)} rows ({n_player_team_pairs} player-team pairs x 3 views, {n_teams} teams) to {output_path}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
