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

## Names check vs the skill

- `eupy.entity.check_player_names` (a DVC stage, runs inside the flows): read-only. Compares box-score players with the fantasy stats, prints the cases and writes `data/stage_03/player_name_check.csv`. It decides nothing; in `pause` mode it only stops the flow.
- `/build-name-maps` (the skill, run by you in a Claude session): the fix. It runs the map builders, researches each open player, records the decision in `player_name_map.json` and rebuilds. DVC cannot call it, so it is always a manual step after the check reports a problem.
