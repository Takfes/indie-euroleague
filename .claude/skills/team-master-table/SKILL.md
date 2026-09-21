---
name: team-master-table
description: Rebuild the unified EuroLeague team master table that joins basketnews team stats (offense/defense, all/home/away) and Dunkest defense-vs-position (what each team concedes to guards, forwards, centers) into one row per team. Use this whenever the user asks to combine/merge/consolidate the two team datasets into a single table, rebuild data/curated/team_master_table.xlsx, or wants an all-in-one team dataframe after either source dataset (basketnews-team-stats, dunkest-defense-positions) has been refreshed.
---

# EuroLeague team master table

Rebuilds `data/curated/team_master_table.xlsx`: a workbook whose `Master` sheet has
one row per team (20 teams), joining the two team datasets collected by other skills
in this repo. The raw source datasets and a column guide ride along as extra sheets.

## When to use this

Run this after either source dataset has been refreshed, or whenever the user wants a
single team-level table. It is a pure join step - it fetches nothing, so both source
CSVs must already exist:

- `data/basketnews-team-stats/basketnews_team_stats.csv`
- `data/dunkest-defense-positions/dunkest_defense_vs_position.csv`

If one is missing or stale, run its own skill first (`basketnews-team-stats`,
`dunkest-defense-positions`).

## How it works

The join logic lives in `src/build_team_master_table.py`; column explanations in
`src/team_master_column_guide.py`; the workbook writing (bold header, frozen header row,
autofilter, widths) is shared with the player builder in `src/master_workbook.py`.

- **Matching teams.** The sources use different team ids (Real Madrid is 368 in
  Basketnews, 45 in Dunkest) and four teams are spelled differently. Teams are matched
  by normalised name (accents, case and spacing ignored) after applying
  `TEAM_NAME_CROSSWALK` in the script: an explicit Dunkest-name -> Basketnews-name
  mapping for the names that differ:

  | Dunkest                    | Basketnews                        |
  | -------------------------- | --------------------------------- |
  | Hapoel IBI Tel Aviv        | Hapoel Shlomo Tel Aviv            |
  | Maccabi Rapyd Tel Aviv     | Maccabi Playtika Tel Aviv         |
  | Baskonia Vitoria-Gasteiz   | Kosner Baskonia Vitoria-Gasteiz   |
  | Virtus Bologna             | Virtus Segafredo Bologna          |

  There is no fuzzy matching. The match must be strictly one-to-one: if a team is left
  over on either side, or two teams collapse onto one, the build stops with an error
  naming the teams on each side.
- **New season / a renamed team (sponsor change).** Re-run the build. If it fails with
  `Team matching failed. Only in BN Team Stats: [...]. Only in Dunkest Defense vs Position: [...]`,
  pair the two spellings and add or correct one line in `TEAM_NAME_CROSSWALK`
  (`"<Dunkest name>": "<Basketnews name>"`). Stale entries are harmless (they simply
  never match). A new team needs nothing if both sources spell it the same way.
  Sanity check: for players in both player datasets the (Basketnews team, Dunkest team)
  pair should agree with the team match (only traded players differ).
- **Column prefixes.** Every source column in `Master` carries a short source prefix,
  defined once in `SOURCE_PREFIXES` in `src/team_master_column_guide.py`:
  `bnteam_` = Basketnews team stats, `dunkdvp_` = Dunkest defense vs position.
- **Master layout.** Identity first: `team_name`, `team_short_name`, `season` (all from
  Basketnews, unprefixed), `bnteam_team_id`, `dunkdvp_team_id`, then
  `bnteam_games_played_all/home/away`. Then the remaining Basketnews columns in source order
  (`bnteam_league_id`, then blocks `offense_all`, `offense_home`, `offense_away`,
  `defense_all`, `defense_home`, `defense_away`, 20 KPIs each), then the Dunkest columns in
  source order (`dunkdvp_guards_*`, `dunkdvp_forwards_*`, `dunkdvp_centers_*`, 8 stats each,
  fantasy points last). The Dunkest `team_name` and `season` are dropped (they repeat the
  identity columns). Rows are sorted by `team_name` (case-insensitive); 20 rows, no blanks
  (the build refuses blank cells). No `found_in` columns: every team is in both sources.
- **Reading the values.** Basketnews: `<offense|defense>_<all|home|away>_<kpi>` (`all` = all
  games, `home`/`away` = the split). The `offense_*` block holds the team's own numbers, the
  `defense_*` block what it concedes (opponent side) plus its own defensive actions; note
  `steals_opponent` sits under offense (opponent steals, kept as the page shows them).
  Dunkest: `<position>_<stat>` = per-game amount the team concedes to that position (higher
  = weaker defense against it); Dunkest computes it on players with 18+ minutes.

## Running it

```bash
cd <repo root>
uv run python src/build_team_master_table.py
```

Pass `--out PATH` to write elsewhere; by default it overwrites
`data/curated/team_master_table.xlsx`. The build checks that the Column Guide lists every
source column exactly once and warns if a source column has no explanation in
`src/team_master_column_guide.py` (add one there when a source gains a column).

## Output

| Sheet                         | Content                                                                                                                                              |
| ----------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Column Guide`                | `Source dataset`, `Column name` (as in that source), `Explanation`; every source column once (uncertain meanings say "(unverified)")                 |
| `Master`                      | The joined table, one row per team (layout above); header frozen along with `team_name`                                                              |
| `BN Team Stats`               | Raw `basketnews_team_stats.csv`, as-is                                                                                                               |
| `Dunkest Defense vs Position` | Raw `dunkest_defense_vs_position.csv`, as-is                                                                                                         |

## Committing the refreshed data

`.gitignore` has an exception for `data/curated/`, so a normal `git add` picks up the workbook:

```bash
git add data/curated/team_master_table.xlsx
git commit -m "chore(data): refresh team master table"
```
