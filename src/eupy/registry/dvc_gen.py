"""Generate `dvc.yaml` from the registry plus `pipelines.toml` (never hand-edited).

`pipelines.toml` (repo root) holds pipeline membership: one table per pipeline, each with `dvc` (bool)
and `scripts` (script names). Lint: every registry script sits in exactly one pipeline, and every listed
name is a registry script. No pipeline script (or library module) imports another script (`lint_script_imports`).

DVC steps ("DVC stages" in DVC's own terms -- not the data-depth stage of the registry): one per pure
script in a pipeline with `dvc = true`, named after the script. Pure scripts (`Impure: false`) and fetchers
(impure scripts under `src/eupy/fetchers/`) are steps; other impure scripts (entity ingest, which needs
`--verdicts`) never are:

* `cmd`: `uv run python <script path>` (the script must run with no arguments);
* `deps`: the script file, then every in-repo module it imports, transitively (`import_closure`; so editing
  a library module reruns the step), then `config/config.toml` when the closure reads the config, then its
  input dataset paths (directories are hashed as directories);
* `outs`: its output dataset paths, each `cache: false` (fingerprints go in `dvc.lock`, no file copies).

A fetcher step also carries `always_changed: true` (nothing on disk says whether the live source changed, so
it always reruns) and `persist: true` on its outs (DVC keeps them instead of deleting them before the run:
incremental fetchers resume from them, and a failed fetch must not wipe raw data). Downstream steps compare
the fetched files' hashes, so they rerun only when the fetch actually returned something new.

A `dvc = true` pipeline made only of fetchers also gets an aggregate step named after the pipeline
(`dvc repro <pipeline>` fetches everything in it): it depends on all the fetchers' outputs and has no outs.

`data/stage_99/` symlinks are never outs. Steps are sorted by name and the output has no timestamps, so
regeneration is byte-identical.
"""

from __future__ import annotations

import ast
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from eupy.registry.headers import LIBRARY_MODULES
from eupy.registry.model import REPO_ROOT, Registry, RegistryError

PIPELINES_FILE = "pipelines.toml"
FETCHERS_DIR = "src/eupy/fetchers/"
CONFIG_LOADER = "src/eupy/config/loader.py"
CONFIG_FILE = "config/config.toml"  # user config override; a dep of every step whose closure loads the config
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
    fetcher: bool = False  # always_changed + persist outs


def is_fetcher(registry: Registry, script: str) -> bool:
    """Impure script located under `src/eupy/fetchers/`; impure ingest scripts elsewhere are never fetchers."""
    s = registry.scripts[script]
    return s.impure and s.path.startswith(FETCHERS_DIR)


def is_step_script(registry: Registry, script: str) -> bool:
    """Scripts that become DVC steps: pure scripts and fetchers."""
    return not registry.scripts[script].impure or is_fetcher(registry, script)


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


def lint_script_imports(
    registry: Registry, root: Path = REPO_ROOT, libraries: Iterable[str] = LIBRARY_MODULES
) -> list[str]:
    """Problems with direct imports between pipeline scripts, or from a library module into a script.

    Shared code lives in library modules (`libraries`, paths relative to `src/eupy/`); a script importing another
    script hides the dependency from the registry's I/O view, so it is rejected, and so is a library importing a
    script. A script reaching another script through a library is caught at the library's own import.
    """
    script_files = {(root / s.path).resolve(): name for name, s in registry.scripts.items()}
    sources = {(root / s.path).resolve(): f"script {name}" for name, s in registry.scripts.items()}
    sources |= {(root / "src" / "eupy" / lib).resolve(): f"library module {lib}" for lib in libraries}
    problems: list[str] = []
    for file, label in sorted(sources.items(), key=lambda kv: kv[1]):
        for imported in sorted({f.resolve() for f in _imported_files(file, root)}):
            if imported in script_files and imported != file:
                problems.append(
                    f"{label} imports script {script_files[imported]}; pipeline scripts must not be imported "
                    "-- move the shared code into a library module (see LIBRARY_MODULES in registry/headers.py)"
                )
    return problems


def checked_pipelines(registry: Registry, path: Path, root: Path = REPO_ROOT) -> dict[str, Pipeline]:
    """Load `pipelines.toml` from `path` and lint it; raises `RegistryError` listing every problem."""
    pipelines = load_pipelines(path)
    problems = (
        lint_pipelines(registry, pipelines)
        + lint_wrappers(pipelines, load_wrappers(path))
        + lint_script_imports(registry, root)
    )
    if problems:
        raise RegistryError(problems)
    return pipelines


def dvc_steps(registry: Registry, pipelines: dict[str, Pipeline], root: Path = REPO_ROOT) -> tuple[Step, ...]:
    """One `Step` per pure script or fetcher of a `dvc = true` pipeline, plus aggregate steps, sorted by name.

    `root` is where the script sources are read to compute each step's import closure. Raises `RegistryError`
    when the membership lint fails or a path cannot be written as a DVC path.
    """
    problems = lint_pipelines(registry, pipelines)
    if problems:
        raise RegistryError(problems)
    names = sorted(s for p in pipelines.values() if p.dvc for s in p.scripts if is_step_script(registry, s))
    steps: list[Step] = []
    for name in names:
        script = registry.scripts[name]
        closure = import_closure(script.path, root)
        deps = (
            script.path,
            *closure,
            *([CONFIG_FILE] if CONFIG_LOADER in closure else []),
            *sorted(_dvc_path(registry.path(ds)) for ds in script.inputs),
        )
        outs = tuple(sorted(_dvc_path(registry.path(ds)) for ds in script.outputs))
        problems += [
            f"step {name}: path {p!r} is not safe to write unquoted" for p in (*deps, *outs) if not _SAFE_PATH.match(p)
        ]
        problems += [f"step {name}: {o} is under data/stage_99/ (never a DVC out)" for o in outs if _is_link(o)]
        steps.append(Step(name, f"uv run python {script.path}", deps, outs, is_fetcher(registry, name)))
    by_name = {s.name: s for s in steps}
    for pipe in pipelines.values():
        if pipe.dvc and pipe.scripts and all(is_fetcher(registry, s) for s in pipe.scripts):
            outs = sorted(o for s in pipe.scripts for o in by_name[s].outs)
            steps.append(Step(pipe.name, f"echo {pipe.name} fetched", tuple(outs), ()))
    if problems:
        raise RegistryError(problems)
    return tuple(sorted(steps, key=lambda step: step.name))


def render_dvc(registry: Registry, pipelines: dict[str, Pipeline], root: Path = REPO_ROOT) -> str:
    """Full text of `dvc.yaml`; raises `RegistryError` like `dvc_steps`."""
    lines = [BANNER, "stages:"]
    for step in dvc_steps(registry, pipelines, root):
        lines += [f"  {step.name}:", f"    cmd: {step.cmd}", "    deps:", *(f"    - {dep}" for dep in step.deps)]
        if step.outs:
            lines.append("    outs:")
        for out in step.outs:
            lines += [f"    - {out}:", "        cache: false"]
            if step.fetcher:
                lines.append("        persist: true")
        if step.fetcher:
            lines.append("    always_changed: true")
    return "\n".join(lines) + "\n"


def import_closure(script: str, root: Path) -> tuple[str, ...]:
    """Repo-relative paths of the in-repo modules `script` imports, transitively (the script itself excluded).

    Only modules under `src/eupy/` count (a module file, or a package's `__init__.py` when non-empty);
    stdlib, third-party and unresolvable imports are ignored. Sorted, deduplicated, cycle-safe.
    """
    start = (root / script).resolve()
    seen: set[Path] = set()
    stack = [start]
    while stack:
        file = stack.pop()
        if file in seen or not file.is_file():
            continue
        seen.add(file)
        stack.extend(_imported_files(file, root))
    seen.discard(start)
    return tuple(sorted(p.relative_to(root.resolve()).as_posix() for p in seen))


def _imported_files(file: Path, root: Path) -> list[Path]:
    """Source files of the `eupy` modules (and their parent packages) that `file` imports; may include empties."""
    try:
        tree = ast.parse(file.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return []
    src = (root / "src").resolve()
    resolved = file.resolve()
    package = resolved.relative_to(src).with_suffix("").parts[:-1] if resolved.is_relative_to(src) else ()
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)] if node.level - 1 <= len(package) else ()
                if not base:
                    continue
                prefix = ".".join(base) + (f".{node.module}" if node.module else "")
            else:
                prefix = node.module or ""
            names += [prefix, *(f"{prefix}.{a.name}" for a in node.names)]
    found: list[Path] = []
    for name in names:
        parts = name.split(".")
        if parts[0] != "eupy":
            continue
        for i in range(1, len(parts) + 1):  # parent packages run their __init__ too
            found += [f for f in _module_files(src, parts[:i]) if f.name != "__init__.py" or _has_code(f)]
    return found


def _has_code(path: Path) -> bool:
    """False for a blank `__init__.py` (nothing to track)."""
    return bool(path.read_text(encoding="utf-8").strip())


def _module_files(src: Path, parts: list[str]) -> list[Path]:
    """The file backing module `parts` (a module file, else the package `__init__.py`), as a 0/1-item list."""
    base = src.joinpath(*parts)
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return [candidate]
    return []


def _dvc_path(path: str) -> str:
    """Registry paths mark directories with a trailing `/`; DVC wants the bare path."""
    return path.rstrip("/")


def _is_link(path: str) -> bool:
    return path.startswith("data/stage_99/")
