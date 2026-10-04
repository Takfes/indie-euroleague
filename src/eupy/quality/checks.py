"""Data-quality checks run once per script output, right before it is written.

`validate(rows, key, name)` finds empty rows (every value blank) and duplicate keys, logs every
violation as one line to the shared log file, and raises `DataQualityError` so the script (and any
DVC stage running it) fails. `key` is a column name or a sequence of column names (composite key).
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from eupy.logging_setup import get_logger

MAX_LISTED = 10  # keys/rows quoted per violation line; the count is always exact


class DataQualityError(ValueError):
    """A dataset failed validation; the message summarises the violations."""


def _blank(value: Any) -> bool:
    return value is None or not str(value).strip()


def validate(
    rows: Sequence[Mapping[str, Any]],
    key: str | Sequence[str],
    name: str,
    logger: logging.Logger | None = None,
) -> None:
    """Raise `DataQualityError` if `rows` contain empty rows or duplicate `key` values.

    `name` is the dataset's name in log lines. Row numbers are 1-based data rows (header excluded).
    """
    log = logger or get_logger("quality")
    cols = [key] if isinstance(key, str) else list(key)
    problems: list[str] = []

    empty = [i for i, row in enumerate(rows, 1) if all(_blank(v) for v in row.values())]
    if empty:
        problems.append(f"{len(empty)} empty row(s), e.g. rows {empty[:MAX_LISTED]}")

    missing = [c for c in cols if rows and c not in rows[0]]
    if missing:
        problems.append(f"key column(s) {missing} not in the data")
    else:
        seen: dict[tuple[str, ...], list[int]] = defaultdict(list)
        for i, row in enumerate(rows, 1):
            if i not in set(empty):
                seen[tuple(str(row[c]) for c in cols)].append(i)
        dups = {k: v for k, v in seen.items() if len(v) > 1}
        if dups:
            sample = [(k if len(k) > 1 else k[0], v) for k, v in list(dups.items())[:MAX_LISTED]]
            problems.append(f"{len(dups)} duplicate key(s) on {cols}, e.g. (key, rows) {sample}")

    if not problems:
        log.info("%s: %d rows ok (key=%s)", name, len(rows), cols)
        return
    for problem in problems:
        log.error("%s: %s", name, problem)
    raise DataQualityError(f"{name} failed validation: " + "; ".join(problems))
