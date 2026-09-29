# Spec: script header key-value block (ticket 1/8 of the DVC pipeline initiative)

## Goal

- Make every script's I/O header machine-parseable, so a registry can derive datasets, lineage and stages from it (ticket 2) without an agent reading prose.
- Headers stay the single source of truth for script I/O (`AGENTS.md` → Data).

## Scope

- Define one strict key-value block, placed at the **end** of each pipeline script's module docstring. The prose body stays for humans.
- Convert every pipeline script under `src/eupy/` (fetchers, transform, entity resolvers/appliers, optimize). Shared library modules with no I/O block (`matching.py`, `player_crosswalk.py`, `team_crosswalk.py`) stay untouched.
- Document the grammar in `AGENTS.md` (Data section) and `docs/quickstart.md`.

## Grammar

```
Inputs:
  - <dataset-name>: <path>          # one entry per input dataset
Sources:
  - <free-text external origin>      # e.g. live.euroleague.net, verdicts JSON (--verdicts)
Outputs:
  - <dataset-name>: <path>
Final: true|false
Impure: true|false
Refresh: <free text>                 # optional
Notes: <free text>                   # optional
```

- `<dataset-name>` follows the catalogue convention: raw = `<source-dir>/<logical-name>`, produced = file stem.
- `<path>` is repo-relative. A directory of per-run files ends in `/` (e.g. `data/raw_data/euroleague_net/box_score/`); templated file names are not allowed in paths.
- `Impure: true` marks scripts that hit a live source or otherwise cannot be re-derived from disk (all fetchers). Everything else is `false`.
- No `Pipeline:` key — pipeline membership lives in one central file (ticket 5), so restructuring never touches script headers.
- Keys appear in the order above; `Inputs`/`Sources`/`Outputs` may be `none`.

## Decisions

- Line-based grammar parsed by a tiny hand-written reader (no YAML dependency in scripts' docstrings).
- The stage in a declared path (`stage_01`) is a **declared** value; ticket 2 checks it against the computed stage.
- The free-text notes currently living under the catalogue tables move into `Notes:` keys (rendered by ticket 3).

## Out of scope

- The parser itself (ticket 2), any behaviour change in scripts, moving scripts to resolve paths through the registry (deferred).

## Depends on

- Nothing.

## Pass criteria

- [ ] Every pipeline script has the block; prose above it is unchanged apart from removing statements now duplicated by the block.
- [ ] Dataset names/paths in the blocks match the current `docs/data-catalogue.md` exactly (reviewed by diff against the catalogue).
- [ ] Grammar documented in `AGENTS.md` and `docs/quickstart.md`.
- [ ] `ruff check` + `ruff format --check` clean; `uv run pytest` green.
- [ ] Catalogue + graph unchanged after regenerating (headers say the same thing as before).
