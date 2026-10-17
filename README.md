# indie-euroleague

- [Run guide](docs/run-guide.md)
- [Rules of the game](docs/rules.md)
- [Resources and links](docs/resources.md)

## Cheatsheet

Repo root. Every flag, stage and config key: [run guide](docs/run-guide.md).

```bash
uv run dvc repro refresh_and_build_eubook     # 1-4: fetch, merge, names, workbook (stops on unmatched names)
uv run dvc repro refresh_data                 # 1-3: the same without the workbook
uv run dvc repro fetch_update                 # fetch only: box score, headers, fantasy stats, schedule
uv run dvc repro -s merge_boxscore merge_headers      # merge only, offline
uv run dvc repro -s check_player_names        # unmatched names? (exit 0 = nothing to do)
uv run dvc repro -s -f build_player_name_map  # rebuild the map after recording decisions (/build-name-maps)
uv run dvc repro fetch_kaggle                 # Kaggle history, separate from the update
```

Fantasy stats per round: set `fantasy_rounds = "1-6"` in `config/config.toml` (`"all"` = season average), then `uv run dvc repro fetch_euroleague_fantasy_stats`.

Dev: `uv run pytest` · `uv run ruff check src tests && uv run ruff format --check src tests` · `uv run python -m eupy.registry check`
