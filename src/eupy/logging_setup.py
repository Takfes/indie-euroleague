"""Logging setup: one append-only log file plus the console, shared by every script.

`get_logger(script)` returns a logger that writes `timestamp | LEVEL | script | message` to the file
named in the config (`[logging] path`, default `logs/log.log` at the repo root) and to stderr.
"""

from __future__ import annotations

import logging
from pathlib import Path

from eupy.config import get_config
from eupy.config.loader import REPO_ROOT

FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def get_logger(script: str, path: Path | None = None) -> logging.Logger:
    """Logger named `script`, writing to `path` (default: the configured log file) and stderr.

    Idempotent per (script, path): repeated calls do not stack handlers.
    """
    cfg = get_config().logging
    log_path = path or REPO_ROOT / cfg.path
    logger = logging.getLogger(script)
    logger.setLevel(cfg.level.upper())
    logger.propagate = False
    if not any(getattr(h, "baseFilename", None) == str(log_path.resolve()) for h in logger.handlers):
        log_path.parent.mkdir(parents=True, exist_ok=True)
        for handler in (logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()):
            handler.setFormatter(logging.Formatter(FORMAT))
            logger.addHandler(handler)
    return logger
