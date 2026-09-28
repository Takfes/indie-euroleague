<!-- GENERATED — do not edit. Regenerated from the Inputs/Outputs/Final header of every script in src/eupy/. -->

# Data catalogue

> **GENERATED — do not edit.** Source of truth: the `Inputs` / `Outputs` / `Final` block in each script's module docstring. Stage = max(input stages) + 1; raw = stage 0. Stage numbers are display-only and may shift — refer to datasets by name. Lineage: [data-graph.md](data-graph.md).

## Raw datasets (`data/raw_data/`, git-ignored)

| Dataset | Path | Produced by | Consumed by | Refresh |
| --- | --- | --- | --- | --- |
| `basketballsphere_prices` | `data/raw_data/fantasy_prices/basketballsphere_prices.csv` | `fetch_basketballsphere_prices.py` | `resolve_player_names.py`, `resolve_team_names.py` | full overwrite per run |
| `euroleague_fantasy_stats/players` | `data/raw_data/euroleague_fantasy_stats/players.csv` | `fetch_euroleague_fantasy_stats.py` | — | full overwrite per run |
| `euroleague_fantasy_stats/head_coaches` | `data/raw_data/euroleague_fantasy_stats/head_coaches.csv` | `fetch_euroleague_fantasy_stats.py` | — | full overwrite per run |
| `euroleague_live/box_score` | `data/raw_data/euroleague_live/box_score/{season}_delta_{utc_timestamp}.csv` (+ state file `data/raw_data/euroleague_live/_state.json`) | `fetch_euroleague_live_boxscores.py` | `append_live_boxscores.py` | incremental: one delta file per run with new games |
| `euroleague_api/schedule` | `data/raw_data/euroleague_api/schedule_{season}.csv` | `fetch_euroleague_schedule.py` | `build_schedule_turns.py` | full overwrite per run (per season) |
| `kaggle_data/euroleague_box_score` | `data/raw_data/kaggle_data/euroleague_box_score.csv` | external (Kaggle, manual download) | `resolve_player_names.py`, `append_live_boxscores.py` | manual |
| `kaggle_data/euroleague_header` | `data/raw_data/kaggle_data/euroleague_header.csv` | external (Kaggle, manual download) | `resolve_team_names.py` | manual |
| `optimizer_input` | `data/raw_data/optimizer/optimizer_input.csv` | external (prepared manually; upstream feature/prediction script not built yet) | `optimize_squad.py` | manual |

The other `data/raw_data/kaggle_data/*.csv` files (comparison, play_by_play, players, points, teams) are not referenced by any script header yet.

## Produced datasets (`data/stage_XX/`)

| Dataset | Stage | Path | Produced by | Inputs | Final | `stage_99` symlink |
| --- | --- | --- | --- | --- | --- | --- |
| `player_name_crosswalk` | 01 | `data/stage_01/player_name_crosswalk.csv` | `resolve_player_names.py`, `apply_player_name_verdicts.py` (updates in place) | `basketballsphere_prices`, `kaggle_data/euroleague_box_score` | true | `data/stage_99/player_name_crosswalk.csv` |
| `team_name_crosswalk` | 01 | `data/stage_01/team_name_crosswalk.csv` | `resolve_team_names.py`, `apply_team_name_verdicts.py` (updates in place) | `basketballsphere_prices`, `kaggle_data/euroleague_header` | true | `data/stage_99/team_name_crosswalk.csv` |
| `schedule` | 01 | `data/stage_01/schedule.csv` | `build_schedule_turns.py` | `euroleague_api/schedule` | true | `data/stage_99/schedule.csv` |
| `team_round_turn` | 01 | `data/stage_01/team_round_turn.csv` | `build_schedule_turns.py` | `euroleague_api/schedule` | true | `data/stage_99/team_round_turn.csv` |
| `box_score_current` | 01 | `data/stage_01/box_score_current.csv` | `append_live_boxscores.py` | `kaggle_data/euroleague_box_score` (base season, default E2025), `euroleague_live/box_score` (all deltas) | false | — |
| `squad_solution` | 01 | `data/stage_01/squad_solution.csv` | `optimize_squad.py` | `optimizer_input` | true | `data/stage_99/squad_solution.csv` (link to be created after the first run — the stage file does not exist yet) |

Column notes for the schedule-derived datasets (both from `build_schedule_turns.py`):

- `schedule`: the raw schedule (20 columns, verbatim, same row order) plus a trailing `turn` (`T1` / `T2`). Within each `gameday`, the latest `date` is `T2` and every earlier date is `T1` (single-date round → all `T1`). The rule is an assumption, not an API field.
- `team_round_turn`: columns `team` (club code from `homecode` / `awaycode`), `round` (= raw `gameday`; not the raw `round` column, which is the phase label `RS`), `turn`. One row per team per round, sorted by `round`, `team`.

The `apply_*_name_verdicts.py` scripts read and rewrite the stage-1 crosswalk in place, so they add no stage of their own.

## Scripts

| Script | Inputs | Outputs | Final |
| --- | --- | --- | --- |
| `src/eupy/fetchers/fetch_basketballsphere_prices.py` | none (live: basketballsphere.com) | `basketballsphere_prices` | false |
| `src/eupy/fetchers/fetch_euroleague_fantasy_stats.py` | none (live: fantaking-api.dunkest.com; `EUROLEAGUE_FANTASY_AUTH_TOKEN`) | `euroleague_fantasy_stats/players`, `euroleague_fantasy_stats/head_coaches` | false |
| `src/eupy/fetchers/fetch_euroleague_live_boxscores.py` | none (live: live.euroleague.net) | `euroleague_live/box_score` | false |
| `src/eupy/fetchers/fetch_euroleague_schedule.py` | none (live: euroleague-advanced-api.eu) | `euroleague_api/schedule` | false |
| `src/eupy/transform/build_schedule_turns.py` | `euroleague_api/schedule` (`--input`; `--season`) | `schedule`, `team_round_turn` | true |
| `src/eupy/transform/append_live_boxscores.py` | `kaggle_data/euroleague_box_score` (`--base-season`, default E2025), `euroleague_live/box_score` (all deltas) | `box_score_current` | false |
| `src/eupy/entity/resolve_player_names.py` | `basketballsphere_prices`, `kaggle_data/euroleague_box_score` | `player_name_crosswalk` | true |
| `src/eupy/entity/apply_player_name_verdicts.py` | `player_name_crosswalk`, verdicts JSON (`--verdicts`) | `player_name_crosswalk` | true |
| `src/eupy/entity/resolve_team_names.py` | `basketballsphere_prices`, `kaggle_data/euroleague_header` | `team_name_crosswalk` | true |
| `src/eupy/entity/apply_team_name_verdicts.py` | `team_name_crosswalk`, verdicts JSON (`--verdicts`) | `team_name_crosswalk` | true |
| `src/eupy/optimize/optimize_squad.py` | `optimizer_input` (`--input`); scalars `--cash`, `--max-trades`, `--w-budget` | `squad_solution` | true |
