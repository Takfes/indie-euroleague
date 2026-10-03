#!/usr/bin/env python3
"""One-time migration: extract the agent-resolved crosswalk rows into initial verdict batches.

Before verdict batches existed, agent verdicts lived only inside the crosswalks (the resolvers merged
them in place). This tool reads the three committed crosswalks and writes every row whose
`match_status` is `confirmed` / `rejected` / `no_match` as a verdict record into
`data/curated/<crosswalk>_verdicts/0001_initial-extraction.json`, so the now-pure resolvers can
rebuild the crosswalks byte-identically from raw data + batches.

Kept (not deleted after the migration) so the extraction stays reproducible and testable. It lives in
`devtools/` because it is not a pipeline step: its outputs are hand-curated inputs, not stage data.
`match_score` is copied as the CSV string, so the rebuilt value is byte-identical.

Usage:
    python src/eupy/devtools/extract_initial_verdicts.py [--stage-dir DIR] [--curated-dir DIR]
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from eupy.entity.verdict_batches import TERMINAL_STATUSES, write_batch

REPO_ROOT = Path(__file__).resolve().parents[3]
STAGE_DIR = REPO_ROOT / "data" / "stage_01"
CURATED_DIR = REPO_ROOT / "data" / "curated"
BATCH_NAME = "0001_initial-extraction.json"
INGESTED_AT = "2026-09-30"  # fixed, so re-running the extraction is byte-identical

# (crosswalk file, verdict dir, key field, target field)
CROSSWALKS = [
    ("player_name_crosswalk.csv", "player_name_verdicts", "name", "boxscore_name"),
    ("team_name_crosswalk.csv", "team_name_verdicts", "name", "boxscore_team_name"),
    ("fantasy_stats_player_name_crosswalk.csv", "fantasy_stats_player_name_verdicts", "player_id", "resolved_name"),
]


def extract_verdicts(rows: list[dict[str, str]], key_field: str, target_field: str) -> list[dict[str, str]]:
    """Verdict records for the agent-resolved rows, sorted by key."""
    fields = (key_field, "match_status", target_field, "match_score", "matched_by", "notes")
    verdicts = [{f: row[f] for f in fields} for row in rows if row["match_status"] in TERMINAL_STATUSES]
    return sorted(verdicts, key=lambda v: v[key_field])


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage-dir", type=Path, default=STAGE_DIR, help="Directory of the committed crosswalks")
    parser.add_argument("--curated-dir", type=Path, default=CURATED_DIR, help="Root of the verdict directories")
    args = parser.parse_args(argv)

    for crosswalk, verdict_dir, key_field, target_field in CROSSWALKS:
        with (args.stage_dir / crosswalk).open(newline="", encoding="utf-8") as f:
            verdicts = extract_verdicts(list(csv.DictReader(f)), key_field, target_field)
        out = args.curated_dir / verdict_dir / BATCH_NAME
        write_batch(out, verdicts, ingested_at=INGESTED_AT, source=f"extracted from committed {crosswalk}")
        print(f"Wrote {len(verdicts)} verdict(s) to {out}")


if __name__ == "__main__":
    main()
