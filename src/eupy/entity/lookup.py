"""Shared pieces of the name lookup classes (`TeamNames`, `PlayerNames`).

Library module (no I/O header, never run directly): the lookup errors and the key every spelling is reduced to.
"""

from __future__ import annotations

from eupy.entity.matching import normalize_name


class UnknownNameError(LookupError):
    """The spelling is not a variant of any entry in the map."""


class AmbiguousNameError(LookupError):
    """The spelling is a variant of more than one entry; more context (e.g. the club) is needed."""


def lookup_key(text: str) -> str:
    """Reduce a spelling to its lookup key: accents stripped, upper-case, letters and spaces only."""
    return normalize_name(text)
