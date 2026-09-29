"""Read pipeline-script I/O headers: the key-value block at the end of each module docstring.

The docstring is read with `ast` -- scripts are never imported, so a script with heavy or broken
imports still parses. Grammar (see `specs/spec-header-format.md` and `docs/quickstart.md`), keys in
this exact order, the last two optional:

    Inputs:            list of `- <dataset-name>: <path>` entries, or `none`
    Sources:           list of `- <free text>` entries, or `none`
    Outputs:           list of `- <dataset-name>: <path>` entries, or `none`
    Final:             true | false
    Impure:            true | false
    Refresh:           free text (optional)
    Notes:             free text (optional)

The block starts at the first docstring line beginning with `Inputs:` (column 0) and runs to the end
of the docstring. Any indented line belongs to the key above it: list items for the list keys,
continuation text for `Refresh` / `Notes`. Anything else -- unknown or out-of-order keys, blank lines
inside the block, templated or space-containing paths, duplicate names -- is a `HeaderError`.

Discovery rule (`discover_headers`): every `*.py` under `src/eupy/` except `__init__.py` is read.
A module with a block is a pipeline script wherever it lives. A module without a block is an error
only inside the pipeline dirs (`PIPELINE_DIRS`), unless it is one of the known library modules
(`LIBRARY_MODULES`); everywhere else (`devtools/`, this `registry/` package, ...) it is skipped.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

PIPELINE_DIRS = frozenset({"fetchers", "transform", "entity", "optimize"})
LIBRARY_MODULES = frozenset({"entity/matching.py", "entity/player_crosswalk.py", "entity/team_crosswalk.py"})

LIST_KEYS = ("Inputs", "Sources", "Outputs")
KEY_ORDER = ("Inputs", "Sources", "Outputs", "Final", "Impure", "Refresh", "Notes")
REQUIRED_KEYS = frozenset({"Inputs", "Sources", "Outputs", "Final", "Impure"})

_KEY_LINE = re.compile(r"^([A-Za-z]+):(?:\s(.*))?$")
_DATASET_NAME = re.compile(r"^[a-z0-9_]+(/[a-z0-9_]+)*$")
_PATH = re.compile(r"^[A-Za-z0-9_./-]+$")


class HeaderError(ValueError):
    """A module's I/O block is missing where required or does not follow the grammar."""


@dataclass(frozen=True)
class Header:
    """Parsed I/O block of one pipeline script.

    `inputs` / `outputs` are `(dataset-name, path)` pairs in declaration order; `sources` are the
    free-text external origins. `refresh` / `notes` are `None` when the key is absent.
    """

    inputs: tuple[tuple[str, str], ...]
    sources: tuple[str, ...]
    outputs: tuple[tuple[str, str], ...]
    final: bool
    impure: bool
    refresh: str | None = None
    notes: str | None = None


def find_block(docstring: str) -> str | None:
    """Return the block text (from the `Inputs:` line to the end), or `None` if there is none."""
    lines = docstring.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("Inputs:"):
            return "\n".join(lines[i:])
    return None


def parse_block(block: str) -> Header:
    """Parse a block (as returned by `find_block`) into a `Header`; raise `HeaderError` if malformed."""
    groups = _group_lines(block)
    keys = [k for k, _, _ in groups]
    if len(set(keys)) != len(keys) or keys != sorted(keys, key=KEY_ORDER.index):
        raise HeaderError(f"keys must appear once each in the order {', '.join(KEY_ORDER)}; got {', '.join(keys)}")
    missing = REQUIRED_KEYS - set(keys)
    if missing:
        raise HeaderError(f"missing required key(s): {', '.join(sorted(missing, key=KEY_ORDER.index))}")

    lists: dict[str, tuple[str, ...]] = {}
    flags: dict[str, bool] = {}
    texts: dict[str, str] = {}
    for key, inline, extra in groups:
        if key in LIST_KEYS:
            lists[key] = _parse_list(key, inline, extra)
        elif key in ("Final", "Impure"):
            if extra or inline not in ("true", "false"):
                raise HeaderError(f"{key}: expected a single 'true' or 'false', got {inline!r}")
            flags[key] = inline == "true"
        else:
            text = " ".join([inline, *extra]).strip()
            if not text:
                raise HeaderError(f"{key}: empty value")
            texts[key] = text

    return Header(
        inputs=_parse_datasets("Inputs", lists["Inputs"]),
        sources=lists["Sources"],
        outputs=_parse_datasets("Outputs", lists["Outputs"]),
        final=flags["Final"],
        impure=flags["Impure"],
        refresh=texts.get("Refresh"),
        notes=texts.get("Notes"),
    )


def _group_lines(block: str) -> list[tuple[str, str, list[str]]]:
    """Split a block into `(key, inline value, indented lines)` groups, one per key line."""
    groups: list[tuple[str, str, list[str]]] = []
    for n, line in enumerate(block.rstrip().splitlines(), start=1):
        if not line.strip():
            raise HeaderError(f"line {n}: blank line inside the I/O block")
        if line[0].isspace():
            if not groups:
                raise HeaderError(f"line {n}: indented line before any key")
            groups[-1][2].append(line.strip())
            continue
        m = _KEY_LINE.match(line)
        if not m or m.group(1) not in KEY_ORDER:
            raise HeaderError(f"line {n}: expected one of {', '.join(KEY_ORDER)}, got {line!r}")
        groups.append((m.group(1), (m.group(2) or "").strip(), []))
    return groups


def _parse_list(key: str, inline: str, extra: list[str]) -> tuple[str, ...]:
    """A list key is either `Key: none` alone or `Key:` followed by one or more `- item` lines."""
    if inline == "none" and not extra:
        return ()
    if inline or not extra:
        raise HeaderError(f"{key}: expected 'none' or a list of '- ...' lines")
    items = []
    for line in extra:
        if not line.startswith("- ") or not line[2:].strip():
            raise HeaderError(f"{key}: list lines must look like '- ...', got {line!r}")
        items.append(line[2:].strip())
    return tuple(items)


def _parse_datasets(key: str, items: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    """Split `name: path` entries and validate both parts; no duplicate names within one key."""
    pairs = []
    for item in items:
        name, sep, path = item.partition(": ")
        name, path = name.strip(), path.strip()
        if not sep or not _DATASET_NAME.match(name):
            raise HeaderError(f"{key}: expected '<dataset-name>: <path>', got {item!r}")
        if not _PATH.match(path) or path.startswith("/") or ".." in path.split("/"):
            raise HeaderError(f"{key}: {name}: path must be repo-relative with no templates or spaces, got {path!r}")
        pairs.append((name, path))
    names = [n for n, _ in pairs]
    if len(set(names)) != len(names):
        raise HeaderError(f"{key}: duplicate dataset name")
    return tuple(pairs)


def read_header(path: Path) -> Header | None:
    """Parse the module docstring of `path` (via `ast`, never importing it).

    Returns `None` when the docstring has no block; raises `HeaderError` when the block is malformed.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        raise HeaderError(f"cannot parse module: {exc}") from exc
    docstring = ast.get_docstring(tree, clean=False) or ""
    block = find_block(docstring)
    return None if block is None else parse_block(block)


def discover_headers(package_dir: Path) -> tuple[dict[Path, Header], list[str]]:
    """Read every module under `package_dir` (`src/eupy/`) per the discovery rule in the module doc.

    Returns `(headers by module path, problems)`; each problem is a human-readable message. Sorted
    iteration keeps the result deterministic.
    """
    headers: dict[Path, Header] = {}
    problems: list[str] = []
    for path in sorted(package_dir.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        rel = path.relative_to(package_dir).as_posix()
        try:
            header = read_header(path)
        except HeaderError as exc:
            problems.append(f"{rel}: {exc}")
            continue
        if header is not None:
            headers[path] = header
        elif rel.split("/")[0] in PIPELINE_DIRS and rel not in LIBRARY_MODULES:
            problems.append(f"{rel}: pipeline script has no I/O block (docstring line starting 'Inputs:')")
    return headers, problems
