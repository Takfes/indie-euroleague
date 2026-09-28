#!/usr/bin/env python3
"""Append EuroLeague live-API header deltas to the Kaggle base season.

Header twin of `append_live_boxscores.py`. `fetch_euroleague_live_headers.py` writes one
delta CSV per run to `data/raw_data/euroleague_live/headers/`, in the schema of
`data/raw_data/kaggle_data/euroleague_header.csv`. This script rebuilds one current header
table from scratch on every run: the base season's Kaggle rows first (file order
preserved), then every delta file in filename (= chronological) order. A `game_id` present
more than once (e.g. re-fetched in a later delta, or a live row for a base-season game)
keeps its last occurrence and is reported. A delta whose columns differ from the Kaggle
header raises. Values are copied as text; re-running gives byte-identical output.

Note: live rows carry only the final score after overtime (`score_extra_time_1_*`), so a
base-season game re-fetched live replaces Kaggle's per-overtime split with the coarser form.

Usage:
    python src/eupy/transform/append_live_headers.py [--base-season E2025] [--kaggle PATH] [--live-dir PATH] [--out PATH]

Inputs: data/raw_data/kaggle_data/euroleague_header.csv,
    data/raw_data/euroleague_live/headers/{season}_delta_{utc_timestamp}.csv (all files).
Outputs: data/stage_01/header_current.csv.
Final: false -- intermediate; not exposed under data/stage_99/.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from eupy.transform.append_live_boxscores import DEFAULT_BASE_SEASON, REPO_ROOT, load_base, load_deltas, write_rows

KAGGLE_PATH = REPO_ROOT / "data" / "raw_data" / "kaggle_data" / "euroleague_header.csv"
LIVE_DIR = REPO_ROOT / "data" / "raw_data" / "euroleague_live" / "headers"
OUT_PATH = REPO_ROOT / "data" / "stage_01" / "header_current.csv"
KEY = "game_id"


def append_rows(base: list[dict[str, str]], deltas: list[dict[str, str]]) -> tuple[list[dict[str, str]], int]:
    """Concatenate base + deltas, keeping the last occurrence of each `game_id`.

    Returns `(rows, n_duplicates_dropped)`. Surviving rows keep their first-seen position.
    """
    merged: dict[str, dict[str, str]] = {}
    for row in [*base, *deltas]:
        merged[row[KEY]] = row
    return list(merged.values()), len(base) + len(deltas) - len(merged)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument(
        "--base-season", default=DEFAULT_BASE_SEASON, help="Kaggle season_code used as base (default: %(default)s)"
    )
    parser.add_argument("--kaggle", type=Path, default=KAGGLE_PATH)
    parser.add_argument("--live-dir", type=Path, default=LIVE_DIR)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    header, base = load_base(args.kaggle, args.base_season)
    deltas = load_deltas(args.live_dir, header)
    rows, dropped = append_rows(base, deltas)
    write_rows(rows, header, args.out)
    print(
        f"Wrote {len(rows)} rows to {args.out}: base {args.base_season}={len(base)}, "
        f"live deltas={len(deltas)}, duplicates dropped={dropped}"
    )


if __name__ == "__main__":
    main()
