# indie-euroleague

- [Run guide](docs/run-guide.md)
- [Rules of the game](docs/rules.md)
- [Resources and links](docs/resources.md)

## Cheatsheet

Repo root. Every flag, stage and config key: [run guide](docs/run-guide.md).

```bash
uv run dvc repro normalize_fantasy_stats                  # whole update: fetch, merge, name map, normalize (stops on unmatched names)
uv run dvc repro fetch_netboxscore_and_fantasystats       # fetch only: box score + headers (euroleague.net) + fantasy stats
uv run dvc repro -s merge_boxscore merge_headers          # merge only, offline
uv run python -m eupy.entity.check_player_names           # any unmatched names? (0 = skip matching)
uv run dvc repro -s -f build_player_name_map              # rebuild the map after recording decisions (/build-name-maps)
```

Fantasy stats per round: set `fantasy_rounds = "1-6"` in `config/config.toml` (`"all"` = season average), then `uv run dvc repro fetch_euroleague_fantasy_stats`.

Dev: `uv run pytest` · `uv run ruff check src tests && uv run ruff format --check src tests` · `uv run python -m eupy.registry check`
