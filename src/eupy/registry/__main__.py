"""Registry CLI: lint the script headers (`check`), print the model (`show`) or render the catalogue / graph.

Usage:
    uv run python -m eupy.registry check   # exit 0 when consistent, 1 with one message per problem
    uv run python -m eupy.registry show    # scripts (execution order), datasets, sources; deterministic
    uv run python -m eupy.registry catalogue [--stdout]  # write docs/data-catalogue.md (or print it)
    uv run python -m eupy.registry graph [--stdout]      # write docs/data-graph.md (or print it)
    uv run python -m eupy.registry docs [--stdout]       # catalogue + graph

`--root` points at another repo checkout (default: this one).
"""

from __future__ import annotations

import argparse
import sys
from graphlib import CycleError
from pathlib import Path

from eupy.registry.model import REPO_ROOT, Registry, RegistryError
from eupy.registry.render_catalogue import render_catalogue
from eupy.registry.render_graph import GraphError, render_graph

DOCS = {"catalogue": ("data-catalogue.md", render_catalogue), "graph": ("data-graph.md", render_graph)}


def render(registry: Registry) -> str:
    """Plain-text dump of the model: sorted/topological, no timestamps, so reruns are byte-identical."""
    lines = ["Scripts (execution order)"]
    for name in registry.topo_order():
        s = registry.scripts[name]
        flags = f"final={str(s.final).lower()} impure={str(s.impure).lower()}"
        lines.append(f"  {name}  [{s.path}]  {flags}")
        lines.append(f"    inputs:   {', '.join(s.inputs) or 'none'}")
        lines.append(f"    outputs:  {', '.join(s.outputs) or 'none'}")
        if s.in_place:
            lines.append(f"    in_place: {', '.join(s.in_place)}")
        if s.sources:
            lines.append(f"    sources:  {' | '.join(s.sources)}")
    lines += ["", "Datasets (stage, name)"]
    for d in sorted(registry.datasets.values(), key=lambda d: (d.stage, d.name)):
        lines.append(f"  [{d.stage}] {d.name}  {d.path}  final={str(d.final).lower()}")
        lines.append(f"    producer:  {d.producer or f'external: {d.origin}'}")
        if d.updaters:
            lines.append(f"    updaters:  {', '.join(d.updaters)}")
        lines.append(f"    consumers: {', '.join(d.consumers) or 'none'}")
        if d.refresh:
            lines.append(f"    refresh:   {d.refresh}")
    lines += ["", "Sources"]
    for src in registry.sources.values():
        feeds = [*src.scripts, *src.datasets]
        lines.append(f"  {src.label}  ->  {', '.join(feeds)}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Run `check`, `show`, `catalogue`, `graph` or `docs`; returns the process exit code."""
    parser = argparse.ArgumentParser(prog="python -m eupy.registry", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["check", "show", "catalogue", "graph", "docs"])
    parser.add_argument("--stdout", action="store_true", help="catalogue/graph/docs: print instead of writing the file")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="repo root (default: this checkout)")
    args = parser.parse_args(argv)

    try:
        registry = Registry.from_repo(args.root)
    except RegistryError as exc:
        for problem in exc.problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1
    except CycleError as exc:
        nodes = " -> ".join(exc.args[1]) if len(exc.args) > 1 else str(exc)
        print(f"error: cycle not on the in-place allowlist: {nodes}", file=sys.stderr)
        return 1

    if args.command == "show":
        sys.stdout.write(render(registry))
    elif args.command in ("catalogue", "graph", "docs"):
        rendered: list[tuple[str, str]] = []
        for name in DOCS if args.command == "docs" else [args.command]:
            filename, renderer = DOCS[name]
            try:
                rendered.append((filename, renderer(registry)))
            except GraphError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 1
        # Render everything before writing anything, so a failure never leaves the docs out of sync.
        for filename, text in rendered:
            if args.stdout:
                sys.stdout.write(text)
            else:
                (args.root / "docs" / filename).write_text(text, encoding="utf-8")
    else:
        print(
            f"ok: {len(registry.scripts)} scripts, {len(registry.datasets)} datasets, {len(registry.sources)} sources"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
