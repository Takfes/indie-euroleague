"""Shared raw-data readers of the player-name resolvers (`resolve_player_names.py`, `resolve_fantasy_stats_player_names.py`).

Library module (no I/O header, never run directly): default paths and loaders for `basketballsphere_prices.csv`
and the Kaggle `euroleague_box_score.csv`. Scripts import it instead of each other, so DVC and the registry lint
see one shared dependency.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

MASTER_PATH = (
    Path(__file__).resolve().parents[3] / "data" / "raw_data" / "fantasy_prices" / "basketballsphere_prices.csv"
)
BOXSCORE_PATH = Path(__file__).resolve().parents[3] / "data" / "raw_data" / "kaggle_data" / "euroleague_box_score.csv"


def reorder_boxscore_name(name: str) -> str:
    """Reorder a box-score `"LAST, First"` name to `"First Last"`; passes through names with no comma."""
    if "," not in name:
        return name
    last, first = name.split(",", 1)
    return f"{first.strip()} {last.strip()}"


def load_master_rows(path: Path) -> list[dict[str, str]]:
    """Load basketballsphere_prices.csv `role=player` rows, dropping `role=head_coach`.

    The box-score dataset has no coach data, so matching head coaches is
    structurally impossible -- they are filtered out here, before matching,
    rather than passed through as noise.
    """
    with path.open(newline="", encoding="utf-8") as f:
        return [row for row in csv.DictReader(f) if row["role"] == "player"]


def load_boxscore_spellings(path: Path) -> dict[str, set[str]]:
    """Load distinct box-score display-name spellings and the player_ids that use each.

    Keyed by the reordered `"First LAST"` spelling, excluding synthetic TOTAL
    rows. A spelling used by more than one distinct `player_id` signals a
    genuine name collision (two different real players, or an upstream
    data-quality duplicate) -- kept here for internal collision detection,
    even though `player_id` is never written to the crosswalk.
    """
    spellings: dict[str, set[str]] = defaultdict(set)
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["dorsal"] == "TOTAL":
                continue
            spellings[reorder_boxscore_name(row["player"])].add(row["player_id"])
    return dict(spellings)
