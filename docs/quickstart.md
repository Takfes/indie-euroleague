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

Writes to `data/raw_data/euroleague_live/box_score/`, tracks progress in `data/raw_data/euroleague_live/_state.json`.

**Fantasy prices** (full overwrite — re-run any time to refresh, e.g. after a new round):

```bash
uv run python src/eupy/fetchers/fetch_basketballsphere_prices.py
```

Writes to `data/raw_data/fantasy_prices/basketballsphere_prices.csv`.

## Dev loop

```bash
uv run pytest                        # tests
ruff check src tests                 # lint
ruff format --check src tests        # format check
```
