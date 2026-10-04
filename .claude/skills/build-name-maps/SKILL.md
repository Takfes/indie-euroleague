---
name: build-name-maps
description: |
  Rebuild indie-euroleague's two name maps (JSON in `src/eupy/entity/maps/` plus the CSVs
  `data/stage_01/team_name_map.csv` and `data/stage_02/player_name_map.csv`) behind the `TeamNames` and `PlayerNames` lookups. Checks the prerequisite data,
  runs the two builders, then works through every open player (new transfers, rookies, odd spellings) with
  verdicts until none is left. Use after a new fetch brings new stats players, a new season's schedule, or
  whenever the maps look stale or `PlayerNames.unresolved()` is not empty.
---

# Build Name Maps

The builders and the engine live in `src/eupy/entity/` (see `specs/spec-name-maps.md`). This skill only calls them.
It writes no mapping logic of its own.

## 1. Check the prerequisites

All three files must exist; if one is missing, say which fetch or merge produces it and stop:

- `data/raw_data/game_schedule/schedule_*.csv`: `uv run dvc repro fetch_game_schedule`
- `data/raw_data/fantasy_stats/players.csv`: `uv run dvc repro fetch_euroleague_fantasy_stats`
- `data/stage_01/euroleague_box_score_merged.csv`: `uv run dvc repro merge_boxscore`

## 2. Build the maps

```bash
uv run python -m eupy.entity.build_team_name_map
uv run python -m eupy.entity.build_player_name_map
```

The team map must come first: the player engine uses it to scope candidates to the player's club. Read the
summary line. If a stats team code is missing from the team map, the entry's `notes` say so; fix the team map
source (the schedule), do not work around it in verdicts.

## 3. Resolve what is open

Open entries are in `data/stage_02/player_name_map.csv` (and the JSON next to the package) with `match_status` `needs_review` or
`no_candidate` (`PlayerNames.load().unresolved()` lists them). For each one:

- The row key is `stats_team` + `stats_name`. `notes` lists the fuzzy candidates, which can be older namesakes.
- Decide the full name: use the candidates, the box-score history, and the web when the player is new to the club
  or the league (a transfer has no games for the new club yet; a rookie has no box-score history).
- Record one verdict per player:
  - `confirmed` with `resolved_name` set to the full `First Last` name (it need not exist in the box score),
  - `no_match` when the player cannot be identified,
  - `rejected` when every candidate is wrong and the stats name should stay as it is.
  Every verdict needs a short `notes` rationale.

Write the verdicts to a JSON list, then ingest them as a new batch and rebuild:

```bash
uv run python -m eupy.entity.apply_player_name_verdicts --verdicts <file>.json --label <short-slug>
uv run python -m eupy.entity.build_player_name_map
```

The record shape is in the docstring of `apply_player_name_verdicts.py`. Batches are append-only files under
`data/curated/player_name_verdicts/`; a later batch wins on the same key. Never edit a batch or the map by hand.
Repeat until the summary reports no open entries.

## 4. Report

Give the number of entries per `match_status` for the player map, the number of teams in the team map, and the
verdicts recorded this run. The JSON maps are committed with the package (the CSVs are git-ignored data), so list the
JSON files as changed and commit them together with the new verdict batches.
