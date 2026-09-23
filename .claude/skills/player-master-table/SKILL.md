---
name: player-master-table
description: Rebuild the unified EuroLeague player master table that joins basketnews advanced stats, basketnews on/off stats, Dunkest fantasy stats, per-player Kaggle KPIs, and basketballsphere fantasy prices into one row per player. Use this whenever the user asks to combine/merge/consolidate the separate EuroLeague datasets into a single table, rebuild data/curated/player_master_table.xlsx, or wants an all-in-one player dataframe with stats and price after any of the source datasets (basketnews-players-stats, basketnews-onoff-stats, dunkest-data, euroleague-fantasy, player-kpis) has been refreshed.
---

# EuroLeague player master table

Rebuilds `data/curated/player_master_table.xlsx`: a workbook whose
`Master` sheet has one row per player, joining together the stats/price data
collected by other skills in this repo (the source datasets and a
column guide ride along as extra sheets).

## When to use this

Run this after any source dataset has been refreshed, or
whenever the user wants a single player-level table instead of
separate ones. It is a pure join step - it does not fetch anything itself,
so all sources must already exist:

- `data/basketnews-players-stats/basketnews_players_advanced_stats.csv`
- `data/basketnews-onoff-stats/onoff_stats.csv`
- `data/dunkest-data/player_stats.csv`
- `data/euroleague-fantasy/basketballsphere_prices.csv`
- `data/curated/player_kpis.xlsx` (the Kaggle KPIs; a curated workbook, not a fetched csv)

If one is missing or stale, run its own skill first
(`basketnews-player-advanced-stats`, `basketnews-onoff-stats`,
`dunkest-player-stats`, `basketballsphere-fantasy-prices`). The Kaggle KPI workbook
comes from a two-step chain, run in this order (the first step needs the git-ignored
`data/kaggle-euroleague-data/` csv files, see the `player-game-stats` skill):

```bash
uv run python src/build_game_player_stats.py   # game-level dataset with PIR and usage proxy
uv run python src/build_player_kpis.py         # per-player KPIs (skills: player-game-stats, player-kpis)
uv run python src/build_player_master_table.py
```

If `player_kpis.xlsx` is missing, the master build stops with an error naming it and these two commands.

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
- **Player identity.** Each player gets a stable `player_key`
  (`src/player_identity.py`), assigned only *after* the matching below has
  fully resolved which rows are the same player (basketnews, Dunkest, price
  list and Kaggle are name-matched; the on/off rows ride on basketnews'
  `player_id`), in one pass over the final groups - so a player can never be
  split by which source's row happened to be matched first. A group reuses
  the key of any (source, source_id) link already in the previous
  `data/curated/player_alias_table.xlsx` (the smallest, if its rows hold
  several), otherwise takes its name slug ("wade-baldwin", suffixed "-2",
  "-3" if that key is already taken). That table (`Alias Table`: one row per source row with
  its player_key, plus a wide `Identity View` for eyeballing every source's
  raw name/id side by side) is a build output rewritten each run; the
  matching itself never reads it.
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
  keeps their own row rather than being dropped or force-merged.
- **Matching the Kaggle KPI players.** They are matched last, against the rows the
  four other sources produced. Kaggle names are `LAST, FIRST` in capitals;
  `kaggle_display_name` (in `src/player_name_matching.py`) turns them into "First Last"
  (`BALDWIN IV, WADE` -> "Wade Baldwin IV", multi-word surnames and hyphens kept) before
  the usual normalisation. Order of attempts: exact name key; a spelling another source
  uses for the same row (`map_alternate_spellings`, e.g. Dunkest's "Iffe" Lundberg vs
  basketnews "Gabriel"); then the fallback rules above with the team as tie-breaker
  (no games-played rule) plus one Kaggle-only rule for a compound surname on the same
  team ("Nigel Hayes" vs "Nigel Hayes-Davis"). The team comes from `KAGGLE_TEAM_CROSSWALK`
  in `src/build_player_master_table.py` (Kaggle three-letter code -> the master's Dunkest
  team name); the build stops on an unmapped code and names it. After a new season, rerun
  and check that players present in both Dunkest and Kaggle sit on the same team.
  Kaggle players nobody else lists (mostly DNP-only youth players) get their own row
  with a blank `position`. After a
  source refresh, check the players present in only some sources for spelling
  variants the rules cannot link (the unit tests in
  `tests/test_player_name_matching.py` cover the rules).
- **Column layout.** The order, the dropped duplicates and the excluded columns live in
  `src/player_master_layout.py`; a source column the layout does not place makes the build raise, so a new
  one has to be added there on purpose. The first columns are, exactly: `player_name`, `team_name_hist`,
  `team_name_current`, `canonical_team_name`, `position`, `found_in`, `found_in_count`, `games_played`,
  `kag_minutes_avg`, `kag_minutes_pct`, `price`, `kag_pir_avg`, `kag_pir_sd`, `kag_pir_per_min`,
  `kag_pir_per_min_sd`, `pir_per_credit`, `pir_per_min_per_credit`, `expected_pir`, `breakeven_pir`,
  `expected_price_change`, `expected_price_next_round`. Then `capital_yield_pct`, `season` and the id /
  sample-size columns, then the raw stats (production and availability, offense, defense, then team and lineup
  level: `dunk_plus_minus` and all `bnoo_*`: total `_tot`, offense `_off`, defense `_def`, source order inside
  each group), then the 11 signed contribution shares (`kag_*_contribution_pct`, they sum to 100), then the
  Kaggle distribution KPIs. Within a stat family the sub-order is one pattern: per-game attempts, season-total
  attempts, per-game makes, season-total makes, then the percentage / rate.
  - Teams. `team_name_hist` is Dunkest, then basketnews, then the price list, then Kaggle (the former
    `team_name`; it can lag a player's current team). `team_name_current` is the price list club only, no
    fallback, blank without a price row (price-list spelling, e.g. "Milano", not the canonical names).
    `canonical_team_name` resolves `team_name_hist` onto one of the 20 canonical names in
    `data/curated/team_kpis.xlsx` (`resolve_team_name`: exact/`teams_compatible()` match, then fuzzy, then the
    manual `TEAM_NAME_OVERRIDES`); blank if there is no current match (e.g. "Besiktas", not a EuroLeague team
    this season).
  - Position and provenance. `position` is Dunkest's value for every player present in Dunkest; players missing
    from Dunkest fall back to basketnews `positions`, normalised from its 5-position scheme (PG/SG/SF/PF/C,
    multi-position combos take the first-listed token) onto the Dunkest/price-list G/F/C buckets
    (`normalize_bn_positions`), then the price list's `G`/`F`/`C`, so the cell is never blank unless all three
    sources lack it. `found_in` lists the sources holding the player as codes joined with `", "` in fixed order
    (e.g. `dunk, elf`), `found_in_count` counts them (1-5). Codes: `dunk` = Dunkest, `bnadv` = basketnews
    advanced, `bnoo` = basketnews on/off, `kag` = Kaggle KPIs, `elf` = fantasy prices; defined once in
    `FOUND_IN_SOURCES` in `src/player_master_column_guide.py`. They come from the join provenance, not from
    non-null values; the run prints `found_in_null_disagreements` (0 = agrees with a null-based inference) and
    how many players used the position fallback. `season` exists only in basketnews (blank for players missing
    there) and traded players' `bnadv_*`/`bnoo_*` values come from their max-games stint while the identity
    columns follow Dunkest.
  - Derived columns. `kag_minutes_pct` = `kag_minutes_avg` / 40 x 100 (0-100 scale). `expected_pir` =
    `kag_pir_avg_recent` (recent-5 average PIR). `pir_per_credit` (`kag_pir_avg_recent` / `price`) and
    `pir_per_min_per_credit` (`kag_pir_per_min` / `price`) are blank when the KPI or the price is missing or the
    price is 0. The unofficial price projection - `breakeven_pir` (`0.9 x price`), `expected_price_change`
    (`(kag_pir_avg - breakeven_pir) / 10`), `capital_yield_pct` (`expected_price_change / price x 100`) and
    `expected_price_next_round` (`price + expected_price_change`) - is the community-reverse-engineered price
    formula from `docs/rules.md`, treating `kag_pir_avg` (season average, not recent-5) as the Round score; an
    ESTIMATE only (open question: whether the real Round score includes a 10% team-win bonus that `kag_pir_avg`
    does not), blank under the same conditions.
  - Duplicates (`DUPLICATE_COLUMNS`). A stat two sources both report appears once, decided on value agreement
    (direct comparison, correlation, agreement with the Kaggle box-score means), precision and completeness:
    per-game counts keep Dunkest over basketnews advanced; shooting percentages (FG, 3P, FT, TS) keep the Kaggle
    KPI (exact, blank without attempts; note it is a 0-1 fraction, the rest are 0-100); the basketnews team
    ratings keep the on/off total view. Five on/off metrics identical across two views (e.g.
    `offensive_rating_lineup` in total and offense) are kept per view anyway.
  - Exclusions (`EXCLUDED_COLUMNS`). Ids and sample-size columns (`price_rank`, `dunk_cr`, `dunk_min`,
    `dunk_slug`, `player_id`, `bnadv_points`, `kag_player_id`, `kag_player_name_raw`, `kag_player_name`,
    `kag_team_id`, `kag_games_played`, `kag_recent_games`) are still computed but dropped as the very last
    step; remove an entry from the list to bring the column back at its place in the layout.
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
it overwrites `data/curated/player_master_table.xlsx`. The run
prints join statistics (players per source, players in all four and all five, fallback name
matches, Kaggle matching counts, position-fallback players) and warns if a source column has no entry
in `src/player_master_column_guide.py`.

## Output and current coverage

The workbook has these sheets, in order:

| Sheet            | Content                                                                                                                                                                                              |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Column Guide`   | One row per `Master` column, in `Master` order: `Source dataset` (the source, or `Master (derived)`), `Column name` (as in the Master), `Explanation` (texts in `src/player_master_column_guide.py`; uncertain meanings say "(unverified)"; a kept duplicate names the columns dropped in its favour) |
| `Master`         | The joined table, one row per player (layout above); header frozen along with `player_name`                                                                                                          |
| `Source Column Guide` | One row per column of every source sheet below, named as in that source                                                                                                                       |
| `Dunkest`        | Raw `player_stats.csv`, as-is                                                                                                                                                                        |
| `BN Advanced`    | Raw `basketnews_players_advanced_stats.csv`, as-is                                                                                                                                                   |
| `BN On-Off`      | Raw `onoff_stats.csv`, long format, rows ordered total, offensive, defensive                                                                                                                         |
| `Fantasy Prices` | Raw `basketballsphere_prices.csv`, as-is (head coaches included; `Master` excludes them)                                                                                                             |
| `Player KPIs`    | The KPI workbook's table (`data/curated/player_kpis.xlsx`), as-is                                                                                                                                    |

As of the last run: 446 unique players, 243 `Master` columns; 341 of the 351 Kaggle players
joined an existing row, 10 added a new row (see the increment-2 adjudication in
`KAGGLE_PLAYER_IDENTITY_OVERRIDES`/`src/build_player_master_table.py` for why those 10 stay
unmatched). Coverage per source (a player can be missing from some sources and still appear, since
the join is an outer join throughout):

| Source                                            | Players present |
| ------------------------------------------------- | --------------- |
| basketnews-players-stats / basketnews-onoff-stats | 336             |
| Dunkest                                           | 305             |
| basketballsphere (price)                          | 326             |
| Kaggle KPIs                                       | 351             |
| All four original sources                         | 220             |
| All five sources                                  | 220             |

These numbers will shift as the source datasets are refreshed - re-run the
script rather than trusting this table after any source update.

## Committing the refreshed data

The run also updates `data/curated/player_alias_table.xlsx` (the checked-in
`player_key` alias table + identity view; see "Player identity" above) -
commit it alongside the master table, since the next run reuses its
(source, source_id) -> `player_key` links to keep keys stable.

`.gitignore` has an explicit exception for this folder
(`!data/curated/` + `!data/curated/**`), so a
normal `git add` picks up both workbooks without needing `-f`
(`git check-ignore -v` on an xlsx reports that exception, not an ignore):

```bash
git add data/curated/player_master_table.xlsx data/curated/player_alias_table.xlsx
git commit -m "chore(data): refresh player master table"
```
