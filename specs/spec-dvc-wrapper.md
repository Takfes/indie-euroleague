# Spec: pipeline wrapper CLI, `stage_99` links, pipeline subgraphs (ticket 6/8)

## Goal

- Run any pipeline, or a group of pipelines, with one command.
- `data/stage_99/` links are derived from the registry (`Final: true`), not made by hand.
- The markdown graph shows which pipeline each script belongs to.

## Scope

- `eupy` console script (`[project.scripts]`) with:
  - `eupy run <pipeline|wrapper> [--fetch]` — runs `dvc repro` for the pipeline's `dvc = true` steps; with `--fetch`, first runs that pipeline's impure members directly (fetchers), in dependency order.
  - `eupy run acquire` — runs the impure fetchers only.
  - `eupy link-final` — create/refresh relative symlinks in `data/stage_99/` for every `Final: true` dataset; remove only stale symlinks it manages. Also called at the end of `eupy run`.
- Wrappers defined in `pipelines.toml`: `all = ["schedule", "net"]` (entity added in ticket 8).
- `render_graph.py`: one Mermaid `subgraph` per pipeline containing its scripts; sources and raw datasets stay outside.

## Decisions

- A wrapper is just an ordered list of pipeline names; order among them is enforced by DVC dependencies, not by the list.
- Impure fetches are opt-in (`--fetch`) because they hit live sources and need `EUROLEAGUE_FANTASY_AUTH_TOKEN`; default `eupy run` is fully offline and deterministic.
- `link-final` never touches non-symlink files and never creates absolute links (per `AGENTS.md`).

## Out of scope

- HTML (ticket 7), control panel (deferred), entity pipeline (ticket 8).

## Depends on

- Ticket 5 (DVC core).

## Pass criteria

- [x] `eupy run all` runs; a second invocation is a no-op.
- [x] `eupy run schedule --fetch` runs the fetcher before the steps (verified with a stub/dry mode; no live calls in tests).
- [x] `link-final` output equals the set of `Final: true` datasets, links relative, idempotent; stale managed link removed.
- [x] Graph regenerated with pipeline subgraphs; coverage invariant from ticket 4 still holds.
- [x] Unit tests for target resolution (wrapper expansion, unknown name error), link-final (create/idempotent/stale), subgraph rendering.
- [x] `docs/quickstart.md` documents `eupy run` / `eupy link-final`.
- [x] `ruff check` + `ruff format --check` clean; `uv run pytest` green.
