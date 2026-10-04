"""`eupy` command line: refresh the `data/stage_99/` links.

Usage (from the repo root, `uv run eupy ...`):
    eupy link-final

`link-final` creates relative symlinks in `data/stage_99/` for every produced `Final: true` dataset whose file
exists (missing ones are reported and skipped) and removes stale symlinks it no longer manages. Pipelines run
through DVC directly (`uv run dvc repro ...`, see `docs/run-guide.md`).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from eupy.registry.link_final import link_final
from eupy.registry.model import REPO_ROOT, Registry, RegistryError


def main(argv: list[str] | None = None, root: Path = REPO_ROOT) -> int:
    """Entry point; `root` is injectable for tests. Returns the process exit code."""
    parser = argparse.ArgumentParser(prog="eupy", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("link-final", help="refresh data/stage_99/ symlinks from the registry")
    parser.parse_args(argv)

    try:
        lines = link_final(Registry.from_repo(root), root).lines()
    except (OSError, RegistryError) as exc:
        problems = exc.problems if isinstance(exc, RegistryError) else [str(exc)]
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1
    for line in lines or ["link-final: nothing to change"]:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
