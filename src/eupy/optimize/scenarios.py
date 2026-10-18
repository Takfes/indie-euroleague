"""Scenarios for the squad optimizer: a starting team plus optional config overrides, read from one TOML file.

`config/scenarios.toml` holds one table per scenario. The table key names the scenario and its output file
(`data/stage_99/optimizer_output_<key>.xlsx`), so keys use letters, digits and underscores only:

    [team_1]
    roster = ["Yam Madar", "Mathias Lessort", ...]   # the players you own; [] = a fresh squad
    cash = 3.5                                        # any other key is an [optimization] key
    upside_beta = 1.0

`apply_scenario` returns a copy of the input table with `in_prev_roster` set from the roster and the run
parameters with the overrides applied (code defaults < `config.toml` < scenario). Nothing is written to disk and
the input workbook is never changed. A scenario without `roster` keeps the workbook's `in_prev_roster` column.
This module is pure (no solving, no output); `optimize_squad.main --scenarios` drives it. It is a registry library
module (no I/O header): the scenarios file is hand-edited config, not a catalogued dataset.
"""

from __future__ import annotations

import difflib
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from eupy.config.defaults import Optimization
from eupy.config.loader import _apply

REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIOS_PATH = REPO_ROOT / "config" / "scenarios.toml"
ROSTER_KEY = "roster"
KEY_PATTERN = re.compile(r"^[A-Za-z0-9_]+$")


@dataclass(frozen=True)
class Scenario:
    """One scenario: `roster` is None when the workbook's own `in_prev_roster` column should be used."""

    key: str
    roster: tuple[str, ...] | None
    overrides: dict[str, Any] = field(default_factory=dict)


def load_scenarios(path: Path = SCENARIOS_PATH) -> dict[str, Scenario]:
    """Read and shape-check the scenarios file (values are validated later, against the data and the config).

    Args:
        path: The TOML file.

    Returns:
        The scenarios in file order, by key.

    Raises:
        FileNotFoundError: If the file is missing.
        ValueError: If the file has no scenarios, a key is not letters/digits/underscores, an entry is not a table,
            or `roster` is not a list of distinct names.
    """
    if not path.is_file():
        raise FileNotFoundError(f"Scenarios file {path} not found. Create it with one [<key>] table per scenario.")
    with path.open("rb") as f:
        raw = tomllib.load(f)
    if not raw:
        raise ValueError(f"{path} has no scenarios. Add a table such as [team_1] with a `roster` list.")
    scenarios = {}
    for key, body in raw.items():
        if not KEY_PATTERN.fullmatch(key):
            raise ValueError(
                f"Scenario key {key!r} must use letters, digits and underscores only (it names the output file), "
                f"e.g. {re.sub(r'[^A-Za-z0-9_]', '_', key)!r}."
            )
        if not isinstance(body, dict):
            raise ValueError(  # noqa: TRY004 -- callers collect ValueErrors
                f"Scenario {key!r} must be a table ([{key}]), got {type(body).__name__}."
            )
        overrides = {k: v for k, v in body.items() if k != ROSTER_KEY}
        roster = body.get(ROSTER_KEY)
        if roster is not None:
            if not isinstance(roster, list) or not all(isinstance(n, str) for n in roster):
                raise ValueError(f"[{key}] roster must be a list of player names (strings); [] means a fresh squad.")
            if len(set(roster)) != len(roster):
                dupes = sorted({n for n in roster if roster.count(n) > 1})
                raise ValueError(f"[{key}] roster lists {dupes} more than once.")
            roster = tuple(roster)
        scenarios[key] = Scenario(key, roster, overrides)
    return scenarios


def select_scenarios(scenarios: dict[str, Scenario], selector: str) -> dict[str, Scenario]:
    """Pick scenarios by a comma-separated list of keys, or all of them with `all`.

    Raises:
        ValueError: If a requested key is not in the file (the message lists the known keys).
    """
    if selector.strip() == "all":
        return dict(scenarios)
    wanted = [k.strip() for k in selector.split(",") if k.strip()]
    unknown = [k for k in wanted if k not in scenarios]
    if unknown or not wanted:
        raise ValueError(f"Unknown scenario(s) {unknown or selector!r}. Known: {sorted(scenarios)}.")
    return {k: scenarios[k] for k in wanted}


def apply_scenario(
    df: pd.DataFrame,
    cfg: Optimization,
    scenario: Scenario,
    player_column: str = "player",
    roster_column: str = "in_prev_roster",
) -> tuple[pd.DataFrame, Optimization]:
    """The input table and run parameters of one scenario; both arguments are left unchanged.

    Args:
        df: The input table as read from the workbook.
        cfg: The run parameters from `config.toml` (plus defaults).
        scenario: The scenario to apply.
        player_column: The workbook column holding the player names the roster is matched against.
        roster_column: The workbook column that marks the owned players (1) and is replaced by the roster.

    Returns:
        A copy of `df` with `roster_column` = 1 for the roster (0 for everybody else) when the scenario has a roster,
        and `cfg` with the scenario's overrides applied.

    Raises:
        ValueError: If a roster name is not in the table (the message suggests close matches) or an override key
            is not an [optimization] key.
    """
    try:
        new_cfg = _apply(cfg, scenario.overrides, f"{scenario.key}")
    except ValueError as err:
        raise ValueError(f"[{scenario.key}] {err}") from None
    if scenario.roster is None:
        return df.copy(), new_cfg
    known = set(df[player_column].astype(str))
    missing = [name for name in scenario.roster if name not in known]
    if missing:
        hints = "; ".join(
            f"{name!r}: did you mean {difflib.get_close_matches(name, known, n=3, cutoff=0.6) or 'no close match'}?"
            for name in missing
        )
        raise ValueError(f"[{scenario.key}] roster names not in the input: {hints}")
    out = df.copy()
    out[roster_column] = out[player_column].astype(str).isin(scenario.roster).astype(int)
    return out, new_cfg
