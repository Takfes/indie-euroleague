#!/usr/bin/env python3
"""Merge the EuroLeague live-API header deltas into one game-header table (optionally under the Kaggle history).

Header twin of `merge_boxscore.py`. `fetch_euroleague_live_headers.py` writes one delta CSV per run to
`data/raw_data/euroleague_net/headers/`. This script rebuilds one merged header table from scratch on every run:
every delta file in filename (= chronological) order, with the header taken from the first delta, which all
others must match. Re-running from the same raw files gives byte-identical output.

The Kaggle dump (`euroleague_header.csv`, E2007-E2025) is left out by default. With `--include-kaggle` (or
`[transformation] include_kaggle = true` in `config/config.toml`) its rows come first (file order preserved) and
Kaggle rows of any `season_code` that appears in a delta are dropped before the append, since the live deltas are
authoritative for the seasons they hold. `--base-season` (or `[transformation] base_season`) then keeps only that
Kaggle `season_code`. A delta whose columns differ from the merge header raises. Values are copied as text. The
merged rows are validated before writing (no empty rows, unique `game_id`); a violation is logged to the shared
log file and fails the run -- nothing is dropped silently.

Usage:
    python src/eupy/transform/merge_headers.py [--include-kaggle [--base-season E2025]] [--kaggle PATH]
        [--live-dir PATH] [--out PATH]

Inputs:
  - euroleague_net/headers: data/raw_data/euroleague_net/headers/
Sources: none
Outputs:
  - euroleague_header_merged: data/stage_01/euroleague_header_merged.csv
Final: false
Impure: false
Notes: Live deltas only by default. With include_kaggle the Kaggle file data/raw_data/kaggle_data/euroleague_header.csv
  is merged under them; it is deliberately not a declared input, so a merge never pulls the Kaggle fetch into the
  graph (after refreshing Kaggle with include_kaggle on, rerun with `dvc repro -f merge_headers`).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from eupy.config import get_config
from eupy.quality import validate
from eupy.transform.merge_common import (
    REPO_ROOT,
    drop_covered_seasons,
    first_delta_header,
    load_base,
    load_deltas,
    write_rows,
)

KAGGLE_PATH = REPO_ROOT / "data" / "raw_data" / "kaggle_data" / "euroleague_header.csv"
LIVE_DIR = REPO_ROOT / "data" / "raw_data" / "euroleague_net" / "headers"
OUT_PATH = REPO_ROOT / "data" / "stage_01" / "euroleague_header_merged.csv"

KEY = "game_id"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument(
        "--base-season",
        default=get_config().transformation.base_season,
        help="With --include-kaggle: the Kaggle season_code to keep (default: config transformation.base_season; unset = full history)",
    )
    parser.add_argument(
        "--include-kaggle",
        action=argparse.BooleanOptionalAction,
        default=get_config().transformation.include_kaggle,
        help="Merge the Kaggle history under the live deltas (default: config transformation.include_kaggle, off)",
    )
    parser.add_argument("--kaggle", type=Path, default=KAGGLE_PATH)
    parser.add_argument("--live-dir", type=Path, default=LIVE_DIR)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.include_kaggle:
        header, base = load_base(args.kaggle, args.base_season)
    else:
        header, base = first_delta_header(args.live_dir), []
    deltas = load_deltas(args.live_dir, header)
    base = drop_covered_seasons(base, deltas)
    rows = [*base, *deltas]
    validate(rows, KEY, "euroleague_header_merged")
    write_rows(rows, header, args.out)
    print(f"Wrote {len(rows)} rows to {args.out}: kaggle={len(base)}, live deltas={len(deltas)}")


if __name__ == "__main__":
    main()
