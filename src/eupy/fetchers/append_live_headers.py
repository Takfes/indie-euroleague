#!/usr/bin/env python3
"""Append EuroLeague live-API header deltas to the Kaggle header history.

`fetch_euroleague_live_headers.py` writes one delta CSV per run to
`data/raw_data/euroleague_live/headers/`, in the schema of
`data/raw_data/kaggle_data/euroleague_header.csv`. This script rebuilds the combined
dataset from scratch on every run (no hidden state -- same inputs, same output):

1. **Base** -- Kaggle header rows with `season_code` up to and including `--base-season`
   (default `E2025`, the last Kaggle season).
2. **Deltas** -- every `*_delta_*.csv` in the live headers directory, in filename
   (= timestamp) order. A `game_id` fetched more than once keeps its latest delta row.
3. **Overlap** -- a delta row whose `game_id` is already in the base is dropped: the base
   wins (Kaggle carries the per-overtime splits the live API cannot provide).
4. Output is sorted by season, then gamecode.

Usage:
    python src/eupy/fetchers/append_live_headers.py [--base-season E2025] [--base PATH]
        [--deltas-dir PATH] [--out PATH]

Inputs: data/raw_data/kaggle_data/euroleague_header.csv,
    data/raw_data/euroleague_live/headers/*_delta_*.csv.
Outputs: data/stage_01/euroleague_header_appended.csv.
Final: true -- consumption dataset; symlinked into data/stage_99/.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASE_PATH = ROOT / "data" / "raw_data" / "kaggle_data" / "euroleague_header.csv"
DELTAS_DIR = ROOT / "data" / "raw_data" / "euroleague_live" / "headers"
OUT_PATH = ROOT / "data" / "stage_01" / "euroleague_header_appended.csv"
DEFAULT_BASE_SEASON = "E2025"


def season_year(season_code: str) -> int:
    """`E2025` -> 2025."""
    return int(season_code.removeprefix("E"))


def game_sort_key(row: dict[str, str]) -> tuple[int, int]:
    """Sort by season year then numeric gamecode (`E2025_010` -> (2025, 10))."""
    season, _, code = row["game_id"].partition("_")
    return season_year(season), int(code)


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Read a CSV as (fieldnames, rows), all values kept as strings."""
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def append_headers(
    base_rows: list[dict[str, str]],
    delta_rows: list[dict[str, str]],
    base_season: str,
) -> list[dict[str, str]]:
    """Combine base (<= base_season) with de-duplicated delta rows; base wins on overlap."""
    cutoff = season_year(base_season)
    base = [r for r in base_rows if season_year(r["season_code"]) <= cutoff]
    base_ids = {r["game_id"] for r in base}
    latest_delta = {r["game_id"]: r for r in delta_rows}  # later rows overwrite earlier
    extra = [r for game_id, r in latest_delta.items() if game_id not in base_ids]
    return sorted(base + extra, key=game_sort_key)


def run(base_path: Path, deltas_dir: Path, out_path: Path, base_season: str) -> None:
    """Rebuild the combined header dataset and write it to `out_path`."""
    fieldnames, base_rows = read_csv(base_path)
    delta_rows: list[dict[str, str]] = []
    delta_files = sorted(deltas_dir.glob("*_delta_*.csv"))
    for path in delta_files:
        delta_fields, rows = read_csv(path)
        if delta_fields != fieldnames:
            raise ValueError(f"{path.name} columns differ from the base header schema")
        delta_rows.extend(rows)

    combined = append_headers(base_rows, delta_rows, base_season)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(combined)
    print(
        f"Wrote {len(combined)} rows to {out_path} "
        f"({len(combined) - len(base_rows)} net vs base, from {len(delta_files)} delta file(s))"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-season", default=DEFAULT_BASE_SEASON, help="Last Kaggle season kept (default: %(default)s)"
    )
    parser.add_argument("--base", type=Path, default=BASE_PATH, help="Kaggle header CSV")
    parser.add_argument("--deltas-dir", type=Path, default=DELTAS_DIR, help="Live header deltas directory")
    parser.add_argument("--out", type=Path, default=OUT_PATH, help="Combined output CSV")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run(args.base, args.deltas_dir, args.out, args.base_season)


if __name__ == "__main__":
    main()
