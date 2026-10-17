# indie-euroleague

- [Run guide](docs/run-guide.md)
- [Rules of the game](docs/rules.md)
- [Resources and links](docs/resources.md)

## Cheatsheet

Repo root. Every flag, stage and config key: [run guide](docs/run-guide.md).

```bash
uv run dvc repro refresh_and_build_eubook     # 1-4: fetch, merge, names, workbook
uv run dvc repro refresh_data                 # 1-3: the same without the workbook
uv run dvc repro fetch_update                 # fetch only: box score, headers, fantasy stats, schedule
uv run dvc repro -s merge_boxscore merge_headers      # merge only, offline
uv run dvc repro -s check_player_names        # names check (see below)
uv run dvc repro -s -f build_player_name_map  # rebuild the map after recording decisions (/build-name-maps)
uv run dvc repro fetch_kaggle                 # Kaggle history, separate from the update
```

Names check (`names_mode` in `config/config.toml`): players only in the stats (not played yet) and players who played in the box score but are missing from the stats are both listed with the fix, and the flow goes on (`continue`, default). Set `names_mode = "pause"` to stop on the second kind. Fix: `/build-name-maps`, then `uv run dvc repro -s -f build_player_name_map`, then rerun the flow.

Fantasy stats per round: set `fantasy_rounds = "1-6"` in `config/config.toml` (`"all"` = season average), then `uv run dvc repro fetch_euroleague_fantasy_stats`.

Dev: `uv run pytest` · `uv run ruff check src tests && uv run ruff format --check src tests` · `uv run python -m eupy.registry check`

## Make targets

```bash
make clean-data                  # delete all git-ignored data (raw + stages); tracked files stay. ARGS=--dry-run lists only
make clean-data-raw              # raw data only
make clean-cache                 # caches (pycache, ruff, pytest, ...)
make clean-git                   # merged task branches/worktrees (ARGS=--yes / --dry-run)
make clean-all                   # clean-cache + clean-git (data is never included)
make test                        # pytest
make check                       # lint via pre-commit
```

After `clean-data` run `uv run dvc repro refresh_and_build_eubook` to rebuild everything.

## Name matching: who does what

Fantasy stats (`E. Lastname`-style names) and box score (`LASTNAME, FIRST`) spell players differently. Four steps, in flow order:

1. `build_player_name_map` (script): matches each stats player to a box-score player by normalized name within the same club. Exact matches are final; the rest are left open (`needs_review` / `no_candidate`) in `player_name_map.json`.
2. `/build-name-maps` (skill, manual, Claude session): researches the open entries and records a decision (`confirmed` with the full name, `no_match`, `rejected`) in the JSON, then `uv run dvc repro -s -f build_player_name_map`. DVC cannot call it.
3. `check_player_names` (script, read-only): matches nothing and fixes nothing. It audits the result and writes `data/stage_03/player_name_check.csv` with one row per finding: `stats_only` (info: not played yet), `box_only` (a player with minutes the map does not resolve: needs step 2) and `map_stale` (map not rebuilt). `names_mode = "pause"` stops the flow on `box_only`.
4. `normalize_fantasy_stats` (script): rewrites the stats names to the full names from the map.

The check CSV is a human-readable report and also the input that makes the workbook step run after the check.
