<!-- Maintained by Claude: update it in the same commit as any change to a command, stage, path or config key. Do not hand-edit. -->

# Run guide

How to run the pipeline. Commands run from the repo root; `uv run` puts the project venv on the path.

## Setup

```bash
uv sync
```

Python 3.12+ (managed by `uv`). Credentials: the Kaggle download needs `~/.kaggle/kaggle.json` (or `KAGGLE_USERNAME`/`KAGGLE_KEY`); the fantasy stats fetcher needs `EUROLEAGUE_FANTASY_AUTH_TOKEN` (env var or repo-root `.env`). The other fetchers use public endpoints.

## Fetch raw data

Each fetcher is a DVC stage (`always_changed`, outputs `persist`) in `dvc.yaml`, and a module you can run directly.

```bash
uv run dvc repro fetch_raw                               # all fetchers, nothing else
uv run dvc repro fetch_kaggle_euroleague                 # one fetcher, tracked by DVC
uv run python -m eupy.fetchers.fetch_kaggle_euroleague   # the same script, untracked
```

| Fetcher module (`eupy.fetchers.`) | Source | Writes (`data/raw_data/`) | Refresh |
|---|---|---|---|
| `fetch_game_schedule` | EuroLeague schedule API | `game_schedule/schedule_<season>.csv` | full overwrite |
| `fetch_euroleague_fantasy_stats` | fantasy API (token) | `fantasy_stats/players.csv`, `head_coaches.csv` | full overwrite |
| `fetch_kaggle_euroleague` | Kaggle dataset | `kaggle_data/euroleague_box_score.csv`, `euroleague_header.csv` | full overwrite; validated before replacing the old files |
| `fetch_euroleague_live_boxscores` | live.euroleague.net | `euroleague_net/box_score/<season>_delta_<ts>.csv` | incremental, state in `box_score/_state.json` |
| `fetch_euroleague_live_headers` | live.euroleague.net | `euroleague_net/headers/<season>_delta_<ts>.csv` | incremental, state in `headers/_state.json` |

The live fetchers take `--season=E2026` (default from `config/config.toml`).

`fetch_raw` is an aggregate stage whose deps are all the fetchers' outputs; `dvc repro` runs the producing stages first.

## Merge Kaggle history with the live deltas

```bash
uv run dvc repro merge_boxscore merge_headers        # also runs the upstream fetchers (always_changed)
uv run dvc repro -s merge_boxscore merge_headers     # offline: only the merges
uv run python -m eupy.transform.merge_boxscore       # the same script, untracked
```

Writes `data/stage_01/euroleague_box_score_merged.csv` and `euroleague_header_merged.csv`. `--base-season E2025` (or `[transformation] base_season` in config) limits the Kaggle base to one season; default is the full history.

## Schedule turns

```bash
uv run python -m eupy.transform.build_schedule_turns --season=E2026
```

Needs the schedule fetched first. Writes `data/stage_01/schedule.csv` and `team_round_turn.csv` (the round's latest date is T2, earlier dates T1). Not a DVC stage yet.

## Entity resolution (broken, pending rework)

The resolvers in `src/eupy/entity/` still read `basketballsphere_prices`, whose fetcher was removed; the fantasy stats dataset is to replace it. Until then the registry check fails on that input, and the `entity` pipeline is `dvc = false`. Shared pieces: `matching.py` (exact→fuzzy engine), `verdict_batches.py`, `player_crosswalk.py` / `team_crosswalk.py` (read-only lookups). Verdict batches live in `data/curated/<x>_verdicts/` (append-only, tracked in git); the `apply_*_verdicts.py --verdicts PATH` ingest scripts append them.

## Config, checks, log

- `config/config.toml` (optional) overrides the defaults in `src/eupy/config/defaults.py`: `[acquisition]` season, request_delay, max_gap, kaggle_dataset; `[transformation]` base_season; `[logging]` path, level. Unknown keys raise an error. Steps that load the config depend on the file in `dvc.yaml`.
- Every fetcher and merge script calls `eupy.quality.validate(rows, key, name)` before writing: empty rows or duplicate keys are logged to `logs/log.log` (git-ignored) and fail the run, leaving the previous output and state untouched.

## DVC

`dvc.yaml` is generated from the script headers plus `pipelines.toml`; never edit it.

```bash
uv run python -m eupy.registry dvc   # regenerate dvc.yaml after a header or pipelines.toml change
uv run dvc repro                     # everything: fetchers always rerun, the rest only if their script or inputs changed
uv run dvc status                    # what is stale
```

- A step's deps: its script, every in-repo module it imports (transitively), `config/config.toml` when it loads the config, then its input datasets.
- A step's `cmd` is `uv run python -m <module>`; outputs are `cache: false` (fingerprints in the committed `dvc.lock`, no copies).
- Fetcher steps always rerun; steps downstream rerun only if the fetched files' hashes changed. `persist: true` keeps a fetcher's outputs between runs (incremental resume; a failed fetch never wipes raw data).
- `pipelines.toml` assigns every script to exactly one pipeline and sets `dvc = true|false`; today `fetch_raw` and `merge_history_live` are `dvc = true`.
- `.dvcignore` excludes `.gitkeep` and `_state.json` from directory hashes.
- DVC deletes a non-`persist` step's outputs before running it, so a failing step loses its output: check inputs exist first.

## Script I/O header

Every pipeline script under `src/eupy/` ends its module docstring with this block (the single source of truth for inputs/outputs; catalogue, graph, map and `dvc.yaml` derive from it):

```
Inputs:
  - fantasy_stats/players: data/raw_data/fantasy_stats/players.csv
Sources:
  - JSON verdicts file (--verdicts PATH)
Outputs:
  - fantasy_stats_players_normalized: data/stage_02/fantasy_stats_players_normalized.csv
Final: true
Impure: false
Refresh: full overwrite per run
Notes: free text
```

- Names: raw = `<source-dir>/<logical-name>`, produced = file stem. A directory of per-run files ends in `/`; no templated file names (the pattern goes in `Notes`).
- `Sources`: external origins and CLI-given files. Any of the three lists may be `none`.
- `Impure: true` for anything hitting a live source (all fetchers).
- `Refresh` and `Notes` are optional; `Notes` may continue on 2-space-indented lines.

## Registry

`src/eupy/registry/` reads the headers (via `ast`, scripts are never imported). Raw datasets no script produces need an entry in `src/eupy/registry/raw_sources.toml`.

```bash
uv run python -m eupy.registry check      # lint: exit 1 + one line per problem
uv run python -m eupy.registry show       # print the model
uv run python -m eupy.registry docs       # docs/data-catalogue.md + docs/data-graph.md
uv run python -m eupy.registry html       # docs/data-map.html (interactive lineage map); --status adds DVC freshness
uv run python -m eupy.registry dvc        # dvc.yaml
```

- `check` fails on: a missing/malformed block in `fetchers/`, `transform/`, `entity/`, `optimize/` (library modules are exempt), a script importing another script, two producers of one dataset, a declared `stage_XX` that differs from the computed one, a cycle, an undeclared or stale raw source, bad pipeline membership.
- Stage: under `data/raw_data/` or `data/curated/` = 0; otherwise `max(input stages) + 1`.
- In code: `Registry.from_repo()` → `.path(name)`, `.stage(name)`, `.producer(name)`, `.consumers(name)`, `.upstream(script)`, `.topo_order()`, `.edges()`.
- Generated files carry a "GENERATED — do not edit" banner; regenerate them (the `update-data-docs` skill) before committing.

## Data layout

`data/` is versioned as structure only: `.gitkeep` skeletons plus the allow-list in `.gitignore` (today `data/curated/**`). Everything else is ignored; derived outputs are fingerprinted by `dvc.lock`.

- Raw data must be fetched first; a fresh clone has only empty directories.
- `data/stage_99/` holds relative symlinks to `Final: true` datasets; `uv run eupy link-final` recreates them.
- A new data directory needs a `.gitkeep`; a new versioned exception needs a `!data/<path>` line under `# ALLOW-LIST` with a reason.

## Dev loop

```bash
uv run pytest
ruff check src tests
ruff format --check src tests
```

## Git cleanup

- `make merge-worktree` (or the `merge-worktree` skill) merges a finished branch under `.claude/worktrees/` into main after you approve the diff, then removes the branch and worktree.
- `make clean-git` lists merged task worktrees, branches and stale locks, then deletes the safe ones (needs an authenticated `gh`); `ARGS=--yes` skips the prompt, `ARGS=--dry-run` never deletes.
- `make clean-cache` removes rebuildable caches; `make clean-all` runs both.
