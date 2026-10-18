"""Player names: turn any known spelling of a player into one normalized full name.

`PlayerNames` loads the player map `data/stage_02/player_name_map.csv`, written by `build_player_name_map.py`, once
and keeps it in memory, so the map must have been built (`uv run dvc repro -s build_player_name_map`).

    players = PlayerNames.load()
    players.normalize("JONES, CARLIK")             # 'Carlik Jones'
    players.normalize("C. Jones", team="PAO")      # 'Carlik Jones'; `team` only matters when players share a spelling
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from eupy.entity.matching import OPEN_STATUSES, AmbiguousNameError, UnknownNameError, normalize_name
from eupy.entity.team_names import TeamNames

MAP_PATH = Path(__file__).resolve().parents[3] / "data" / "stage_02" / "player_name_map.csv"


def load_entries(path: Path) -> list[dict[str, object]]:
    """Read the player map CSV into entries; `variants` is split back into a list."""
    with path.open(newline="", encoding="utf-8") as f:
        return [{**row, "variants": row["variants"].split(" | ")} for row in csv.DictReader(f)]


class PlayerNames:
    """Lookup from every spelling of a player to the normalized full name."""

    def __init__(self, entries: list[dict[str, object]], teams: TeamNames) -> None:
        """Index the resolved `entries`; open ones (`needs_review`, `no_candidate`) are kept for `unresolved()` only."""
        self._teams = teams
        self._open = [e for e in entries if e["match_status"] in OPEN_STATUSES]
        self._index: dict[str, list[dict[str, object]]] = defaultdict(list)
        for entry in entries:
            if entry["match_status"] in OPEN_STATUSES:
                continue
            for variant in entry["variants"]:  # type: ignore[union-attr]
                self._index[normalize_name(str(variant))].append(entry)

    @classmethod
    def load(cls, path: Path | None = None, teams: TeamNames | None = None) -> PlayerNames:
        """Load the player map (default: `data/stage_02/player_name_map.csv`) and the team lookup."""
        resolved = path or MAP_PATH
        if not resolved.exists():
            raise FileNotFoundError(
                f"{resolved} not found; build it with `python -m eupy.entity.build_player_name_map`"
            )
        return cls(load_entries(resolved), teams or TeamNames.load())

    def normalize(self, name: str, team: str | None = None) -> str:
        """Return the normalized full name for any known spelling of a player.

        Args:
            name: A box-score, stats or already-normalized spelling.
            team: A club code or name; only used to choose between players that share `name`.

        Raises:
            UnknownNameError: If `name` is not in the map, or `team` is needed but unknown to the team map.
            AmbiguousNameError: If several players share `name` and `team` is missing or does not single one out.
        """
        entries = self._index.get(normalize_name(name), [])
        names = {str(e["name"]) for e in entries}
        if not names:
            raise UnknownNameError(f"Unknown player {name!r}; rebuild the player map if they are new")
        if len(names) == 1:
            return next(iter(names))
        if team is not None:
            club = self._teams.normalize(team)
            names = {str(e["name"]) for e in entries if e["team"] == club}
            if len(names) == 1:
                return next(iter(names))
        raise AmbiguousNameError(f"{name!r} is shared by {sorted({str(e['name']) for e in entries})}; pass team=")

    def unresolved(self) -> list[dict[str, object]]:
        """Entries still awaiting a decision (`needs_review`, `no_candidate`), in map order."""
        return list(self._open)
