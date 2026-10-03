"""Verdict batches: the tracked, append-only record of agent decisions on a crosswalk.

The `resolve_*` scripts build their crosswalk from raw data alone, then apply every agent verdict
recorded in a verdict directory (`data/curated/<crosswalk>_verdicts/`). Each directory holds
immutable batch files, applied in file-name order (hence the zero-padded `0001_...json` prefix);
a later batch wins on the same key.

Batch file shape (one JSON object per file):
    {
        "ingested_at": "2026-09-30",
        "source": "...",                 # where the verdicts came from
        "verdicts": [ {...}, ... ]       # verdict records, see below
    }

Verdict record (the schema `apply_*_verdicts.py` and the resolve-* skills already use):
    {
        "<key>": "...",                  # row key: `name` (player/team) or `player_id` (fantasy)
        "match_status": "confirmed",     # confirmed | rejected | no_match
        "<target>": "...",               # matched name: required on confirmed, empty otherwise
        "match_score": 95.0,             # optional; numbers render as "95.0", strings verbatim
        "matched_by": "agent",           # optional, defaults to "agent"
        "notes": "..."                   # required rationale
    }

Library module (no I/O header): imported by the three resolvers and the three `apply_*_verdicts`
ingest scripts (`ingest_batch` / `run_ingest_cli`).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TERMINAL_STATUSES = {"confirmed", "rejected", "no_match"}


def load_batches(directory: Path) -> list[dict[str, Any]]:
    """Load every `*.json` batch in `directory`, in file-name order, and return their verdict records.

    Raises:
        FileNotFoundError: If `directory` doesn't exist (a silently empty rebuild would drop verdicts).
        ValueError: If a batch file isn't a `{ingested_at, source, verdicts: [objects]}` JSON object.
    """
    if not directory.is_dir():
        raise FileNotFoundError(f"Verdict directory not found: {directory}")
    records: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json"), key=lambda p: p.name):
        batch = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(batch, dict):
            raise ValueError(f"{path}: a verdict batch must be a JSON object, got {type(batch).__name__}")  # noqa: TRY004 -- bad data, not a caller type error
        for field in ("ingested_at", "source"):
            if not isinstance(batch.get(field), str) or not batch[field]:
                raise ValueError(f"{path}: batch needs a non-empty string {field!r}")
        verdicts = batch.get("verdicts")
        if not isinstance(verdicts, list) or not all(isinstance(v, dict) for v in verdicts):
            raise ValueError(f"{path}: batch 'verdicts' must be a list of objects")
        records.extend(verdicts)
    return records


def merge_verdicts(records: list[dict[str, Any]], key_field: str, target_field: str) -> dict[str, dict[str, Any]]:
    """Validate verdict records and merge them by key; a later record wins on the same key.

    Raises:
        ValueError: On a missing key, a status outside `TERMINAL_STATUSES`, `confirmed` without
            `target_field` (or any other status with one), or a missing `notes` rationale.
    """
    merged: dict[str, dict[str, Any]] = {}
    for verdict in records:
        if key_field not in verdict:
            raise ValueError(f"Verdict has no {key_field!r}: {verdict!r}")
        key = str(verdict[key_field])
        status = verdict.get("match_status")
        if status not in TERMINAL_STATUSES:
            raise ValueError(
                f"Verdict for {key!r}: match_status must be one of {sorted(TERMINAL_STATUSES)}, got {status!r}"
            )
        if (status == "confirmed") != bool(verdict.get(target_field, "")):
            raise ValueError(f"Verdict for {key!r}: {target_field} is required for, and only allowed on, 'confirmed'")
        if not verdict.get("notes"):
            raise ValueError(f"Verdict for {key!r} has no notes -- every verdict needs a brief rationale")
        merged[key] = verdict
    return merged


def apply_verdicts(
    rows_by_key: dict[str, dict[str, str]], verdicts_by_key: dict[str, dict[str, Any]], target_field: str
) -> None:
    """Overwrite the resolution fields of each keyed row with its verdict, in place.

    Verdicts whose key isn't in `rows_by_key` (e.g. a player dropped from a newer snapshot) are
    skipped: batches accumulate across snapshots, so a stale key is not an error.
    """
    for key, verdict in verdicts_by_key.items():
        row = rows_by_key.get(key)
        if row is None:
            continue
        score = verdict.get("match_score", "")
        row |= {
            "match_status": verdict["match_status"],
            target_field: verdict.get(target_field, ""),
            "match_score": f"{score:.1f}" if isinstance(score, int | float) else str(score),
            "matched_by": verdict.get("matched_by", "agent"),
            "notes": verdict["notes"],
        }


def write_batch(path: Path, verdicts: list[dict[str, Any]], *, ingested_at: str, source: str) -> None:
    """Write one batch file deterministically (indent=2, sorted keys, trailing newline)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    batch = {"ingested_at": ingested_at, "source": source, "verdicts": verdicts}
    path.write_text(json.dumps(batch, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def _read_verdict_list(path: Path) -> list[dict[str, Any]]:
    """Read a plain JSON list of verdict records, raising `ValueError` with a clear message otherwise."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Cannot read verdicts file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError(f"{path}: verdicts must be a JSON list of objects, got {type(data).__name__}")  # noqa: TRY004 -- bad data
    if not data:
        raise ValueError(f"{path}: verdict list is empty -- nothing to ingest")
    if not all(isinstance(v, dict) for v in data):
        raise ValueError(f"{path}: every verdict must be a JSON object")
    return data


def _next_batch_path(batches_dir: Path, slug: str) -> Path:
    """Return `NNNN_<slug>.json` with NNNN = highest existing numeric prefix + 1 (1 when none)."""
    numbers = [int(m.group(1)) for p in batches_dir.glob("*.json") if (m := re.match(r"(\d+)_", p.name))]
    return batches_dir / f"{max(numbers, default=0) + 1:04d}_{slug}.json"


def ingest_batch(
    verdicts_path: Path,
    batches_dir: Path,
    key_field: str,
    target_field: str,
    *,
    label: str | None = None,
    today: str | None = None,
) -> Path:
    """Validate a verdicts JSON list and write it as a NEW batch file in `batches_dir`.

    Everything is validated before anything is written, so an invalid input leaves the directory
    untouched. Existing batches are never modified: the new file is opened with exclusive create.

    Args:
        verdicts_path: Plain JSON list of verdict records.
        batches_dir: Batch directory (created if missing).
        key_field: Row-key field of the crosswalk (`name` or `player_id`).
        target_field: Matched-name field required on `confirmed` verdicts.
        label: Slug for the file name; defaults to the sanitized input file stem.
        today: Ingest date `YYYY-MM-DD` (UTC today when omitted; injectable for tests).

    Returns:
        Path of the written batch file.

    Raises:
        ValueError: Unreadable/non-JSON/non-list/empty input, or a record violating the verdict schema.
        FileExistsError: The target batch file already exists.
    """
    records = _read_verdict_list(verdicts_path)
    merge_verdicts(records, key_field, target_field)  # schema validation only
    slug = re.sub(r"[^a-z0-9]+", "-", (label or verdicts_path.stem).lower()).strip("-")
    if not slug:
        raise ValueError(f"Cannot derive a batch label from {label or verdicts_path.stem!r}; pass --label SLUG")
    batches_dir.mkdir(parents=True, exist_ok=True)
    path = _next_batch_path(batches_dir, slug)
    batch = {
        "ingested_at": today or datetime.now(UTC).strftime("%Y-%m-%d"),
        "source": verdicts_path.name,
        "verdicts": records,
    }
    payload = json.dumps(batch, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    try:
        with path.open("x", encoding="utf-8") as f:
            f.write(payload)
    except FileExistsError as exc:
        raise FileExistsError(f"Refusing to overwrite existing batch {path}") from exc
    return path


def run_ingest_cli(description: str, default_dir: Path, key_field: str, target_field: str) -> int:
    """Shared CLI for the `apply_*_verdicts` scripts; returns the process exit code (0 ok, 1 rejected)."""
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--verdicts", type=Path, required=True, help="JSON file (list) of verdict records")
    parser.add_argument("--batches-dir", type=Path, default=default_dir, help="Verdict batch directory")
    parser.add_argument("--label", help="Batch file-name slug (default: input file stem)")
    args = parser.parse_args()
    try:
        path = ingest_batch(args.verdicts, args.batches_dir, key_field, target_field, label=args.label)
    except (ValueError, FileExistsError) as exc:
        print(f"Rejected, nothing written: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {path}")
    return 0
