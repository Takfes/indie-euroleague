"""Team names: turn any known spelling of a team (name, schedule code, TV code) into one normalized name.

`TeamNames` loads `maps/team_name_map.json`, written by `build_team_name_map.py`, once and keeps it in
memory. It reads no data folder, so it works wherever the package is installed.

    >>> TeamNames.load().normalize("PAO")
    'Panathinaikos'
"""

from __future__ import annotations

import json
from pathlib import Path

from eupy.entity.lookup import UnknownNameError, lookup_key

MAP_PATH = Path(__file__).resolve().parent / "maps" / "team_name_map.json"


class TeamNames:
    """Lookup from every spelling of a team to its normalized name."""

    def __init__(self, entries: list[dict[str, object]]) -> None:
        """Index `entries` (`{"name": str, "variants": [str, ...]}`); a variant used by two teams is an error."""
        self._index: dict[str, str] = {}
        for entry in entries:
            name = str(entry["name"])
            for variant in [name, *entry["variants"]]:  # type: ignore[union-attr]
                key = lookup_key(str(variant))
                if self._index.setdefault(key, name) != name:
                    raise ValueError(f"Team spelling {variant!r} belongs to both {self._index[key]!r} and {name!r}")

    @classmethod
    def load(cls, path: Path | None = None) -> TeamNames:
        """Load the team map (default: the one shipped in `eupy/entity/maps/`)."""
        resolved = path or MAP_PATH
        if not resolved.exists():
            raise FileNotFoundError(f"{resolved} not found; build it with `python -m eupy.entity.build_team_name_map`")
        return cls(json.loads(resolved.read_text(encoding="utf-8"))["teams"])

    def normalize(self, text: str) -> str:
        """Return the normalized team name for any known spelling.

        Raises:
            UnknownNameError: If `text` is not a spelling of any team in the map.
        """
        try:
            return self._index[lookup_key(text)]
        except KeyError:
            raise UnknownNameError(f"Unknown team {text!r}; rebuild the team map if it is a new team") from None
