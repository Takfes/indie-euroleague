#!/usr/bin/env python3
"""Build the player name map: one entry per fantasy-stats player, resolved to a full, normalized name.

Runs the matching engine (`player_matching.py`): club-scoped exact match against the recent box scores, unscoped
fuzzy candidates, then the agent verdicts recorded in `data/curated/player_name_verdicts/`. The result is written
to `maps/player_name_map.json`, which `PlayerNames` (`player_names.py`) turns into a lookup. Players that are not
in the stats data never get an entry.

Entries still open after the verdicts (`needs_review`, `no_candidate`) are written with their candidates in
`notes` and listed in the summary; resolving them is the `build-name-maps` skill's job.

Usage:
    python -m eupy.entity.build_player_name_map [--stats PATH] [--boxscore PATH] [--verdicts-dir DIR]
        [--recent-seasons N] [--out PATH]

Inputs:
  - fantasy_stats/players: data/raw_data/fantasy_stats/players.csv
  - euroleague_box_score_merged: data/stage_01/euroleague_box_score_merged.csv
  - team_name_map: src/eupy/entity/maps/team_name_map.json
  - player_name_verdicts: data/curated/player_name_verdicts/
Sources: none
Outputs:
  - player_name_map: src/eupy/entity/maps/player_name_map.json
Final: false
Impure: false
Notes: Verdict batches are applied in file-name order; a later batch wins on the same `team|name` key. A missing
  verdicts directory means no verdicts yet. The map is committed with the package; rerunning on the same inputs
  rewrites identical bytes. The previous map is never read.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from eupy.entity.player_matching import (
    RECENT_SEASONS,
    VERDICT_KEY_FIELDS,
    build_entries,
    build_rows,
    load_boxscore_pools,
    load_stats_players,
)
from eupy.entity.team_names import TeamNames
from eupy.entity.verdict_batches import load_batches, merge_verdicts
from eupy.quality import validate

REPO_ROOT = Path(__file__).resolve().parents[3]
STATS_PATH = REPO_ROOT / "data" / "raw_data" / "fantasy_stats" / "players.csv"
BOXSCORE_PATH = REPO_ROOT / "data" / "stage_01" / "euroleague_box_score_merged.csv"
VERDICTS_DIR = REPO_ROOT / "data" / "curated" / "player_name_verdicts"
OUT_PATH = Path(__file__).resolve().parent / "maps" / "player_name_map.json"


def write_map(entries: list[dict[str, object]], path: Path) -> None:
    """Write the map deterministically (indent=2, sorted keys, trailing newline)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"players": entries}, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stats", type=Path, default=STATS_PATH, help="fantasy_stats/players.csv path")
    parser.add_argument("--boxscore", type=Path, default=BOXSCORE_PATH, help="merged box-score CSV path")
    parser.add_argument("--verdicts-dir", type=Path, default=VERDICTS_DIR, help="Verdict batch directory")
    parser.add_argument("--recent-seasons", type=int, default=RECENT_SEASONS, help="Seasons that scope the club pools")
    parser.add_argument("--out", type=Path, default=OUT_PATH, help="Player map JSON output path")
    args = parser.parse_args(argv)

    teams = TeamNames.load()
    players = load_stats_players(args.stats)
    if not players:
        raise ValueError(f"No players in {args.stats}; refetch with `uv run dvc repro fetch_euroleague_fantasy_stats`")
    pools = load_boxscore_pools(args.boxscore, teams, args.recent_seasons)
    batches = load_batches(args.verdicts_dir) if args.verdicts_dir.is_dir() else []
    verdicts = merge_verdicts(batches, VERDICT_KEY_FIELDS, "resolved_name")

    entries = build_entries(build_rows(players, pools, teams, verdicts), pools)
    validate(entries, ["stats_team", "stats_name"], "player_name_map")
    write_map(entries, args.out)
    summary = ", ".join(f"{s}={n}" for s, n in sorted(Counter(str(e["match_status"]) for e in entries).items()))
    print(f"Wrote {len(entries)} players to {args.out}: {summary}")
    open_count = sum(1 for e in entries if e["match_status"] in {"needs_review", "no_candidate"})
    if open_count:
        print(f"{open_count} open entries need a verdict (see `notes` in the map).")


if __name__ == "__main__":
    main()
