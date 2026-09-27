"""Read-only accessor over the published team-name crosswalk.

Decoupled from how the crosswalk gets regenerated: this class only reads
whatever's currently on disk at `data/stage_99/team_name_crosswalk.csv`
(the project's stable, dataset-named consumption path). Regenerating the
artifact means re-running `resolve_team_names.py` / invoking the
`resolve-team-names` skill -- never a side effect of loading or looking up
through this class.
"""

from __future__ import annotations

import csv
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parents[3] / "data" / "stage_99" / "team_name_crosswalk.csv"

RESOLVED_MATCH_STATUSES = {"exact", "confirmed"}


class TeamNameCrosswalk:
    """Look up a basketballsphere fantasy-price club name's EuroLeague box-score team name."""

    def __init__(self, rows_by_key: dict[str, dict[str, str]]) -> None:
        self._rows_by_key = rows_by_key

    @classmethod
    def load(cls, path: Path | None = None) -> TeamNameCrosswalk:
        """Load the crosswalk CSV into a lookup keyed by `name`.

        Args:
            path: Crosswalk CSV path. Defaults to
                `data/stage_99/team_name_crosswalk.csv`.

        Returns:
            A loaded `TeamNameCrosswalk`.

        Raises:
            FileNotFoundError: If the crosswalk file doesn't exist yet --
                run `resolve_team_names.py` (or the resolve-team-names
                skill) first.
        """
        resolved_path = path if path is not None else DEFAULT_PATH
        if not resolved_path.exists():
            raise FileNotFoundError(
                f"No team-name crosswalk found at {resolved_path}. Run "
                f"`uv run python src/eupy/entity/resolve_team_names.py` "
                f"(or the resolve-team-names skill) to generate it."
            )
        with resolved_path.open(newline="", encoding="utf-8") as f:
            rows_by_key = {row["name"]: row for row in csv.DictReader(f)}
        return cls(rows_by_key)

    def boxscore_team_name_for(self, name: str) -> str | None:
        """Look up the EuroLeague box-score team name for a fantasy-price club name.

        Args:
            name: The `basketballsphere_prices.csv` `club` value.

        Returns:
            The matched box-score team-name spelling when `match_status` is
            `exact` or `confirmed`; `None` for every other status, including
            still-open (`needs_review`, `no_candidate`) rows, so callers
            never need to know the status vocabulary.
        """
        row = self._rows_by_key.get(name)
        if row is None or row["match_status"] not in RESOLVED_MATCH_STATUSES:
            return None
        return row["boxscore_team_name"]
