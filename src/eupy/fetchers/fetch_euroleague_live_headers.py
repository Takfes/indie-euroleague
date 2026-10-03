#!/usr/bin/env python3
"""Fetch EuroLeague live-API game headers incrementally, shaped like the Kaggle header CSV.

The live API's `Header` endpoint (`live.euroleague.net/api/Header?gamecode=N&seasoncode=E2022`)
returns one JSON object per game. This script polls gamecodes upward from where the last
run left off (per season) and writes each newly *completed* game as one row in the schema of
`data/raw_data/kaggle_data/euroleague_header.csv` -- so live deltas and the Kaggle history
can be stacked (see `append_live_headers.py`).

Mapping notes (verified against Kaggle rows for E2022 / E2025):
- `game_id` is `{season}_{gamecode:03d}` (zero-padded, as in Kaggle).
- `date` `dd/mm/yyyy` -> ISO `yyyy-mm-dd`; `time` `"19:45 "` -> `19:45:00`.
- `score_quarter_N_*` are cumulative running scores, same as Kaggle.
- Extra time: the live API only exposes the *final* score in `ScoreExtraTime{A,B}` (0 when
  no overtime), whereas Kaggle splits it per overtime period. Lossy by construction: the
  final score lands in `score_extra_time_1_*` and periods 2-4 stay blank, so multi-overtime
  games differ from Kaggle there. Everything else matches.
- Gamecodes can have gaps (unplayed playoff games), so an empty response only stops the
  run after `--max-gap` consecutive misses. A game still `Live` stops the run immediately
  so it is picked up, complete, on the next run.

Known limitation: a game whose gamecode is *below* the last ingested one (e.g. postponed
and played late) is not revisited; delete the season's state entry to re-fetch it.

Usage:
    python src/eupy/fetchers/fetch_euroleague_live_headers.py [--season E2026] [--out-dir PATH] [--max-gap 5]

Inputs: none
Sources:
  - live.euroleague.net/api/Header (live)
Outputs:
  - euroleague_net/headers: data/raw_data/euroleague_net/headers/
Final: false
Impure: true
Refresh: incremental: one delta file per run with new games
Notes: Delta files are named {season}_delta_{utc_timestamp}.csv, in the Kaggle euroleague_header schema.
  State file data/raw_data/euroleague_net/headers/_state.json tracks progress and is updated every
  run.
"""

from __future__ import annotations

import argparse
import csv
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from eupy.fetchers.live_euroleague import (
    DEFAULT_OUT_DIR,
    REQUEST_DELAY_SECONDS,
    fetch_header,
    load_state,
    save_state,
)

DEFAULT_MAX_GAP = 5

CSV_FIELDNAMES = [
    "game_id",
    "game",
    "date",
    "time",
    "round",
    "phase",
    "season_code",
    "score_a",
    "score_b",
    "team_a",
    "team_b",
    "team_id_a",
    "team_id_b",
    "coach_a",
    "coach_b",
    "game_time",
    "remaining_partial_time",
    "referee_1",
    "referee_2",
    "referee_3",
    "stadium",
    "capacity",
    "w_id",
    "fouls_a",
    "fouls_b",
    "timeouts_a",
    "timeouts_b",
    "score_quarter_1_a",
    "score_quarter_2_a",
    "score_quarter_3_a",
    "score_quarter_4_a",
    "score_quarter_1_b",
    "score_quarter_2_b",
    "score_quarter_3_b",
    "score_quarter_4_b",
    "score_extra_time_1_a",
    "score_extra_time_2_a",
    "score_extra_time_3_a",
    "score_extra_time_4_a",
    "score_extra_time_1_b",
    "score_extra_time_2_b",
    "score_extra_time_3_b",
    "score_extra_time_4_b",
]


def is_header_complete(header: dict[str, Any] | None) -> bool:
    """A header is ingestible when non-empty, not live, and the game has been played (score > 0)."""
    if header is None or header.get("Live"):
        return False
    return int(header["ScoreA"] or 0) + int(header["ScoreB"] or 0) > 0


def _extra_time(value: Any) -> Any:
    """Live `ScoreExtraTime` is the final score if overtime was played, else 0 -> blank."""
    return value if value else ""


def build_header_row(header: dict[str, Any], season: str, gamecode: int) -> dict[str, Any]:
    """Map one live Header response to a Kaggle-header-schema row."""
    returned = header["pcom"].strip()
    if returned != season:
        raise ValueError(f"Header for gamecode {gamecode} reports season {returned!r}, expected {season!r}")
    date = datetime.strptime(header["Date"], "%d/%m/%Y").date().isoformat()
    row: dict[str, Any] = {
        "game_id": f"{season}_{gamecode:03d}",
        "game": f"{header['CodeTeamA']}-{header['CodeTeamB']}",
        "date": date,
        "time": f"{header['Hour'].strip()}:00",
        "round": header["Round"],
        "phase": header["Phase"],
        "season_code": season,
        "score_a": header["ScoreA"],
        "score_b": header["ScoreB"],
        "team_a": header["TeamA"],
        "team_b": header["TeamB"],
        "team_id_a": header["CodeTeamA"],
        "team_id_b": header["CodeTeamB"],
        "coach_a": header["CoachA"],
        "coach_b": header["CoachB"],
        "game_time": header["GameTime"],
        "remaining_partial_time": header["RemainingPartialTime"],
        "referee_1": header["Referee1"],
        "referee_2": header["Referee2"],
        "referee_3": header["Referee3"],
        "stadium": header["Stadium"],
        "capacity": header["Capacity"],
        "w_id": header["wid"],
        "fouls_a": header["FoultsA"],
        "fouls_b": header["FoultsB"],
        "timeouts_a": header["TimeoutsA"],
        "timeouts_b": header["TimeoutsB"],
    }
    for quarter in range(1, 5):
        row[f"score_quarter_{quarter}_a"] = header[f"ScoreQuarter{quarter}A"]
        row[f"score_quarter_{quarter}_b"] = header[f"ScoreQuarter{quarter}B"]
    for period in range(1, 5):
        row[f"score_extra_time_{period}_a"] = _extra_time(header["ScoreExtraTimeA"]) if period == 1 else ""
        row[f"score_extra_time_{period}_b"] = _extra_time(header["ScoreExtraTimeB"]) if period == 1 else ""
    return {name: row[name] for name in CSV_FIELDNAMES}


def write_delta_csv(rows: list[dict[str, Any]], out_path: Path) -> None:
    """Write newly ingested header rows to a delta CSV. Caller ensures rows is non-empty."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def run(season: str, out_dir: Path, max_gap: int = DEFAULT_MAX_GAP) -> None:
    """Poll new gamecodes for `season`, write a delta CSV if any completed, update state."""
    headers_dir = out_dir / "headers"
    state_path = headers_dir / "_state.json"
    state = load_state(state_path)
    season_state = state.get(season, {})
    last_ingested: int | None = season_state.get("last_ingested_gamecode")
    start_gamecode = (last_ingested or 0) + 1

    rows: list[dict[str, Any]] = []
    gamecode = start_gamecode
    misses = 0
    while misses < max_gap:
        header = fetch_header(season, gamecode)
        time.sleep(REQUEST_DELAY_SECONDS)
        if header is None:
            misses += 1
        elif header.get("Live"):
            break  # in progress: pick it up, complete, next run
        elif is_header_complete(header):
            rows.append(build_header_row(header, season, gamecode))
            last_ingested = gamecode
            misses = 0
        else:
            misses += 1  # scheduled but unplayed
        gamecode += 1

    season_state["last_checked_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if rows:
        season_state["last_ingested_gamecode"] = last_ingested
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        delta_path = headers_dir / f"{season}_delta_{timestamp}.csv"
        write_delta_csv(rows, delta_path)
        print(f"Wrote {len(rows)} header row(s) to {delta_path}")
    else:
        print(f"No new completed games for {season} (checked from gamecode {start_gamecode})")

    state[season] = season_state
    save_state(state, state_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", default="E2026", help="Season code, e.g. E2026 (default: %(default)s)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Raw output directory")
    parser.add_argument(
        "--max-gap",
        type=int,
        default=DEFAULT_MAX_GAP,
        help="Consecutive empty/unplayed gamecodes that end the run (default: %(default)s)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run(args.season, args.out_dir, args.max_gap)


if __name__ == "__main__":
    main()
