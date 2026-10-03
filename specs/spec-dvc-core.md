# Spec: DVC core — generated `dvc.yaml` for the acyclic pipelines (ticket 5/8)

## Goal

- `dvc repro` runs the `schedule` and `net` data pipelines and skips whatever is unchanged.
- `dvc.yaml` is generated from the registry, never hand-edited.

## Scope

- Add `dvc` as a dev dependency; `dvc init`; no remote; hash-only (no data versioning).
- `pipelines.toml` (repo root) — the one place pipeline membership lives:
  - `schedule`: `fetch_game_schedule`, `build_schedule_turns`
  - `net`: `fetch_euroleague_live_boxscores`, `fetch_euroleague_live_headers`, `append_live_boxscores`, `append_live_headers`
  - `entity` and `optimize`: listed with `dvc = false` (complete membership, not yet runnable under DVC)
  - fetchers of other sources sit in `acquire` (`dvc = false`, impure).
  - Lint: every pipeline script is in exactly one pipeline.
- `src/eupy/registry/dvc_gen.py` + subcommand `python -m eupy.registry dvc` → `dvc.yaml` with a "GENERATED — do not edit" comment.
- Commit `dvc.lock`.

## Decisions

- Only pure scripts (`Impure: false`) in pipelines with `dvc = true` become DVC stages. Impure fetchers are **not** stages; their raw outputs are plain deps of downstream stages (changed raw file → downstream reruns).
- Stage per script: `cmd: uv run python <script path>` (must run with no arguments), `deps:` = script file + input paths, `outs:` = output paths with `cache: false` (fingerprints go in `dvc.lock`, no file copies, no remote).
- `data/stage_99/` symlinks are never DVC outs (ticket 6 creates them).
- Directory inputs (e.g. the live delta folders) are hashed as directories. Whether DVC keeps a tiny directory manifest in `.dvc/cache` is verified here; acceptable if so.
- Terminology: DVC's unit is a "step"/"DVC stage" in prose; the data-depth concept stays "stage" in registry docs — spec text must not conflate them.

## Out of scope

- The wrapper CLI, `stage_99` links, pipeline subgraphs (ticket 6); entity pipeline under DVC (ticket 8).

## Depends on

- Ticket 2 (registry). Tickets 3–4 are not required, but land first per the agreed order.

## Pass criteria

- [x] `dvc repro` builds all `dvc = true` pipelines from raw inputs on a clean checkout of the outputs.
- [x] Immediate second `dvc repro` is a no-op.
- [x] Touching one raw input reruns only its downstream steps (checked on a `net` input).
- [x] Outputs after `dvc repro` are byte-identical to the previous committed/known outputs (reproducibility).
- [x] `dvc.yaml` regeneration is idempotent; lint rejects a script in zero or two pipelines.
- [x] Unit tests: stage generation (deps/outs/cmd/`cache: false`), impure scripts skipped, `dvc = false` pipelines skipped.
- [x] `dvc.lock` committed; `docs/quickstart.md` notes `dvc repro`.
- [x] `ruff check` + `ruff format --check` clean; `uv run pytest` green.
