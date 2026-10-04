#!/usr/bin/env python3
"""Build the team name map from the schedule: one entry per team, listing every spelling the schedule uses.

The schedule carries, for each home team, its name (`hometeam`) and two code schemes (`homecode`,
`hometv`). The distinct triples become entries `{"name": <hometeam>, "variants": [name, code, tv]}`, which
`TeamNames` (`team_names.py`) turns into a lookup. The normalized name is the schedule's `hometeam` as
written. Every team plays at home, so the home columns cover all of them.

Fails if the schedule is empty, if one spelling points at two teams, or if one team appears with two
different code pairs; fix the source or extend this script rather than guessing.

Two files are written from the same entries: `maps/team_name_map.json`, which `TeamNames` (`team_names.py`)
loads, and the CSV `data/stage_01/team_name_map.csv` for inspection and joins.

Usage:
    python -m eupy.entity.build_team_name_map [--schedule-dir DIR] [--out PATH] [--csv-out PATH]

Inputs:
  - game_schedule/schedule: data/raw_data/game_schedule/
Sources: none
Outputs:
  - team_name_map: data/stage_01/team_name_map.csv
Final: false
Impure: false
Notes: Also writes the JSON copy src/eupy/entity/maps/team_name_map.json (committed with the package, read by
  TeamNames); the CSV is the registered dataset. Reads every schedule_*.csv in the directory; rerunning on the
  same schedule rewrites identical bytes. CSV columns: name (normalized team name), variants (joined with
  " | ").
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from eupy.entity.matching import normalize_name
from eupy.quality import validate

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEDULE_DIR = REPO_ROOT / "data" / "raw_data" / "game_schedule"
OUT_PATH = Path(__file__).resolve().parent / "maps" / "team_name_map.json"
CSV_PATH = REPO_ROOT / "data" / "stage_01" / "team_name_map.csv"


def load_schedule_rows(directory: Path) -> list[dict[str, str]]:
    """All rows of every `schedule_*.csv` in `directory`; raises if there is none."""
    paths = sorted(directory.glob("schedule_*.csv"))
    if not paths:
        raise FileNotFoundError(f"No schedule_*.csv in {directory}; run `uv run dvc repro fetch_game_schedule` first")
    rows: list[dict[str, str]] = []
    for path in paths:
        with path.open(newline="", encoding="utf-8") as f:
            rows.extend(csv.DictReader(f))
    return rows


def build_team_entries(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    """One entry per `hometeam`, with its name, code and TV code as variants; sorted by name.

    Raises:
        ValueError: On an empty schedule, a team with two code pairs, or a spelling shared by two teams.
    """
    pairs: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for row in rows:
        pairs[row["hometeam"].strip()].add((row["homecode"].strip(), row["hometv"].strip()))
    if not pairs:
        raise ValueError("The schedule has no rows; refetch it with `uv run dvc repro fetch_game_schedule`")
    entries = []
    for name, codes in sorted(pairs.items()):
        if len(codes) > 1:
            raise ValueError(f"Team {name!r} appears with several code pairs {sorted(codes)}; resolve in the schedule")
        code, tv = next(iter(codes))
        entries.append({"name": name, "variants": sorted({name, code, tv})})
    validate(entries, "name", "team_name_map")  # duplicate names
    owners: dict[str, str] = {}
    for entry in entries:
        for variant in entry["variants"]:  # type: ignore[union-attr]
            key = normalize_name(str(variant))
            if owners.setdefault(key, str(entry["name"])) != entry["name"]:
                raise ValueError(f"Spelling {variant!r} belongs to both {owners[key]!r} and {entry['name']!r}")
    return entries


def write_map(entries: list[dict[str, object]], path: Path) -> None:
    """Write the map deterministically (indent=2, sorted keys, trailing newline)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"teams": entries}, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def write_csv(entries: list[dict[str, object]], path: Path) -> None:
    """Write the entries as a flat CSV (`variants` joined with ` | `), same order as the JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["name", "variants"])
        writer.writerows([entry["name"], " | ".join(entry["variants"])] for entry in entries)  # type: ignore[arg-type]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schedule-dir", type=Path, default=SCHEDULE_DIR, help="Directory of schedule_*.csv")
    parser.add_argument("--out", type=Path, default=OUT_PATH, help="Team map JSON output path")
    parser.add_argument("--csv-out", type=Path, default=CSV_PATH, help="Team map CSV output path")
    args = parser.parse_args(argv)
    entries = build_team_entries(load_schedule_rows(args.schedule_dir))
    write_map(entries, args.out)
    write_csv(entries, args.csv_out)
    print(f"Wrote {len(entries)} teams to {args.out} and {args.csv_out}")


if __name__ == "__main__":
    main()
