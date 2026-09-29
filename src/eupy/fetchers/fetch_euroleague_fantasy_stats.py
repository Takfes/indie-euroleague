#!/usr/bin/env python3
"""Fetch EuroLeague Fantasy Challenge round-by-round stats and write two CSVs.

The Fantasy Challenge web app (`euroleaguefantasy.euroleaguebasketball.net`)
is a Flutter/CanvasKit SPA that calls a JSON API on `fantaking-api.dunkest.com`
for its Stats table. This script reproduces those calls directly: one request
to the league config endpoint to find which round to fetch through (and the
matchday ids -- these do NOT line up 1:1 with the visible round number), then
one paginated request per round per role (players vs. head coaches) to the
stats table endpoint.

Fetches through the *current* (in-progress) round, not just completed ones:
the API starts publishing a round's `quotation` (price entering that round)
as soon as it becomes `current_matchday`, well before that round's box-score
columns are final -- waiting for the round to close would miss capturing that
price snapshot, since `quotation` typically moves again once the round ends.
Box-score columns for an in-progress round come back as placeholders
(`0`/`"-"`) and get overwritten with final values by a later run, once that
round becomes `previous_matchday`.

Auth is a static bearer token (Laravel-Sanctum-shaped: `{id}|{token}`, not a
JWT) captured once from an already-logged-in browser session
(`localStorage.getItem('flutter.authToken')`) and stored as
`EUROLEAGUE_FANTASY_AUTH_TOKEN` in `.env` -- see
specs/spec-euroleague-fantasy-stats.md for the full bootstrap procedure
and the empirical findings behind every design choice below (why one matchday
per call, why no `active_players` filter, why `per_page` is capped at 100,
etc).

Every run re-derives the round range from the live API and rewrites both
CSVs from scratch (no incremental state) -- cheap enough given the season's
round count, and avoids state-file complexity for what's ultimately a
full-history re-derivation each time.

Usage:
    python src/eupy/fetchers/fetch_euroleague_fantasy_stats.py [--out-dir PATH]

Inputs: none
Sources:
  - fantaking-api.dunkest.com (live; auth via EUROLEAGUE_FANTASY_AUTH_TOKEN env var or repo-root .env)
Outputs:
  - fantasy_stats/players: data/raw_data/fantasy_stats/players.csv
  - fantasy_stats/head_coaches: data/raw_data/fantasy_stats/head_coaches.csv
Final: false
Impure: true
Refresh: full overwrite per run
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

CONFIG_URL = "https://fantaking-api.dunkest.com/api/v1/leagues/10/config"
STATS_URL_TEMPLATE = "https://fantaking-api.dunkest.com/api/v1/competitions/{competition_id}/stats/players/table"

PLAYER_POSITIONS = "Guard,Forward,Center"
HEAD_COACH_POSITION = "Head Coach"

# The API rejects anything above 100 with a 422 (and rejects -1 too --
# there's no "give me everything" option, real pagination is required).
MAX_PER_PAGE = 100

# Polite-client delay between sequential requests.
REQUEST_DELAY_SECONDS = 0.2

DEFAULT_OUT_DIR = Path(__file__).resolve().parents[3] / "data" / "raw_data" / "fantasy_stats"
DEFAULT_ENV_PATH = Path(__file__).resolve().parents[3] / ".env"

# Fixed CSV headers: "round"/"player_id" (synthesized here) followed by a
# subset of the API's own `data.columns` names, sanitized ("win_1-10" ->
# "win_1_10" -- CSV headers shouldn't contain hyphens that read like minus
# signs). Hardcoded (rather than derived per-response) so the output schema
# is stable even for a round with zero rows; matches this repo's other
# fetchers.
#
# The API returns the same 26 columns for both position filters, but only
# half apply to each: box-score columns are always "-" for head coaches,
# and win/loss-bucket columns are always "-" for players (see spec's
# "Endpoints" section). Each output keeps only the columns that are ever
# real for that entity, so neither CSV carries columns that are always
# placeholder.
_COMMON_FIELDNAMES = [
    "round",
    "player_id",
    "rank",
    "name",
    "position",
    "team",
    "fpt",
    "quotation",
    "plus",
]

PLAYER_CSV_FIELDNAMES = [
    *_COMMON_FIELDNAMES,
    "pts",
    "reb",
    "ast",
    "stl",
    "tov",
    "blk",
    "blka",
    "fd",
    "pf",
    "fg_missed",
    "ft_missed",
]

HEAD_COACH_CSV_FIELDNAMES = [
    *_COMMON_FIELDNAMES,
    "win_1_10",
    "win_11_20",
    "win_20",
    "win_ot",
    "loss_1_10",
    "loss_11_20",
    "loss_20",
    "loss_ot",
]


def sanitize_column_name(name: str) -> str:
    """Turn an API column name into a valid CSV/dict-key field name."""
    return name.replace("-", "_")


def get_auth_token(env_path: Path = DEFAULT_ENV_PATH) -> str:
    """Get the bearer token from the environment, falling back to a `.env` file.

    Args:
        env_path: Path to a `.env` file to fall back to if the environment
            variable isn't already set.

    Returns:
        The bearer token value (without the `Bearer ` prefix).

    Raises:
        ValueError: If the token isn't set in the environment or `.env`.
    """
    token = os.environ.get("EUROLEAGUE_FANTASY_AUTH_TOKEN")
    if token:
        return token
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() == "EUROLEAGUE_FANTASY_AUTH_TOKEN":
                return value.strip()
    raise ValueError(
        "EUROLEAGUE_FANTASY_AUTH_TOKEN is not set. Log into "
        "https://euroleaguefantasy.euroleaguebasketball.net in a browser, run "
        "localStorage.getItem('flutter.authToken') in devtools, and store the "
        "result as EUROLEAGUE_FANTASY_AUTH_TOKEN in .env "
        "(see specs/spec-euroleague-fantasy-stats.md)."
    )


def fetch_json(url: str, token: str) -> dict[str, Any]:
    """Fetch a URL with the bearer token and parse the response as JSON."""
    request = urllib.request.Request(  # noqa: S310
        url,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def fetch_config(token: str) -> dict[str, Any]:
    """Fetch the league config: matchday list, previous/current round, competition id."""
    return fetch_json(CONFIG_URL, token)["data"]


def matchday_ids_by_round(config: dict[str, Any]) -> dict[int, int]:
    """Build `{round_number: matchday_id}` from the config's `matchdays` list."""
    return {matchday["number"]: matchday["id"] for matchday in config["matchdays"]}


def max_round_to_fetch(config: dict[str, Any]) -> int:
    """Return the highest round number to fetch this run: the in-progress round.

    Includes `current_matchday`, not just `previous_matchday` (the last
    *completed* round) -- see the module docstring for why the in-progress
    round's price data would otherwise be missed.
    """
    return config["current_matchday"]["number"]


def fetch_round_rows(
    competition_id: int, matchday_id: int, positions: str, round_number: int, token: str
) -> list[dict[str, Any]]:
    """Fetch every row for one round and one `positions` filter, paginating as needed."""
    url = STATS_URL_TEMPLATE.format(competition_id=competition_id)
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        params = {
            "stats_type": "tot",
            "matchdays": matchday_id,
            "positions": positions,
            "page": page,
            "per_page": MAX_PER_PAGE,
        }
        payload = fetch_json(f"{url}?{urllib.parse.urlencode(params)}", token)
        time.sleep(REQUEST_DELAY_SECONDS)

        columns = [sanitize_column_name(c) for c in payload["data"]["columns"]]
        for player in payload["data"]["players"]:
            record = dict(zip(columns, player["row"], strict=True))
            record["round"] = round_number
            record["player_id"] = player["id"]
            rows.append(record)

        if page >= payload["meta"]["last_page"]:
            break
        page += 1
    return rows


def write_csv(rows: list[dict[str, Any]], out_path: Path, fieldnames: list[str]) -> None:
    """Write rows to CSV (full overwrite), creating parent dirs as needed.

    Each row dict carries all 26 API columns regardless of entity type (see
    `PLAYER_CSV_FIELDNAMES`/`HEAD_COACH_CSV_FIELDNAMES`); `extrasaction`
    drops whichever ones aren't wanted in this output.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run(out_dir: Path, token: str) -> None:
    """Fetch every round's players and head-coach stats through the in-progress round, write both CSVs."""
    config = fetch_config(token)
    competition_id: int = config["current_competition_id"]
    max_round = max_round_to_fetch(config)
    matchday_by_round = matchday_ids_by_round(config)

    players_rows: list[dict[str, Any]] = []
    head_coaches_rows: list[dict[str, Any]] = []
    for round_number in range(1, max_round + 1):
        matchday_id = matchday_by_round[round_number]
        players_rows.extend(fetch_round_rows(competition_id, matchday_id, PLAYER_POSITIONS, round_number, token))
        head_coaches_rows.extend(
            fetch_round_rows(competition_id, matchday_id, HEAD_COACH_POSITION, round_number, token)
        )

    players_path = out_dir / "players.csv"
    head_coaches_path = out_dir / "head_coaches.csv"
    write_csv(players_rows, players_path, PLAYER_CSV_FIELDNAMES)
    write_csv(head_coaches_rows, head_coaches_path, HEAD_COACH_CSV_FIELDNAMES)

    print(
        f"Wrote {len(players_rows)} player rows to {players_path} and "
        f"{len(head_coaches_rows)} head-coach rows to {head_coaches_path} "
        f"(rounds 1-{max_round}, including the in-progress round)"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Raw output directory")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run(args.out_dir, get_auth_token())


if __name__ == "__main__":
    main()
