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

    include_kaggle: bool = False  # True = merge the Kaggle history under the live deltas; False = deltas only
    base_season: str | None = None  # with include_kaggle: None = full history, else keep only this season_code


@dataclass(frozen=True)
class Logging:
    """Log-file settings."""

    path: str = "logs/log.log"  # relative to the repo root
    level: str = "INFO"


@dataclass(frozen=True)
class Config:
    """Root of the config tree."""

    acquisition: Acquisition = field(default_factory=Acquisition)
    transformation: Transformation = field(default_factory=Transformation)
    logging: Logging = field(default_factory=Logging)
