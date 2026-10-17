# Spec: euroleague.net update flow with a names check

## Goal

One DVC-driven flow that refreshes the working datasets (euroleague.net box score, fantasy stats), merges the euroleague.net data only, resolves player names between the fantasy stats and the box score, and stops for a decision only when a stats player is unmatched.

## Scope

- Merge scripts (`merge_boxscore`, `merge_headers`) read the live deltas only by default; the deltas carry their own header. Kaggle is optional (`[transformation] include_kaggle`, `--include-kaggle`) and is not a declared DVC dependency of the merge.
- Fantasy stats mode comes from `[acquisition] fantasy_rounds` / `fantasy_stats_type` (CLI flags override), because the DVC stage passes no arguments.
- `check_player_names`: read-only report of stats players missing from the player map or still open; exit 0 / 1.
- `build_player_name_map` does not fail on open players (stats players with no box-score match are expected). Names match without `Jr`/`Sr`/`II`/`III`/`IV`. The agent decisions are a declared input (`player_name_decisions`). `normalize_fantasy_stats` and `build_team_name_map` are gone: nothing read the former's output, and the team lookup is derived from the schedule.
- `check_player_names` compares the datasets: in both (ok), stats only (info), box score only with minutes played (listed with where found and the likely stats entry). Mode `[transformation] names_mode`: `continue` (default, exit 0) or `pause` (exit 1 on box-score-only players; stats-only never pauses).
- Pipelines: `fetch_update` (box score, fantasy stats, schedule), `live_headers` (headers fetch + merge, kept and runnable alone, not in the flows: nothing reads the merged headers), `fetch_kaggle` (separate flow; `fetch_raw` is gone), `entity` and `workbook` are DVC pipelines, `build_boxscore_kpis` sits in `kpis` (`dvc = false`).
- `check_player_names` writes a report (`player_name_check`) so a flow can depend on it.
- `build_eubook` (`transform/`): the pipeline version of `scripts/dataprep.py`, which stays untouched; output `eubook.xlsx` (final) plus an untracked round-stamped copy.
- Flows are `[wrappers]` entries, emitted as aggregate DVC stages: `refresh_data` (fetch, merge, names) and `refresh_and_build_eubook` (the same plus the workbook).

Out of scope: backfilling past seasons, deleting Kaggle data, changes to `scripts/dataprep.py`.

## Pass criteria

- Merged outputs hold only delta rows; with `include_kaggle` the previous behaviour returns (live deltas replace Kaggle rows of the seasons they cover).
- No deltas and no Kaggle fails with an actionable message; deltas with different headers fail.
- `check_player_names` lists box-score players with minutes that no stats player resolves to (club, games, rounds, likely stats entry) and exits 1 for them only in `pause` mode; stats players with no box-score match are listed and never fail it.
- Open players do not stop the map builder.
- `uv run dvc repro --dry refresh_and_build_eubook` lists fetchers, merges, player map, check, then the workbook; the Kaggle fetcher is not in it.
- `build_eubook` gives the same sheets as `scripts/dataprep.py` on the same data.
- `uv run pytest`, `ruff check`, `ruff format --check`, `eupy.registry check` are clean.

## Known caveats

- `fantasy_rounds = "all"` and a round selection write different shapes into `players.csv` (`round` column, average vs total); name matching ignores it, `scripts/dataprep.py` depends on it.
- The team map needs the schedule, so the chain also fetches `game_schedule`.
- Without Kaggle the name pool is E2026 only: players with no E2026 game yet need an agent decision until they play.
