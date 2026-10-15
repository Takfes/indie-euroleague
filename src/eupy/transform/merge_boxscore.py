#!/usr/bin/env python3
"""Merge the Kaggle box-score history with the EuroLeague live-API deltas into one table.

The Kaggle dump (`euroleague_box_score.csv`, E2007-E2025) is static; the live fetcher
(`fetch_euroleague_live_boxscores.py`) writes one delta CSV per run, each holding only the games ingested in
that run. This script rebuilds one merged box-score table from scratch on every run: the Kaggle rows first
(file order preserved), then every delta file under `data/raw_data/euroleague_net/box_score/` in filename
order (the filename embeds a UTC timestamp, so this is chronological). Nothing is incremental -- re-running
from the same raw files gives byte-identical output.

The live deltas are authoritative for the seasons they hold: Kaggle rows of any `season_code` that appears in a
delta are dropped before the append, so a Kaggle dump that already covers part of the live season does not
duplicate its games.

By default the full Kaggle history is merged; `--base-season` (or `[transformation] base_season` in
`config/config.toml`) keeps only that `season_code`.

Both sources share the same 32-column schema (the fetcher was built to mirror the Kaggle file); a delta whose
header differs raises rather than being silently realigned. Values are copied as text, so the Kaggle file's
float-style flags (`0.0`) and the live files' integer flags (`0`) are left exactly as each source wrote them.
The merged rows are validated before writing (no empty rows, unique `game_player_id`); a violation is logged to
the shared log file and fails the run -- nothing is dropped silently.

Usage:
    python src/eupy/transform/merge_boxscore.py [--base-season E2025] [--kaggle PATH] [--live-dir PATH] [--out PATH]

Inputs:
  - kaggle_data/euroleague_box_score: data/raw_data/kaggle_data/euroleague_box_score.csv
  - euroleague_net/box_score: data/raw_data/euroleague_net/box_score/
Sources: none
Outputs:
  - euroleague_box_score_merged: data/stage_01/euroleague_box_score_merged.csv
Final: false
Impure: false
Notes: Full Kaggle history by default (config transformation.base_season / --base-season restricts it); all delta
  files under euroleague_net/box_score are appended, and Kaggle rows of seasons present in a delta are dropped.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from eupy.config import get_config
from eupy.quality import validate
from eupy.transform.merge_common import REPO_ROOT, drop_covered_seasons, load_base, load_deltas, write_rows

KAGGLE_PATH = REPO_ROOT / "data" / "raw_data" / "kaggle_data" / "euroleague_box_score.csv"
LIVE_DIR = REPO_ROOT / "data" / "raw_data" / "euroleague_net" / "box_score"
OUT_PATH = REPO_ROOT / "data" / "stage_01" / "euroleague_box_score_merged.csv"

KEY = "game_player_id"


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
    validate(rows, KEY, "euroleague_box_score_merged")
    write_rows(rows, header, args.out)
    print(f"Wrote {len(rows)} rows to {args.out}: kaggle={len(base)}, live deltas={len(deltas)}")


if __name__ == "__main__":
    main()
