# Spec: dataset/script registry (ticket 2/8)

## Goal

- One place in code that knows every script, dataset, path, stage and edge — derived from the script headers, never hand-maintained.
- Every downstream artifact (catalogue, graph, HTML, `dvc.yaml`) is a rendering of this model, so they cannot disagree.

## Scope

- New package `src/eupy/registry/` (module split proposed here, confirmed with the owner before implementation):
  - `headers.py` — read a script's docstring with `ast` (never import the script) and parse the key-value block from ticket 1.
  - `model.py` — `Script`, `Dataset`, `Source` dataclasses and `Registry` (scripts, datasets, edges).
  - `__main__.py` — `uv run python -m eupy.registry check` (lint) and `show` (print the model).
- `src/eupy/registry/raw_sources.toml` — hand-written declarations for raw datasets no script produces (Kaggle files, `optimizer_input`): origin label + refresh note. TOML → stdlib `tomllib`, no new dependency.
- Registry API: `registry.path(name)`, `.stage(name)`, `.producer(name)`, `.consumers(name)`, `.upstream(script)`, `.topo_order()`.

## Decisions

- DAG on nodes = scripts + datasets (+ external sources); edges dataset → script → dataset. Ordering and cycle detection via stdlib `graphlib.TopologicalSorter`; a cycle raises `CycleError`.
- Stage: raw dataset = 0; produced dataset = `max(input stages) + 1`. Letter suffixes (`stage_01a`) accepted in declared paths when the numeric part matches.
- Lint `check` fails on: unparseable/missing block, dataset with two producers, declared `stage_XX` ≠ computed stage, any cycle **not on the allowlist**.
- Cycle allowlist (temporary, emptied in ticket 8): scripts whose output is also their input — `resolve_player_names`, `resolve_team_names`, `resolve_fantasy_stats_player_names`, `apply_player_name_verdicts`, `apply_team_name_verdicts`. Allowlisted self-edges are recorded in the model as `in_place` and excluded from stage computation.
- Scripts keep hardcoding their own paths; migrating them to `registry.path()` is deferred (`docs/next-steps.md`). The path-vs-stage lint is what catches drift meanwhile.

## Out of scope

- Renderers (tickets 3, 4), DVC (5, 6), HTML (7), removing the cycles (8).

## Depends on

- Ticket 1 (header format).

## Pass criteria

- [x] `python -m eupy.registry check` passes on the real repo.
- [x] Unit tests: block parsing (multi-entry, `none`, trailing-slash dirs, malformed input rejected), stage depth on a synthetic chain/diamond, non-allowlisted cycle rejected, double-producer rejected, path-vs-stage mismatch rejected.
- [x] Stages computed for all current datasets equal the ones in `docs/data-catalogue.md` (e.g. `fantasy_stats_players_normalized` = 2).
- [x] Deterministic: two runs of `show` are byte-identical.
- [x] `ruff check` + `ruff format --check` clean; `uv run pytest` green; module docs current.
