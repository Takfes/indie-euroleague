"""Team names: turn any known spelling of a team (name, schedule code, TV code) into one normalized name.

`TeamNames` is derived from the game schedule (`data/raw_data/game_schedule/schedule_*.csv`) each time it is
loaded: every home team contributes its name (`hometeam`) and its two code schemes (`homecode`, `hometv`), and the
normalized name is the schedule's `hometeam` as written. Every team plays at home, so the home columns cover all of
them. There is no map file: the schedule is the single source, so scripts using `TeamNames` declare it as input.

    teams = TeamNames.load()
    teams.normalize("PAO")      # 'Panathinaikos'
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from eupy.entity.matching import UnknownNameError, normalize_name

SCHEDULE_DIR = Path(__file__).resolve().parents[3] / "data" / "raw_data" / "game_schedule"


def load_schedule_rows(directory: Path) -> list[dict[str, str]]:
    """All rows of every `schedule_*.csv` in `directory`; raises if there is none."""
    paths = sorted(directory.glob("schedule_*.csv"))
    if not paths:
        raise FileNotFoundError(f"No schedule_*.csv in {directory}; run `uv run dvc repro fetch_game_schedule` first")
    rows: list[dict[str, str]] = []
    for path in paths:
        with path.open(newline="", encoding="utf-8") as f:
            rows.extend(csv.DictReader(f))
    return rows


def build_team_entries(rows: list[dict[str, str]]) -> list[dict[str, object]]:
    """One entry per `hometeam`, with its name, code and TV code as variants; sorted by name.

    Raises:
        ValueError: On an empty schedule, a team with two code pairs, or a spelling shared by two teams.
    """
    pairs: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for row in rows:
        pairs[row["hometeam"].strip()].add((row["homecode"].strip(), row["hometv"].strip()))
    if not pairs:
        raise ValueError("The schedule has no rows; refetch it with `uv run dvc repro fetch_game_schedule`")
    entries: list[dict[str, object]] = []
    for name, codes in sorted(pairs.items()):
        if len(codes) > 1:
            raise ValueError(f"Team {name!r} appears with several code pairs {sorted(codes)}; resolve in the schedule")
        code, tv = next(iter(codes))
        entries.append({"name": name, "variants": sorted({name, code, tv})})
    return entries


class TeamNames:
    """Lookup from every spelling of a team to its normalized name."""

    def __init__(self, entries: list[dict[str, object]]) -> None:
        """Index `entries` (`{"name": str, "variants": [str, ...]}`); a variant used by two teams is an error."""
        self._index: dict[str, str] = {}
        for entry in entries:
            name = str(entry["name"])
            for variant in [name, *entry["variants"]]:  # type: ignore[union-attr]
                key = normalize_name(str(variant))
                if self._index.setdefault(key, name) != name:
                    raise ValueError(f"Team spelling {variant!r} belongs to both {self._index[key]!r} and {name!r}")

    @classmethod
    def load(cls, schedule_dir: Path | None = None) -> TeamNames:
        """Derive the lookup from the schedule files in `schedule_dir` (default: `data/raw_data/game_schedule/`)."""
        return cls(build_team_entries(load_schedule_rows(schedule_dir or SCHEDULE_DIR)))

    def normalize(self, text: str) -> str:
        """Return the normalized team name for any known spelling.

        Raises:
            UnknownNameError: If `text` is not a spelling of any team in the schedule.
        """
        try:
            return self._index[normalize_name(text)]
        except KeyError:
            raise UnknownNameError(f"Unknown team {text!r}; refetch the schedule if it is a new team") from None
