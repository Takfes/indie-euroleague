#!/usr/bin/env python3
"""Merge the Kaggle game-header history with the EuroLeague live-API header deltas into one table.

Header twin of `merge_boxscore.py`. The Kaggle dump (`euroleague_header.csv`, E2007-E2025) is static;
`fetch_euroleague_live_headers.py` writes one delta CSV per run to `data/raw_data/euroleague_net/headers/`, in
the schema of the Kaggle file. This script rebuilds one merged header table from scratch on every run: the
Kaggle rows first (file order preserved), then every delta file in filename (= chronological) order.
Re-running from the same raw files gives byte-identical output.

By default the full Kaggle history is merged; `--base-season` (or `[transformation] base_season` in
`config/config.toml`) keeps only that `season_code`. A delta whose columns differ from the Kaggle header raises.
Values are copied as text. The merged rows are validated before writing (no empty rows, unique `game_id`); a
violation is logged to the shared log file and fails the run -- nothing is dropped silently, so a live row for
a game already in Kaggle is an error, not a replacement. The one deliberate drop: the live deltas are
authoritative for the seasons they hold, so Kaggle rows of any `season_code` that appears in a delta are removed
before the append, and a Kaggle dump that already covers part of the live season does not duplicate its games.

Usage:
    python src/eupy/transform/merge_headers.py [--base-season E2025] [--kaggle PATH] [--live-dir PATH] [--out PATH]

Inputs:
  - kaggle_data/euroleague_header: data/raw_data/kaggle_data/euroleague_header.csv
  - euroleague_net/headers: data/raw_data/euroleague_net/headers/
Sources: none
Outputs:
  - euroleague_header_merged: data/stage_01/euroleague_header_merged.csv
Final: false
Impure: false
Notes: Full Kaggle history by default (config transformation.base_season / --base-season restricts it); all delta
  files under euroleague_net/headers are appended, and Kaggle rows of seasons present in a delta are dropped.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from eupy.config import get_config
from eupy.quality import validate
from eupy.transform.merge_common import REPO_ROOT, drop_covered_seasons, load_base, load_deltas, write_rows

KAGGLE_PATH = REPO_ROOT / "data" / "raw_data" / "kaggle_data" / "euroleague_header.csv"
LIVE_DIR = REPO_ROOT / "data" / "raw_data" / "euroleague_net" / "headers"
OUT_PATH = REPO_ROOT / "data" / "stage_01" / "euroleague_header_merged.csv"

KEY = "game_id"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument(
        "--base-season",
        default=get_config().transformation.base_season,
        help="Kaggle season_code to keep (default: config transformation.base_season; unset = full history)",
    )
    parser.add_argument("--kaggle", type=Path, default=KAGGLE_PATH)
    parser.add_argument("--live-dir", type=Path, default=LIVE_DIR)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    header, base = load_base(args.kaggle, args.base_season)
    deltas = load_deltas(args.live_dir, header)
    base = drop_covered_seasons(base, deltas)
    rows = [*base, *deltas]
    validate(rows, KEY, "euroleague_header_merged")
    write_rows(rows, header, args.out)
    print(f"Wrote {len(rows)} rows to {args.out}: kaggle={len(base)}, live deltas={len(deltas)}")


if __name__ == "__main__":
    main()
