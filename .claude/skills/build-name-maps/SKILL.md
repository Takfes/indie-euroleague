---
name: build-name-maps
description: |
  Rebuild indie-euroleague's two name maps (JSON in `src/eupy/entity/maps/` plus the CSVs
  `data/stage_01/team_name_map.csv` and `data/stage_02/player_name_map.csv`) behind the `TeamNames` and
  `PlayerNames` lookups. Checks the prerequisite data, runs the two builders, then decides every open player
  (new transfers, rookies, odd spellings) until none is left. Use after a new fetch brings new stats players, a
  new season's schedule, or whenever the maps look stale or `PlayerNames.unresolved()` is not empty.
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

The team map must come first: the player engine uses it to scope matches to the player's club. Read the summary
line. If a stats team code is missing from the team map, the entry's `notes` say so; fix the team map source (the
schedule), do not work around it with a decision.

The player builder recomputes every exact match from scratch. The decisions the agent made in earlier runs are read
back from `src/eupy/entity/maps/player_name_map.json` and carried forward, so only players with no decision yet are
open.

## 3. Decide what is open

Open entries are in `src/eupy/entity/maps/player_name_map.json` with `match_status` `needs_review` or
`no_candidate` (`data/stage_02/player_name_map.csv` shows the same rows). For each one:

- The entry is identified by `stats_team` + `stats_name`. `notes` lists box-score players with the same name
  elsewhere in history, who can be older namesakes; there is no approximate matching, so a spelling difference
  leaves no candidate at all.
- Decide the full name: use the candidates, the box-score history, and the web when the player is new to the club
  or the league (a transfer has no games for the new club yet; a rookie has no box-score history). When the player
  is in the box score, use that spelling reordered to `First Last` and title-cased (for example
  `Mckinley Wright Iv`), so the box-score spelling stays a variant.
- Record the decision on that entry in the JSON by setting two fields:
  - `match_status`: `confirmed` (also set `name` to the full `First Last` name), `no_match` when the player cannot
    be identified, or `rejected` when every candidate is wrong and the stats name should stay as it is;
  - `notes`: a short rationale, which names the source for a web-researched decision.

Then rebuild, which validates the decisions (`notes` is required, `confirmed` needs a `name`), recomputes the
variants and rewrites the JSON and the CSV:

```bash
uv run python -m eupy.entity.build_player_name_map
```

Repeat until the summary reports no open entries. Edit only open entries: a decision never overrides an `exact`
match, and changing any other field has no effect because the builder recomputes it.

## 4. Report

Give the number of entries per `match_status` for the player map, the number of teams in the team map, and the
decisions recorded this run. The JSON maps are committed with the package (the CSVs are git-ignored data), so list
the JSON files as changed and commit them: the player JSON holds the agent's decisions.
