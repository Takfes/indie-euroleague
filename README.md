# indie-euroleague

- [Run guide](docs/run-guide.md)
- [Rules of the game](docs/rules.md)
- [Resources and links](docs/resources.md)

## Cheatsheet

Repo root. Every flag, stage and config key: [run guide](docs/run-guide.md).

```bash
uv run dvc repro refresh_and_build_eubook     # 1-4: fetch, merge, names, workbook
uv run dvc repro refresh_data                 # 1-3: the same without the workbook
uv run dvc repro fetch_update                 # fetch only: box score, fantasy stats, schedule
uv run dvc repro -s merge_boxscore            # merge only, offline
uv run dvc repro -s build_player_name_map     # player map + names audit, e.g. after recording decisions (/build-name-maps)
uv run dvc repro fetch_kaggle                 # Kaggle history, separate from the update
```

Names audit (`names_mode` in `config/config.toml`): `build_player_name_map` ends by listing players only in the stats (not played yet, information) and players who played in the box score but are not linked (a warning with the fix); the flow goes on (`continue`, default). Set `names_mode = "pause"` to stop on the second kind. Fix: `/build-name-maps`, then `uv run dvc repro -s build_player_name_map`, then rerun the flow.

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

The fantasy stats (`C. Jones`) and the box score (`JONES, CARLIK`) spell players differently. In flow order:

1. `build_player_name_map` (script, one flow stage): matches each stats player to a box-score player of the same club by name, ignoring `Jr`/`IV` suffixes. Exact matches are final, the rest stay open. It applies the recorded decisions, writes the derived map `data/stage_02/player_name_map.csv` (never edited by hand), then audits the map and prints the players who played but are not linked.
2. `/build-name-maps` (skill, manual, in a Claude session): only when step 1 lists such players. It researches them and records a decision in `src/eupy/entity/maps/player_name_decisions.json` (committed). DVC cannot call it.
3. Re-run step 1 (`uv run dvc repro -s build_player_name_map`; it reruns by itself when the decisions change) until the list is empty.
4. `build_eubook` (script) joins the stats and the box score on the normalized names from the map.
