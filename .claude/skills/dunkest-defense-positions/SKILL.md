---
name: dunkest-defense-positions
description: Fetch/refresh EuroLeague "defense vs position" data -- how many points, rebounds, assists, steals, blocks, turnovers, 3-pointers made and fantasy points each team concedes to guards, forwards and centers -- from dunkest.com's API and save it as one row per team to data/dunkest-defense-positions/dunkest_defense_vs_position.csv. Use this whenever the user asks to pull, update or refresh Dunkest defense-vs-position / DvP / matchup-difficulty data, wants to know which EuroLeague defenses are weakest against a position for fantasy picks, mentions dunkest.com's team stats pages, or wants opponent-strength features to join with the Dunkest player stats -- even if they just say "refresh the DvP data".
---

# Dunkest Defense vs Position

## What this does

Downloads the amount each of the 20 EuroLeague teams **concedes per game to a given position**
(guards / forwards / centers) for each stat on Dunkest's picker, and writes one wide row per team to
`data/dunkest-defense-positions/dunkest_defense_vs_position.csv`. Higher value = weaker defense against
that position (better fantasy matchup). Dunkest computes it on players with at least 18 minutes played.

## Why this is API calls, not a scrape

The page (`https://www.dunkest.com/en/euroleague/stats/teams/defense-vs-position?season_id=23&stats_id=25&position_id=1&sort_by=all&sort_order=desc`)
fills its table from a public JSON endpoint, no auth or cookies:

```
GET https://www.dunkest.com/api/stats/defense-vs-position?season_id=23&stats_id=25&position_id=1
-> [{"id":"32","name":"Anadolu Efes Istanbul","l3":"9.20","l5":"12.43","l10":"13.23","all":"13.33"}, ...]  # 20 teams
```

One request per (position, stat) combination: 3 positions x 8 stats = 24 requests, 0.5 s apart.
`sort_by` / `sort_order` in the page URL are client-side only; the API ignores them.

## How to run it

From the repo root:

```bash
python src/fetch_dunkest_defense_positions.py
```

Overwrites the CSV with a fresh pull; re-running with no new games gives an identical file. Options
(`--help` for all): `--out PATH`, `--season-id ID` (23 = 2025-26; older: 17 = 2024-25, 15 = 2023-24,
11 = 2022-23) with `--season LABEL` for the `season` column, `--window {all,l10,l5,l3}`, `--delay SECONDS`.

## Filters and defaults kept

- **Season**: `season_id=23` (2025-26), same season as `dunkest-player-stats`.
- **Window**: the API returns four values per team -- `all` (whole season), `l10`, `l5`, `l3` (last 10/5/3
  games). The page's default sort is `all`, so the CSV holds `all`. Use `--window` for a recent-form snapshot.
- Values are per-game averages conceded to that position, shown by the page rounded to 1 decimal; the CSV keeps
  the API's 2 decimals.
- The page offers no other filters (no home/away or period).

## Output columns

27 columns, 20 rows. `team_id, team_name, season` (ids and names identical to
`data/dunkest-data/player_stats.csv`, so the tables join on `team_id`), then `<position>_<stat>` in
position-major order (`guards_*`, `forwards_*`, `centers_*`), each with the same stat order (the picker's,
with the stats_id in brackets): `points` (4), `rebounds` (26), `assists` (5), `steals` (6), `blocks` (7),
`turnovers` (20), `3point_field_goals_made` (12), `fantasy_points` (25).
Example: `guards_points, guards_rebounds, ..., centers_3point_field_goals_made, centers_fantasy_points`.

If Dunkest adds a stat to the picker, add it to `STATS` in the script (find ids in the `#statsSelect` options of the page source).

## Verification after running

- `python -c "import csv; r=list(csv.DictReader(open('data/dunkest-defense-positions/dunkest_defense_vs_position.csv'))); print(len(r), len({x['team_id'] for x in r}), len(r[0]))"`
  should print `20 20 27`; no empty cells.
- Spot-check a few cells against the page (it shows 1 decimal), e.g. `guards_fantasy_points` for Baskonia = 15.48 (page: 15.5).
