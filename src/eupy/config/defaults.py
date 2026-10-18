"""Built-in defaults of the project config: frozen dataclasses, always importable.

These values are the fallback when `config/config.toml` is absent and the base that a present file
overrides key by key (see `eupy.config.loader`). Only settings that change pipeline behaviour live here.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Acquisition:
    """Fetcher settings."""

    season: str = "E2026"  # EuroLeague season code the live/schedule fetchers target by default
    request_delay: float = 0.3  # polite-client pause (seconds) between live-API requests
    max_gap: int = 5  # consecutive empty/unplayed gamecodes that end a live-headers run
    kaggle_dataset: str = "babissamothrakis/euroleague-datasets"
    fantasy_rounds: str = "all"  # fantasy stats fetch: "all" (season in one pass) or rounds such as "1-4,6"
    fantasy_stats_type: str | None = None  # "avg" or "tot" for the all-rounds fetch; None = avg


@dataclass(frozen=True)
class Transformation:
    """Transform settings."""

    names_mode: str = "continue"  # names check: "continue" = report and go on; "pause" = stop on case-3 players
    include_kaggle: bool = False  # True = merge the Kaggle history under the live deltas; False = deltas only
    base_season: str | None = None  # with include_kaggle: None = full history, else keep only this season_code


@dataclass(frozen=True)
class Logging:
    """Log-file settings."""

    path: str = "logs/log.log"  # relative to the repo root
    level: str = "INFO"


@dataclass(frozen=True)
class Optimization:
    """Squad-optimizer (hyper)parameters, read by `scripts/optimize.py`."""

    target: str = "exp_pir"  # workbook column the squad is optimised for (e.g. "exp_pir", "pir_mean")
    robustness_lambda: float = 0.0  # penalty per unit of the outcome's standard deviation (0 = off)
    upside_beta: float = 0.0  # reward per unit of (p90 - target), positive part (0 = off)
    downside_gamma: float = 0.0  # penalty per unit of (target - p10), positive part (0 = off)
    cash: float = 100.0  # credits in the bank before trading (round 1: the whole budget)
    max_trades: int = 4  # max buys this round; ignored when unlimited_trades is true
    unlimited_trades: bool = False  # true = no trade limit (round 1 / free windows)
    credit_value: float = 0.0  # points per credit of expected price growth (~3.0 early season, 0.0 late)
    min_players_per_turn: list[int] = field(
        default_factory=list
    )  # [3, 3] = squad has >= 3 turn-1 and >= 3 turn-2 players
    solver: str = "appsi_highs"  # Pyomo solver name
    # Passed to the solver as is; a [optimization.solver_options] table replaces this dict, it is not merged.
    # threads=1 + fixed seed = reproducible; mip_rel_gap=0 = proven optimality (HiGHS defaults to 0.01%).
    solver_options: dict[str, float | int | str] = field(
        default_factory=lambda: {"threads": 1, "random_seed": 42, "mip_rel_gap": 0.0}
    )


@dataclass(frozen=True)
class Config:
    """Root of the config tree."""

    acquisition: Acquisition = field(default_factory=Acquisition)
    transformation: Transformation = field(default_factory=Transformation)
    logging: Logging = field(default_factory=Logging)
    optimization: Optimization = field(default_factory=Optimization)
