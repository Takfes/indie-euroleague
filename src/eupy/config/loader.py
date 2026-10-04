"""Load the project config: built-in defaults overridden by the user's `config/config.toml`.

`get_config()` is cached and never fails on a missing file (defaults apply). A present file overrides
defaults key by key; an unknown section or key raises `ValueError` so typos are not silently ignored.
"""

from __future__ import annotations

import tomllib
from dataclasses import fields, is_dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any

from eupy.config.defaults import Config

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = REPO_ROOT / "config" / "config.toml"


def _apply(section: Any, overrides: dict[str, Any], where: str) -> Any:
    """Return `section` (a frozen dataclass) with `overrides` applied recursively."""
    known = {f.name for f in fields(section)}
    unknown = sorted(set(overrides) - known)
    if unknown:
        raise ValueError(f"Unknown config key(s) {unknown} in [{where}]; known: {sorted(known)}")
    updates = {}
    for name, value in overrides.items():
        current = getattr(section, name)
        if is_dataclass(current):
            if not isinstance(value, dict):
                raise ValueError(f"[{where}.{name}] must be a table")
            updates[name] = _apply(current, value, f"{where}.{name}")
        else:
            updates[name] = value
    return replace(section, **updates)


def load_config(path: Path = CONFIG_PATH) -> Config:
    """Defaults, overridden by `path` when it exists."""
    config = Config()
    if not path.exists():
        return config
    with path.open("rb") as f:
        return _apply(config, tomllib.load(f), "root")


@lru_cache(maxsize=1)
def get_config() -> Config:
    """Process-wide config (read once)."""
    return load_config()
