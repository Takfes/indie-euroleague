"""`eupy` command line: run pipelines under DVC and refresh the `data/stage_99/` links.

Usage (from the repo root, `uv run eupy ...`):
    eupy run <pipeline|wrapper> [--fetch] [--dry-run]
    eupy link-final

Targets come from `pipelines.toml`: a pipeline name, or a wrapper (an ordered list of pipelines; the order
between them is enforced by the DVC dependencies, not by the list).

* `dvc = true` pipeline: `uv run dvc repro <steps>` for its pure scripts (DVC also reruns upstream steps).
  Fully offline by default; `--fetch` first runs the pipeline's impure members (fetchers) directly with
  `uv run python <script>`, in dependency order -- those hit live sources.
* `dvc = false` pipeline: no DVC steps. `eupy run` runs only its impure members (so `eupy run acquire` runs the
  fetchers); a pipeline without any (`entity`, `optimize`) fails with a "not runnable under DVC yet" message.
* After a successful run, `link-final` refreshes `data/stage_99/`.

Every command goes through one injectable `Runner`; `--dry-run` prints the commands instead of executing them.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections.abc import Callable, Sequence
from graphlib import CycleError
from pathlib import Path

from eupy.registry.dvc_gen import PIPELINES_FILE, Pipeline, checked_pipelines, dvc_steps, load_wrappers
from eupy.registry.link_final import link_final
from eupy.registry.model import REPO_ROOT, Registry, RegistryError

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


def plan_run(registry: Registry, pipelines: dict[str, Pipeline], names: list[str], fetch: bool) -> list[list[str]]:
    """Commands for the given pipelines, in order: impure scripts (when fetching or non-DVC), then one `dvc repro`.

    Raises `CliError` for a non-DVC pipeline with nothing to run.
    """
    order = {name: i for i, name in enumerate(registry.topo_order())}
    fetchers: list[str] = []
    steps: list[str] = []
    for name in names:
        pipe = pipelines[name]
        impure = [s for s in pipe.scripts if registry.scripts[s].impure]
        if not pipe.dvc and not impure:
            raise CliError(
                f"pipeline {name!r} is not runnable under DVC yet (dvc = false in {PIPELINES_FILE}, no fetchers)"
            )
        if fetch or not pipe.dvc:
            fetchers += impure
        if pipe.dvc:
            steps += [s for s in pipe.scripts if not registry.scripts[s].impure]
    commands = [
        ["uv", "run", "python", registry.scripts[s].path]
        for s in sorted(dict.fromkeys(fetchers), key=order.__getitem__)
    ]
    if not commands and not steps:
        raise CliError("nothing to run (all members are impure fetchers); use --fetch")
    if steps:
        commands.append(["uv", "run", "dvc", "repro", *sorted(dict.fromkeys(steps), key=order.__getitem__)])
    return commands


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
    run.add_argument("--fetch", action="store_true", help="run the impure fetchers first (live sources)")
    run.add_argument("--dry-run", action="store_true", help="print the commands instead of running them")
    sub.add_parser("link-final", help="refresh data/stage_99/ symlinks from the registry")
    args = parser.parse_args(argv)

    try:
        registry = Registry.from_repo(root)
        if args.command == "link-final":
            _print_report(link_final(registry, root).lines())
            return 0
        pipelines = checked_pipelines(registry, root / PIPELINES_FILE)
        dvc_steps(registry, pipelines)  # fail early on unrunnable DVC definitions
        names = resolve_target(args.target, pipelines, load_wrappers(root / PIPELINES_FILE))
        commands = plan_run(registry, pipelines, names, args.fetch)
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
