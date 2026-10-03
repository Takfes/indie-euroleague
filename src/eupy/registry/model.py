"""Registry model: scripts, datasets, external sources, lineage edges and stages.

Everything here is derived from the script I/O headers (`headers.py`) plus `raw_sources.toml`
(origin + refresh note for raw datasets no script produces) -- never hand-maintained.

DAG: nodes are scripts, datasets and external sources (ids `script:<stem>`, `dataset:<name>`,
`source:<label>`); edges run dataset -> script (input) -> dataset (output), source -> script
(`Sources:` entry) and source -> dataset (`raw_sources.toml` origin). Ordering and cycle detection use
`graphlib.TopologicalSorter`; a cycle raises `graphlib.CycleError`.

Stage rules:

* a dataset whose path is under `data/raw_data/` or `data/curated/` is stage 0 (tracked manual inputs --
  see `is_stage0_path`), whether or not a script produces it: fetchers write raw data, the impure `apply_*`
  ingest scripts append verdict batches to curated dirs;
* any other dataset is `max(stage of its producer's inputs) + 1` (1 when the producer has none);
* the `stage_XX` in its declared path must match, a letter suffix (`stage_01a`) being accepted when
  the numeric part matches.

A dataset listed in both `Inputs` and `Outputs` of one script is a cycle and raises `CycleError`.
"""

from __future__ import annotations

import re
import tomllib
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from graphlib import TopologicalSorter
from pathlib import Path

from eupy.registry.headers import Header, discover_headers

REPO_ROOT = Path(__file__).resolve().parents[3]
RAW_SOURCES_PATH = Path(__file__).with_name("raw_sources.toml")
RAW_PREFIX = "data/raw_data/"
CURATED_PREFIX = "data/curated/"

_STAGE_DIR = re.compile(r"^data/stage_(\d+)([a-z]?)/")

RawSources = Mapping[str, Mapping[str, str]]  # raw dataset name -> {"origin": ..., "refresh": ...}


def is_stage0_path(path: str) -> bool:
    """True for paths under `data/raw_data/` or `data/curated/`: stage 0, never produced by the DAG proper."""
    return path.startswith((RAW_PREFIX, CURATED_PREFIX))


class RegistryError(Exception):
    """The headers do not form a consistent registry; `problems` lists every issue found."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("\n".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Script:
    """A pipeline script, named by its file stem; `path` is repo-relative."""

    name: str
    path: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    sources: tuple[str, ...]
    final: bool
    impure: bool
    refresh: str | None
    notes: str | None


@dataclass(frozen=True)
class Dataset:
    """A dataset declared in some header. `producer` is `None` for raw datasets no script writes."""

    name: str
    path: str
    stage: int
    raw: bool
    producer: str | None
    consumers: tuple[str, ...]
    final: bool  # the producer's Final flag (False when unproduced)
    origin: str | None  # raw_sources.toml origin, for unproduced raw datasets
    refresh: str | None  # producer's Refresh, or raw_sources.toml refresh


@dataclass(frozen=True)
class Source:
    """An external origin: a script `Sources:` entry or a `raw_sources.toml` origin label."""

    label: str
    scripts: tuple[str, ...]
    datasets: tuple[str, ...]


def _node(kind: str, name: str) -> str:
    return f"{kind}:{name}"


class Registry:
    """Scripts, datasets and sources plus the lineage DAG; build with `from_repo` or `from_headers`."""

    def __init__(
        self,
        scripts: dict[str, Script],
        datasets: dict[str, Dataset],
        sources: dict[str, Source],
        graph: dict[str, set[str]],
        order: tuple[str, ...],
    ) -> None:
        self.scripts = scripts
        self.datasets = datasets
        self.sources = sources
        self._graph = graph  # node id -> predecessor node ids
        self._order = order  # all node ids, topological, ties broken alphabetically

    # --- construction ------------------------------------------------------------------

    @classmethod
    def from_repo(cls, root: Path = REPO_ROOT, raw_sources_path: Path = RAW_SOURCES_PATH) -> Registry:
        """Discover every header under `<root>/src/eupy/` and build the registry.

        Raises `RegistryError` on header or consistency problems, `graphlib.CycleError` on a cycle.
        """
        found, problems = discover_headers(root / "src" / "eupy")
        if problems:
            raise RegistryError(problems)
        headers = {path.relative_to(root).as_posix(): header for path, header in found.items()}
        with raw_sources_path.open("rb") as fh:
            raw_sources = tomllib.load(fh).get("datasets", {})
        return cls.from_headers(headers, raw_sources)

    @classmethod
    def from_headers(
        cls,
        headers: Mapping[str, Header],
        raw_sources: RawSources,
    ) -> Registry:
        """Build from `{repo-relative script path: Header}` and `{raw dataset name: {origin, refresh}}`.

        Raises `RegistryError` listing every consistency problem, `graphlib.CycleError` on a cycle.
        """
        path_of = _dataset_paths(headers)
        scripts, problems = _scripts(headers)
        producer, producer_problems = _producers(scripts)
        problems += producer_problems + _raw_problems(path_of, scripts, raw_sources)
        if problems:
            raise RegistryError(problems)

        graph = _graph(scripts, path_of, producer, raw_sources)
        order = _topological(graph)  # raises CycleError
        stages = _stages(order, path_of, scripts, producer)

        consumers: dict[str, list[str]] = defaultdict(list)
        for script in scripts.values():
            for ds in script.inputs:
                consumers[ds].append(script.name)
        datasets = {
            ds: Dataset(
                name=ds,
                path=path,
                stage=stages[ds],
                raw=is_stage0_path(path),
                producer=producer.get(ds),
                consumers=tuple(sorted(consumers[ds])),
                final=scripts[producer[ds]].final if ds in producer else False,
                origin=None if ds in producer else raw_sources[ds]["origin"],
                refresh=scripts[producer[ds]].refresh if ds in producer else raw_sources[ds]["refresh"],
            )
            for ds, path in sorted(path_of.items())
        }
        return cls(scripts, datasets, _sources(scripts, raw_sources), graph, order)

    # --- queries -----------------------------------------------------------------------

    def path(self, name: str) -> str:
        """Repo-relative path of dataset `name`."""
        return self.datasets[name].path

    def stage(self, name: str) -> int:
        """Computed stage of dataset `name` (0 = raw)."""
        return self.datasets[name].stage

    def producer(self, name: str) -> str | None:
        """Canonical producer script of dataset `name`, or `None` for unproduced raw data."""
        return self.datasets[name].producer

    def consumers(self, name: str) -> tuple[str, ...]:
        """Scripts listing dataset `name` as an input, sorted."""
        return self.datasets[name].consumers

    def upstream(self, script: str) -> tuple[str, ...]:
        """Every script `script` transitively depends on (itself excluded), sorted."""
        seen: set[str] = set()
        stack = [_node("script", script)]
        while stack:
            for pred in self._graph.get(stack.pop(), ()):
                if pred not in seen:
                    seen.add(pred)
                    stack.append(pred)
        return tuple(sorted(n.partition(":")[2] for n in seen if n.startswith("script:") and n != f"script:{script}"))

    def edges(self) -> tuple[tuple[str, str], ...]:
        """Every lineage edge as sorted `(from node id, to node id)`; ids are `script:`/`dataset:`/`source:` prefixed."""
        return tuple(sorted((pred, node) for node, preds in self._graph.items() for pred in preds))

    def topo_order(self) -> tuple[str, ...]:
        """Script names in a valid execution order (ties broken alphabetically, so deterministic)."""
        return tuple(n.partition(":")[2] for n in self._order if n.startswith("script:"))


# --- build steps -----------------------------------------------------------------------


def _dataset_paths(headers: Mapping[str, Header]) -> dict[str, str]:
    """Dataset name -> path; every declaration of a name must agree, else `RegistryError`."""
    paths: dict[str, set[str]] = defaultdict(set)
    for header in headers.values():
        for name, path in (*header.inputs, *header.outputs):
            paths[name].add(path)
    problems = [
        f"dataset {name}: declared with different paths {sorted(declared)}"
        for name, declared in sorted(paths.items())
        if len(declared) > 1
    ]
    if problems:
        raise RegistryError(problems)
    return {name: next(iter(declared)) for name, declared in paths.items()}


def _scripts(headers: Mapping[str, Header]) -> tuple[dict[str, Script], list[str]]:
    """One `Script` per header, named by file stem."""
    scripts: dict[str, Script] = {}
    problems: list[str] = []
    for rel in sorted(headers):
        header, name = headers[rel], Path(rel).stem
        if name in scripts:
            problems.append(f"script {name}: two scripts share this name ({scripts[name].path}, {rel})")
            continue
        inputs = tuple(n for n, _ in header.inputs)
        outputs = tuple(n for n, _ in header.outputs)
        scripts[name] = Script(
            name=name,
            path=rel,
            inputs=inputs,
            outputs=outputs,
            sources=header.sources,
            final=header.final,
            impure=header.impure,
            refresh=header.refresh,
            notes=header.notes,
        )
    return scripts, problems


def _producers(scripts: dict[str, Script]) -> tuple[dict[str, str], list[str]]:
    """The single producer of every written dataset; more than one writer is a problem."""
    writers: dict[str, list[str]] = defaultdict(list)
    for script in scripts.values():
        for out in script.outputs:
            writers[out].append(script.name)
    producer: dict[str, str] = {}
    problems: list[str] = []
    for ds, names in sorted(writers.items()):
        if len(names) > 1:
            problems.append(f"dataset {ds}: produced by more than one script ({', '.join(names)})")
        else:
            producer[ds] = names[0]
    return producer, problems


def _raw_problems(path_of: dict[str, str], scripts: dict[str, Script], raw_sources: RawSources) -> list[str]:
    """Unwritten datasets must be raw and in raw_sources.toml; toml entries must be exactly those."""
    written = {out for script in scripts.values() for out in script.outputs}
    problems: list[str] = []
    for ds, path in sorted(path_of.items()):
        if ds in written:
            continue
        if not is_stage0_path(path):
            problems.append(
                f"dataset {ds}: no script produces it and its path is not under {RAW_PREFIX} or {CURATED_PREFIX}"
            )
        elif ds not in raw_sources:
            problems.append(f"dataset {ds}: raw and unproduced, but not declared in raw_sources.toml")
    for ds, decl in sorted(raw_sources.items()):
        if ds not in path_of or ds in written or not is_stage0_path(path_of[ds]):
            problems.append(f"raw_sources.toml: {ds} is not an unproduced raw dataset in any header")
        elif not decl.get("origin") or not decl.get("refresh"):
            problems.append(f"raw_sources.toml: {ds} needs both 'origin' and 'refresh'")
    return problems


def _sources(scripts: dict[str, Script], raw_sources: RawSources) -> dict[str, Source]:
    """External origins from script `Sources:` entries and raw_sources.toml, keyed by label."""
    feeds_scripts: dict[str, set[str]] = defaultdict(set)
    feeds_datasets: dict[str, set[str]] = defaultdict(set)
    for script in scripts.values():
        for label in script.sources:
            feeds_scripts[label].add(script.name)
    for ds, decl in raw_sources.items():
        feeds_datasets[decl["origin"]].add(ds)
    return {
        label: Source(label, tuple(sorted(feeds_scripts[label])), tuple(sorted(feeds_datasets[label])))
        for label in sorted(feeds_scripts.keys() | feeds_datasets.keys())
    }


def _graph(
    scripts: dict[str, Script], path_of: dict[str, str], producer: dict[str, str], raw_sources: RawSources
) -> dict[str, set[str]]:
    """DAG as node -> predecessors."""
    graph: dict[str, set[str]] = {}
    for script in scripts.values():
        inputs = {_node("dataset", ds) for ds in script.inputs}
        graph[_node("script", script.name)] = inputs | {_node("source", s) for s in script.sources}
    for ds in path_of:
        pred = _node("script", producer[ds]) if ds in producer else _node("source", raw_sources[ds]["origin"])
        graph[_node("dataset", ds)] = {pred}
    return graph


def _topological(graph: dict[str, set[str]]) -> tuple[str, ...]:
    """Deterministic topological order of all nodes; raises `graphlib.CycleError` on a cycle."""
    sorter = TopologicalSorter(graph)
    sorter.prepare()
    order: list[str] = []
    while sorter.is_active():
        ready = sorted(sorter.get_ready())
        order.extend(ready)
        sorter.done(*ready)
    return tuple(order)


def _stages(
    order: tuple[str, ...], path_of: dict[str, str], scripts: dict[str, Script], producer: dict[str, str]
) -> dict[str, int]:
    """Computed stage per dataset (rules in the module doc); `RegistryError` on declared-path mismatch."""
    stages: dict[str, int] = {}
    problems: list[str] = []
    for node in order:  # topological, so a producer's inputs are staged before its outputs
        kind, _, ds = node.partition(":")
        if kind != "dataset":
            continue
        if is_stage0_path(path_of[ds]):
            stages[ds] = 0
            continue
        prod = scripts[producer[ds]]
        stages[ds] = 1 + max((stages[i] for i in prod.inputs), default=0)
        m = _STAGE_DIR.match(path_of[ds])
        if not m or int(m.group(1)) != stages[ds]:
            declared = f"stage_{m.group(1)}{m.group(2)}" if m else "no stage_XX directory"
            problems.append(f"dataset {ds}: computed stage {stages[ds]}, but path {path_of[ds]} declares {declared}")
    if problems:
        raise RegistryError(problems)
    return stages
