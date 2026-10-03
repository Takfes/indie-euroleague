# Quickstart

## Setup

```bash
uv sync
```

Requires Python 3.12+ (managed automatically by `uv`). No `.env` or secrets needed for the fetchers below — they hit public, unauthenticated endpoints.

## Running fetchers

**Live box scores** (incremental — polls new gamecodes since the last run, writes a delta CSV if any completed games are found):

```bash
uv run python src/eupy/fetchers/fetch_euroleague_live_boxscores.py --season=E2026
```

Writes to `data/raw_data/euroleague_net/box_score/`, tracks progress in `data/raw_data/euroleague_net/_state.json`.

**Live headers** (same incremental pattern; Kaggle `euroleague_header` schema, works for any season — `--season=E2022` replicates a Kaggle season from live):

```bash
uv run python src/eupy/fetchers/fetch_euroleague_live_headers.py --season=E2026
uv run python src/eupy/transform/append_live_headers.py   # Kaggle E2025 + all deltas -> data/stage_01/header_current.csv
```

Writes to `data/raw_data/euroleague_net/headers/`, tracks progress in `headers/_state.json`.

**Fantasy prices** (full overwrite — re-run any time to refresh, e.g. after a new round):

```bash
uv run python src/eupy/fetchers/fetch_basketballsphere_prices.py
```

Writes to `data/raw_data/fantasy_prices/basketballsphere_prices.csv`.

## Schedule turns

Adds `turn` (T1/T2 within each round) to the raw schedule and derives a per-team `(team, round, turn)` table. Needs `fetch_euroleague_schedule.py` to have run first:

```bash
uv run python src/eupy/transform/build_schedule_turns.py --season=E2026
```

Writes `data/stage_01/schedule.csv` and `data/stage_01/team_round_turn.csv` (symlinked in `data/stage_99/`). Turn rule: the round's latest date is T2, earlier dates are T1.

## Entity resolution

Name-matching pipelines linking `basketballsphere_prices.csv` to the Kaggle box-score data (`src/eupy/entity/`):

| Script | Does |
|---|---|
| `matching.py` | Shared exact→fuzzy matching engine (no CLI). |
| `resolve_player_names.py` | Builds/refreshes `player_name_crosswalk.csv` (player names). |
| `apply_player_name_verdicts.py` | Ingest: appends a verdict batch to `data/curated/player_name_verdicts/`. |
| `player_crosswalk.py` | `PlayerNameCrosswalk` — read-only lookup class. |
| `resolve_team_names.py` | Builds/refreshes `team_name_crosswalk.csv` (club names). |
| `apply_team_name_verdicts.py` | Ingest: appends a verdict batch to `data/curated/team_name_verdicts/`. |
| `team_crosswalk.py` | `TeamNameCrosswalk` — read-only lookup class. |
| `resolve_fantasy_stats_player_names.py` | Builds/refreshes `fantasy_stats_player_name_crosswalk.csv` (fantasy stats `"C. Jones"` + club code → full name, keyed by `player_id`); reads the batches in `data/curated/fantasy_stats_player_name_verdicts/`. |
| `apply_fantasy_stats_player_name_verdicts.py` | Ingest: appends a verdict batch to that directory. |

Resolvers are pure functions of raw data + the verdict batch dirs `data/curated/<x>_verdicts/` (append-only `NNNN_<slug>.json`, tracked in git); they never rewrite their own output. Record verdicts with the `apply_*_verdicts.py --verdicts PATH` ingest scripts (impure, no DVC step, never run by `eupy run`), then rebuild: `uv run dvc repro resolve_player_names resolve_team_names resolve_fantasy_stats_player_names normalize_fantasy_stats` (or `uv run eupy run entity`) — only the steps whose batches changed rerun. Full exact→fuzzy→agent flow: `resolve-player-names` / `resolve-team-names` skills.

Normalize the fantasy stats table (full names via the crosswalk, `Guard`/`Forward`/`Center` → `G`/`F`/`C`; refuses to run while any crosswalk row is open):

```bash
uv run eupy run entity                                                       # after fetch_euroleague_fantasy_stats.py; new players show up as needs_review/no_candidate
uv run python src/eupy/entity/apply_fantasy_stats_player_name_verdicts.py --verdicts verdicts.json   # record verdicts for those rows, then rerun the line above
```

Writes `data/stage_01/fantasy_stats_player_name_crosswalk.csv` and `data/stage_02/fantasy_stats_players_normalized.csv` (both symlinked in `data/stage_99/`).

## Script I/O header

Every pipeline script under `src/eupy/` ends its module docstring with a key-value block (the single source of truth for its inputs/outputs; catalogue and graph are generated from it). Keys in this order, one per line:

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

- `Inputs`/`Outputs`: `- <dataset-name>: <repo-relative path>`; raw names are `<source-dir>/<logical-name>`, produced names are the file stem. Directories of per-run files end in `/`; no templated file names.
- `Sources`: external origins and CLI-given files (live URLs, verdicts JSON), free text. Any of the three lists may be `none`.
- `Impure: true` for anything hitting a live source (all fetchers). A verdict applier that rewrites an existing file lists it in both `Inputs` and `Outputs`; resolvers that merge into their own previous output do not (see `Notes`).
- `Refresh` and `Notes` are optional; `Notes` may continue on 2-space-indented lines. Templated file names go in `Notes`; the path is the directory.

## Registry

`src/eupy/registry/` derives scripts, datasets, paths, stages and lineage from those headers (read via `ast`, scripts are never imported). Raw datasets no script produces get their origin + refresh note in `src/eupy/registry/raw_sources.toml`.

```bash
uv run python -m eupy.registry check   # lint: exit 1 + one line per problem
uv run python -m eupy.registry show    # print the model (deterministic)
uv run python -m eupy.registry catalogue  # (re)write docs/data-catalogue.md; --stdout prints instead
uv run python -m eupy.registry graph      # (re)write docs/data-graph.md (Mermaid); --stdout prints instead
uv run python -m eupy.registry docs       # both docs in one pass (what the update-data-docs skill runs)
uv run python -m eupy.registry dvc        # (re)write dvc.yaml from pipelines.toml; --stdout prints instead
```

- `check` fails on: a malformed/missing block in `fetchers/`, `transform/`, `entity/`, `optimize/` (library modules `matching.py`, `verdict_batches.py`, `*_crosswalk.py`, `player_sources.py`, `live_append.py`, `live_euroleague.py` exempt), a pipeline script importing another script (shared code goes in a library module), a dataset with two producers, a declared `stage_XX` ≠ computed stage, a cycle, a raw dataset missing from / stale in `raw_sources.toml`.
- `catalogue` renders `docs/data-catalogue.md` from the registry (never hand-edit). `Notes:` header text is printed verbatim under the raw table (scripts writing raw data) or the produced table (all other scripts).
- `graph` renders `docs/data-graph.md`: stadium = external source, rectangle = script, cylinder = dataset; one hue per stage, `★` + thick border = `final`. `Registry.edges()` is the edge list it draws (plus in-place self-loops).
- `html` renders `docs/data-map.html`: an interactive lineage map (one self-contained file, open it from disk, no server). Same nodes/colours as the graph; pipeline/wrapper filter dims everything else; click a node for path, inputs/outputs, pipeline, docstring paragraph, `Notes`. Not part of `docs` (regenerate it when headers or `pipelines.toml` change). `html --status` also writes the git-ignored `docs/data-map.status.html` with DVC fresh/stale marks from `dvc status`.
- Stage: under `data/raw_data/` or `data/curated/` = 0 (curated dirs are written by the impure `apply_*` ingest scripts); otherwise `max(input stages) + 1`.
- In code: `Registry.from_repo()` → `.path(name)`, `.stage(name)`, `.producer(name)`, `.consumers(name)`, `.upstream(script)`, `.topo_order()`, `.edges()`.
- No script may list a dataset as both input and output (`IN_PLACE_ALLOWLIST` is empty; the mechanism is kept only for tests).

## Data layout

`data/` is versioned as **structure only**: directory skeletons (`.gitkeep`) plus the allow-list in `.gitignore` (section "data/ layout"). Everything else under `data/` is ignored.

- Versioned: the skeleton and `data/curated/**` (verdict batches are human/agent judgements, not derivable from raw data).
- Derived outputs (`data/stage_*`) are not in git; their md5s are fingerprinted in the committed `dvc.lock`. Rebuild them with `uv run eupy run all`.
- Raw data (`data/raw_data/`) must be fetched/downloaded first (fetchers, Kaggle); a fresh clone has only empty directories.
- `data/stage_99/` symlinks are untracked too: `uv run eupy link-final` recreates them.
- New exception: add one `!data/<path>` line under `# ALLOW-LIST` in `.gitignore`, with a reason comment. A new data directory needs a `.gitkeep`.
- `.dvcignore` excludes `.gitkeep` and `_state.json` so directory-dep hashes ignore skeleton/state files.

## DVC pipelines

`pipelines.toml` (repo root) assigns every script to exactly one pipeline (`check` lints this). Pure scripts of `dvc = true` pipelines (`schedule`, `net`, `entity`) become DVC steps in the generated `dvc.yaml`; impure scripts (fetchers, entity ingest) are not steps — run them yourself, then:

```bash
uv run python -m eupy.registry dvc   # regenerate dvc.yaml after a header or pipelines.toml change (never hand-edit)
uv run dvc repro                     # rerun only the steps whose script or inputs changed
```

A step's deps are its script, every in-repo module it imports (transitively, so editing a library module reruns the step), then its input datasets. `eupy run` first checks that the external inputs of the steps it will run exist, and exits 1 without running anything otherwise (DVC deletes a step's outputs before running it); `--dry-run` skips the check.

Outputs are `cache: false` (no copies, no remote); their fingerprints live in the committed `dvc.lock`. `data/raw_data/` is git-ignored: on a fresh clone the raw deps are missing until the fetchers (and the Kaggle download) have run, so `dvc repro` only works after that.

### `eupy run` / `eupy link-final`

Wrapper CLI over `dvc repro` (`[project.scripts] eupy`; run as `uv run eupy ...`). Targets come from `pipelines.toml`: a pipeline name, or a wrapper (`[wrappers]`, e.g. `all = ["schedule", "net", "entity"]`; order between pipelines comes from DVC dependencies).

```bash
uv run eupy run all                 # offline: dvc repro for schedule + net + entity steps; a second run is a no-op
uv run eupy run schedule --fetch    # first run the pipeline's fetchers (live API calls), then dvc repro
uv run eupy run acquire             # dvc = false pipeline: runs its fetchers only (no DVC)
uv run eupy run all --dry-run       # print the commands, execute nothing
uv run eupy link-final              # refresh data/stage_99/ links (also runs after a successful `eupy run`)
```

- `optimize` is `dvc = false` with no fetchers: `eupy run optimize` exits 1 ("not runnable under DVC yet").
- `--fetch` runs the pipeline's *fetchers* (impure scripts under `src/eupy/fetchers/`) with `uv run python <script>` in dependency order; the default run never touches the network. Ingest scripts (need `--verdicts`) are never run by `eupy run`.
- `link-final` creates relative symlinks in `data/stage_99/` for every produced `Final: true` dataset whose file exists (missing ones are reported and skipped), removes stale symlinks it no longer manages, and never touches regular files or directories.

## Dev loop

```bash
uv run pytest                        # tests
ruff check src tests                 # lint
ruff format --check src tests        # format check
```

## Finishing a task branch

`make merge-worktree` merges a finished branch under `.claude/worktrees/`
into main and cleans up (branch + worktree, local and remote) once you
approve the diff — prompts if more than one exists. From an interactive
Claude Code session, the `merge-worktree` skill does the same thing
conversationally.

## Cleaning up after PRs merge

`make tidy` lists merged task worktrees, local/remote branches and stale
lock files, asks once, then deletes the safe ones (integrated, clean, not in
use) and prints manual commands for the rest. `make tidy ARGS=--yes` skips the
prompt and reports what it could not delete; `ARGS=--dry-run` never deletes.
It also flags branches/worktrees that break the `<type>/<short-name>` naming.
