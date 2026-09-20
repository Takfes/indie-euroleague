---
name: player-master-table
description: Rebuild the unified EuroLeague player master table that joins basketnews advanced stats, basketnews on/off stats, Dunkest fantasy stats, and basketballsphere fantasy prices into one row per player. Use this whenever the user asks to combine/merge/consolidate the separate EuroLeague datasets into a single table, rebuild data/player-master-table/player_master_table.csv, or wants an all-in-one player dataframe with stats and price after any of the four source datasets (basketnews-players-stats, basketnews-onoff-stats, dunkest-data, euroleague-fantasy) has been refreshed.
---

# EuroLeague player master table

Rebuilds `data/player-master-table/player_master_table.csv`: one row per
player, joining together the stats/price data collected by four other
skills in this repo.

## When to use this

Run this after any of the four source datasets have been refreshed, or
whenever the user wants a single player-level table instead of four
separate ones. It is a pure join step - it does not fetch anything itself,
so all four source CSVs must already exist:

- `data/basketnews-players-stats/basketnews_players_advanced_stats.csv`
- `data/basketnews-onoff-stats/onoff_stats.csv`
- `data/dunkest-data/player_stats.csv`
- `data/euroleague-fantasy/basketballsphere_prices.csv`

If one is missing or stale, run its own skill first
(`basketnews-player-advanced-stats`, `basketnews-onoff-stats`,
`dunkest-player-stats`, `basketballsphere-fantasy-prices`).

## How it works

The join logic lives in `src/build_player_master_table.py` at the repo
root (not inside this skill folder, since it's a general project script,
not a one-off scraper). The parts worth knowing before re-running or
extending it:

- **Reshaping the two basketnews sources.** Both come split across
  multiple rows per player (advanced stats: one row per offensive/defensive
  mode; on/off stats: one row per total/offensive/defensive view). These
  are collapsed to one row per player first - the advanced-stats rows are
  column-disjoint per mode so a plain "first non-null value" collapse is
  safe, but the on/off rows share column names across views with genuinely
  *different* values per view, so those get a `_tot`/`_off`/`_def` suffix
  instead of being collapsed. A handful of players (5 out of 336) have
  rows for two teams because they were traded mid-season; only the
  team-stint with the most games played is kept.
- **Matching players across sources.** There's no shared player ID across
  basketnews / Dunkest / basketballsphere, so players are matched by
  normalized full name (accents stripped, lowercased). That covers ~90%+
  of cases. For the rest - mostly nicknames, e.g. Dunkest/basketballsphere
  list "Sasha Vezenkov" while basketnews lists "Aleksandr Vezenkov" - it
  falls back to normalized last name + team/club overlap, but only applies
  the fallback when it resolves to exactly one unambiguous candidate.
  Anyone still unmatched keeps their own row rather than being dropped or
  force-merged into the wrong player.
- **Column layout.** Identity columns (`player_name`, `team_name`,
  `position`, `season`, `games_played`) are deduplicated to one column
  each, preferring basketnews-players-stats, then Dunkest, then
  basketballsphere. All other columns keep a source prefix (`bnadv_`,
  `bnoo_`, `dunk_`) because they're genuinely distinct measurements (e.g.
  `dunk_fouls_received` is Dunkest's own count, not the same field as
  `bnadv_fouls_received`) - collapsing those would silently discard real
  data. `price` and `price_rank` from basketballsphere are appended last,
  as the rightmost columns.
- **Head coaches are excluded.** basketballsphere's price list includes a
  `role` of `head_coach` alongside `player`; coaches have no player stats
  in any of the other three sources, so they're filtered out before
  joining.

## Running it

```bash
cd <repo root>
uv run python src/build_player_master_table.py
```

(`uv run` picks up `pandas`, already available transitively via this
repo's `pyproject.toml`/`uv.lock`.) Pass `--out PATH` to write elsewhere;
by default it overwrites `data/player-master-table/player_master_table.csv`.

## Output and current coverage

As of the last run: 444 unique players, 125 columns. Coverage per source
(a player can be missing from some sources and still appear, since the
join is an outer join throughout):

| Source | Players present |
|---|---|
| basketnews-players-stats / basketnews-onoff-stats | 336 |
| Dunkest | 305 |
| basketballsphere (price) | 326 |
| All four sources | 213 |

These numbers will shift as the source datasets are refreshed - re-run the
script rather than trusting this table after any source update.

## Committing the refreshed data

`.gitignore` has an explicit exception for this folder
(`!data/player-master-table/` + `!data/player-master-table/**`), so a
normal `git add` picks up the CSV without needing `-f`:

```bash
git add data/player-master-table/player_master_table.csv
git commit -m "chore(data): refresh player master table"
```
