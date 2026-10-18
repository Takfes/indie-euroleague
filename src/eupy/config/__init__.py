"""Project configuration: typed defaults plus an optional `config/config.toml` override."""

from eupy.config.defaults import Config
from eupy.config.loader import get_config, load_config

__all__ = ["Config", "get_config", "load_config"]
