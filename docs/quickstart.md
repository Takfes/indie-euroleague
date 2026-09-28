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
| `apply_player_name_verdicts.py` | Applies agent verdicts to the player crosswalk. |
| `player_crosswalk.py` | `PlayerNameCrosswalk` — read-only lookup class. |
| `resolve_team_names.py` | Builds/refreshes `team_name_crosswalk.csv` (club names). |
| `apply_team_name_verdicts.py` | Applies agent verdicts to the team crosswalk. |
| `team_crosswalk.py` | `TeamNameCrosswalk` — read-only lookup class. |

Regenerate: `uv run python src/eupy/entity/resolve_player_names.py` (or `resolve_team_names.py`). Full exact→fuzzy→agent flow: `resolve-player-names` / `resolve-team-names` skills.

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
