#!/usr/bin/env python3
"""Compare the players of the fantasy stats and the box score; report both gaps, optionally pause on one.

The fantasy stats are the core dataset (see `build_player_name_map.py`). Each player falls into one of three cases:

1. In both datasets: the map resolves the box-score spelling to the stats player. Fine.
2. In the stats, not in the box score: a player who has not played yet (injury, new signing) or whom the agent
   closed as `no_match` / `rejected`. Fine: highlighted for information, the run goes on.
3. In the box score, not in the stats: a player who played (minutes on the clock) but that no stats player
   resolves to. Highlighted: each player is listed with where he was found (club, games, rounds) and where not,
   plus the same-club stats players that have no box-score match, the likely counterparts
   (a spelling the matching could not pair shows up as one case-2 player and one case-3 player of the same club,
   sharing a surname).

Two modes (`--mode`, default from `[transformation] names_mode`), both printing every case-2 and case-3 player
and how to fix them:

- `continue` (default): exit 0 whatever is found, so the flow goes on. The stats are the left side of every later
  join, so a case-3 player simply drops out of the joined data (the workbook counts the dropped rows).
- `pause`: exit 1 when there is a case-3 player, so the flow stops until it is fixed or the mode is `continue`.
  Case-2 players never pause: most of them have simply not played.

A case-3 player is fixed by deciding the counterpart stats entry with the `build-name-maps` skill, giving it the
box-score spelling reordered as its full name (a `confirmed` decision under another spelling leaves the box-score
spelling unlinked); when no stats entry shares his surname, the fantasy data lacks the player. Box-score rows without
minutes (`DNP`) are ignored. The check reads the map as the builder left it: run `build_player_name_map` first
(the DVC flows do); stats players still missing from the map are reported, and case-3 results are unreliable until
the map is rebuilt.

The cases are also written to a report CSV (`case`, `team`, `name`, `detail`; header only when empty), so a DVC
flow can depend on the check: downstream steps (the workbook) list the report as an input.

Usage:
    python -m eupy.entity.check_player_names [--mode continue|pause] [--stats PATH] [--boxscore PATH] [--map PATH] [--out PATH]

Inputs:
  - fantasy_stats/players: data/raw_data/fantasy_stats/players.csv
  - euroleague_box_score_merged: data/stage_01/euroleague_box_score_merged.csv
  - player_name_map: data/stage_02/player_name_map.csv
  - game_schedule/schedule: data/raw_data/game_schedule/
Sources: none
Outputs:
  - player_name_check: data/stage_03/player_name_check.csv
Final: false
Impure: false
Notes: Exit 1 only in pause mode with a case-3 player. Report cases: stats_only (case 2), box_only (case 3), map_stale (stats players not
  in the map yet).
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from eupy.config import get_config
from eupy.entity.matching import UnknownNameError, load_stats_players, normalize_name
from eupy.entity.player_names import MAP_PATH, PlayerNames, load_entries
from eupy.entity.team_names import TeamNames

REPO_ROOT = Path(__file__).resolve().parents[3]
STATS_PATH = REPO_ROOT / "data" / "raw_data" / "fantasy_stats" / "players.csv"
BOXSCORE_PATH = REPO_ROOT / "data" / "stage_01" / "euroleague_box_score_merged.csv"
OUT_PATH = REPO_ROOT / "data" / "stage_03" / "player_name_check.csv"

RESOLVED_STATUSES = frozenset({"exact", "confirmed"})
NOT_PLAYED = frozenset({"", "DNP", "0:00"})
MODES = ("continue", "pause")


@dataclass(frozen=True)
class BoxOnly:
    """A box-score player who played but resolves to no stats player."""

    team: str  # box-score team_id
    club: str  # normalized club ("" when the team code is not in the team map)
    player: str
    games: int
    rounds: tuple[int, ...]


def find_stats_only(
    stats_players: list[dict[str, str]], entries: list[dict[str, object]]
) -> tuple[list[dict[str, str]], list[tuple[str, str]]]:
    """Case 2 and stale-map players of the stats.

    Returns `(stats_only, missing)`: `stats_only` are the stats players whose map entry is not `exact` / `confirmed`
    (`team`, `name`, `club`, `status`, `notes`); `missing` the `(team, name)` pairs with no entry at all.
    """
    by_key = {(str(e["stats_team"]), str(e["stats_name"])): e for e in entries}
    stats_only, missing = [], []
    for player in stats_players:
        entry = by_key.get((player["team"], player["name"]))
        if entry is None:
            missing.append((player["team"], player["name"]))
        elif entry["match_status"] not in RESOLVED_STATUSES:
            stats_only.append({
                "team": player["team"],
                "name": player["name"],
                "club": str(entry.get("team", "")),
                "status": str(entry["match_status"]),
                "notes": str(entry.get("notes", "")),
            })
    return stats_only, missing


def find_box_only(path: Path, players: PlayerNames, teams: TeamNames) -> list[BoxOnly]:
    """Case 3: box-score players with minutes that the map does not resolve, with their games and rounds."""
    games: dict[tuple[str, str], set[tuple[str, int]]] = defaultdict(set)
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["dorsal"] != "TOTAL" and row["minutes"] not in NOT_PLAYED:
                games[(row["team_id"], row["player"])].add((row["game_id"], int(row["round"])))
    found = []
    for (team, player), played in sorted(games.items()):
        try:
            players.normalize(player, team=team)
        except UnknownNameError:
            try:
                club = teams.normalize(team)
            except UnknownNameError:
                club = ""
            found.append(BoxOnly(team, club, player, len(played), tuple(sorted({r for _, r in played}))))
        except LookupError:  # ambiguous even with the club: the name is in the map, so it is resolved
            continue
    return found


def _rounds(rounds: tuple[int, ...]) -> str:
    return f"{rounds[0]}-{rounds[-1]}" if len(rounds) > 1 else str(rounds[0])


def _looks_alike(stats_name: str, box_name: str) -> bool:
    """True when a stats name (`J. Begarin`) shares a surname token with a box-score name (`BEGARIN, JOAN`)."""
    surname = set(normalize_name(stats_name.partition(" ")[2]).split())
    return bool(surname & set(normalize_name(box_name).split()))


def box_name_as_full_name(box_name: str) -> str:
    """`"WASHINGTON JR, TYTY"` -> `"Tyty Washington Jr"`, the full name that makes the box-score spelling a variant."""
    last, _, first = box_name.partition(",")
    return f"{first.strip()} {last.strip()}".strip().title()


def describe_box_only(item: BoxOnly, unlinked: list[dict[str, str]]) -> str:
    """Where the player was found and where not, naming the same-club stats entries (not matched to any box-score
    spelling) that share his surname."""
    where = f"box score: {item.team}, {item.games} game(s), round(s) {_rounds(item.rounds)}; stats: not found"
    if not item.club:
        return f"{where}; team code {item.team!r} is not in the team map"
    likely = [s for s in unlinked if s["club"] == item.club and _looks_alike(s["name"], item.player)]
    if not likely:
        return f"{where}; no stats player of that club shares his surname, so the fantasy data lacks him"
    names = "; ".join(f"{s['team']} {s['name']} ({s['status']})" for s in likely)
    return (
        f"{where}; likely the stats entry {names}. Decide it as {box_name_as_full_name(item.player)!r} "
        "(the box-score spelling reordered) so this spelling resolves"
    )


def write_report(rows: list[tuple[str, str, str, str]], path: Path) -> None:
    """Write `(case, team, name, detail)` rows as CSV (header only when empty)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["case", "team", "name", "detail"])
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    """Print the three cases; return the exit code (1 only in pause mode with a played box-score player unmatched)."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument(
        "--mode",
        choices=MODES,
        default=get_config().transformation.names_mode,
        help="continue: report and exit 0; pause: exit 1 on box-score players missing from the stats "
        "(default: config transformation.names_mode)",
    )
    parser.add_argument("--stats", type=Path, default=STATS_PATH, help="fantasy_stats/players.csv path")
    parser.add_argument("--boxscore", type=Path, default=BOXSCORE_PATH, help="merged box-score CSV path")
    parser.add_argument("--map", type=Path, default=MAP_PATH, help="player name map CSV path")
    parser.add_argument("--out", type=Path, default=OUT_PATH, help="report CSV output path")
    args = parser.parse_args(argv)

    stats_players = load_stats_players(args.stats)
    if not stats_players:
        raise ValueError(f"No players in {args.stats}; refetch with `uv run dvc repro fetch_euroleague_fantasy_stats`")
    if not args.map.exists():
        raise FileNotFoundError(f"{args.map} not found; build it with `python -m eupy.entity.build_player_name_map`")
    entries = load_entries(args.map)
    stats_only, missing = find_stats_only(stats_players, entries)
    unlinked = [  # stats entries no box-score spelling resolved to automatically: candidates for the hint
        {
            "team": str(e["stats_team"]),
            "name": str(e["stats_name"]),
            "club": str(e["team"]),
            "status": str(e["match_status"]),
        }
        for e in entries
        if e["match_status"] != "exact"
    ]
    teams = TeamNames.load()
    box_only = find_box_only(args.boxscore, PlayerNames.load(args.map, teams), teams)

    report = [("map_stale", t, n, "stats player not in the map yet") for t, n in missing]
    report += [("stats_only", s["team"], s["name"], f"{s['status']}; {s['notes']}".rstrip("; ")) for s in stats_only]
    report += [("box_only", b.team, b.player, describe_box_only(b, unlinked)) for b in box_only]
    write_report(report, args.out)

    matched = len(stats_players) - len(stats_only) - len(missing)
    print(f"1. in both datasets: {matched} of {len(stats_players)} stats players")
    print(f"2. in the stats, no box-score match (info; most have not played yet, no action needed): {len(stats_only)}")
    by_team: dict[str, list[str]] = defaultdict(list)
    for s in stats_only:
        by_team[s["team"]].append(s["name"])
    for team, names in sorted(by_team.items()):
        print(f"     {team}: {', '.join(names)}")
    if missing:
        print(f"   map is stale: {len(missing)} stats players are not in it; run build_player_name_map first")
    if not box_only:
        print("3. in the box score (played), not in the stats: 0")
        return 0
    pausing = args.mode == "pause"
    print(
        f"3. {'PAUSED' if pausing else 'WARNING'}: {len(box_only)} box-score player(s) who played are not in the "
        f"stats{'' if pausing else ' (left out of the joins; the flow continues)'}:"
    )
    for b in box_only:
        print(f"     {b.player}: {describe_box_only(b, unlinked)}")
    print(
        "   Fix: run the /build-name-maps skill to decide the likely stats entry above, then "
        "`uv run dvc repro -s build_player_name_map`; `uv run python -m eupy.entity.check_player_names` shows this list again."
    )
    if pausing:
        print('   Pause mode: the flow stops here; set [transformation] names_mode = "continue" to go on without them.')
    return 1 if pausing else 0


if __name__ == "__main__":
    sys.exit(main())
