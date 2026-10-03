"""Generate `dvc.yaml` from the registry plus `pipelines.toml` (never hand-edited).

`pipelines.toml` (repo root) holds pipeline membership: one table per pipeline, each with `dvc` (bool)
and `scripts` (script names). Lint: every registry script sits in exactly one pipeline, and every listed
name is a registry script.

DVC steps ("DVC stages" in DVC's own terms -- not the data-depth stage of the registry): one per pure
script (`Impure: false`) in a pipeline with `dvc = true`, named after the script:

* `cmd`: `uv run python <script path>` (the script must run with no arguments);
* `deps`: the script file, then its input dataset paths (directories are hashed as directories);
* `outs`: its output dataset paths, each `cache: false` (fingerprints go in `dvc.lock`, no file copies).

Impure fetchers are not steps: their raw outputs are plain deps of the steps downstream, so a changed raw
file reruns those steps. `data/stage_99/` symlinks are never outs. Steps are sorted by name and the output
has no timestamps, so regeneration is byte-identical.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from eupy.registry.model import Registry, RegistryError

PIPELINES_FILE = "pipelines.toml"
WRAPPERS_KEY = "wrappers"  # reserved table: wrapper name -> ordered list of pipeline names (not a pipeline)
BANNER = "# GENERATED — do not edit. Regenerate with `uv run python -m eupy.registry dvc` (from pipelines.toml)."
_SAFE_PATH = re.compile(r"^[A-Za-z0-9_./-]+$")  # written unquoted into YAML


@dataclass(frozen=True)
class Pipeline:
    """A named group of scripts; `dvc` says whether its pure scripts become DVC steps."""

    name: str
    dvc: bool
    scripts: tuple[str, ...]


@dataclass(frozen=True)
class Step:
    """One DVC step (a "DVC stage" in DVC's terms), named after its script."""

    name: str
    cmd: str
    deps: tuple[str, ...]
    outs: tuple[str, ...]


def load_pipelines(path: Path) -> dict[str, Pipeline]:
    """Parse `pipelines.toml`; raises `RegistryError` when it is missing or malformed."""
    if not path.is_file():
        raise RegistryError([f"{path.name}: not found at {path}"])
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    pipelines: dict[str, Pipeline] = {}
    problems: list[str] = []
    for name, table in data.items():
        if name == WRAPPERS_KEY:
            continue
        dvc, scripts = (table.get("dvc"), table.get("scripts")) if isinstance(table, dict) else (None, None)
        if not isinstance(dvc, bool) or not isinstance(scripts, list) or not all(isinstance(s, str) for s in scripts):
            problems.append(f"{path.name}: [{name}] needs 'dvc' (true/false) and 'scripts' (list of script names)")
            continue
        pipelines[name] = Pipeline(name, dvc, tuple(scripts))
    if problems:
        raise RegistryError(problems)
    return pipelines


def load_wrappers(path: Path) -> dict[str, tuple[str, ...]]:
    """Parse the `[wrappers]` table of `pipelines.toml` (empty when absent); raises `RegistryError` when malformed."""
    if not path.is_file():
        raise RegistryError([f"{path.name}: not found at {path}"])
    with path.open("rb") as fh:
        table = tomllib.load(fh).get(WRAPPERS_KEY, {})
    bad = [n for n, v in table.items() if not isinstance(v, list) or not v or not all(isinstance(p, str) for p in v)]
    if bad:
        raise RegistryError([
            f"{path.name}: [{WRAPPERS_KEY}] {n} must be a non-empty list of pipeline names" for n in bad
        ])
    return {name: tuple(v) for name, v in table.items()}


def lint_wrappers(pipelines: dict[str, Pipeline], wrappers: dict[str, tuple[str, ...]]) -> list[str]:
    """Problems with wrappers: a name that is also a pipeline, or a member that is not a pipeline."""
    problems = [
        f"{PIPELINES_FILE}: wrapper {w} has the same name as a pipeline" for w in sorted(wrappers) if w in pipelines
    ]
    for w, members in sorted(wrappers.items()):
        problems += [f"{PIPELINES_FILE}: wrapper {w} lists unknown pipeline {p}" for p in members if p not in pipelines]
    return problems


def lint_pipelines(registry: Registry, pipelines: dict[str, Pipeline]) -> list[str]:
    """Problems with pipeline membership: unknown names, scripts in zero or several pipelines."""
    member_of: dict[str, list[str]] = {}
    problems: list[str] = []
    for pipe in pipelines.values():
        for script in pipe.scripts:
            if script not in registry.scripts:
                problems.append(f"{PIPELINES_FILE}: [{pipe.name}] lists unknown script {script}")
            member_of.setdefault(script, []).append(pipe.name)
    for script in sorted(registry.scripts):
        found = member_of.get(script, [])
        if len(found) != 1:
            where = f"{len(found)} pipelines ({', '.join(found)})" if found else "no pipeline"
            problems.append(f"{PIPELINES_FILE}: script {script} is in {where}; it must be in exactly one")
    return problems


def checked_pipelines(registry: Registry, path: Path) -> dict[str, Pipeline]:
    """Load `pipelines.toml` from `path` and lint it; raises `RegistryError` listing every problem."""
    pipelines = load_pipelines(path)
    problems = lint_pipelines(registry, pipelines) + lint_wrappers(pipelines, load_wrappers(path))
    if problems:
        raise RegistryError(problems)
    return pipelines


def dvc_steps(registry: Registry, pipelines: dict[str, Pipeline]) -> tuple[Step, ...]:
    """One `Step` per pure script of a `dvc = true` pipeline, sorted by name.

    Raises `RegistryError` when the membership lint fails or a path cannot be written as a DVC path.
    """
    problems = lint_pipelines(registry, pipelines)
    if problems:
        raise RegistryError(problems)
    names = sorted(s for p in pipelines.values() if p.dvc for s in p.scripts if not registry.scripts[s].impure)
    steps: list[Step] = []
    for name in names:
        script = registry.scripts[name]
        deps = (script.path, *sorted(_dvc_path(registry.path(ds)) for ds in script.inputs))
        outs = tuple(sorted(_dvc_path(registry.path(ds)) for ds in script.outputs))
        problems += [
            f"step {name}: path {p!r} is not safe to write unquoted" for p in (*deps, *outs) if not _SAFE_PATH.match(p)
        ]
        problems += [f"step {name}: {o} is under data/stage_99/ (never a DVC out)" for o in outs if _is_link(o)]
        steps.append(Step(name, f"uv run python {script.path}", deps, outs))
    if problems:
        raise RegistryError(problems)
    return tuple(steps)


def render_dvc(registry: Registry, pipelines: dict[str, Pipeline]) -> str:
    """Full text of `dvc.yaml`; raises `RegistryError` like `dvc_steps`."""
    lines = [BANNER, "stages:"]
    for step in dvc_steps(registry, pipelines):
        lines += [f"  {step.name}:", f"    cmd: {step.cmd}", "    deps:", *(f"    - {dep}" for dep in step.deps)]
        lines.append("    outs:")
        for out in step.outs:
            lines += [f"    - {out}:", "        cache: false"]
    return "\n".join(lines) + "\n"


def _dvc_path(path: str) -> str:
    """Registry paths mark directories with a trailing `/`; DVC wants the bare path."""
    return path.rstrip("/")


def _is_link(path: str) -> bool:
    return path.startswith("data/stage_99/")
