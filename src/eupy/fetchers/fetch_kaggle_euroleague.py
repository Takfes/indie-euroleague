#!/usr/bin/env python3
"""Download the Kaggle EuroLeague dataset and keep only the two files the pipeline reads.

Downloads `babissamothrakis/euroleague-datasets` (configurable: `[acquisition] kaggle_dataset`) into a
temporary directory, keeps `euroleague_box_score.csv` and `euroleague_header.csv` (the other CSVs --
points, teams, players, comparison, play-by-play -- are discarded), validates both (no empty rows, unique
`game_player_id` / `game_id`; violations are logged and fail the run) and only then moves them into
`data/raw_data/kaggle_data/`. A failed download or validation never touches the previous good files.

Needs Kaggle credentials (`~/.kaggle/kaggle.json` or the `KAGGLE_USERNAME`/`KAGGLE_KEY` env vars).

Usage:
    python src/eupy/fetchers/fetch_kaggle_euroleague.py [--out-dir PATH] [--dataset OWNER/NAME]

Inputs: none
Sources:
  - kaggle.com/datasets/babissamothrakis/euroleague-datasets (Kaggle API)
Outputs:
  - kaggle_data/euroleague_box_score: data/raw_data/kaggle_data/euroleague_box_score.csv
  - kaggle_data/euroleague_header: data/raw_data/kaggle_data/euroleague_header.csv
Final: false
Impure: true
Refresh: manual: the dataset is a static dump through E2025
Notes: Only the two named CSVs are kept; every other file in the Kaggle archive is dropped.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import tempfile
from pathlib import Path

from eupy.config import get_config
from eupy.quality import validate

DEFAULT_OUT_DIR = Path(__file__).resolve().parents[3] / "data" / "raw_data" / "kaggle_data"

# kept file -> primary key column
KEEP = {"euroleague_box_score.csv": "game_player_id", "euroleague_header.csv": "game_id"}


def download(dataset: str, dest: Path) -> None:
    """Download and unzip the Kaggle dataset into `dest` (imported lazily: the import needs no credentials)."""
    from kaggle.api.kaggle_api_extended import KaggleApi

    api = KaggleApi()
    api.authenticate()
    api.dataset_download_files(dataset, path=str(dest), unzip=True, force=True)


def run(out_dir: Path, dataset: str) -> None:
    """Download, keep and validate the two files, then move them into `out_dir`."""
    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        download(dataset, staging)
        for name, key in KEEP.items():
            path = next(staging.rglob(name), None)
            if path is None:
                raise FileNotFoundError(f"{name} not found in the downloaded dataset {dataset!r}")
            with path.open(newline="", encoding="utf-8") as f:
                validate(list(csv.DictReader(f)), key, f"kaggle_data/{name.removesuffix('.csv')}")
        out_dir.mkdir(parents=True, exist_ok=True)
        for name in KEEP:
            shutil.move(str(next(staging.rglob(name))), out_dir / name)
    print(f"Wrote {', '.join(KEEP)} to {out_dir}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR, help="Raw output directory")
    parser.add_argument("--dataset", default=get_config().acquisition.kaggle_dataset, help="Kaggle OWNER/NAME")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run(args.out_dir, args.dataset)


if __name__ == "__main__":
    main()
