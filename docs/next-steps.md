# Next steps

Deferred work, off the critical path.

- **No `data/stage_XX` dataset-path registry yet.** `resolve_player_names.py` and
  `apply_player_name_verdicts.py` hardcode their `data/stage_01/player_name_crosswalk.csv` path,
  same as the existing fetchers hardcode their `raw_data` paths. Expected until a registry is
  built (tracked as a standing gap alongside the catalogue/graph generator, not a one-off here).
- **`euroleague_box_score.csv` has at least 2 known cases of one real player under two different
  `player_id`s across seasons** (e.g. Lucas Mari: `P011974`/E2022 vs `P010754`/E2023, same club —
  likely an accent-spelling-driven re-mint upstream; also Marko Simonovic, not in the current price
  snapshot). Not fixable by the name-resolution pipeline; worth knowing about for any future
  box-score analysis that assumes one `player_id` per player.
