# Spec: live headers replication + delta append

## Goal

- Replicate `kaggle_data/euroleague_header.csv` from `live.euroleague.net/api/Header` (any season, e.g. `E2022`).
- Keep the Kaggle header extended with live data: base (Kaggle season `E2025` by default) + every live delta.

## Scope

- `fetchers/fetch_euroleague_live_headers.py` — incremental per-season fetch → `raw_data/euroleague_net/headers/{season}_delta_{ts}.csv` (Kaggle schema) + `headers/_state.json`.
- `transform/merge_headers.py` — stateless rebuild → `stage_01/euroleague_header_merged.csv` (`final: false`), twin of `merge_boxscore.py`.
- Out of scope: repointing `resolve_team_names.py` at the appended dataset; per-overtime splits (not in the live API).

## Decisions

- `game_id` zero-padded (`E2025_001`) like Kaggle. Note: the box-score fetcher writes unpadded ids (`E2026_10`); joins across the two need normalizing.
- Extra time: live exposes only the final score → `score_extra_time_1_*`; periods 2–4 blank.
- Gamecode gaps exist (unplayed playoff games) → run ends after `--max-gap` (default 5) consecutive empty/unplayed codes; a `Live` game stops the run at once.
- Append: mirrors `merge_boxscore.py` — base = full Kaggle history (or `--base-season` / config `transformation.base_season`); a duplicate `game_id` fails validation (no silent last-wins).

## Pass criteria

- [x] Live E2022 replication equals Kaggle E2022: 328/328 rows, 0 differing fields (42 columns).
- [x] Append of Kaggle E2025 + E2026 delta: 402 + 10 rows; re-run byte-identical.
- [x] Unit tests for row mapping, run loop (gaps, live stop, resume), append rules (schema-drift guard reused from `merge_common.load_deltas`).
- [x] Catalogue + graph updated.
