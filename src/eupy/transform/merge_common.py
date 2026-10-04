"""Shared helpers of the merge scripts (`merge_boxscore.py`, `merge_headers.py`).

Library module (no I/O header, never run directly): CSV read/write and the Kaggle-base + live-delta loaders
both scripts use. Scripts import it instead of each other, so DVC sees it in their dependency closure.
"""

from __future__ import annotations

import csv
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Return `(header, rows)` of a CSV, values as raw text."""
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader.fieldnames or []), list(reader)


def load_base(path: Path, season: str | None) -> tuple[list[str], list[dict[str, str]]]:
    """Kaggle rows in file order: every row when `season` is None, else that season's. Raises if it is absent."""
    header, rows = read_rows(path)
    if season is None:
        return header, rows
    base = [row for row in rows if row["season_code"] == season]
    if not base:
        raise ValueError(
            f"No rows with season_code={season!r} in {path}; check the base season (--base-season / [transformation] base_season)."
        )
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


def write_rows(rows: list[dict[str, str]], header: list[str], path: Path) -> None:
    """Write `rows` as UTF-8 CSV with `\\n` line endings, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
