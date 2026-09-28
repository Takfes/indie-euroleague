#!/usr/bin/env python3
"""Append EuroLeague live-API box-score deltas to the Kaggle base season.

The Kaggle dump (`euroleague_box_score.csv`, E2007-E2025) is static; the live
fetcher (`fetch_euroleague_live_boxscores.py`) writes one delta CSV per run,
each holding only the games ingested in that run. This script rebuilds one
current box-score table from scratch on every run: the base season's Kaggle
rows first (file order preserved), then every delta file under
`data/raw_data/euroleague_live/box_score/` in filename order (the filename
embeds a UTC timestamp, so this is chronological). Nothing is incremental --
re-running from the same raw files gives byte-identical output.

Both sources share the same 32-column schema (the fetcher was built to mirror
the Kaggle file); a delta whose header differs raises rather than being
silently realigned. Values are copied as text, so the Kaggle file's float-style
flags (`0.0`) and the live files' integer flags (`0`) are left exactly as
each source wrote them. A `game_player_id` present more than once (e.g. a
game re-ingested in a later delta) keeps its last occurrence and is reported.

Usage:
    python src/eupy/transform/append_live_boxscores.py [--base-season E2025] [--kaggle PATH] [--live-dir PATH] [--out PATH]

Inputs: data/raw_data/kaggle_data/euroleague_box_score.csv,
    data/raw_data/euroleague_live/box_score/{season}_delta_{utc_timestamp}.csv (all files).
Outputs: data/stage_01/box_score_current.csv.
Final: false -- intermediate; not exposed under data/stage_99/.
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
KAGGLE_PATH = REPO_ROOT / "data" / "raw_data" / "kaggle_data" / "euroleague_box_score.csv"
LIVE_DIR = REPO_ROOT / "data" / "raw_data" / "euroleague_live" / "box_score"
OUT_PATH = REPO_ROOT / "data" / "stage_01" / "box_score_current.csv"

DEFAULT_BASE_SEASON = "E2025"
KEY = "game_player_id"


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Return `(header, rows)` of a CSV, values as raw text."""
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def load_base(path: Path, season: str) -> tuple[list[str], list[dict[str, str]]]:
    """Kaggle rows of one season, in file order. Raises if the season is absent."""
    header, rows = read_rows(path)
    base = [row for row in rows if row["season_code"] == season]
    if not base:
        raise ValueError(f"No rows with season_code={season!r} in {path}; check --base-season.")
    return header, base


def load_deltas(live_dir: Path, header: list[str]) -> list[dict[str, str]]:
    """All delta files in filename (= chronological) order; each must match `header` exactly."""
    rows: list[dict[str, str]] = []
    for path in sorted(live_dir.glob("*_delta_*.csv")):
        delta_header, delta_rows = read_rows(path)
        if delta_header != header:
            raise ValueError(
                f"{path.name} columns differ from the Kaggle base. "
                f"Missing: {sorted(set(header) - set(delta_header))}, extra: {sorted(set(delta_header) - set(header))} "
                "(order-only differences also fail). Fix the fetcher output or the base before appending."
            )
        rows.extend(delta_rows)
    return rows


def append_rows(base: list[dict[str, str]], deltas: list[dict[str, str]]) -> tuple[list[dict[str, str]], int]:
    """Concatenate base + deltas, keeping the last occurrence of each `game_player_id`.

    Returns `(rows, n_duplicates_dropped)`. Surviving rows keep their first-seen position.
    """
    merged: dict[str, dict[str, str]] = {}
    total = 0
    for row in [*base, *deltas]:
        merged[row[KEY]] = row
        total += 1
    return list(merged.values()), total - len(merged)


def write_rows(rows: list[dict[str, str]], header: list[str], path: Path) -> None:
    """Write `rows` as UTF-8 CSV with `\\n` line endings, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


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
