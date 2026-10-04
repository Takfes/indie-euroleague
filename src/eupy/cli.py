"""`eupy` command line: run pipelines under DVC and refresh the `data/stage_99/` links.

Usage (from the repo root, `uv run eupy ...`):
    eupy run <pipeline|wrapper> [--dry-run]
    eupy link-final

Targets come from `pipelines.toml`: a pipeline name, or a wrapper (an ordered list of pipelines).

* `dvc = true` pipeline: `uv run dvc repro --single-item <steps>` for its steps (pure scripts and fetchers),
  in dependency order. `--single-item` keeps a run to exactly those steps: upstream steps (the fetchers
  included) never run unless their pipeline is part of the target, so a run is offline unless the target
  includes a fetcher pipeline (`fetch_raw`, or a wrapper such as `full`).
* A *fetcher* is an impure script located under `src/eupy/fetchers/`. Other impure scripts (the `entity`
  `apply_*_verdicts` ingest scripts, which need `--verdicts`) are never run by `eupy run`.
* Before `dvc repro`, a precheck verifies that every external input (stage 0: raw data / curated) of the
  steps about to run exists on disk or is produced by one of them -- DVC deletes a step's outputs before
  running it, so a step failing on a missing raw input would destroy untracked outputs. Skipped under `--dry-run`.
* `dvc = false` pipeline: no DVC steps; `eupy run` fails with a "not runnable under DVC yet" message.
* After a successful run, `link-final` refreshes `data/stage_99/`.

Plain DVC works too: `uv run dvc repro fetch_raw` fetches all raw data (and nothing else), and
`uv run dvc repro` runs everything, fetchers included.

Every command goes through one injectable `Runner`; `--dry-run` prints the commands instead of executing them.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Sequence
from graphlib import CycleError
from pathlib import Path

from eupy.registry.dvc_gen import (
    PIPELINES_FILE,
    Pipeline,
    checked_pipelines,
    dvc_steps,
    is_step_script,
    load_wrappers,
)
from eupy.registry.link_final import link_final
from eupy.registry.model import REPO_ROOT, Registry, RegistryError, is_stage0_path

Runner = Callable[[Sequence[str]], int]  # command -> exit code


class CliError(Exception):
    """A user-facing failure (unknown target, pipeline not runnable); printed as `error: ...`, exit 1."""


def resolve_target(target: str, pipelines: dict[str, Pipeline], wrappers: dict[str, tuple[str, ...]]) -> list[str]:
    """Pipeline names a target stands for (a wrapper expands to its members); `CliError` listing valid names."""
    if target in wrappers:
        return list(wrappers[target])
    if target in pipelines:
        return [target]
    raise CliError(
        f"unknown target {target!r}; pipelines: {', '.join(sorted(pipelines))}; wrappers: {', '.join(sorted(wrappers)) or 'none'}"
    )


def _steps(registry: Registry, pipelines: dict[str, Pipeline], names: list[str]) -> list[str]:
    """Step scripts of the selected `dvc = true` pipelines, in dependency order."""
    order = {name: i for i, name in enumerate(registry.topo_order())}
    steps = {s for n in names if pipelines[n].dvc for s in pipelines[n].scripts if is_step_script(registry, s)}
    return sorted(steps, key=order.__getitem__)


def missing_inputs(
    registry: Registry, pipelines: dict[str, Pipeline], names: list[str], root: Path
) -> list[tuple[str, str]]:
    """`(dataset, path)` of external inputs absent on disk for the steps `eupy run` would trigger.

    Only stage-0 datasets count (raw / curated), minus those produced by a selected step. Directories need
    only exist.
    """
    steps = _steps(registry, pipelines, names)
    produced = {ds for s in steps for ds in registry.scripts[s].outputs}
    wanted = {ds for s in steps for ds in registry.scripts[s].inputs if is_stage0_path(registry.path(ds))}
    return [
        (ds, registry.path(ds))
        for ds in sorted(wanted - produced)
        if not (root / registry.path(ds).rstrip("/")).exists()
    ]


def plan_run(registry: Registry, pipelines: dict[str, Pipeline], names: list[str]) -> list[list[str]]:
    """The one `dvc repro --single-item` command for the given pipelines' steps, in dependency order.

    Raises `CliError` when a selected pipeline is not a DVC pipeline or has no steps.
    """
    for name in names:
        if not pipelines[name].dvc:
            raise CliError(f"pipeline {name!r} is not runnable under DVC yet (dvc = false in {PIPELINES_FILE})")
    steps = _steps(registry, pipelines, names)
    if not steps:
        raise CliError("nothing to run (no DVC steps in the selected pipelines)")
    return [["uv", "run", "dvc", "repro", "--single-item", *steps]]


def _require_inputs(registry: Registry, pipelines: dict[str, Pipeline], names: list[str], root: Path) -> None:
    """Precheck: `CliError` listing every missing external input of the steps about to run."""
    if missing := missing_inputs(registry, pipelines, names, root):
        raise CliError(
            "missing external inputs; nothing was run (DVC would delete outputs before failing):\n"
            + "\n".join(f"  {ds}: {path}" for ds, path in missing)
            + "\nfetch them with `eupy run fetch_raw` (or `eupy run full`)"
        )


def subprocess_runner(root: Path) -> Runner:
    """Runner executing each command in `root`, streaming output; returns the exit code."""
    return lambda cmd: subprocess.run(list(cmd), cwd=root, check=False).returncode  # noqa: S603


def _print_report(lines: list[str]) -> None:
    for line in lines or ["link-final: nothing to change"]:
        print(line)


def _run_commands(commands: list[list[str]], execute: Runner, dry_run: bool) -> int:
    """Print and (unless `dry_run`) execute each command in order; stops at the first failure, returning its code."""
    for cmd in commands:
        print("$ " + " ".join(cmd), flush=True)
        if dry_run:
            continue
        try:
            code = execute(cmd)
        except OSError as exc:
            print(f"error: could not run {cmd[0]!r}: {exc}", file=sys.stderr)
            return 1
        if code:
            print(f"error: command failed with exit code {code}", file=sys.stderr)
            return code
    return 0


def main(argv: list[str] | None = None, runner: Runner | None = None, root: Path = REPO_ROOT) -> int:
    """Entry point; `runner` and `root` are injectable for tests. Returns the process exit code."""
    parser = argparse.ArgumentParser(prog="eupy", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run a pipeline or wrapper from pipelines.toml")
    run.add_argument("target", help="pipeline or wrapper name")
    run.add_argument("--dry-run", action="store_true", help="print the commands instead of running them")
    sub.add_parser("link-final", help="refresh data/stage_99/ symlinks from the registry")
    args = parser.parse_args(argv)

    try:
        registry = Registry.from_repo(root)
        if args.command == "link-final":
            _print_report(link_final(registry, root).lines())
            return 0
        pipelines = checked_pipelines(registry, root / PIPELINES_FILE)
        dvc_steps(registry, pipelines, root)  # fail early on unrunnable DVC definitions
        names = resolve_target(args.target, pipelines, load_wrappers(root / PIPELINES_FILE))
        commands = plan_run(registry, pipelines, names)
        if not args.dry_run:
            _require_inputs(registry, pipelines, names, root)
    except RegistryError as exc:
        for problem in exc.problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1
    except CycleError as exc:
        print(f"error: cycle: {exc}", file=sys.stderr)
        return 1
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if code := _run_commands(commands, runner or subprocess_runner(root), args.dry_run):
        return code
    if args.dry_run:
        print("$ eupy link-final  (skipped: dry run)")
        return 0
    try:
        _print_report(link_final(registry, root).lines())
    except (OSError, RegistryError) as exc:
        print(f"error: link-final failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
