# Spec: euroleague.net update flow with a name-match gate

## Goal

One DVC-driven flow that refreshes the working datasets (euroleague.net box score and headers, fantasy stats), merges the euroleague.net data only, resolves player names between the fantasy stats and the box score, and stops for a decision only when a stats player is unmatched.

## Scope

- Merge scripts (`merge_boxscore`, `merge_headers`) read the live deltas only by default; the deltas carry their own header. Kaggle is optional (`[transformation] include_kaggle`, `--include-kaggle`) and is not a declared DVC dependency of the merge.
- Fantasy stats mode comes from `[acquisition] fantasy_rounds` / `fantasy_stats_type` (CLI flags override), because the DVC stage passes no arguments.
- `check_player_names`: read-only report of stats players missing from the player map or still open; exit 0 / 1.
- `build_player_name_map` exits 1 while entries are open, after writing its files, so the DVC chain stops before `normalize_fantasy_stats`.
- Pipelines: new `fetch_netboxscore_and_fantasystats` (the three euroleague.net/fantasy fetchers; `fetch_raw` keeps schedule and Kaggle), `entity` is a DVC pipeline, `build_boxscore_kpis` sits in `kpis` (`dvc = false`).
- Wrapper: `dvc repro normalize_fantasy_stats` (its upstream is the whole chain).

Out of scope: backfilling past seasons, deleting Kaggle data, a DVC dependency on the map JSON (decisions are applied with `dvc repro -s -f build_player_name_map`), changes to `apps/teamstats.py`.

## Pass criteria

- Merged outputs hold only delta rows; with `include_kaggle` the previous behaviour returns (live deltas replace Kaggle rows of the seasons they cover).
- No deltas and no Kaggle fails with an actionable message; deltas with different headers fail.
- `check_player_names` exits 1 with the unmatched list, 0 when none; box-score-only players never fail it.
- The builder returns the open count and the process exits 1 while any is open.
- `uv run dvc repro --dry normalize_fantasy_stats` lists fetchers, merge, team map, player map, normalize in that order; the Kaggle fetcher is not in it.
- `uv run pytest`, `ruff check`, `ruff format --check`, `eupy.registry check` are clean.

## Known caveats

- `fantasy_rounds = "all"` and a round selection write different shapes into `players.csv` (`round` column, average vs total); name matching ignores it, `apps/teamstats.py` depends on it.
- The team map needs the schedule, so the chain also fetches `game_schedule`.
- Without Kaggle the name pool is E2026 only: players with no E2026 game yet need an agent decision until they play.
