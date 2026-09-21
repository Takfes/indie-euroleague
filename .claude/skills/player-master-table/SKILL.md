---
name: player-master-table
description: Rebuild the unified EuroLeague player master table that joins basketnews advanced stats, basketnews on/off stats, Dunkest fantasy stats, and basketballsphere fantasy prices into one row per player. Use this whenever the user asks to combine/merge/consolidate the separate EuroLeague datasets into a single table, rebuild data/player-master-table/player_master_table.xlsx, or wants an all-in-one player dataframe with stats and price after any of the four source datasets (basketnews-players-stats, basketnews-onoff-stats, dunkest-data, euroleague-fantasy) has been refreshed.
---

# EuroLeague player master table

Rebuilds `data/player-master-table/player_master_table.xlsx`: a workbook whose
`Master` sheet has one row per player, joining together the stats/price data
collected by four other skills in this repo (the raw source datasets and a
column guide ride along as extra sheets).

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
  instead of being collapsed (five metrics happen to be identical across two
  views, e.g. `offensive_rating_lineup` in total and offense; they are kept per
  view anyway). A handful of players (5 out of 336) have
  rows for two teams because they were traded mid-season; only the
  team-stint with the most games played is kept.
- **Matching players across sources.** There's no shared player ID across
  basketnews / Dunkest / basketballsphere, so players are matched by a
  normalized name key (`src/player_name_matching.py`): accents stripped,
  lowercased, hyphens treated as spaces, apostrophes/dots dropped, and
  trailing generational suffixes (jr, sr, ii, iii, iv, v) removed - so
  "Wade Baldwin IV" (basketnews, Dunkest) and "Wade Baldwin" (price list)
  share one key. Rows with no exact match are resolved against the
  still-unclaimed rows of the other source, and only when the rule yields
  exactly one candidate: (1) same surname (suffix excluded) + equivalent first
  name (identical or a known nickname group such as Sasha/Aleksandr), or a
  loosely compatible one (prefix such as Zac/Zachary, near-identical spelling)
  on the same team, with the team as tie-breaker when several remain; (2) same team + near-identical full name (Gur Lavi /
  Gur Lavy); (3) for Dunkest vs basketnews only: same surname + compatible
  team + identical games played, for nicknames no name rule can link (Iffe /
  Gabriel Lundberg). A price row spelled like a Dunkest row that was linked
  under another spelling follows that link. Team names are compared by token
  subset (or same first word + 3 shared tokens, absorbing sponsor changes such
  as "Maccabi Playtika/Rapyd Tel Aviv"). If two rows would collapse onto the
  same player, both are left unmerged, so different players never merge
  (Wade, Patrick and Kamar Baldwin stay three rows). Anyone still unmatched
  keeps their own row rather than being dropped or force-merged. After a
  source refresh, check the players present in only some sources for spelling
  variants the rules cannot link (the unit tests in
  `tests/test_player_name_matching.py` cover the rules).
- **Column layout.** Leading identity columns: `player_name`, `team_name`,
  `position`, `season`, `games_played`, taken from Dunkest first, then
  basketnews, then the price list. Right after them come two provenance columns:
  `found_in` (the sources holding the player, as codes joined with `", "` in fixed
  order, e.g. `dunk, elf`) and `found_in_count` (integer, 1-4). Codes: `dunk` =
  Dunkest, `bnadv` = basketnews advanced, `bnoo` = basketnews on/off, `elf` =
  fantasy prices; defined once in `FOUND_IN_SOURCES` in
  `src/player_master_column_guide.py` (edit there to rename). They come from the join
  provenance, not from non-null values; the run prints
  `found_in_null_disagreements` (0 = agrees with a null-based inference). `position` is Dunkest's value for every
  player present in Dunkest; players missing from Dunkest fall back to
  basketnews `positions` (e.g. `PG`, `SG,SF`), then the price list's `G`/`F`/`C`,
  so the cell is never blank (the run prints how many used the fallback). Then,
  in this order: `found_in`, `found_in_count`, Dunkest columns (`dunk_*`), basketnews advanced (`player_id`,
  then `bnadv_*`), basketnews on/off (`bnoo_*`: total `_tot`, then offense
  `_off`, then defense `_def`, source order inside each group), and last the
  fantasy `price`, `price_rank`. `season` exists only in basketnews (blank for
  players missing there) and traded players' `bnadv_*`/`bnoo_*` values come from
  their max-games stint while the identity columns follow Dunkest. Source prefixes stay because these are
  genuinely distinct measurements (e.g. `dunk_fouls_received` is Dunkest's own
  count, not the same field as `bnadv_fouls_received`).
- **Head coaches are excluded.** basketballsphere's price list includes a
  `role` of `head_coach` alongside `player`; coaches have no player stats
  in any of the other three sources, so they're filtered out before
  joining.

## Running it

```bash
cd <repo root>
uv run python src/build_player_master_table.py
```

(`uv run` picks up `pandas` and `openpyxl` from this repo's
`pyproject.toml`/`uv.lock`.) Pass `--out PATH` to write elsewhere; by default
it overwrites `data/player-master-table/player_master_table.xlsx`. The run
prints join statistics (players per source, players in all four, fallback name
matches, position-fallback players) and warns if a source column has no entry
in `src/player_master_column_guide.py`.

## Output and current coverage

The workbook has these sheets, in order:

| Sheet            | Content                                                                                                                                                                                              |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Column Guide`   | One row per column of every source dataset, plus `found_in`/`found_in_count` under `Master (derived)`: `Source dataset`, `Column name` (as in that source), `Explanation` (texts in `src/player_master_column_guide.py`; uncertain meanings say "(unverified)") |
| `Master`         | The joined table, one row per player (layout above); header frozen along with `player_name`                                                                                                          |
| `Dunkest`        | Raw `player_stats.csv`, as-is                                                                                                                                                                        |
| `BN Advanced`    | Raw `basketnews_players_advanced_stats.csv`, as-is                                                                                                                                                   |
| `BN On-Off`      | Raw `onoff_stats.csv`, long format, rows ordered total, offensive, defensive                                                                                                                         |
| `Fantasy Prices` | Raw `basketballsphere_prices.csv`, as-is (head coaches included; `Master` excludes them)                                                                                                             |

As of the last run: 436 unique players, 127 `Master` columns. Coverage per
source (a player can be missing from some sources and still appear, since the
join is an outer join throughout):

| Source                                            | Players present |
| ------------------------------------------------- | --------------- |
| basketnews-players-stats / basketnews-onoff-stats | 336             |
| Dunkest                                           | 305             |
| basketballsphere (price)                          | 326             |
| All four sources                                  | 220             |

These numbers will shift as the source datasets are refreshed - re-run the
script rather than trusting this table after any source update.

## Committing the refreshed data

`.gitignore` has an explicit exception for this folder
(`!data/player-master-table/` + `!data/player-master-table/**`), so a
normal `git add` picks up the workbook without needing `-f`
(`git check-ignore -v` on the xlsx reports that exception, not an ignore):

```bash
git add data/player-master-table/player_master_table.xlsx
git commit -m "chore(data): refresh player master table"
```
