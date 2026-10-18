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
uv run dvc repro fetch_update                            # the update's fetchers: euroleague.net box score, fantasy stats, schedule
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
uv run dvc repro -s merge_boxscore     # offline: only the merge
uv run dvc repro merge_boxscore        # also runs the euroleague.net box-score fetcher first (always_changed)
uv run python -m eupy.transform.merge_boxscore   # the same script, untracked
```

Writes `data/stage_01/euroleague_box_score_merged.csv` from the euroleague.net deltas only; the deltas carry their own header (taken from the first one, all must match). Kaggle is left out. To merge the Kaggle history under them again, set `[transformation] include_kaggle = true` (or pass `--include-kaggle`; `--base-season E2025` or `base_season` keeps one Kaggle season): the live deltas then win for the seasons they cover, and Kaggle rows of those seasons are dropped before the append. The Kaggle files are deliberately not DVC deps of the merge, so after refreshing Kaggle with the option on, run `uv run dvc repro -s -f merge_boxscore merge_headers`.

The game headers are outside the flows (nothing reads the merged headers): `uv run dvc repro merge_headers` fetches and merges them (`pipelines.toml`: `live_headers`).

## Flows

Two named DVC commands run the update. Each is a wrapper in `pipelines.toml` (an aggregate stage over its pipelines), so `dvc repro <flow>` runs every stage upstream of it, fetchers included.

```bash
uv run dvc repro refresh_data                  # 1-3: fetch, merge the deltas, resolve player names
uv run dvc repro refresh_and_build_eubook      # 1-4: the same, then the eubook workbook
```

1. `fetch_update`: euroleague.net box score, fantasy stats (mode from the config), schedule.
2. `merge_boxscore`: the consolidated box score.
3. `build_player_name_map`: the player map, then an audit of it (the fantasy stats are the core, the left side of every join):
   - in both: fine;
   - in the stats, not in the box score (not played yet, injured, closed as `no_match`): listed for information;
   - in the box score with minutes played, not in the stats (case 3): listed with where he was found (club, games, rounds), where not, and the likely stats entry. He drops out of the joins (the workbook counts the dropped rows).
4. `build_eubook`: the workbook.

The audit has two modes, `[transformation] names_mode` in `config/config.toml` (or `--mode` on a direct run):

| Mode | Case 3 | Exit code |
|---|---|---|
| `continue` (default) | warning, the flow goes on | 0 |
| `pause` | the map stage fails after writing the map, so the flow stops before the workbook until it is fixed or the mode is `continue` | 1 |

Case 2 never pauses. Both modes print the fix. DVC cannot call an agent, so fixing is manual:

1. `/build-name-maps` (the skill) records the decision for the likely stats entry, using the box-score spelling reordered as its full name.
2. `uv run dvc repro -s build_player_name_map` (it audits again), then the flow command again.

A box-score player no stats entry resembles is missing from the fantasy data; nothing can be decided for him, he stays out of the joins.

Single steps: `uv run dvc repro -s <stage>` (stage names in `dvc.yaml`). Without `-s` a stage also runs everything upstream. The player names need the schedule (team lookup), so both flows fetch it.

## Eubook workbook

```bash
uv run dvc repro -s build_eubook                  # offline
uv run python -m eupy.transform.build_eubook      # the same script, untracked
```

Writes `data/stage_03/eubook.xlsx` (always the latest, tracked by DVC) and a real-file copy `data/stage_99/eubook_<next round>.xlsx` (e.g. `eubook_007.xlsx`, one per round, not tracked by DVC), so stage_99 collects the delivered workbooks. `eubook` is not `Final`, so `link-final` adds no link for it. Sheets: schedule, teams, boxscore, history of PIR per position (long and wide), the next round's team grades, the player analytical record. It is the pipeline version of `apps/dataprep.py` (same output on the same data; the app is untouched). The player analytical record carries the PIR percentiles `pir_p10`, `pir_p50`, `pir_p75`, `pir_p90` (over the slow window = the player's latest 5 games; `pir_fast`/`mins_fast` use the latest 3) after `pir_std`, and its empty KPI cells are filled with defaults (`fill_empty`: `pir_std`, `mins_std`, `pir_per_min` = 0, `pir_trend`, `mins_trend` = 1, `pir_lag1`/`pir_lag2` = the player's `pir_mean`; the run prints how many cells per column). One difference: with season-wide fantasy stats (`round` = "all") positions and prices are joined on the player alone, so `quotation` is the current price in every row.

## Squad optimizer

`uv run python scripts/optimize.py` (outside DVC: it writes to `data/stage_99/`, which DVC must not own) reads `data/stage_04/optimizer_input.xlsx` (first sheet) and writes `data/stage_99/optimizer_output.xlsx`: sheet `selection` (the input rows plus `selected`, `role`, `action`, `is_captain`) and sheet `summary` (objective and its split, benefit over keeping the current squad, trades, cash, solver status and time, run parameters).

- **Also:** `make optimize`, or the package twin `uv run python -m eupy.optimize.optimize_squad` (same code, registry header; a test fails if the two drift apart, so change both). Method, objective modes and parameters: [optimizer.md](optimizer.md).
- **Column mapping:** the `COLUMNS` / `RISK_COLUMNS` dicts at the top of the script map the model's names (`player`, `team`, `position`, `turn`, `price`, `in_prev_roster`, `std`, `p90`, `p10`) to workbook columns (`price` = `crd_last`, `std` = `pir_std`, `p90` = `pir_p90`, `p10` = `pir_p10`); the input is never renamed. The value to maximise is `[optimization] target` (default `exp_pir`; any numeric column).
- **Empty cells:** any empty cell in a column the run uses (the mapped columns, `target`, and the risk column of the selected mode) stops the run with an error; other columns are ignored.
- **Parameters:** all in `[optimization]` of `config/config.toml`, no CLI flags: `target`, `objective_mode`, `robustness_lambda`, `upside_beta`, `downside_gamma`, `credit_value`, `cash`, `max_trades`, `unlimited_trades`, `min_players_per_turn`, `solver`, `solver_options`. `min_players_per_turn = [3, 3]` requires at least 3 turn-1 and 3 turn-2 players in the 10-player squad (not only the starting five); `[]` or `[0, 0]` = no constraint. `[optimization.solver_options]` replaces the default solver options as a whole.

## Player names

Five datasets spell a player differently; `PlayerNames` (`src/eupy/entity/player_names.py`) turns any spelling into one normalized full name: `PlayerNames.load().normalize("JONES, CARLIK")` and `normalize("C. Jones", team="PAR")` both give `Carlik Jones`. The same goes for teams: `TeamNames.load().normalize("PAO")` gives the schedule's team name; it is derived from the schedule on load (no file), which is why the scripts using it declare `game_schedule/schedule` as input. Design: `specs/spec-name-maps.md`.

Who does what, in flow order:

1. `build_player_name_map` (script, a flow stage): matches each fantasy-stats player to a box-score player of the same club by name, ignoring `Jr`/`Sr`/`II`/`III`/`IV`. An exact match is accepted; anything else stays open (`needs_review` or `no_candidate`). Then it applies the agent decisions and writes the derived map `data/stage_02/player_name_map.csv` (never edited by hand). Finally it audits the map (`name_audit.py`, the cases above) and prints the players who played but are not linked. Instant: plain string matching, no agent involved.
2. `/build-name-maps` (skill, manual, in a Claude session; DVC cannot call it): only when step 1 lists such players. It researches them and records a decision (`confirmed` with the full name, `no_match`, `rejected`) in `src/eupy/entity/maps/player_name_decisions.json`, the one committed player file. It is a declared input of the builder, so `uv run dvc repro -s build_player_name_map` rebuilds and re-audits after a decision.
3. `build_eubook` calls `PlayerNames` to join the stats and the box score on the normalized name.

By hand: `uv run python -m eupy.entity.build_player_name_map` (stats + merged box score + schedule + decisions -> player map CSV, then the audit; `--mode pause` exits 1 for a played box-score player missing from the stats, default `continue`).

Prerequisites: the schedule, the fantasy stats players and the merged box score (`merge_boxscore`). Open entries never stop a flow; only the audit's case 3 needs attention. A suffix the stats and the box score disagree on needs no decision. After `make clean-data` run the whole flow once (`refresh_data`) before single `-s` stages: they need the upstream files to exist. The `entity` pipeline is `dvc = true`.

## Config, checks, log

- `config/config.toml` (optional) overrides the defaults in `src/eupy/config/defaults.py`: `[acquisition]` season, request_delay, max_gap, kaggle_dataset, fantasy_rounds, fantasy_stats_type; `[transformation]` names_mode, include_kaggle, base_season; `[logging]` path, level; `[optimization]` cash, max_trades, unlimited_trades, credit_value, min_players_per_turn, solver, solver_options (see Squad optimizer). Unknown keys raise an error. Steps that load the config depend on the file in `dvc.yaml`.
- The live box-score, live header and fantasy-stats fetchers log one progress line per request or step (`gamecode 41: complete, 22 rows`, `round 3 (3/6): fetching ...`, `page 2/4 fetched`) to the console and `logs/log.log`, so a slow run can be told from a stuck one (`tail -f logs/log.log`). Each request times out after 30 s.
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
