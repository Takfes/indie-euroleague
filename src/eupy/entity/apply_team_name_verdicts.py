#!/usr/bin/env python3
"""Apply agent verdicts onto the team-name crosswalk, keyed by `name`.

This is the write path for the agent stage of `resolve_team_names.py`'s
exact -> fuzzy-candidates -> agent-verifies pipeline: after reviewing every
`needs_review` / `no_candidate` row, an agent records its decisions as a JSON
list of verdict records and applies them here, instead of hand-editing the
crosswalk CSV. Each verdict must be a "confirmed" / "rejected" / "no_match"
call with a rationale -- convention (not enforced here) is `confirmed` /
`rejected` for rows that started as `needs_review` (a candidate held up, or
none did), and `no_match` for rows that started as `no_candidate` (no
candidate was ever proposed).

Verdict record shape (JSON list, one object per row to update):
    {
        "name": "...",                              # required: row key
        "match_status": "confirmed",               # required: confirmed | rejected | no_match
        "boxscore_team_name": "...",                 # required for confirmed, must be empty otherwise
        "match_score": 95.0,                        # optional
        "matched_by": "agent",                       # optional, defaults to "agent"
        "notes": "..."                               # required: brief rationale
    }

Usage:
    python src/eupy/entity/apply_team_name_verdicts.py --verdicts PATH [--crosswalk PATH]

Inputs:
  - team_name_crosswalk: data/stage_01/team_name_crosswalk.csv
Sources:
  - JSON verdicts file (--verdicts PATH)
Outputs:
  - team_name_crosswalk: data/stage_01/team_name_crosswalk.csv
Final: true
Impure: false
Notes: The crosswalk must already exist and is updated in place, so this script adds no stage of its own.
  Writes the same file resolve_team_names.py produces.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from eupy.entity.resolve_team_names import CROSSWALK_PATH, load_existing_crosswalk, write_crosswalk

TERMINAL_STATUSES = {"confirmed", "rejected", "no_match"}


def load_verdicts(path: Path) -> list[dict[str, Any]]:
    """Load a JSON list of verdict records."""
    return json.loads(path.read_text(encoding="utf-8"))


def apply_verdicts(rows_by_key: dict[str, dict[str, str]], verdicts: list[dict[str, Any]]) -> None:
    """Apply verdicts onto crosswalk rows in place, keyed by `name`.

    Raises:
        ValueError: If a verdict targets a row not in the crosswalk, uses a
            non-terminal `match_status`, is `confirmed` without a
            `boxscore_team_name` (or non-confirmed with one), or has no
            `notes` rationale.
    """
    for verdict in verdicts:
        key = verdict["name"]
        if key not in rows_by_key:
            raise ValueError(f"Verdict targets a row not in the crosswalk: {key!r}")

        status = verdict["match_status"]
        if status not in TERMINAL_STATUSES:
            raise ValueError(f"match_status must be one of {sorted(TERMINAL_STATUSES)}, got {status!r}")

        boxscore_team_name = verdict.get("boxscore_team_name", "")
        if status == "confirmed" and not boxscore_team_name:
            raise ValueError(f"Verdict for {key!r} is 'confirmed' but has no boxscore_team_name")
        if status != "confirmed" and boxscore_team_name:
            raise ValueError(
                f"Verdict for {key!r} is {status!r} but carries a boxscore_team_name -- only confirmed rows should"
            )

        notes = verdict.get("notes", "")
        if not notes:
            raise ValueError(f"Verdict for {key!r} has no notes -- every agent verdict needs a brief rationale")

        match_score = verdict.get("match_score", "")
        row = rows_by_key[key]
        row["match_status"] = status
        row["boxscore_team_name"] = boxscore_team_name
        row["match_score"] = f"{match_score:.1f}" if isinstance(match_score, int | float) else str(match_score)
        row["matched_by"] = verdict.get("matched_by", "agent")
        row["notes"] = notes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crosswalk", type=Path, default=CROSSWALK_PATH, help="Crosswalk CSV to update")
    parser.add_argument("--verdicts", type=Path, required=True, help="JSON file of verdict records")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows_by_key = load_existing_crosswalk(args.crosswalk)
    if not rows_by_key:
        raise ValueError(f"No crosswalk found at {args.crosswalk} -- run resolve_team_names.py first")

    verdicts = load_verdicts(args.verdicts)
    apply_verdicts(rows_by_key, verdicts)
    write_crosswalk(list(rows_by_key.values()), args.crosswalk)
    print(f"Applied {len(verdicts)} verdict(s) to {args.crosswalk}")


if __name__ == "__main__":
    main()
