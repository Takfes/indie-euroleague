# Spec: pipeline wrapper CLI, `stage_99` links, pipeline subgraphs (ticket 6/8)

## Goal

- Run any pipeline, or a group of pipelines, with one command.
- `data/stage_99/` links are derived from the registry (`Final: true`), not made by hand.
- The markdown graph shows which pipeline each script belongs to.

## Scope

- `eupy` console script (`[project.scripts]`) with:
  - `eupy run <pipeline|wrapper>` — runs `dvc repro --single-item` for the pipeline's `dvc = true` steps (pure scripts and fetchers), in dependency order.
  - `eupy run fetch_raw` — runs the raw-data fetcher steps only.
  - `eupy link-final` — create/refresh relative symlinks in `data/stage_99/` for every `Final: true` dataset; remove only stale symlinks it manages. Also called at the end of `eupy run`.
- Wrappers defined in `pipelines.toml`: `all = ["schedule", "merge_history_live"]` (entity added in ticket 8).
- `render_graph.py`: one Mermaid `subgraph` per pipeline containing its scripts; sources and raw datasets stay outside.

## Decisions

- A wrapper is just an ordered list of pipeline names; order among them is enforced by DVC dependencies, not by the list.
- Fetches hit live sources and need `EUROLEAGUE_FANTASY_AUTH_TOKEN`, so they only run when the target includes `fetch_raw` (`eupy run fetch_raw` / `full`); `--single-item` keeps every other target offline and deterministic.
- `link-final` never touches non-symlink files and never creates absolute links (per `AGENTS.md`).

## Out of scope

- HTML (ticket 7), control panel (deferred), entity pipeline (ticket 8).

## Depends on

- Ticket 5 (DVC core).

## Pass criteria

- [x] `eupy run all` runs; a second invocation is a no-op.
- [x] `eupy run full` runs the fetcher steps before the merge steps in one `dvc repro --single-item` (verified with a fake runner; no live calls in tests).
- [x] `link-final` output equals the set of `Final: true` datasets, links relative, idempotent; stale managed link removed.
- [x] Graph regenerated with pipeline subgraphs; coverage invariant from ticket 4 still holds.
- [x] Unit tests for target resolution (wrapper expansion, unknown name error), link-final (create/idempotent/stale), subgraph rendering.
- [x] `docs/quickstart.md` documents `eupy run` / `eupy link-final`.
- [x] `ruff check` + `ruff format --check` clean; `uv run pytest` green.
