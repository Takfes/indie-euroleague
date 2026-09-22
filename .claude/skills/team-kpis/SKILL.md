---
name: team-kpis
description: Rebuild the derived team-level KPI workbook (pace factor, foul rate per 40 minutes drawn/committed, and positional funnel ratio for guards/forwards/centers) from the two team-level raw datasets into data/curated/team_kpis.xlsx. Use this whenever the user asks to refresh or change the derived team KPIs, or before rebuilding the team master table, which needs this workbook to exist.
---

# EuroLeague team KPIs (derived, no new data)

Builds `data/curated/team_kpis.xlsx` (sheets `Column Guide` and `Team KPIs`): one row per
team (20 teams), computed as pure arithmetic on the same two raw CSVs
`team-master-table` reads. No new data is fetched here.

- `data/basketnews-team-stats/basketnews_team_stats.csv`
- `data/dunkest-defense-positions/dunkest_defense_vs_position.csv`

Pipeline: raw CSVs -> **this** -> `team-master-table`.

## When to use this

After either raw source has been refreshed, or when a KPI definition changes. Both source
CSVs must already exist; if one is missing or stale, run its own skill first
(`basketnews-team-stats`, `dunkest-defense-positions`).

```bash
cd <repo root>
uv run python src/build_team_kpis.py
```

Then rebuild the master (`uv run python src/build_team_master_table.py`), which now depends
on this file.

## Definitions

Teams are matched one-to-one between the two sources with `match_teams()` from
`src/build_team_master_table.py` (same `TEAM_NAME_CROSSWALK`, no matching logic
reimplemented here).

| Column                       | Formula                                                                                     |
| ----------------------------- | -------------------------------------------------------------------------------------------- |
| `pace_factor`                 | `offense_all_possessions` / the 20-team league average of that column                        |
| `foul_rate_per40_drawn`       | `offense_all_fouls_received` per 40 minutes of team play (see normalization below)            |
| `foul_rate_per40_committed`   | `defense_all_fouls` per 40 minutes of team play (see normalization below)                     |
| `funnel_ratio_guards`         | `guards_fantasy_points` / the 20-team league average for guards                              |
| `funnel_ratio_forwards`       | `forwards_fantasy_points` / the 20-team league average for forwards                          |
| `funnel_ratio_centers`        | `centers_fantasy_points` / the 20-team league average for centers                            |

**`foul_rate_per40_*` normalization.** A EuroLeague game is 40 regulation minutes (4 x
10-minute quarters). The raw `offense_all_fouls_received` and `defense_all_fouls` columns
are already per-game averages, and per-team overtime minutes are not available anywhere in
the raw data. Under the documented assumption that every game is exactly 40 minutes
(overtime not modeled), a per-game rate already equals a per-40-minutes rate, so these
columns numerically restate the raw per-game rate under that explicit assumption rather than
leaving the unit implicit. There is no single obviously-correct convention here; if
per-team overtime minutes ever become available, revisit this in `src/build_team_kpis.py`
and `src/team_master_column_guide.py` (`team_kpis_guide()`).

`pace_factor` and each `funnel_ratio_*` are centered near 1.0 across the league by
construction (they are each team's value divided by the mean of that same column across all
20 teams).

## Verification

Recompute the six KPIs for all 20 teams with independent code from the two raw CSVs and
compare; confirm `pace_factor` and each `funnel_ratio_*` average to 1.0 across the league.
Column Guide texts live in `src/team_master_column_guide.py` (`team_kpis_guide()`). Unit
tests: `tests/test_team_kpis.py`.

## Committing

```bash
git add data/curated/team_kpis.xlsx
git commit -m "chore(data): refresh team KPIs"
```
