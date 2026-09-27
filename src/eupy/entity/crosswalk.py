"""Read-only accessor over the published player-name crosswalk.

Decoupled from how the crosswalk gets regenerated: this class only reads
whatever's currently on disk at `data/stage_99/player_name_crosswalk.csv`
(the project's stable, dataset-named consumption path). Regenerating the
artifact means re-running `resolve_player_names.py` / invoking the
`resolve-player-names` skill -- never a side effect of loading or looking up
through this class.
"""

from __future__ import annotations

import csv
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "data" / "stage_99" / "player_name_crosswalk.csv"

RESOLVED_MATCH_STATUSES = {"exact", "confirmed"}


class PlayerNameCrosswalk:
    """Look up a basketballsphere fantasy-price name's EuroLeague box-score display name."""

    def __init__(self, rows_by_key: dict[tuple[str, str], dict[str, str]]) -> None:
        self._rows_by_key = rows_by_key

    @classmethod
    def load(cls, path: Path | None = None) -> PlayerNameCrosswalk:
        """Load the crosswalk CSV into a lookup keyed by `(name, role)`.

        Args:
            path: Crosswalk CSV path. Defaults to
                `data/stage_99/player_name_crosswalk.csv`.

        Returns:
            A loaded `PlayerNameCrosswalk`.

        Raises:
            FileNotFoundError: If the crosswalk file doesn't exist yet --
                run `resolve_player_names.py` (or the resolve-player-names
                skill) first.
        """
        resolved_path = path if path is not None else DEFAULT_PATH
        if not resolved_path.exists():
            raise FileNotFoundError(
                f"No player-name crosswalk found at {resolved_path}. Run "
                f"`uv run python src/eupy/entity/resolve_player_names.py` "
                f"(or the resolve-player-names skill) to generate it."
            )
        with resolved_path.open(newline="", encoding="utf-8") as f:
            rows_by_key = {(row["name"], row["role"]): row for row in csv.DictReader(f)}
        return cls(rows_by_key)

    def boxscore_name_for(self, name: str, role: str = "player") -> str | None:
        """Look up the EuroLeague box-score display name for a fantasy-price name.

        Args:
            name: The `basketballsphere_prices.csv` `name` value.
            role: The `basketballsphere_prices.csv` `role` value.

        Returns:
            The matched box-score display name when `match_status` is
            `exact` or `confirmed`; `None` for every other status, including
            still-open (`needs_review`, `no_candidate`) rows, so callers
            never need to know the status vocabulary.
        """
        row = self._rows_by_key.get((name, role))
        if row is None or row["match_status"] not in RESOLVED_MATCH_STATUSES:
            return None
        return row["boxscore_name"]
