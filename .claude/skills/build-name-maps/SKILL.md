---
name: build-name-maps
description: |
  Decide the open players of indie-euroleague's player name map. The map (`data/stage_02/player_name_map.csv`) is
  built from the fantasy stats and the box score; players it cannot match on its own (new transfers, rookies, odd
  spellings) get a decision recorded in `src/eupy/entity/maps/player_name_decisions.json`. Use after a fetch brings
  new stats players, or when `check_player_names` reports a box-score player missing from the stats.
---

# Build Name Maps

The builders and the engine live in `src/eupy/entity/` (see `specs/spec-name-maps.md`). This skill only calls them.
It writes no mapping logic of its own and edits one file: the decisions file. The map CSV is derived output; never
edit it.

## 1. Check the prerequisites

All three files must exist; if one is missing, say which fetch or merge produces it and stop:

- `data/raw_data/game_schedule/schedule_*.csv`: `uv run dvc repro fetch_game_schedule`
- `data/raw_data/fantasy_stats/players.csv`: `uv run dvc repro fetch_euroleague_fantasy_stats`
- `data/stage_01/euroleague_box_score_merged.csv`: `uv run dvc repro merge_boxscore`

## 2. Build the maps

```bash
uv run python -m eupy.entity.build_player_name_map
```

Read the summary line. The player engine scopes matches to the player's club through `TeamNames`, which is derived
from the schedule. If a stats team code is missing from the schedule, the entry's `notes` say so; refetch the
schedule, do not work around it with a decision.

The player builder recomputes every match from scratch: an exact match within the club (ignoring `Jr`/`Sr`/`II`/
`III`/`IV`) is accepted on its own, everything else is open. The decisions recorded in earlier runs are applied on
top, so only players with no decision yet are open.

## 3. Decide what is open

Open entries are the rows of `data/stage_02/player_name_map.csv` with `match_status` `needs_review` or
`no_candidate`. For each one:

- The row is identified by `stats_team` + `stats_name`. `notes` lists box-score players with the same name
  elsewhere in history, who can be older namesakes; there is no approximate matching, so a spelling difference
  leaves no candidate at all.
- Decide the full name: use the candidates, the box-score history, and the web when the player is new to the club
  or the league (a transfer has no games for the new club yet; a rookie has no box-score history). When the player
  is in the box score, use that spelling reordered to `First Last` and title-cased (for example
  `Mckinley Wright Iv`), so the box-score spelling stays a variant.
- Record the decision by adding an object to `decisions` in `src/eupy/entity/maps/player_name_decisions.json`:

  ```json
  {"stats_team": "PAO", "stats_name": "C. Payne", "match_status": "confirmed", "name": "Cameron Payne", "notes": "Web search 2026-10-05: signed from Valencia (Eurohoops)"}
  ```

  - `match_status`: `confirmed` (with the full `First Last` name in `name`), `no_match` when the player cannot be
    identified, or `rejected` when every candidate is wrong and the stats name should stay as it is (`name` empty
    for the last two);
  - `notes`: a short rationale, which names the source for a web-researched decision.

Then rebuild, which validates the decisions (`notes` is required, `confirmed` needs a `name`):

```bash
uv run dvc repro -s build_player_name_map
```

(the decisions file is a declared DVC input of that stage; `uv run python -m eupy.entity.build_player_name_map`
does the same without DVC.) Repeat until `check_player_names` no longer lists a box-score player who played but is
not in the stats; other open entries may stay (players who have not played yet). Open entries do not stop a flow.

`uv run python -m eupy.entity.check_player_names` lists the box-score players who played but are not in the stats (it
stops a flow only with `[transformation] names_mode = "pause"`). It names the likely stats entry and the full name to
decide it as (the box-score spelling reordered); record that as a `confirmed` decision for that entry, replacing an
earlier decision whose name differs from the box-score spelling. A player no stats entry resembles is missing from
the fantasy data: nothing to decide here. A decision never overrides an `exact` match; remove decisions that became
`exact` (the builder says nothing about them, the CSV shows `exact` for the row).

## 4. Report

Give the number of entries per `match_status` for the player map and the decisions recorded this run. The decisions file is committed with the package (the CSV maps are git-ignored data), so
list it as changed and commit it: it holds the agent's decisions.
