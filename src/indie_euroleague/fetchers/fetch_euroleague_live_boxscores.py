#!/usr/bin/env python3
"""Fetch EuroLeague live-API box scores incrementally and write delta CSVs.

EuroLeague's live API (`live.euroleague.net`) exposes per-game data via two
unauthenticated JSON endpoints, `Header` and `BoxScore`, keyed by `gamecode`
(sequential across the whole season, not reset per round) and `seasoncode`
(e.g. `E2026` for 2026-27). A gamecode with no game (yet) returns a completely
empty HTTP body -- that's the signal used here to know when to stop.

This script polls gamecodes upward from where the last run left off, and for
each newly *completed* game (not currently live, both teams' box scores
populated) builds rows shaped like the historical dataset at
`data/raw_data/kaggle_data/euroleague_box_score.csv`: one row per player plus
a synthesized team-total row (`dorsal="TOTAL"`) per team, per game.

Known limitation (not fixed here): the live API's player IDs (e.g. `P011157`)
use a different ID scheme than the historical CSV's `PBDE`-style 4-char codes.
No reconciliation is attempted -- live-API rows carry the live API's own IDs.

See docs/specs/spec-euroleague-live-fetcher.md for the full design.

Usage:
    python src/indie_euroleague/fetchers/fetch_euroleague_live_boxscores.py [--season E2026] [--out-dir PATH]

Each run appends at most one delta CSV (only newly ingested games) and updates
the season's `last_ingested_gamecode` in the state file. If there's nothing new,
no delta file is written and the state's `last_checked_at` is bumped.

Inputs: none -- fetched live from live.euroleague.net/api/{Header,BoxScore}.
Outputs: data/raw_data/euroleague_live/box_score/{season}_delta_{utc_timestamp}.csv
    (one new file per run with new data), data/raw_data/euroleague_live/_state.json
    (updated every run).
Final: false -- raw fetch output, not exposed under data/stage_99/.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HEADER_URL = "https://live.euroleague.net/api/Header"
BOXSCORE_URL = "https://live.euroleague.net/api/BoxScore"

# The live API 403s urllib's default User-Agent ("Python-urllib/x.y"); any other
# value, including this one, passes -- verified empirically against the real API.
USER_AGENT = "Mozilla/5.0 (compatible; euroleague-live-fetcher/1.0)"

# Polite-client delay between sequential requests.
REQUEST_DELAY_SECONDS = 0.3

DEFAULT_OUT_DIR = Path(__file__).resolve().parents[3] / "data" / "raw_data" / "euroleague_live"

CSV_FIELDNAMES = [
    "game_player_id",
    "game_id",
    "game",
    "round",
    "phase",
    "season_code",
    "player_id",
    "is_starter",
    "is_playing",
    "team_id",
    "dorsal",
    "player",
    "minutes",
    "points",
    "two_points_made",
    "two_points_attempted",
    "three_points_made",
    "three_points_attempted",
    "free_throws_made",
    "free_throws_attempted",
    "offensive_rebounds",
    "defensive_rebounds",
    "total_rebounds",
    "assists",
    "steals",
    "turnovers",
    "blocks_favour",
    "blocks_against",
    "fouls_committed",
    "fouls_received",
    "valuation",
    "plus_minus",
]


def fetch_json(url: str) -> dict[str, Any] | None:
    """Fetch a URL and parse it as JSON.

    Returns None when the response body is empty -- the live API's way of
    saying a gamecode doesn't have a game (yet).
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        body = response.read()
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def fetch_header(season: str, gamecode: int) -> dict[str, Any] | None:
    """Fetch the Header endpoint for one game."""
    return fetch_json(f"{HEADER_URL}?gamecode={gamecode}&seasoncode={season}")


def fetch_boxscore(season: str, gamecode: int) -> dict[str, Any] | None:
    """Fetch the BoxScore endpoint for one game."""
    return fetch_json(f"{BOXSCORE_URL}?gamecode={gamecode}&seasoncode={season}")


def is_game_complete(header: dict[str, Any] | None, boxscore: dict[str, Any] | None) -> bool:
    """Check whether a game's data is ready to ingest.

    Requires a non-empty Header with `Live: false` (not currently in progress)
    and a non-empty BoxScore with both teams' PlayersStats populated. The
    `Live` check guards against ingesting partial stats from an in-progress
    game, whose BoxScore may already have non-empty but non-final PlayersStats.
    """
    if header is None or boxscore is None:
        return False
    if header.get("Live"):
        return False
    stats = boxscore.get("Stats")
    if not isinstance(stats, list) or len(stats) != 2:
        return False
    return all(isinstance(team.get("PlayersStats"), list) and team["PlayersStats"] for team in stats)


def _assert_season_matches(header: dict[str, Any], season: str, gamecode: int) -> None:
    """Sanity-check the Header's own season code against the one we requested."""
    returned = header["pcom"].strip()
    if returned != season:
        raise ValueError(f"Header for gamecode {gamecode} reports season {returned!r}, expected {season!r}")


def _player_row(player: dict[str, Any], game_id: str, game: str, header: dict[str, Any], season: str) -> dict[str, Any]:
    """Map one BoxScore PlayersStats entry to a target-schema row."""
    player_id = player["Player_ID"].strip()
    return {
        "game_player_id": f"{game_id}_{player_id}",
        "game_id": game_id,
        "game": game,
        "round": header["Round"],
        "phase": header["Phase"],
        "season_code": season,
        "player_id": player_id,
        "is_starter": player["IsStarter"],
        "is_playing": player["IsPlaying"],
        "team_id": player["Team"],
        "dorsal": player["Dorsal"],
        "player": player["Player"],
        "minutes": player.get("Minutes") or "",
        "points": player["Points"],
        "two_points_made": player["FieldGoalsMade2"],
        "two_points_attempted": player["FieldGoalsAttempted2"],
        "three_points_made": player["FieldGoalsMade3"],
        "three_points_attempted": player["FieldGoalsAttempted3"],
        "free_throws_made": player["FreeThrowsMade"],
        "free_throws_attempted": player["FreeThrowsAttempted"],
        "offensive_rebounds": player["OffensiveRebounds"],
        "defensive_rebounds": player["DefensiveRebounds"],
        "total_rebounds": player["TotalRebounds"],
        "assists": player["Assistances"],
        "steals": player["Steals"],
        "turnovers": player["Turnovers"],
        "blocks_favour": player["BlocksFavour"],
        "blocks_against": player["BlocksAgainst"],
        "fouls_committed": player["FoulsCommited"],
        "fouls_received": player["FoulsReceived"],
        "valuation": player["Valuation"],
        "plus_minus": player["Plusminus"],
    }


def _team_total_row(
    team_stats: dict[str, Any], team_code: str, game_id: str, game: str, header: dict[str, Any], season: str
) -> dict[str, Any]:
    """Synthesize the team-total row (`dorsal="TOTAL"`) from a team's `totr` block."""
    totr = team_stats["totr"]
    return {
        "game_player_id": f"{game_id}_{team_code}",
        "game_id": game_id,
        "game": game,
        "round": header["Round"],
        "phase": header["Phase"],
        "season_code": season,
        "player_id": team_code,
        "is_starter": 0,
        "is_playing": 1,
        "team_id": team_code,
        "dorsal": "TOTAL",
        "player": team_stats["Team"],
        "minutes": totr.get("Minutes") or "",
        "points": totr["Points"],
        "two_points_made": totr["FieldGoalsMade2"],
        "two_points_attempted": totr["FieldGoalsAttempted2"],
        "three_points_made": totr["FieldGoalsMade3"],
        "three_points_attempted": totr["FieldGoalsAttempted3"],
        "free_throws_made": totr["FreeThrowsMade"],
        "free_throws_attempted": totr["FreeThrowsAttempted"],
        "offensive_rebounds": totr["OffensiveRebounds"],
        "defensive_rebounds": totr["DefensiveRebounds"],
        "total_rebounds": totr["TotalRebounds"],
        "assists": totr["Assistances"],
        "steals": totr["Steals"],
        "turnovers": totr["Turnovers"],
        "blocks_favour": totr["BlocksFavour"],
        "blocks_against": totr["BlocksAgainst"],
        "fouls_committed": totr["FoulsCommited"],
        "fouls_received": totr["FoulsReceived"],
        "valuation": totr["Valuation"],
        "plus_minus": 0,
    }


def build_game_rows(
    header: dict[str, Any], boxscore: dict[str, Any], season: str, gamecode: int
) -> list[dict[str, Any]]:
    """Build all target-schema rows (players + team totals) for one completed game.

    Row order matches the historical CSV: team A's players, team A's TOTAL,
    team B's players, team B's TOTAL. `Stats` is assumed to be in the same
    order as Header's TeamA/TeamB (a documented invariant of the live API).
    """
    game_id = f"{season}_{gamecode}"
    game = f"{header['CodeTeamA']}-{header['CodeTeamB']}"
    team_codes = (header["CodeTeamA"], header["CodeTeamB"])

    rows: list[dict[str, Any]] = []
    for team_stats, team_code in zip(boxscore["Stats"], team_codes, strict=True):
        rows.extend(_player_row(p, game_id, game, header, season) for p in team_stats["PlayersStats"])
        rows.append(_team_total_row(team_stats, team_code, game_id, game, header, season))
    return rows


def load_state(state_path: Path) -> dict[str, dict[str, Any]]:
    """Load the ingestion state file, or an empty dict if it doesn't exist yet."""
    if not state_path.exists():
        return {}
    return json.loads(state_path.read_text(encoding="utf-8"))


def save_state(state: dict[str, dict[str, Any]], state_path: Path) -> None:
    """Write the ingestion state file, creating parent dirs as needed."""
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def write_delta_csv(rows: list[dict[str, Any]], out_path: Path) -> None:
    """Write newly ingested rows to a delta CSV. Caller ensures rows is non-empty."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def run(season: str, out_dir: Path) -> None:
    """Poll new gamecodes for `season`, write a delta CSV if any completed, update state."""
    state_path = out_dir / "_state.json"
    state = load_state(state_path)
    season_state = state.get(season, {})
    last_ingested: int | None = season_state.get("last_ingested_gamecode")
    start_gamecode = (last_ingested or 0) + 1

    rows: list[dict[str, Any]] = []
    gamecode = start_gamecode
    while True:
        header = fetch_header(season, gamecode)
        time.sleep(REQUEST_DELAY_SECONDS)
        if header is None:
            break
        boxscore = fetch_boxscore(season, gamecode)
        time.sleep(REQUEST_DELAY_SECONDS)
        if not is_game_complete(header, boxscore):
            break
        _assert_season_matches(header, season, gamecode)
        assert boxscore is not None  # noqa: S101 -- is_game_complete already checked this
        rows.extend(build_game_rows(header, boxscore, season, gamecode))
        last_ingested = gamecode
        gamecode += 1

    season_state["last_checked_at"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if rows:
        season_state["last_ingested_gamecode"] = last_ingested
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        delta_path = out_dir / "box_score" / f"{season}_delta_{timestamp}.csv"
        write_delta_csv(rows, delta_path)
        num_games = gamecode - start_gamecode
        print(f"Wrote {len(rows)} rows from {num_games} new game(s) to {delta_path}")
    else:
        print(f"No new completed games for {season} (checked from gamecode {start_gamecode})")

    state[season] = season_state
    save_state(state, state_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", default="E2026", help="Season code, e.g. E2026 (default: %(default)s)")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Raw output directory")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run(args.season, args.out_dir)


if __name__ == "__main__":
    main()
