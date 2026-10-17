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
uv run dvc repro fetch_update                            # the update's fetchers: euroleague.net box score + headers, fantasy stats, schedule
uv run dvc repro fetch_kaggle                            # the Kaggle history, a separate flow (not part of the update)
uv run dvc repro fetch_kaggle_euroleague                 # one fetcher, tracked by DVC
uv run python -m eupy.fetchers.fetch_kaggle_euroleague   # the same script, untracked
```

| Fetcher module (`eupy.fetchers.`) | Source | Writes (`data/raw_data/`) | Refresh |
|---|---|---|---|
| `fetch_game_schedule` | EuroLeague schedule API | `game_schedule/schedule_<season>.csv` (`gamecode` zero-padded, e.g. `E2026_007`) | full overwrite |
| `fetch_euroleague_fantasy_stats` | fantasy API (token) | `fantasy_stats/players.csv`, `head_coaches.csv` | full overwrite |
| `fetch_kaggle_euroleague` | Kaggle dataset | `kaggle_data/euroleague_box_score.csv`, `euroleague_header.csv` | full overwrite; validated before replacing the old files |
| `fetch_euroleague_live_boxscores` | live.euroleague.net | `euroleague_net/box_score/<season>_delta_<ts>.csv` | incremental, state in `box_score/_state.json` |
| `fetch_euroleague_live_headers` | live.euroleague.net | `euroleague_net/headers/<season>_delta_<ts>.csv` | incremental, state in `headers/_state.json` |

The live fetchers take `--season=E2026` (default from `config/config.toml`).

The fantasy stats fetcher has two modes (`--rounds`), `--stats-type` picks the aggregate of the all-rounds mode, and `--delay SECONDS` sets the pause between requests. The API allows about 5 requests per 2 s window and answers `HTTP Error 429` beyond it; the fetcher does not retry.

```bash
uv run python -m eupy.fetchers.fetch_euroleague_fantasy_stats                      # default: whole season in one pass, per-game average, delay 0.2 s, `round` column = "all"
uv run python -m eupy.fetchers.fetch_euroleague_fantasy_stats --stats-type tot     # whole season, summed (the only way to recover games played, roughly tot / avg)
uv run python -m eupy.fetchers.fetch_euroleague_fantasy_stats --rounds 1-4,6       # one fetch per listed round, always summed, delay 1.0 s, `round` = the number
uv run python -m eupy.fetchers.fetch_euroleague_fantasy_stats --delay 1.0          # slower fetch if a run hits 429
```

`--stats-type avg` with `--rounds` is rejected: a single round has no average to take. `avg` changes what `fpt` and the box-score columns mean (per game, not season sum); `quotation` and `plus` are the same in both.

`dvc repro fetch_euroleague_fantasy_stats` runs the mode set in `config/config.toml` (`[acquisition] fantasy_rounds`, default `all`, and `fantasy_stats_type`, default `avg`); the DVC stage passes no arguments. The CLI flags override the config. To fetch per round through DVC, set `fantasy_rounds = "1-6"` in the config, then `dvc repro fetch_euroleague_fantasy_stats`.

`fetch_update` and `fetch_kaggle` are aggregate stages whose deps are their fetchers' outputs; `dvc repro` runs the producing stages first.

## Merge the live deltas

```bash
uv run dvc repro -s merge_boxscore merge_headers     # offline: only the merges
uv run dvc repro merge_boxscore merge_headers        # also runs the euroleague.net fetchers first (always_changed)
uv run python -m eupy.transform.merge_boxscore       # the same script, untracked
```

Writes `data/stage_01/euroleague_box_score_merged.csv` and `euroleague_header_merged.csv` from the euroleague.net deltas only; the deltas carry their own header (taken from the first one, all must match). Kaggle is left out. To merge the Kaggle history under them again, set `[transformation] include_kaggle = true` (or pass `--include-kaggle`; `--base-season E2025` or `base_season` keeps one Kaggle season): the live deltas then win for the seasons they cover, and Kaggle rows of those seasons are dropped before the append. The Kaggle files are deliberately not DVC deps of the merge, so after refreshing Kaggle with the option on, run `uv run dvc repro -s -f merge_boxscore merge_headers`.

## Flows

Two named DVC commands run the update. Each is a wrapper in `pipelines.toml` (an aggregate stage over its pipelines), so `dvc repro <flow>` runs every stage upstream of it, fetchers included.

```bash
uv run dvc repro refresh_data                  # 1-3: fetch, merge the deltas, resolve player names
uv run dvc repro refresh_and_build_eubook      # 1-4: the same, then the eubook workbook
```

1. `fetch_update`: euroleague.net box score and headers, fantasy stats (mode from the config), schedule.
2. `merge_boxscore`, `merge_headers`: the consolidated tables.
3. `build_team_name_map`, `build_player_name_map`, `check_player_names`, `normalize_fantasy_stats`: names. The check is a normal stage of the flow, run after the map is rebuilt. It compares the two datasets (the fantasy stats are the core, the left side of every join):
   - in both: fine;
   - in the stats, not in the box score (not played yet, injured, closed as `no_match`): listed for information; `normalize_fantasy_stats` keeps their abbreviated name;
   - in the box score with minutes played, not in the stats (case 3): listed with where he was found (club, games, rounds), where not, and the likely stats entry. He drops out of the joins (the workbook counts the dropped rows).
4. `build_eubook`: the workbook. It lists the check's report as an input, so it never runs ahead of the check.

The check has two modes, `[transformation] names_mode` in `config/config.toml` (or `--mode` on a direct run):

| Mode | Case 3 | Exit code |
|---|---|---|
| `continue` (default) | warning, the flow goes on | 0 |
| `pause` | the flow stops until it is fixed or the mode is `continue` | 1 |

Case 2 never pauses. Both modes print the fix. DVC cannot call an agent, so fixing is manual:

1. `/build-name-maps` (the skill) records the decision for the likely stats entry, using the box-score spelling reordered as its full name.
2. `uv run dvc repro -s -f build_player_name_map`, then the flow command again.
3. `uv run python -m eupy.entity.check_player_names` shows the list again at any time (report: `data/stage_03/player_name_check.csv`).

A box-score player no stats entry resembles is missing from the fantasy data; nothing can be decided for him, he stays out of the joins.

Single steps: `uv run dvc repro -s <stage>` (stage names in `dvc.yaml`). Without `-s` a stage also runs everything upstream. The team map needs the schedule, so both flows fetch the schedule.

## Eubook workbook

```bash
uv run dvc repro -s build_eubook                  # offline
uv run python -m eupy.transform.build_eubook      # the same script, untracked
```

Writes `data/stage_04/eubook.xlsx` (linked in `data/stage_99/` by `uv run eupy link-final`) and a copy `eubook_<next round>.xlsx` beside it, not tracked by DVC. Sheets: schedule, teams, boxscore, history of PIR per position (long and wide), the next round's team grades, the player analytical record. It is the pipeline version of `apps/dataprep.py` (same output on the same data; the app is untouched). One difference: with season-wide fantasy stats (`round` = "all") positions and prices are joined on the player alone, so `quotation` is the current price in every row.

## Schedule turns

```bash
uv run python -m eupy.transform.build_schedule_turns --season=E2026
```

Needs the schedule fetched first. Writes `data/stage_01/schedule.csv` and `team_round_turn.csv` (the round's latest date is T2, earlier dates T1). Not a DVC stage yet.

## Box score with positions

```bash
uv run python -m eupy.transform.build_boxscore_kpis [--from-year 2025]
```

Needs the merged box score, the fantasy stats players and the name maps (see below). Writes `data/stage_03/euroleague_box_score_positions.csv`: box-score rows from `--from-year` on, without the `TOTAL` team rows, inner-joined with the stats players' `position` (`G`/`F`/`C`), plus `year`, `minutes_num`, `player_name`, `opponent` and `home_away`. Not a DVC stage yet.

## Name maps (players and teams)

`TeamNames` and `PlayerNames` (`src/eupy/entity/`) turn any spelling of a team or player into one normalized name: `PlayerNames.load().normalize("JONES, CARLIK")` and `normalize("C. Jones", team="PAR")` both give `Carlik Jones`; `TeamNames.load().normalize("PAO")` gives the schedule's team name. Each class reads a JSON map committed in `src/eupy/entity/maps/` (`team_name_map.json`, `player_name_map.json`), so no data folder is needed at lookup time. Each builder also writes the same entries as a CSV, `data/stage_01/team_name_map.csv` and `data/stage_02/player_name_map.csv`, the datasets the registry knows. Design: `specs/spec-name-maps.md`.

Refresh both maps with the `build-name-maps` skill (`.claude/skills/build-name-maps/`). By hand:

```bash
uv run python -m eupy.entity.build_team_name_map     # schedule -> team map (JSON + CSV)
uv run python -m eupy.entity.build_player_name_map   # stats + merged box score + agent decisions in the JSON -> player map (JSON + CSV)
```

Check first, in one line, whether any stats player is unmatched (`--mode pause` exits 1 for a played box-score player missing from the stats; default `continue`; writes the report `data/stage_03/player_name_check.csv`):

```bash
uv run python -m eupy.entity.check_player_names
```

Prerequisites: the schedule, the fantasy stats players and the merged box score (`merge_boxscore`). Players still open after the engine need an agent decision (new transfers, rookies); the builder prints how many. The agent writes the decision on the entry in `src/eupy/entity/maps/player_name_map.json`, and every rebuild carries it forward, so only new players are ever decided. Open entries do not stop a flow; `check_player_names` decides what needs a decision. The map JSON is not a DVC dep: after recording decisions run `uv run dvc repro -s -f build_player_name_map`, then continue the chain. The `entity` pipeline is `dvc = true`; `build_boxscore_kpis` (pipeline `kpis`) is `dvc = false` for now.

## Config, checks, log

- `config/config.toml` (optional) overrides the defaults in `src/eupy/config/defaults.py`: `[acquisition]` season, request_delay, max_gap, kaggle_dataset, fantasy_rounds, fantasy_stats_type; `[transformation]` names_mode, include_kaggle, base_season; `[logging]` path, level. Unknown keys raise an error. Steps that load the config depend on the file in `dvc.yaml`.
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
- `pipelines.toml` assigns every script to exactly one pipeline and sets `dvc = true|false`; today `fetch_update`, `fetch_kaggle`, `merge_history_live`, `entity` and `workbook` are `dvc = true`. Each `[wrappers]` entry is also an aggregate stage (`refresh_data`, `refresh_and_build_eubook`).
- `.dvcignore` excludes `.gitkeep` and `_state.json` from directory hashes.
- DVC deletes a non-`persist` step's outputs before running it, so a failing step loses its output: check inputs exist first.

## Script I/O header

Every pipeline script under `src/eupy/` ends its module docstring with this block (the single source of truth for inputs/outputs; catalogue, graph, map and `dvc.yaml` derive from it):

```
Inputs:
  - fantasy_stats/players: data/raw_data/fantasy_stats/players.csv
Sources:
  - CLI-given file (--input PATH)
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

`data/` is versioned as structure only: `.gitkeep` skeletons plus the allow-list in `.gitignore` (`data/curated/**` is allowed for tracked judgements; nothing is stored there today). Everything else is ignored; derived outputs are fingerprinted by `dvc.lock`.

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
- `make clean-data` deletes every git-ignored file under `data/` (raw and all stages), keeping tracked files such as `.gitkeep` and the committed squad CSV (`ARGS=--dry-run` only lists them); rebuild with `uv run dvc repro refresh_and_build_eubook`. `make clean-all` does not include it.
- `make clean-data-raw` deletes every data file under `data/raw_data/`, keeping the folders and `.gitkeep` files (`ARGS=--dry-run` only lists them). `make clean-all` does not include it.
