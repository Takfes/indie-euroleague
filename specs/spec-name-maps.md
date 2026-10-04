# Spec: player and team name maps (entity module rebuild)

## Goal

The pipeline reads the same players and teams from several sources, each spelling them differently:

- Fantasy stats (`fantasy_stats/players`): abbreviated names (`C. Jones`) and the fantasy app's club codes (`PAO`).
- Box scores (Kaggle history and the live delta): `JONES, CARLIK` and the box-score `team_id` codes (`PAN`).
- Schedule: full team names plus two code schemes per team.

Give downstream code two small, self-contained classes that turn any of these spellings into one normalized form:

- `PlayerNames.normalize("JONES, CARLIK")` and `PlayerNames.normalize("C. Jones", team="PAO")` both return `Carlik Jones`.
- `TeamNames.normalize("PAN")` and `TeamNames.normalize("PAO")` return the same team name (the schedule's `hometeam`).

The fantasy stats data is the trusted set. Players that do not appear in it are out of scope: they never get an entry.

## Scope

In scope: the `src/eupy/entity/` module rebuild, as below.

Also in scope: `transform/normalize_fantasy_stats.py` now takes names from `PlayerNames` (it used the removed crosswalk).

Out of scope, handled in follow-up reviews:

- DVC stages for the two builders (the entity pipeline stays `dvc = false`).
- Season history for teams: the team map covers whatever the schedule dataset covers (today the current season).

## Design

Two classes, two committed JSON maps inside the package, two builder scripts, one skill that drives both.

```
src/eupy/entity/
  team_names.py                    class TeamNames
  player_names.py                  class PlayerNames
  build_team_name_map.py           script: schedule -> team_name_map (JSON + CSV)
  build_player_name_map.py         script: stats + box score + verdicts -> player_name_map (JSON + CSV); holds the engine
  apply_player_name_verdicts.py    ingest script: agent verdicts -> data/curated/player_name_verdicts/
  matching.py                      library: normalize_name, exact candidate finder, lookup errors
  verdict_batches.py               library: append-only verdict batch store (load, merge, apply, ingest)
  maps/team_name_map.json          committed, written only by build_team_name_map.py, read by TeamNames
  maps/player_name_map.json        committed, written only by build_player_name_map.py, read by PlayerNames
data/stage_01/team_name_map.csv    same entries as the team JSON, registered dataset
data/stage_02/player_name_map.csv  same entries as the player JSON, registered dataset
```

The JSON maps live in the package so the classes need no data folder. They are committed because they contain agent
judgements that cannot be re-derived from raw data. Each builder also writes the same entries as a CSV under
`data/stage_NN/` (`team_name_map` at stage 1, `player_name_map` at stage 2): that CSV is the dataset the registry,
the catalogue and the graph know, and the JSON is its in-package copy, written by the same run. Stage numbers are
computed from the headers (the player map depends on the merged box score, a stage-1 dataset), not chosen. The verdict batches stay in `data/curated/`, the tracked record the
builder re-applies on every run. A rebuild from the same raw data and verdicts reproduces the map byte for byte.

### Lookup behaviour (both classes)

- Lookup is case, accent and punctuation insensitive: spellings are reduced with `matching.normalize_name`.
- `normalize(x)` returns the normalized form, or raises `UnknownNameError` (never guesses, never passes the input through).
- A spelling shared by two entries raises `AmbiguousNameError`; `PlayerNames.normalize(name, team=...)` resolves it with
  the player's club. `team` is a raw club code or name; it is normalized through `TeamNames` inside.
- The normalized form of an entry is itself a variant, so `normalize` is idempotent.
- Both classes load their JSON once (`load()`), keep it in memory and carry no other state.

### Team map

- Builder input: `data/raw_data/game_schedule/schedule_*.csv`. Distinct `(hometeam, homecode, hometv)`.
- Entry: `{"name": <hometeam>, "variants": [<hometeam>, <homecode>, <hometv>]}`, sorted by name. CSV columns: `name`,
  `variants` (joined with ` | `).
- The normalized name is the schedule's `hometeam` verbatim. On the real 2026 schedule these are long upper-case
  names (`PANATHINAIKOS AKTOR ATHENS`, `FC BARCELONA`); a shorter display name would be a later, separate change.
- Fails loudly if one code points to two teams or one team has two code pairs, and on an empty schedule.
- Whether the box-score `team_id` equals `homecode` or `hometv`, and whether the fantasy codes all appear, is checked
  against real data after the first fetch. A code missing from the map is reported by the player builder, never guessed.

### Player map and matching engine

One unit of work is a distinct `(name, team)` pair of the stats players, `team` being the fantasy club code. The unit's key
is `"<team>|<name>"`. Player ids are not used in the output, and not as a key.

Normalized form: the box-score spelling reordered to `First Last` and title-cased (`JONES, CARLIK` becomes `Carlik Jones`).

Stages, in order:

1. Exact, club-scoped. Pool: box-score players who played for the stats player's club in the most recent seasons
   (default: the latest two season codes in the merged box score). The club is compared after `TeamNames`
   normalization on both sides. Pool spellings are abbreviated like the stats names; normalized-string equality with exactly
   one pool player is `exact` and the only automatic accept.
2. Exact, unscoped. Everything else is looked up across every box-score spelling in history. A hit is `needs_review`
   (`matched_by` `exact_other_club`), never auto-accepted, because it may be an older namesake or the player at a
   previous club. No hit is `no_candidate`. There is no approximate (fuzzy) matching: it produced mostly wrong
   suggestions, and the agent stage covers every remaining case.
3. Agent verdicts. Each batch in `data/curated/player_name_verdicts/` is applied on top; a later batch wins on the same key.
   The agent handles what the first two stages leave open, typically players who just moved clubs (no games for the new
   club yet) and rookies with no box-score history. Statuses: `confirmed` (with the full name, which need not exist in the
   box score), `rejected`, `no_match`.

Map entry: `{"name", "variants", "team", "stats_name", "stats_team", "match_status", "matched_by", "notes"}`;
the CSV has the same columns, `variants` joined with ` | `.

- `name`: the normalized full name; `variants`: the stats spelling, the box-score spelling(s) as written and the normalized name.
- `team`: the normalized club of the stats player. It is metadata used to disambiguate, not part of the lookup key.
- Entries with `exact` or `confirmed` carry the full name. `rejected` and `no_match` keep the abbreviated stats name as `name`,
  so a lookup still answers, and are marked as such.
- Open entries (`needs_review`, `no_candidate`) are written with their candidates in `notes` but excluded from lookup;
  `PlayerNames.unresolved()` lists them. A fully resolved map has none.

### Skill

One skill in `.claude/skills/build-name-maps/` refreshes both maps:

1. Check the prerequisite files exist (schedule CSV, merged box score, stats players) and name the fetch/merge step if not.
2. Run `build_team_name_map.py`, then `build_player_name_map.py`.
3. For each open entry the builder reports, research it (box-score history, the web), write a verdicts JSON, ingest it with
   `apply_player_name_verdicts.py` and rebuild; repeat until nothing is open.
4. Report the counts per status.

The skill adds no logic of its own: it only calls the scripts. `docs/run-guide.md` documents it.

## Pass criteria

- `TeamNames` and `PlayerNames` return the normalized form for every variant, are idempotent, and raise on unknown or
  ambiguous input; `team=` disambiguates.
- Builders are deterministic, validate their input (empty or conflicting data fails loudly) and write unchanged bytes
  on a rerun.
- Engine tests cover: club-scoped exact accept, same-abbreviation collision, out-of-club hit going to review,
  near-miss spelling left without candidate, verdict override with later batch winning, transfer and rookie via `confirmed`.
- `src/eupy/entity/` holds only the files in the layout above; the old resolvers, crosswalk accessors, `player_sources.py`
  and their tests are gone, and no spec, doc or header mentions them.
- `ruff check`, `ruff format --check` and the entity tests pass.
