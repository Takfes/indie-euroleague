#!/usr/bin/env python3
"""Fetch the EuroLeague Fantasy Challenge player stats table from its JSON API and write it to CSV.

The stats page (https://euroleaguefantasy.euroleaguebasketball.net/10/stats) is a Flutter canvas app, so
there is no DOM to scrape. It renders from an authenticated JSON API instead:

    GET https://fantaking-api.dunkest.com/api/v1/competitions/<id>/stats/players/table

The API answers 401 without the logged-in user's bearer token and caps ``per_page`` at 100, so this
script pages through the result (4 pages for ~350 players). The token is read from the
``EUROLEAGUE_FANTASY_TOKEN`` environment variable and never printed or written anywhere. To get it, log in
at the site in Chrome, open DevTools -> Console and run ``copy(localStorage['flutter.authToken'].replace(/^"|"$/g, ''))``
(it lands on the clipboard without being displayed), then:

    EUROLEAGUE_FANTASY_TOKEN="$(pbpaste)" uv run python src/fetch_euroleague_fantasy_stats.py

The token is long-lived (an opaque personal access token, no expiry claim), so it can also be kept in a
git-ignored ``.env`` file and loaded with ``uv run --env-file .env python src/...``. Both the per-game
averages ("AVG" toggle) and the season totals ("TOT" toggle) are fetched, into one CSV each. Re-run any
time to refresh them; the files are overwritten.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API_URL = "https://fantaking-api.dunkest.com/api/v1/competitions/{competition_id}/stats/players/table"
TOKEN_ENV_VAR = "EUROLEAGUE_FANTASY_TOKEN"  # noqa: S105 - the variable's name, not a secret

DEFAULT_COMPETITION_ID = 49
PER_PAGE = 100  # the API rejects anything larger (422)
STATS_TYPES = ("avg", "tot")
NO_VALUE = "-"  # what the API shows for a stat that does not apply to a row (e.g. box score for a coach)

DEFAULT_OUT_DIR = Path(__file__).resolve().parents[1] / "data" / "euroleague-fantasy-stats"


def get_token() -> str:
    """Return the bearer token from the environment, or explain how to get one."""
    token = os.environ.get(TOKEN_ENV_VAR, "").strip()
    if not token:
        raise RuntimeError(
            f"{TOKEN_ENV_VAR} is not set. Log in at the EuroLeague Fantasy site in Chrome, run "
            "copy(localStorage['flutter.authToken'].replace(/^\"|\"$/g, '')) in the DevTools console, then re-run with "
            f'{TOKEN_ENV_VAR}="$(pbpaste)" (see this script\'s docstring).'
        )
    return token


def build_params(stats_type: str, page: int) -> list[tuple[str, str]]:
    """Query params the stats page sends, with the credit filter widened so it can never drop a player."""
    return [
        ("stats_type", stats_type),
        ("positions", ""),
        ("matchdays", ""),  # empty = "All rounds"
        ("search", ""),
        ("teams", ""),  # empty = "All teams"
        ("quotations", "0,100"),  # the page's slider stops at 30; wider returns the same players
        ("page", str(page)),
        ("per_page", str(PER_PAGE)),
        ("sort_by", "fpt"),
        ("sort_order", "desc"),
        ("active_players", "true"),  # false returns only the ~15 inactive players, not "everyone"
    ]


def fetch_page(competition_id: int, stats_type: str, page: int, token: str) -> dict:
    """GET one page of the stats table and return the decoded JSON payload."""
    url = f"{API_URL.format(competition_id=competition_id)}?{urllib.parse.urlencode(build_params(stats_type, page))}"
    request = urllib.request.Request(  # noqa: S310
        url,
        headers={"Accept": "application/json", "Authorization": f"Bearer {token}", "User-Agent": "Mozilla/5.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return json.load(response)
    except urllib.error.HTTPError as err:
        if err.code == 401:
            raise RuntimeError(
                f"The API rejected {TOKEN_ENV_VAR} (401). Log in again in Chrome and re-copy the token."
            ) from err
        raise


def to_records(columns: list[str], players: list[dict]) -> list[dict[str, str]]:
    """Turn the API's ``{id, row}`` pairs into dicts keyed by column name, with the player id first."""
    records = []
    for player in players:
        if len(player["row"]) != len(columns):
            raise ValueError(f"Player {player['id']} has {len(player['row'])} cells for {len(columns)} columns")
        cells = ["" if cell == NO_VALUE else cell for cell in player["row"]]
        records.append({"player_id": player["id"], **dict(zip(columns, cells, strict=True))})
    return records


def fetch_stats(competition_id: int, stats_type: str, token: str) -> list[dict[str, str]]:
    """Page through the whole table for one stats type and return one record per player."""
    columns: list[str] | None = None
    players: list[dict] = []
    page = 1
    while True:
        payload = fetch_page(competition_id, stats_type, page, token)
        data, meta = payload["data"], payload["meta"]
        if columns is None:
            columns = data["columns"]
        elif data["columns"] != columns:
            raise ValueError(f"Column list changed between pages: {columns} vs {data['columns']}")
        players += data["players"]
        if page >= meta["last_page"]:
            break
        page += 1

    if len(players) != meta["total"]:
        raise ValueError(f"Fetched {len(players)} players but the API reports {meta['total']}")
    ids = [player["id"] for player in players]
    if len(set(ids)) != len(ids):
        raise ValueError("Duplicate player ids across pages (unstable sort between requests) -- re-run")
    return to_records(columns, players)


def write_csv(records: list[dict[str, str]], out_path: Path) -> None:
    """Write records to CSV, using the key order of the first record as the header."""
    if not records:
        raise ValueError("No players returned -- refusing to overwrite the CSV with an empty file")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(records[0].keys()))
        writer.writeheader()
        writer.writerows(records)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Directory for the CSV files")
    parser.add_argument(
        "--competition-id",
        type=int,
        default=DEFAULT_COMPETITION_ID,
        help="Competition id in the API path (49 = the current EuroLeague Fantasy season)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    token = get_token()
    for stats_type in STATS_TYPES:
        records = fetch_stats(args.competition_id, stats_type, token)
        out_path = args.out_dir / f"player_stats_{stats_type}.csv"
        write_csv(records, out_path)
        team_count = len({record["team"] for record in records})
        print(f"Wrote {len(records)} players ({team_count} teams) to {out_path}")


if __name__ == "__main__":
    main()
