#!/usr/bin/env python3
"""Fetch the full-season EuroLeague schedule from the Euroleague Advanced API and write it as a raw CSV.

`GET https://euroleague-advanced-api.eu/Euroleague/schedule?season={year}` is free and
unauthenticated (only the API's advanced-stats endpoints need a subscription) and returns a JSON
array with one flat object per game: round/gameday, date, tip-off time, arena, home/away teams and
a `played` flag. Its `gamecode` (e.g. `E2026_7`) has the same format as the live box-score
fetcher's `game_id`, so it is the join key between the two.

The schedule is mutable (`played`, dates and times get confirmed or changed), so every run
refetches and fully overwrites the season's file -- no state file, no timestamps. The raw layer
keeps values verbatim and columns in the API's order; the same API response therefore yields a
byte-identical file. The response is fetched and validated before the output file is opened, so
a failed request or a bad payload never truncates the previous good file (the write itself is not
atomic -- a crash mid-write could).

Usage:
    python src/eupy/fetchers/fetch_euroleague_schedule.py [--season E2026] [--out-dir PATH]

`--season` takes the same code as the live box-score fetcher (`E` + 4-digit start year); the API's
integer `season` parameter is derived from it (`E2026` -> `2026`).

Inputs: none -- fetched live from https://euroleague-advanced-api.eu/Euroleague/schedule?season={year}.
Outputs: data/raw_data/euroleague_schedule/schedule_{season}.csv (overwritten each run).
Final: false -- raw fetch output, not exposed under data/stage_99/.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import urllib.request
from pathlib import Path
from typing import Any

SCHEDULE_URL = "https://euroleague-advanced-api.eu/Euroleague/schedule"

# The live.euroleague.net sibling fetchers need a non-default UA (it 403s urllib's). This API was
# only verified with curl's own UA, so a descriptive one is sent to match the convention.
USER_AGENT = "Mozilla/5.0 (compatible; euroleague-schedule-fetcher/1.0)"

DEFAULT_OUT_DIR = Path(__file__).resolve().parents[3] / "data" / "raw_data" / "euroleague_schedule"

# [0-9], not \d: \d also matches non-ASCII digits, which would slip through to the request.
SEASON_CODE_RE = re.compile(r"E([0-9]{4})")


def parse_season_year(season_code: str) -> int:
    """Convert a season code such as `E2026` to the API's integer year (`2026`).

    Args:
        season_code: `E` followed by exactly four digits.

    Returns:
        The four-digit start year.

    Raises:
        ValueError: If `season_code` is not `E` + 4 digits.
    """
    match = SEASON_CODE_RE.fullmatch(season_code)
    if match is None:
        raise ValueError(
            f"Invalid season code {season_code!r}: expected 'E' followed by a 4-digit start year. "
            f"Pass e.g. --season E2026 (not 2026 or E26)."
        )
    return int(match.group(1))


def validate_schedule(payload: Any, season_code: str, url: str) -> list[dict[str, Any]]:
    """Check that a decoded API payload is a usable, internally consistent schedule.

    Args:
        payload: The decoded JSON response.
        season_code: The requested season code; every `gamecode` must start with `{season_code}_`.
        url: The requested URL, only used to make error messages actionable.

    Returns:
        The payload, unchanged, typed as a list of game dicts.

    Raises:
        ValueError: If the payload is not a non-empty list of dicts sharing one key set, or a
            row's `gamecode` does not belong to `season_code`.
    """
    if not isinstance(payload, list):
        # ValueError (not TypeError) keeps every bad-payload failure one exception type for callers.
        raise ValueError(  # noqa: TRY004
            f"Expected a JSON array of games from {url}, got {type(payload).__name__}. "
            f"The API's response shape may have changed; compare with its /openapi.json."
        )
    if not payload:
        raise ValueError(
            f"{url} returned zero games. A season with no games is treated as a failure, not a result; "
            f"check that {season_code} exists and has started, then re-run."
        )

    first = payload[0]
    keys = set(first) if isinstance(first, dict) else set()
    prefix = f"{season_code}_"
    for index, row in enumerate(payload):
        if not isinstance(row, dict) or set(row) != keys:
            raise ValueError(
                f"Row {index} from {url} is not an object with the same fields as row 0 ({sorted(keys)}). "
                f"The API's row shape may have changed; refusing to write a ragged CSV."
            )
        gamecode = row.get("gamecode")
        if not isinstance(gamecode, str) or not gamecode.startswith(prefix):
            raise ValueError(
                f"Row {index} from {url} has gamecode {gamecode!r}, expected it to start with {prefix!r}. "
                f"The API may have returned a different season than {season_code}; check --season."
            )
    return payload


def fetch_schedule(season_code: str) -> list[dict[str, Any]]:
    """Download and validate the full-season schedule.

    Args:
        season_code: `E` + 4-digit year, e.g. `E2026`.

    Returns:
        One dict per game, keys in the API's order, values as returned.

    Raises:
        ValueError: If the season code is malformed (no request is made) or the payload is invalid.
        RuntimeError: If the request fails (HTTP error, network error, timeout).
    """
    year = parse_season_year(season_code)
    url = f"{SCHEDULE_URL}?season={year}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    try:
        # URLError/HTTPError and timeouts are all OSError subclasses.
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            body = response.read()
    except OSError as e:
        raise RuntimeError(
            f"Request to {url} failed: {e}. Nothing was written. Check your connection and that "
            f"{season_code} is a season the API serves (2000 up to the one under way), then re-run."
        ) from e
    try:
        payload = json.loads(body.decode("utf-8"))
    except ValueError as e:  # JSONDecodeError and UnicodeDecodeError both subclass ValueError
        raise ValueError(f"Response from {url} is not valid UTF-8 JSON: {e}. Nothing was written; re-run later.") from e
    return validate_schedule(payload, season_code, url)


def write_schedule_csv(rows: list[dict[str, Any]], out_path: Path) -> None:
    """Write schedule rows to `out_path`, replacing any existing file.

    Columns follow the keys of the first row. `rows` must be non-empty and share one key set;
    `fetch_schedule` guarantees both.
    """
    fieldnames = list(rows[0])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _season_arg(value: str) -> str:
    """argparse `type=` hook: reject a malformed season code as a usage error, before any request."""
    try:
        parse_season_year(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from e
    return value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--season", type=_season_arg, default="E2026", help="Season code, e.g. E2026 (default: %(default)s)"
    )
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Raw output directory")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = fetch_schedule(args.season)
    out_path = args.out_dir / f"schedule_{args.season}.csv"
    write_schedule_csv(rows, out_path)
    print(f"Wrote {len(rows)} games to {out_path}")


if __name__ == "__main__":
    main()
