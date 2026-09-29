#!/usr/bin/env python3
"""Add a `turn` (T1 / T2) to the raw season schedule and derive a per-team round/turn table.

A fantasy round (`gameday`) is played over two or three calendar days. Its games are split into two
turns by date: the round's **latest** date is `T2`, every earlier date is `T1`. This matches the
calendar: Thu/Fri rounds -> T1 = Thu, T2 = Fri; Tue/Wed rounds -> T1 = Tue, T2 = Wed; the few
Wed/Thu/Fri rounds get their lone Wednesday game(s) in T1 alongside Thursday, T2 = Friday. A round
whose games all fall on one date would be all `T1` (none in E2026). The rule is an assumption, not
an API field -- change `assign_turns` if the fantasy game defines turns differently.

Two outputs, both from the raw `date` column (parsed only to compare dates; written verbatim):

* `schedule` -- the raw schedule (same columns, same row order) plus a trailing `turn` column.
* `team_round_turn` -- one row per team per round: `team` (club code, `homecode`/`awaycode`),
  `round` (the round number = the raw `gameday`; NOT the raw `round` column, which is the phase
  label `RS`), `turn`. Sorted by `round`, `team`. Each team plays at most once per round.

Usage:
    python src/eupy/transform/build_schedule_turns.py [--season E2026] [--input PATH]
        [--out-schedule PATH] [--out-team-turns PATH]

Inputs:
  - euroleague_schedule/schedule: data/raw_data/euroleague_schedule/
Sources: none
Outputs:
  - schedule: data/stage_01/schedule.csv
  - team_round_turn: data/stage_01/team_round_turn.csv
Final: true
Impure: false
Notes: Input file is schedule_{season}.csv (--season, default E2026; --input overrides). Both outputs are
  overwritten each run. Column notes. schedule: the raw schedule (20 columns, verbatim, same row
  order) plus a trailing turn (T1 / T2). Within each gameday, the latest date is T2 and every
  earlier date is T1 (single-date round -> all T1). The rule is an assumption, not an API field.
  team_round_turn: columns team (club code from homecode / awaycode), round (= raw gameday; not the
  raw round column, which is the phase label RS), turn. One row per team per round, sorted by round,
  team.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
RAW_DIR = REPO_ROOT / "data" / "raw_data" / "euroleague_schedule"
DEFAULT_OUT_SCHEDULE = REPO_ROOT / "data" / "stage_01" / "schedule.csv"
DEFAULT_OUT_TEAM_TURNS = REPO_ROOT / "data" / "stage_01" / "team_round_turn.csv"

REQUIRED_COLUMNS = ("gameday", "date", "homecode", "awaycode")
DATE_FORMAT = "%b %d, %Y"  # raw `date`, e.g. "Sep 24, 2026"


def assign_turns(schedule: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of `schedule` with a trailing `turn` column (`T1` / `T2`).

    Within each `gameday`, the latest date is `T2` and all earlier dates are `T1`; a round with a
    single date is entirely `T1`.

    Raises:
        ValueError: If a required column is missing or a `date` does not parse as `Mon DD, YYYY`.
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in schedule.columns]
    if missing:
        raise ValueError(f"Schedule is missing required columns {missing}; got {list(schedule.columns)}.")
    try:
        dates = pd.to_datetime(schedule["date"], format=DATE_FORMAT)
    except ValueError as e:
        raise ValueError(f"Schedule `date` must look like 'Sep 24, 2026': {e}") from e
    by_round = dates.groupby(schedule["gameday"])
    is_t2 = (dates == by_round.transform("max")) & (dates != by_round.transform("min"))
    turn = pd.Series("T1", index=schedule.index).mask(is_t2, "T2")
    return schedule.assign(turn=turn)


def team_round_turns(schedule_with_turns: pd.DataFrame) -> pd.DataFrame:
    """Reshape a turn-annotated schedule into one `(team, round, turn)` row per team per round.

    Raises:
        ValueError: If a team appears more than once in the same round.
    """
    sides = [
        schedule_with_turns[[code, "gameday", "turn"]].set_axis(["team", "round", "turn"], axis=1)
        for code in ("homecode", "awaycode")
    ]
    out = pd.concat(sides, ignore_index=True).sort_values(["round", "team"], kind="stable").reset_index(drop=True)
    dupes = out[out.duplicated(["team", "round"], keep=False)]
    if not dupes.empty:
        pairs = dupes[["team", "round"]].drop_duplicates().values.tolist()
        raise ValueError(f"Teams playing more than once in a round, as [team, round]: {pairs}")
    return out


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the CLI flags."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", default="E2026", help="Season code, e.g. E2026 (default: %(default)s)")
    parser.add_argument(
        "--input", type=Path, help="Raw schedule CSV (default: raw_data/euroleague_schedule/schedule_{season}.csv)"
    )
    parser.add_argument("--out-schedule", type=Path, default=DEFAULT_OUT_SCHEDULE, help="(default: %(default)s)")
    parser.add_argument("--out-team-turns", type=Path, default=DEFAULT_OUT_TEAM_TURNS, help="(default: %(default)s)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: read the raw schedule, add turns, write both stage-01 CSVs."""
    args = parse_args(argv)
    src = args.input or RAW_DIR / f"schedule_{args.season}.csv"
    # dtype=str keeps raw values verbatim (no "true" -> True, no time parsing); only gameday is typed.
    schedule = pd.read_csv(src, dtype=str, keep_default_na=False).astype({"gameday": int})
    with_turns = assign_turns(schedule)
    team_turns = team_round_turns(with_turns)
    for df, path in ((with_turns, args.out_schedule), (team_turns, args.out_team_turns)):
        path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(path, index=False)
    print(
        f"Wrote {len(with_turns)} games to {args.out_schedule} and {len(team_turns)} team-rounds to {args.out_team_turns}"
    )


if __name__ == "__main__":
    main()
