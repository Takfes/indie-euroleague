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

The fantasy stats (`C. Jones`, club `PAO`) are the baseline: later joins keep them on the left. The box score spells players `JONES, CARLIK` (club `PAN`), so the two have to be linked first.

- **`build_player_name_map`** (script, a stage of the DVC flows) reformats the box-score names to the stats form and matches them exactly within the same club, ignoring `Jr`/`IV` suffixes. Clubs are resolved through `TeamNames`, which starts from the schedule. It writes `data/stage_02/player_name_map.csv` (derived, never edited by hand): the exact matches, the open points (`needs_review` / `no_candidate`) and the recorded decisions applied on top.
- **The inline report** that ends the run lists: stats players found in both datasets; stats players not in the box score (information only: usually not played yet, nothing to join, they stay in the map); and box-score players who played but are not linked (a warning: they drop out of the left join).
- **Mode** (`names_mode` in `config/config.toml`, or `--mode`): `continue` (default) prints the report and goes on; `pause` prints the same report, then exits 1 after writing the map, which stops the flow until the open points are resolved or the mode is `continue`.
- **`/build-name-maps`** (skill, run by you in a Claude session) resolves the open points and records the decisions in `src/eupy/entity/maps/player_name_decisions.json` (committed). It is not part of the DVC flow, since DVC cannot invoke an agent. Afterwards `uv run dvc repro -s build_player_name_map` rebuilds the map and reports again (the decisions file is a declared input).
