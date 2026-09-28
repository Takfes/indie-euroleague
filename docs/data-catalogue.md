<!-- GENERATED — do not edit. Regenerated from the Inputs/Outputs/Final header of every script in src/eupy/. -->

# Data catalogue

> **GENERATED — do not edit.** Source of truth: the `Inputs` / `Outputs` / `Final` block in each script's module docstring. Stage = max(input stages) + 1; raw = stage 0. Stage numbers are display-only and may shift — refer to datasets by name. Lineage: [data-graph.md](data-graph.md).

## Raw datasets (`data/raw_data/`, git-ignored)

| Dataset | Path | Produced by | Consumed by | Refresh |
| --- | --- | --- | --- | --- |
| `basketballsphere_prices` | `data/raw_data/fantasy_prices/basketballsphere_prices.csv` | `fetch_basketballsphere_prices.py` | `resolve_player_names.py`, `resolve_team_names.py` | full overwrite per run |
| `euroleague_fantasy_stats/players` | `data/raw_data/euroleague_fantasy_stats/players.csv` | `fetch_euroleague_fantasy_stats.py` | — | full overwrite per run |
| `euroleague_fantasy_stats/head_coaches` | `data/raw_data/euroleague_fantasy_stats/head_coaches.csv` | `fetch_euroleague_fantasy_stats.py` | — | full overwrite per run |
| `euroleague_live/box_score` | `data/raw_data/euroleague_live/box_score/{season}_delta_{utc_timestamp}.csv` (+ state file `data/raw_data/euroleague_live/_state.json`) | `fetch_euroleague_live_boxscores.py` | — | incremental: one delta file per run with new games |
| `euroleague_api/schedule` | `data/raw_data/euroleague_api/schedule_{season}.csv` | `fetch_euroleague_schedule.py` | — | full overwrite per run (per season) |
| `kaggle_data/euroleague_box_score` | `data/raw_data/kaggle_data/euroleague_box_score.csv` | external (Kaggle, manual download) | `resolve_player_names.py` | manual |
| `kaggle_data/euroleague_header` | `data/raw_data/kaggle_data/euroleague_header.csv` | external (Kaggle, manual download) | `resolve_team_names.py` | manual |

The other `data/raw_data/kaggle_data/*.csv` files (comparison, play_by_play, players, points, teams) are not referenced by any script header yet.

## Produced datasets (`data/stage_XX/`)

| Dataset | Stage | Path | Produced by | Inputs | Final | `stage_99` symlink |
| --- | --- | --- | --- | --- | --- | --- |
| `player_name_crosswalk` | 01 | `data/stage_01/player_name_crosswalk.csv` | `resolve_player_names.py`, `apply_player_name_verdicts.py` (updates in place) | `basketballsphere_prices`, `kaggle_data/euroleague_box_score` | true | `data/stage_99/player_name_crosswalk.csv` |
| `team_name_crosswalk` | 01 | `data/stage_01/team_name_crosswalk.csv` | `resolve_team_names.py`, `apply_team_name_verdicts.py` (updates in place) | `basketballsphere_prices`, `kaggle_data/euroleague_header` | true | `data/stage_99/team_name_crosswalk.csv` |

The `apply_*_name_verdicts.py` scripts read and rewrite the stage-1 crosswalk in place, so they add no stage of their own.

## Scripts

| Script | Inputs | Outputs | Final |
| --- | --- | --- | --- |
| `src/eupy/fetchers/fetch_basketballsphere_prices.py` | none (live: basketballsphere.com) | `basketballsphere_prices` | false |
| `src/eupy/fetchers/fetch_euroleague_fantasy_stats.py` | none (live: fantaking-api.dunkest.com; `EUROLEAGUE_FANTASY_AUTH_TOKEN`) | `euroleague_fantasy_stats/players`, `euroleague_fantasy_stats/head_coaches` | false |
| `src/eupy/fetchers/fetch_euroleague_live_boxscores.py` | none (live: live.euroleague.net) | `euroleague_live/box_score` | false |
| `src/eupy/fetchers/fetch_euroleague_schedule.py` | none (live: euroleague-advanced-api.eu) | `euroleague_api/schedule` | false |
| `src/eupy/entity/resolve_player_names.py` | `basketballsphere_prices`, `kaggle_data/euroleague_box_score` | `player_name_crosswalk` | true |
| `src/eupy/entity/apply_player_name_verdicts.py` | `player_name_crosswalk`, verdicts JSON (`--verdicts`) | `player_name_crosswalk` | true |
| `src/eupy/entity/resolve_team_names.py` | `basketballsphere_prices`, `kaggle_data/euroleague_header` | `team_name_crosswalk` | true |
| `src/eupy/entity/apply_team_name_verdicts.py` | `team_name_crosswalk`, verdicts JSON (`--verdicts`) | `team_name_crosswalk` | true |
