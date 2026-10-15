# indie-euroleague

- [Run guide](docs/run-guide.md)
- [Rules of the game](docs/rules.md)
- [Resources and links](docs/resources.md)

## Cheatsheet

Commands from the repo root. Details: [run guide](docs/run-guide.md).

**DVC flows** (each rewrites `dvc.lock`; `-s` runs only the named stage, no upstream)

```bash
uv run dvc repro fetch_raw                              # every fetcher
uv run dvc repro fetch_game_schedule                    # schedule
uv run dvc repro fetch_euroleague_fantasy_stats         # fantasy stats (season average, one pass)
uv run dvc repro fetch_kaggle_euroleague                # Kaggle history
uv run dvc repro fetch_euroleague_live_boxscores        # euroleague.net box score deltas
uv run dvc repro fetch_euroleague_live_headers          # euroleague.net header deltas
uv run dvc repro -s merge_boxscore merge_headers        # merge Kaggle + live, offline (no fetch)
uv run dvc repro merge_boxscore merge_headers           # same, but re-runs the fetchers first
```

**Fantasy stats variants** (direct run, leaves `dvc.lock` alone; add `--delay 1.0` on a 429)

```bash
uv run python -m eupy.fetchers.fetch_euroleague_fantasy_stats                    # average per game, whole season
uv run python -m eupy.fetchers.fetch_euroleague_fantasy_stats --stats-type tot   # season total
uv run python -m eupy.fetchers.fetch_euroleague_fantasy_stats --rounds 1-6       # one fetch per round (up to the in-progress round)
```

**Not DVC stages yet** (direct run)

```bash
uv run python -m eupy.entity.build_team_name_map         # 1. team map
uv run python -m eupy.entity.build_player_name_map       # 2. player map (open players need a decision: /build-name-maps)
uv run python -m eupy.transform.build_boxscore_kpis      # box score + positions, opponent, home/away
uv run python -m eupy.transform.build_schedule_turns --season=E2026
```

**Dev loop**

```bash
uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests
uv run python -m eupy.registry check                     # pipeline/header lint (then: dvc | docs | html)
```
