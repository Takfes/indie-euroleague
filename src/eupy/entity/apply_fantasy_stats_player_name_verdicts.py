#!/usr/bin/env python3
"""Ingest agent verdicts for the fantasy-stats player-name crosswalk as a new, immutable verdict batch.

The agent stage of `resolve_fantasy_stats_player_names.py`: after reviewing
unresolved rows, an agent records its decisions as a JSON list of verdict
records. This script validates that list and writes it as the next
`NNNN_<slug>.json` batch in `data/curated/fantasy_stats_player_name_verdicts/`;
the next `resolve_fantasy_stats_player_names.py` run applies it (later batch
wins on the same key). It never touches the crosswalk and never modifies or
overwrites an existing batch. Invalid input writes nothing. Keys absent from
the current data are not checked here (the resolver skips them).

Verdict record shape (JSON list, one object per row to update):
    {
        "player_id": "...",               # required: row key
        "match_status": "confirmed",      # required: confirmed | rejected | no_match
        "resolved_name": "...",           # required for confirmed, must be empty otherwise
        "match_score": 95.0,              # optional
        "matched_by": "agent",            # optional, defaults to "agent"
        "notes": "..."                    # required: brief rationale
    }

Usage:
    python src/eupy/entity/apply_fantasy_stats_player_name_verdicts.py --verdicts PATH [--batches-dir DIR] [--label SLUG]

Inputs: none
Sources:
  - JSON verdicts file (--verdicts PATH)
Outputs:
  - fantasy_stats_player_name_verdicts: data/curated/fantasy_stats_player_name_verdicts/
Final: false
Impure: true
Notes: Manual ingest, append-only batches, no DVC step. The batch directory is the tracked record that
  resolve_fantasy_stats_player_names.py reads.
"""

from __future__ import annotations

import sys
from pathlib import Path

from eupy.entity.verdict_batches import run_ingest_cli

VERDICTS_DIR = Path(__file__).resolve().parents[3] / "data" / "curated" / "fantasy_stats_player_name_verdicts"


def main() -> int:
    return run_ingest_cli(__doc__, VERDICTS_DIR, "player_id", "resolved_name")


if __name__ == "__main__":
    sys.exit(main())
