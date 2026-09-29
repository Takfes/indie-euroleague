# Spec: static HTML data map (ticket 7/8)

## Goal

- An interactive, read-only view of the data lineage for better navigation and more detail than the markdown graph. Foundation for a later control panel (deferred).

## Scope

- `src/eupy/registry/render_html.py` + subcommand `python -m eupy.registry html` → `docs/data-map.html`, a single self-contained file (no server, works offline via `file://`).
- Content, all from the registry and `pipelines.toml`:
  - Graph with the same shapes/ids/colors as the markdown graph (shared rules from ticket 4).
  - Pipeline filter/highlight (`schedule`, `net`, `entity`, wrappers).
  - Click a node → side panel: script (path, inputs, outputs, final, impure, pipeline, first docstring paragraph, `Notes`) or dataset (path, stage, producer, consumers, final, refresh).
  - Legend for shapes and stage colors.
- Optional local overlay: `python -m eupy.registry html --status` writes a git-ignored variant that colors steps fresh/stale from `dvc status`.

## Decisions

- Committed `docs/data-map.html` contains **static registry content only** (no mtimes, row counts, or DVC status) so regeneration is byte-identical across machines.
- Graph library chosen at implementation (constraint: inlined, offline, no CDN at view time); decision recorded in the PR.
- The HTML is a renderer of the same model — no logic that could diverge from the markdown graph.
- Draws in-place self-loops for the entity scripts until ticket 8.

## Out of scope

- Buttons that run pipelines / any server (deferred to `docs/next-steps.md`).

## Depends on

- Ticket 6 (pipelines, wrapper definitions).

## Pass criteria

- [ ] Opens in a browser from disk with no console errors (screenshot checked, light/dark if themed).
- [ ] Node and edge sets equal the registry's (test on the embedded JSON payload).
- [ ] Pipeline filter and click-through panel work (checked in browser).
- [ ] Two generator runs byte-identical.
- [ ] `--status` variant is git-ignored and not required for the committed file.
- [ ] `ruff check` + `ruff format --check` clean; `uv run pytest` green; quickstart mentions the page.
