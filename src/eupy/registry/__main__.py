"""Registry CLI: lint the script headers (`check`), print the model (`show`) or render the catalogue / graph / dvc.yaml.

Usage:
    uv run python -m eupy.registry check   # exit 0 when consistent, 1 with one message per problem
    uv run python -m eupy.registry show    # scripts (execution order), datasets, sources; deterministic
    uv run python -m eupy.registry catalogue [--stdout]  # write docs/data-catalogue.md (or print it)
    uv run python -m eupy.registry graph [--stdout]      # write docs/data-graph.md (or print it)
    uv run python -m eupy.registry docs [--stdout]       # catalogue + graph
    uv run python -m eupy.registry dvc [--stdout]        # write dvc.yaml from pipelines.toml (or print it)
    uv run python -m eupy.registry html [--stdout]       # write docs/data-map.html (interactive lineage map)
    uv run python -m eupy.registry html --status         # also write ignored docs/data-map.status.html (DVC overlay)

`--root` points at another repo checkout (default: this one).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from graphlib import CycleError
from pathlib import Path

from eupy.registry.dvc_gen import PIPELINES_FILE, checked_pipelines, dvc_steps, load_wrappers, render_dvc
from eupy.registry.model import REPO_ROOT, Registry, RegistryError
from eupy.registry.render_catalogue import render_catalogue
from eupy.registry.render_graph import GraphError, render_graph
from eupy.registry.render_html import parse_dvc_status, render_html

HTML_FILE, HTML_STATUS_FILE = "data-map.html", "data-map.status.html"

DOCS = {
    "catalogue": ("data-catalogue.md", lambda reg, pipelines: render_catalogue(reg)),
    "graph": ("data-graph.md", render_graph),
}


def render(registry: Registry) -> str:
    """Plain-text dump of the model: sorted/topological, no timestamps, so reruns are byte-identical."""
    lines = ["Scripts (execution order)"]
    for name in registry.topo_order():
        s = registry.scripts[name]
        flags = f"final={str(s.final).lower()} impure={str(s.impure).lower()}"
        lines.append(f"  {name}  [{s.path}]  {flags}")
        lines.append(f"    inputs:   {', '.join(s.inputs) or 'none'}")
        lines.append(f"    outputs:  {', '.join(s.outputs) or 'none'}")
        if s.sources:
            lines.append(f"    sources:  {' | '.join(s.sources)}")
    lines += ["", "Datasets (stage, name)"]
    for d in sorted(registry.datasets.values(), key=lambda d: (d.stage, d.name)):
        lines.append(f"  [{d.stage}] {d.name}  {d.path}  final={str(d.final).lower()}")
        lines.append(f"    producer:  {d.producer or f'external: {d.origin}'}")
        lines.append(f"    consumers: {', '.join(d.consumers) or 'none'}")
        if d.refresh:
            lines.append(f"    refresh:   {d.refresh}")
    lines += ["", "Sources"]
    for src in registry.sources.values():
        feeds = [*src.scripts, *src.datasets]
        lines.append(f"  {src.label}  ->  {', '.join(feeds)}")
    return "\n".join(lines) + "\n"


def _emit(text: str, path: Path, stdout: bool) -> None:
    """Print `text` (`--stdout`) or write it to `path`."""
    if stdout:
        sys.stdout.write(text)
    else:
        path.write_text(text, encoding="utf-8")


def _dvc_status(root: Path, steps: list[str]) -> dict[str, str] | None:
    """Fresh/stale per DVC step from `uv run dvc status --json`; `None` (with a message) when it cannot run."""
    cmd = ["uv", "run", "dvc", "status", "--json"]
    try:
        proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=120, check=True)  # noqa: S603
        return parse_dvc_status(proc.stdout, steps)
    except subprocess.CalledProcessError as exc:
        reason = f"dvc status exited {exc.returncode}: {exc.stderr.strip()[:200]}"
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        reason = str(exc)
    print(f"warning: no DVC status overlay ({reason})", file=sys.stderr)
    return None


def _write_html(registry: Registry, pipelines: dict, root: Path, stdout: bool, status: bool) -> int:
    """Write `docs/data-map.html` (and, with `status`, the git-ignored DVC overlay variant)."""
    try:
        wrappers = load_wrappers(root / PIPELINES_FILE)
        page = render_html(registry, pipelines, wrappers, root)
        # `--stdout` prints only the plain page, so the (slow) `dvc status` subprocess would be wasted.
        overlay = (
            _dvc_status(root, [step.name for step in dvc_steps(registry, pipelines, root)])
            if status and not stdout
            else None
        )
        variant = render_html(registry, pipelines, wrappers, root, overlay) if overlay else None
    except (GraphError, RegistryError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    _emit(page, root / "docs" / HTML_FILE, stdout)
    if variant is not None and not stdout:
        _emit(variant, root / "docs" / HTML_STATUS_FILE, False)
    return 0


def _load(args: argparse.Namespace) -> tuple[Registry, dict, str]:
    """Build the registry, the checked pipelines (when the command needs them) and, for `dvc`, the rendered yaml."""
    registry = Registry.from_repo(args.root)
    # Pipeline membership is linted by `check` and needed by `dvc`; rendering happens before any write.
    pipelines = (
        checked_pipelines(registry, args.root / PIPELINES_FILE, args.root)
        if args.command in ("check", "dvc", "graph", "docs", "html")
        else {}
    )
    dvc_yaml = render_dvc(registry, pipelines, args.root) if args.command == "dvc" else ""
    return registry, pipelines, dvc_yaml


def _write_docs(registry: Registry, pipelines: dict, root: Path, names: list[str], stdout: bool) -> int:
    """Render the named docs (`DOCS` keys) and then write them, so a failure never leaves the docs out of sync."""
    rendered: list[tuple[str, str]] = []
    for name in names:
        filename, renderer = DOCS[name]
        try:
            rendered.append((filename, renderer(registry, pipelines)))
        except GraphError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
    for filename, text in rendered:
        _emit(text, root / "docs" / filename, stdout)
    return 0


def _run(args: argparse.Namespace, registry: Registry, pipelines: dict, dvc_yaml: str) -> int:
    """Execute `args.command` against an already-built registry; returns the exit code."""
    if args.command == "show":
        sys.stdout.write(render(registry))
    elif args.command == "dvc":
        _emit(dvc_yaml, args.root / "dvc.yaml", args.stdout)
    elif args.command == "html":
        return _write_html(registry, pipelines, args.root, args.stdout, args.status)
    elif args.command in ("catalogue", "graph", "docs"):
        return _write_docs(
            registry, pipelines, args.root, list(DOCS) if args.command == "docs" else [args.command], args.stdout
        )
    else:
        print(
            f"ok: {len(registry.scripts)} scripts, {len(registry.datasets)} datasets, {len(registry.sources)} sources, "
            f"{len(pipelines)} pipelines"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run `check`, `show`, `catalogue`, `graph`, `docs`, `dvc` or `html`; returns the process exit code."""
    parser = argparse.ArgumentParser(prog="python -m eupy.registry", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["check", "show", "catalogue", "graph", "docs", "dvc", "html"])
    parser.add_argument(
        "--stdout", action="store_true", help="catalogue/graph/docs/dvc: print instead of writing the file"
    )
    parser.add_argument(
        "--status", action="store_true", help="html: also write the ignored DVC fresh/stale overlay variant"
    )
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="repo root (default: this checkout)")
    args = parser.parse_args(argv)

    try:
        loaded = _load(args)
    except RegistryError as exc:
        for problem in exc.problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1
    except CycleError as exc:
        nodes = " -> ".join(exc.args[1]) if len(exc.args) > 1 else str(exc)
        print(f"error: dependency cycle: {nodes}", file=sys.stderr)
        return 1
    return _run(args, *loaded)


if __name__ == "__main__":
    sys.exit(main())
