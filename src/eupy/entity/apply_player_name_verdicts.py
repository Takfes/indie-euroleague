#!/usr/bin/env python3
"""Ingest agent verdicts for the player name map as a new, immutable verdict batch.

The agent stage of `build_player_name_map.py`: after reviewing the open entries (`needs_review` /
`no_candidate`, listed in `maps/player_name_map.json`), an agent records its decisions as a JSON list of verdict
records. This script validates that list and writes it as the next `NNNN_<slug>.json` batch in
`data/curated/player_name_verdicts/`; the next `build_player_name_map.py` run applies it (later batch wins on the
same key). It never touches the map and never modifies or overwrites an existing batch. Invalid input writes
nothing. Keys absent from the current data are not checked here (the builder skips them).

Verdict record shape (JSON list, one object per stats player to decide):
    {
        "team": "PAO",                    # required: the stats club code (with `name`, the row key)
        "name": "S. Francisco",           # required: the stats spelling
        "match_status": "confirmed",      # required: confirmed | rejected | no_match
        "resolved_name": "...",           # required for confirmed (the full name), must be empty otherwise
        "matched_by": "agent",            # optional, defaults to "agent"
        "notes": "..."                    # required: brief rationale
    }

Usage:
    python -m eupy.entity.apply_player_name_verdicts --verdicts PATH [--batches-dir DIR] [--label SLUG]

Inputs: none
Sources:
  - JSON verdicts file (--verdicts PATH)
Outputs:
  - player_name_verdicts: data/curated/player_name_verdicts/
Final: false
Impure: true
Notes: Manual ingest, append-only batches, no DVC step. The batch directory is the tracked record that
  build_player_name_map.py reads.
"""

from __future__ import annotations

import sys
from pathlib import Path

from eupy.entity.verdict_batches import PLAYER_VERDICT_KEY_FIELDS, run_ingest_cli

VERDICTS_DIR = Path(__file__).resolve().parents[3] / "data" / "curated" / "player_name_verdicts"


def main() -> int:
    return run_ingest_cli(__doc__, VERDICTS_DIR, PLAYER_VERDICT_KEY_FIELDS, "resolved_name")


if __name__ == "__main__":
    sys.exit(main())
