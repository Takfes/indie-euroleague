# Spec: catalogue renderer (ticket 3/8)

## Goal

- `docs/data-catalogue.md` generated deterministically from the registry — no agent hand-rewriting.

## Scope

- `src/eupy/registry/render_catalogue.py` + subcommand `python -m eupy.registry catalogue`.
- Same three tables as today, same columns: **Raw datasets** (Dataset, Path, Produced by, Consumed by, Refresh), **Produced datasets** (Dataset, Stage, Path, Produced by, Inputs, Final, `stage_99` symlink), **Scripts** (Script path, Inputs, Outputs, Final).
- Add an `Impure` column to Scripts.
- `Notes:` header keys render as the free-text notes under the relevant table.
- Keep the "GENERATED — do not edit" banner.

## Decisions

- Refresh text comes from the `Refresh:` header key (fetched raw) or `raw_sources.toml` (manual raw).
- `stage_99` symlink column: `data/stage_99/<file>` if `Final: true`, else `—`; "not yet created" nuance is dropped (the file's existence is machine state, not registry state).
- Rows sorted by stage then name, so diffs stay small.

## Out of scope

- The graph (ticket 4), the skill rewrite (ticket 4).

## Depends on

- Ticket 2.

## Pass criteria

- [ ] Running the generator twice produces byte-identical output.
- [ ] Every dataset and script in the registry appears exactly once; row content matches the pre-existing catalogue except for intended additions (`Impure` column, note relocation) — reviewed by diff.
- [ ] Golden-file test on a small synthetic registry; one test that a header change flows into the output.
- [ ] `ruff check` + `ruff format --check` clean; `uv run pytest` green.
