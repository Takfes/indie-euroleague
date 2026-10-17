#!/usr/bin/env python3
"""Fetch EuroLeague Fantasy Challenge stats (season total or round by round) and write two CSVs.

The Fantasy Challenge web app (`euroleaguefantasy.euroleaguebasketball.net`)
is a Flutter/CanvasKit SPA that calls a JSON API on `fantaking-api.dunkest.com`
for its Stats table. This script reproduces those calls directly: one request
to the league config endpoint to find the competition, the in-progress round
and the matchday ids (these do NOT line up 1:1 with the visible round number),
then paginated requests to the stats table endpoint per role (players vs. head
coaches).

Two fetch modes, chosen with `--rounds`:

- `all` (default): one fetch per role with no matchday filter. The API
  aggregates over every round played, and every row carries `round` = "all".
  `--stats-type` picks the aggregate: `avg` (default) is the per-game mean over
  the games each person played, `tot` the season sum (the only way to recover
  the games played, roughly as tot / avg). A handful of requests, so it runs at
  the minimum delay.
- a selection such as `1-4,6`: one fetch per listed round and role, each row
  tagged with its round number, always `tot` (a single round has no average to
  take). Many more requests, so the default delay is longer. The selection is
  bounded by the in-progress round.

The mode and stats type default to `[acquisition] fantasy_rounds` / `fantasy_stats_type` in `config/config.toml`
(the DVC stage passes no arguments, so this is how `dvc repro` is parametrized); the flags override them.

`--delay` sets the pause between requests in either mode. The API rate-limits
at about 5 requests per 2 s window and answers 429 beyond it; the fetcher does
not retry, so keep the delay at or above its mode default if a run fails with
`HTTP Error 429: Too Many Requests`.

A per-round fetch reaches the *current* (in-progress) round, not just completed ones:
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

Every run re-derives the competition and round range from the live API and
rewrites both CSVs from scratch (no incremental state) -- cheap enough given
the season's round count, and avoids state-file complexity for what's
ultimately a full re-derivation each time.

Before writing, players the API listed twice in a round are reduced to their
lowest-rank row (`drop_duplicate_players`, logged as a warning), then both
tables pass the shared duplicate/empty-row validation, which fails the run.

Usage:
    python src/eupy/fetchers/fetch_euroleague_fantasy_stats.py [--rounds all|1-4,6] [--stats-type avg|tot] [--delay SECONDS] [--out-dir PATH]

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

from eupy.config import get_config
from eupy.logging_setup import get_logger
from eupy.quality import validate

CONFIG_URL = "https://fantaking-api.dunkest.com/api/v1/leagues/10/config"
STATS_URL_TEMPLATE = "https://fantaking-api.dunkest.com/api/v1/competitions/{competition_id}/stats/players/table"

PLAYER_POSITIONS = "Guard,Forward,Center"
HEAD_COACH_POSITION = "Head Coach"

# The API rejects anything above 100 with a 422 (and rejects -1 too --
# there's no "give me everything" option, real pagination is required).
MAX_PER_PAGE = 100

# Polite-client delay between sequential requests, per fetch mode. The API allows about 5 requests per 2 s window
# (`X-RateLimit-Limit: 5`; a 429 carries `Retry-After: 1-2`). The all-rounds total needs only a handful of requests,
# so it runs at the minimum delay; a per-round fetch sends a few per round and waits longer. `--delay` overrides both.
REQUEST_DELAY_SECONDS = 0.2
ROUNDS_DELAY_SECONDS = 1.0

# `--rounds` value (and the `round` column value) for the whole-season aggregate.
ALL_ROUNDS = "all"

# `stats_type` values the API accepts: `avg` is the per-game mean over the games each person played, `tot` the sum.
STATS_TYPES = ("avg", "tot")

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


def parse_rounds(spec: str, max_round: int) -> list[int]:
    """Parse a `--rounds` selection such as `1-4,6` into sorted round numbers within `1..max_round`.

    Raises:
        ValueError: If a part is not a number or an `a-b` range, or a round lies outside `1..max_round`.
    """
    rounds: set[int] = set()
    for part in spec.split(","):
        low, _, high = part.strip().partition("-")
        try:
            first, last = int(low), int(high or low)
        except ValueError:
            raise ValueError(
                f"Cannot read rounds {spec!r}: {part!r} is neither a round number nor an a-b range. "
                f"Use '{ALL_ROUNDS}' or a list such as '1-4,6'."
            ) from None
        if first < 1 or last > max_round or first > last:
            raise ValueError(
                f"Rounds {part.strip()!r} are outside 1-{max_round} (the in-progress round is {max_round}). "
                f"Pick rounds within that range or use '{ALL_ROUNDS}'."
            )
        rounds.update(range(first, last + 1))
    return sorted(rounds)


def fetch_round_rows(
    competition_id: int,
    matchday_id: int | None,
    positions: str,
    round_number: int | str,
    token: str,
    delay: float = REQUEST_DELAY_SECONDS,
    stats_type: str = "tot",
) -> list[dict[str, Any]]:
    """Fetch every row for one round and one `positions` filter, paginating as needed.

    A `matchday_id` of None sends no matchday filter: the API then aggregates over every round played (as
    `stats_type` says: `avg` or `tot`), and the caller tags those rows with `ALL_ROUNDS` as `round_number`.
    `delay` is the pause after each request.
    """
    url = STATS_URL_TEMPLATE.format(competition_id=competition_id)
    rows: list[dict[str, Any]] = []
    page = 1
    while True:
        params: dict[str, Any] = {
            "stats_type": stats_type,
            "positions": positions,
            "page": page,
            "per_page": MAX_PER_PAGE,
        }
        if matchday_id is not None:
            params["matchdays"] = matchday_id
        payload = fetch_json(f"{url}?{urllib.parse.urlencode(params)}", token)
        time.sleep(delay)

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


def drop_duplicate_players(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one row per `(round, player_id)`: the one with the lowest `rank`.

    The API sometimes lists a zero-point player twice in a round, with the same values and two different ranks.
    Every dropped row is logged as a warning; row order is otherwise preserved.
    """
    best: dict[tuple[str, str], int] = {}
    for i, row in enumerate(rows):
        key = (str(row["round"]), str(row["player_id"]))
        if key not in best or int(rows[best[key]]["rank"]) > int(row["rank"]):
            best[key] = i
    kept = set(best.values())
    dropped = [row for i, row in enumerate(rows) if i not in kept]
    if dropped:
        get_logger("fetch_euroleague_fantasy_stats").warning(
            "fantasy_stats/players: dropped %d duplicate (round, player_id) row(s), kept the lowest rank: %s",
            len(dropped),
            [(r["round"], r["player_id"], r["name"], r["rank"]) for r in dropped],
        )
    return [row for i, row in enumerate(rows) if i in kept]


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


def run(
    out_dir: Path,
    token: str,
    rounds: str = ALL_ROUNDS,
    delay: float | None = None,
    stats_type: str | None = None,
) -> None:
    """Fetch players and head-coach stats, write both CSVs.

    Args:
        out_dir: Directory for `players.csv` and `head_coaches.csv`.
        token: The fantasy API bearer token.
        rounds: `ALL_ROUNDS` for the one season-wide fetch (`round` column = "all"), or a selection such as
            `1-4,6` for one fetch per listed round, bounded by the in-progress round.
        delay: Seconds between requests; None picks the mode default (`REQUEST_DELAY_SECONDS` for the season-wide
            fetch, `ROUNDS_DELAY_SECONDS` per round).
        stats_type: `avg` or `tot` for the season-wide fetch (None means `avg`). A per-round fetch is always `tot`.

    Raises:
        ValueError: If `stats_type` is not `avg`/`tot`, or is `avg` together with a round selection.
    """
    if stats_type not in (None, *STATS_TYPES):
        raise ValueError(f"Unknown stats type {stats_type!r}. Use one of {STATS_TYPES}.")
    config = fetch_config(token)
    competition_id: int = config["current_competition_id"]
    max_round = max_round_to_fetch(config)
    if rounds == ALL_ROUNDS:
        # (round label, matchday id): no matchday filter gives the season total in one pass.
        targets: list[tuple[int | str, int | None]] = [(ALL_ROUNDS, None)]
        delay = REQUEST_DELAY_SECONDS if delay is None else delay
        stats_type = stats_type or "avg"
    else:
        if stats_type == "avg":
            raise ValueError(
                "stats type 'avg' only applies to the all-rounds fetch; a per-round fetch is always 'tot'. "
                f"Drop --stats-type, or use --rounds {ALL_ROUNDS}."
            )
        stats_type = "tot"
        matchday_by_round = matchday_ids_by_round(config)
        targets = [(n, matchday_by_round[n]) for n in parse_rounds(rounds, max_round)]
        delay = ROUNDS_DELAY_SECONDS if delay is None else delay

    players_rows: list[dict[str, Any]] = []
    head_coaches_rows: list[dict[str, Any]] = []
    for round_number, matchday_id in targets:
        players_rows.extend(
            fetch_round_rows(
                competition_id, matchday_id, PLAYER_POSITIONS, round_number, token, delay, stats_type=stats_type
            )
        )
        head_coaches_rows.extend(
            fetch_round_rows(
                competition_id, matchday_id, HEAD_COACH_POSITION, round_number, token, delay, stats_type=stats_type
            )
        )

    players_rows = drop_duplicate_players(players_rows)
    validate(players_rows, ["round", "player_id"], "fantasy_stats/players")
    validate(head_coaches_rows, ["round", "player_id"], "fantasy_stats/head_coaches")
    players_path = out_dir / "players.csv"
    head_coaches_path = out_dir / "head_coaches.csv"
    write_csv(players_rows, players_path, PLAYER_CSV_FIELDNAMES)
    write_csv(head_coaches_rows, head_coaches_path, HEAD_COACH_CSV_FIELDNAMES)

    print(
        f"Wrote {len(players_rows)} player rows to {players_path} and "
        f"{len(head_coaches_rows)} head-coach rows to {head_coaches_path} "
        f"(rounds: {rounds}, stats type: {stats_type}, delay {delay}s, in-progress round {max_round})"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Raw output directory")
    parser.add_argument(
        "--rounds",
        default=get_config().acquisition.fantasy_rounds,
        help=(
            f"'{ALL_ROUNDS}' for the whole season in one pass, or rounds such as '1-4,6', one fetch each "
            "(default: config acquisition.fantasy_rounds)"
        ),
    )
    parser.add_argument(
        "--stats-type",
        choices=STATS_TYPES,
        default=get_config().acquisition.fantasy_stats_type,
        help=(
            "'avg' per-game mean (default for --rounds all) or 'tot' season sum; a per-round fetch is always 'tot' "
            "(default: config acquisition.fantasy_stats_type)"
        ),
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=None,
        help=f"Seconds between requests (default: {REQUEST_DELAY_SECONDS} for the total, {ROUNDS_DELAY_SECONDS} per round)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run(args.out_dir, get_auth_token(), args.rounds, args.delay, args.stats_type)


if __name__ == "__main__":
    main()
