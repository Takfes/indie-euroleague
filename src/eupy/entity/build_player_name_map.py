#!/usr/bin/env python3
"""Build the player name map: one entry per fantasy-stats player, resolved to a full, normalized name.

The stats data is the trusted set: one unit of work is a distinct `(team, name)` pair of
`fantasy_stats/players` (`C. Jones`, club code `PAO`); players not in the stats data never get an entry. The
box score is only the source of full names. Stages, in order (see specs/spec-name-maps.md):

1. Exact, club-scoped -- pool = box-score players who played for the player's club in the most recent seasons
   (club compared after `TeamNames` normalization on both sides), spelled in the stats' abbreviated form. Exactly
   one normalized-string match is `exact`, the only automatic accept.
2. Fuzzy, unscoped -- everything else is scored against every box-score spelling in history. A hit, including an
   exact one outside the club, is `needs_review`: it may be an older namesake. No hit is `no_candidate`.
3. Verdicts -- agent decisions recorded in `data/curated/player_name_verdicts/` (`confirmed` with the full name,
   `rejected`, `no_match`) are applied on top, a later batch winning on the same key. The agent covers what the
   first two stages leave open: players who just moved club (no games for it yet) and rookies without box-score
   history.

Row key: `"<team>|<name>"`. Normalized form: the box-score spelling reordered to `First Last`, title-cased.

Two files are written from the same entries: `maps/player_name_map.json`, which `PlayerNames`
(`player_names.py`) loads, and the CSV `data/stage_02/player_name_map.csv` for inspection and joins. Entries
still open after the verdicts (`needs_review`, `no_candidate`) are written with their candidates in `notes` and
listed in the summary; resolving them is the `build-name-maps` skill's job.

Usage:
    python -m eupy.entity.build_player_name_map [--stats PATH] [--boxscore PATH] [--verdicts-dir DIR]
        [--recent-seasons N] [--out PATH] [--csv-out PATH]

Inputs:
  - fantasy_stats/players: data/raw_data/fantasy_stats/players.csv
  - euroleague_box_score_merged: data/stage_01/euroleague_box_score_merged.csv
  - team_name_map: data/stage_01/team_name_map.csv
  - player_name_verdicts: data/curated/player_name_verdicts/
Sources: none
Outputs:
  - player_name_map: data/stage_02/player_name_map.csv
Final: false
Impure: false
Notes: Also writes the JSON copy src/eupy/entity/maps/player_name_map.json (committed with the package, read by
  PlayerNames); the CSV is the registered dataset. Reads the team map through TeamNames (the JSON copy of
  team_name_map). Verdict batches are applied in file-name order; a later batch wins on the same team|name key.
  A missing verdicts directory means no verdicts yet. Rerunning on the same inputs rewrites identical bytes. The
  previous map is never read.
  CSV columns: stats_team, stats_name, team (normalized club), name (normalized full name), variants (joined
  with " | "), match_status, matched_by, match_score, notes.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eupy.entity.matching import (
    OPEN_STATUSES,
    UnknownNameError,
    build_normalized_index,
    find_candidates,
    format_candidates_note,
    normalize_name,
)
from eupy.entity.team_names import TeamNames
from eupy.entity.verdict_batches import PLAYER_VERDICT_KEY_FIELDS, apply_verdicts, load_batches, merge_verdicts
from eupy.quality import validate

REPO_ROOT = Path(__file__).resolve().parents[3]
STATS_PATH = REPO_ROOT / "data" / "raw_data" / "fantasy_stats" / "players.csv"
BOXSCORE_PATH = REPO_ROOT / "data" / "stage_01" / "euroleague_box_score_merged.csv"
VERDICTS_DIR = REPO_ROOT / "data" / "curated" / "player_name_verdicts"
OUT_PATH = Path(__file__).resolve().parent / "maps" / "player_name_map.json"
CSV_PATH = REPO_ROOT / "data" / "stage_02" / "player_name_map.csv"
CSV_FIELDS = [
    "stats_team",
    "stats_name",
    "team",
    "name",
    "variants",
    "match_status",
    "matched_by",
    "match_score",
    "notes",
]

RECENT_SEASONS = 2

# (stats name, stats team code) per distinct stats player
StatsPlayer = dict[str, str]
# {spelling: {owner, ...}} -- the shape `matching.find_candidates` works on
Pool = dict[str, set[str]]


@dataclass
class BoxscorePools:
    """Box-score spellings, reordered to `First LAST`, with the player ids behind each and the raw spellings.

    `by_club` is restricted to the recent seasons and keyed by normalized club name. `raw` maps the normalized
    form of a reordered spelling (accents and punctuation dropped) to the spellings as written in the box score
    (`JONES, CARLIK`), so accent variants of one name share their raw spellings.
    """

    all: Pool
    by_club: dict[str, Pool]
    raw: dict[str, set[str]]


def reorder_boxscore_name(name: str) -> str:
    """`"LAST, First"` -> `"First LAST"`; names without a comma pass through."""
    if "," not in name:
        return name
    last, first = name.split(",", 1)
    return f"{first.strip()} {last.strip()}"


def abbreviate_name(name: str) -> str:
    """`"First Last"` -> `"F. Last"`; splits on whitespace so `"Marc-Owen Fodzo Dada"` -> `"M. Fodzo Dada"`."""
    first, _, rest = name.strip().partition(" ")
    return f"{first[0]}. {rest.strip()}" if rest else name.strip()


def abbreviated_pool(full_names: list[str]) -> Pool:
    """Matching pool `{abbreviated spelling: {full names}}`."""
    pool: Pool = defaultdict(set)
    for full in full_names:
        pool[abbreviate_name(full)].add(full)
    return dict(pool)


def load_stats_players(path: Path) -> list[StatsPlayer]:
    """One `{name, team}` record per distinct pair in the stats players file, ordered by team then name."""
    with path.open(newline="", encoding="utf-8") as f:
        pairs = {(r["team"], r["name"]) for r in csv.DictReader(f)}
    return [{"team": team, "name": name} for team, name in sorted(pairs)]


def load_boxscore_pools(path: Path, teams: TeamNames, recent_seasons: int = RECENT_SEASONS) -> BoxscorePools:
    """Read the merged box score into matching pools; TOTAL rows are skipped.

    A club whose `team_id` is not in the team map (a club absent from the current schedule) is left out of
    `by_club`; its players still appear in `all`.
    """
    with path.open(newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["dorsal"] != "TOTAL"]
    recent = set(sorted({r["season_code"] for r in rows})[-recent_seasons:])
    pools = BoxscorePools(all=defaultdict(set), by_club=defaultdict(lambda: defaultdict(set)), raw=defaultdict(set))
    for row in rows:
        spelling = reorder_boxscore_name(row["player"])
        pools.all[spelling].add(row["player_id"])
        pools.raw[normalize_name(spelling)].add(row["player"])
        if row["season_code"] in recent:
            try:
                club = teams.normalize(row["team_id"])
            except UnknownNameError:
                continue
            pools.by_club[club][spelling].add(row["player_id"])
    return BoxscorePools(
        all=dict(pools.all), by_club={c: dict(p) for c, p in pools.by_club.items()}, raw=dict(pools.raw)
    )


def _match(name: str, pool: Pool) -> tuple[str, list[tuple[str, float]], str]:
    """Exact -> fuzzy engine over a box-score pool, with candidates expanded from abbreviations to full names."""
    abbreviated = abbreviated_pool(list(pool))
    status, candidates, matched_by = find_candidates(
        normalize_name(name),
        abbreviated,
        build_normalized_index(abbreviated),
        {s: normalize_name(s) for s in abbreviated},
    )
    return status, [(full, score) for abbrev, score in candidates for full in sorted(abbreviated[abbrev])], matched_by


def build_row(player: StatsPlayer, club: str, pools: BoxscorePools) -> dict[str, str]:
    """Build one fresh row (no verdict state) for a stats player whose club normalizes to `club` (`""` if unknown)."""
    status, candidates, matched_by = _match(player["name"], pools.by_club.get(club, {}))
    if status == "no_candidate":
        status, candidates, matched_by = _match(player["name"], pools.all)
        if status == "exact":
            status = "needs_review"  # outside the club: never auto-accept
    candidates = [(full.title(), score) for full, score in candidates]

    row = {
        "team": player["team"],
        "name": player["name"],
        "club": club,
        "resolved_name": "",
        "match_status": status,
        "match_score": "",
        "matched_by": matched_by,
        "notes": "" if club else f"team code {player['team']!r} is not in the team map",
    }
    if candidates:
        row |= {"resolved_name": candidates[0][0], "match_score": f"{candidates[0][1]:.1f}"}
        if status == "needs_review":
            note = f"candidates: {format_candidates_note(candidates)}"
            row["notes"] = f"{row['notes']}; {note}" if row["notes"] else note
    return row


def build_rows(
    players: list[StatsPlayer], pools: BoxscorePools, teams: TeamNames, verdicts: dict[str, dict[str, Any]]
) -> list[dict[str, str]]:
    """Rows for every stats player, then `verdicts` (keyed `"<team>|<name>"`) applied on top.

    Verdicts for a key that is not among `players` are skipped.
    """
    rows = []
    for player in players:
        try:
            club = teams.normalize(player["team"])
        except UnknownNameError:
            club = ""
        rows.append(build_row(player, club, pools))
    apply_verdicts({f"{r['team']}|{r['name']}": r for r in rows}, verdicts, "resolved_name")
    return rows


def build_entries(rows: list[dict[str, str]], pools: BoxscorePools) -> list[dict[str, object]]:
    """Turn rows into player map entries (see `PlayerNames`), sorted by team then stats name.

    `exact` / `confirmed` rows carry the full name and every spelling known for it; `rejected` / `no_match`
    keep the stats spelling as their name; open rows keep their candidates in `notes` and are not looked up.
    """
    entries = []
    for row in sorted(rows, key=lambda r: (r["team"], r["name"])):
        resolved = row["match_status"] in {"exact", "confirmed"}
        name = row["resolved_name"] if resolved else row["name"]
        variants = {row["name"], name}
        if resolved:
            variants |= pools.raw.get(normalize_name(name), set())
        entries.append({
            "name": name,
            "variants": sorted(variants),
            "team": row["club"],
            "stats_name": row["name"],
            "stats_team": row["team"],
            "match_status": row["match_status"],
            "matched_by": row["matched_by"],
            "match_score": row["match_score"],
            "notes": row["notes"],
        })
    return entries


def write_map(entries: list[dict[str, object]], path: Path) -> None:
    """Write the JSON map deterministically (indent=2, sorted keys, trailing newline)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"players": entries}, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def write_csv(entries: list[dict[str, object]], path: Path) -> None:
    """Write the entries as a flat CSV (`variants` joined with ` | `), same order as the JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        for entry in entries:
            writer.writerow({k: " | ".join(entry[k]) if k == "variants" else entry[k] for k in CSV_FIELDS})  # type: ignore[arg-type]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stats", type=Path, default=STATS_PATH, help="fantasy_stats/players.csv path")
    parser.add_argument("--boxscore", type=Path, default=BOXSCORE_PATH, help="merged box-score CSV path")
    parser.add_argument("--verdicts-dir", type=Path, default=VERDICTS_DIR, help="Verdict batch directory")
    parser.add_argument("--recent-seasons", type=int, default=RECENT_SEASONS, help="Seasons that scope the club pools")
    parser.add_argument("--out", type=Path, default=OUT_PATH, help="Player map JSON output path")
    parser.add_argument("--csv-out", type=Path, default=CSV_PATH, help="Player map CSV output path")
    args = parser.parse_args(argv)

    teams = TeamNames.load()
    players = load_stats_players(args.stats)
    if not players:
        raise ValueError(f"No players in {args.stats}; refetch with `uv run dvc repro fetch_euroleague_fantasy_stats`")
    pools = load_boxscore_pools(args.boxscore, teams, args.recent_seasons)
    batches = load_batches(args.verdicts_dir) if args.verdicts_dir.is_dir() else []
    verdicts = merge_verdicts(batches, PLAYER_VERDICT_KEY_FIELDS, "resolved_name")

    entries = build_entries(build_rows(players, pools, teams, verdicts), pools)
    validate(entries, ["stats_team", "stats_name"], "player_name_map")
    write_map(entries, args.out)
    write_csv(entries, args.csv_out)
    summary = ", ".join(f"{s}={n}" for s, n in sorted(Counter(str(e["match_status"]) for e in entries).items()))
    print(f"Wrote {len(entries)} players to {args.out} and {args.csv_out}: {summary}")
    open_count = sum(1 for e in entries if e["match_status"] in OPEN_STATUSES)
    if open_count:
        print(f"{open_count} open entries need a verdict (see `notes` in the map).")


if __name__ == "__main__":
    main()
