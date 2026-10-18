"""Audit the player name map against the box score: report both gaps, optionally pause on one.

Library module (no I/O header, never run directly): `build_player_name_map.py` calls `audit` right after it has built
the map, so the audit always sees a fresh map.

The fantasy stats are the core dataset. Each player falls into one of three cases:

1. In both datasets: the map resolves the box-score spelling to the stats player. Fine.
2. In the stats, not in the box score: a player who has not played yet (injury, new signing) or whom the agent
   closed as `no_match` / `rejected`. Fine: listed for information, the run goes on.
3. In the box score, not in the stats: a player who played (minutes on the clock) but that no stats player resolves
   to. Listed with where he was found (club, games, rounds) and the likely stats entry, a same-club stats player
   without a box-score match who shares his surname.

Two modes (`names_mode`, `[transformation]` in `config/config.toml`), both printing every case-2 and case-3 player
and how to fix them:

- `continue` (default): exit code 0 whatever is found, so the flow goes on. The stats are the left side of every later
  join, so a case-3 player simply drops out of the joined data (the workbook counts the dropped rows).
- `pause`: exit code 1 when there is a case-3 player, so the flow stops until it is fixed or the mode is `continue`.
  Case-2 players never pause: most of them have simply not played.

A case-3 player is fixed by a decision for the likely stats entry (the `build-name-maps` skill), giving it the
box-score spelling reordered as its full name; when no stats entry shares his surname, the fantasy data lacks the
player. Box-score rows without minutes (`DNP`) are ignored.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from eupy.entity.matching import UnknownNameError, normalize_name
from eupy.entity.player_names import PlayerNames
from eupy.entity.team_names import TeamNames

RESOLVED_STATUSES = frozenset({"exact", "confirmed"})
NOT_PLAYED = frozenset({"", "DNP", "0:00"})
MODES = ("continue", "pause")


@dataclass(frozen=True)
class BoxOnly:
    """A box-score player who played but resolves to no stats player."""

    team: str  # box-score team_id
    club: str  # normalized club ("" when the team code is not in the schedule)
    player: str
    games: int
    rounds: tuple[int, ...]


def find_stats_only(stats_players: list[dict[str, str]], entries: list[dict[str, object]]) -> list[dict[str, str]]:
    """Case 2: the stats players whose map entry is not `exact` / `confirmed` (`team`, `name`, `club`, `status`, `notes`)."""
    by_key = {(str(e["stats_team"]), str(e["stats_name"])): e for e in entries}
    stats_only = []
    for player in stats_players:
        entry = by_key[(player["team"], player["name"])]
        if entry["match_status"] not in RESOLVED_STATUSES:
            stats_only.append({
                "team": player["team"],
                "name": player["name"],
                "club": str(entry.get("team", "")),
                "status": str(entry["match_status"]),
                "notes": str(entry.get("notes", "")),
            })
    return stats_only


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
        return f"{where}; team code {item.team!r} is not in the schedule"
    likely = [s for s in unlinked if s["club"] == item.club and _looks_alike(s["name"], item.player)]
    if not likely:
        return f"{where}; no stats player of that club shares his surname, so the fantasy data lacks him"
    names = "; ".join(f"{s['team']} {s['name']} ({s['status']})" for s in likely)
    return (
        f"{where}; likely the stats entry {names}. Decide it as {box_name_as_full_name(item.player)!r} "
        "(the box-score spelling reordered) so this spelling resolves"
    )


def audit(
    stats_players: list[dict[str, str]],
    entries: list[dict[str, object]],
    boxscore: Path,
    teams: TeamNames,
    mode: str,
) -> int:
    """Print the three cases for a freshly built map; return the exit code (1 only in pause mode with case 3)."""
    stats_only = find_stats_only(stats_players, entries)
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
    box_only = find_box_only(boxscore, PlayerNames(entries, teams), teams)

    print(f"1. in both datasets: {len(stats_players) - len(stats_only)} of {len(stats_players)} stats players")
    print(f"2. in the stats, no box-score match (info; most have not played yet, no action needed): {len(stats_only)}")
    by_team: dict[str, list[str]] = defaultdict(list)
    for s in stats_only:
        by_team[s["team"]].append(s["name"])
    for team, names in sorted(by_team.items()):
        print(f"     {team}: {', '.join(names)}")
    if not box_only:
        print("3. in the box score (played), not in the stats: 0")
        return 0
    pausing = mode == "pause"
    print(
        f"3. {'PAUSED' if pausing else 'WARNING'}: {len(box_only)} box-score player(s) who played are not in the "
        f"stats{'' if pausing else ' (left out of the joins; the flow continues)'}:"
    )
    for b in box_only:
        print(f"     {b.player}: {describe_box_only(b, unlinked)}")
    print(
        "   Fix: run the /build-name-maps skill to decide the likely stats entry above, then "
        "`uv run dvc repro -s build_player_name_map` (it audits again)."
    )
    if pausing:
        print('   Pause mode: the flow stops here; set [transformation] names_mode = "continue" to go on without them.')
    return 1 if pausing else 0
