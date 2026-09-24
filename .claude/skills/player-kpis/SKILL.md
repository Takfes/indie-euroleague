---
name: player-kpis
description: Rebuild the player-level KPI workbook (production, opportunity, stability and profile KPIs per player - PIR, PIR/min, recent averages, minutes trend, PIR percentiles, contribution shares, shooting, usage) from the game-level Kaggle dataset into data/curated/player_kpis.xlsx. Use this whenever the user asks to refresh or change the Kaggle player KPIs, or before rebuilding the player master table, which needs this workbook to exist.
---

# EuroLeague player KPIs (from the Kaggle game-level dataset)

Builds `data/curated/player_kpis.xlsx` (sheets `Column Guide` and `Player KPIs`): one row per Kaggle
player, computed from `data/curated/player_game_stats.xlsx`. The raw Kaggle csv files are not read here.
Pipeline: raw csv files -> `player-game-stats` -> **this** -> `player-master-table`.

## When to use this

After `player-game-stats` was rebuilt, or when a KPI definition or the recent window changes. The
game-level workbook must exist; otherwise the script stops and names the command that builds it:

```bash
cd <repo root>
uv run python src/build_game_player_stats.py   # needs the git-ignored Kaggle csv files, see player-game-stats
uv run python src/build_player_kpis.py
```

Then rebuild the master (`uv run python src/build_player_master_table.py`), which now depends on this file.

## Definitions

Games = games played (minutes > 0). "Recent" = the last `RECENT_GAMES` = 5 games played (constant in
`src/build_player_kpis.py`, chronological by date and time); a player with fewer uses what exists and
`recent_games` says how many. "Season" = all games played in the chosen season and phases (all phases by default).

| Group        | Columns |
| ------------ | ------- |
| Identity     | `player_id` (Kaggle), `player_name_raw` (`LAST, FIRST`), `player_name` (First Last), `team_id` (latest team), `games_played`, `games_dnp`, `dnp_rate`, `recent_games` |
| Recent window| `pir_avg_recent`, `pir_median_recent`, `minutes_avg_recent`, `minutes_trend` (recent - season mean, minutes) |
| Distribution | one family per per-game series, each with the standard set: the average plus `_sd`, `_cv`, `_p10`, `_p50`, `_p90`, `_range` (see below) |
| Contribution | 11 signed PIR components: `{prefix}_contribution_pct` (component average / `pir_avg` x 100; they sum to 100) and the component's own distribution set in PIR points (`_avg`, `_sd`, `_cv`, `_p10`.. `_range`) |
| Other        | `starts_rate` (starts / games played), `fg_pct`, `fg3_pct`, `ft_pct`, `ts_pct` (season totals, 0-1 fractions) |

**Distribution set.** Every KPI family that aggregates a per-game series exposes the same seven stats, all
over the season's games played: the average; `_sd` (sample std) and `_cv` (sd / mean; blank if the mean is not
positive), both blank under `MIN_GAMES_FOR_SPREAD` = 2 games; `_p10`, `_p50`, `_p90` (linear-interpolation
percentiles, defined from 1 game); `_range` = p90 - p10. Families are listed once in `DISTRIBUTION_FAMILIES` in
`src/build_player_kpis.py`: PIR (`pir_avg`), PIR/min (`pir_per_min`), minutes (`minutes_avg`), usage proxy
(`usage_proxy_avg`), usage per minute (`usage_per_min`) and fouls drawn per minute (`fdr_rate`); for the three
per-minute ones the average is a ratio of season totals and the spread stats come from the per-game series.
Shooting percentages are left out on purpose: a per-game percentage is attempt-weighted (0% or 100% on one shot,
undefined without attempts; `fg3_pct` has no per-game series), so the season ratio of totals is the figure.
`starts_rate` and the recent-window figures are single values too. To add or drop a family, edit that dict plus the matching `player_kpis_guide()` entries; a KPI computed
without a guide entry raises.

**Contribution to PIR.** PIR is the sum of 11 signed components (`CONTRIBUTION_STATS` in
`src/build_game_player_stats.py`: `pts`, `reb`, `ast`, `stl`, `blk`, `fdr` plus; `mfg`, `mft`, `tov`, `blkag`,
`pf` minus). Each component is a per-game series in PIR points (minus components negative) and gets the
distribution set over all games played: `{prefix}_contribution_avg`, `_sd`, `_cv` (sd / |mean|, so the negative
components get a positive CV), `_p10`, `_p50`, `_p90`, `_range`; the 11 averages add up to `pir_avg`.
`{prefix}_contribution_pct` = the component's average / `pir_avg` x 100, i.e. a **ratio of season totals**, not
the mean of per-game ratios (a game with a tiny pir makes a per-game share explode, so that version was
unstable). It uses every game played, including games at pir <= 0 (DNP rows are never games played). The 11
pct sum to 100 by construction - no renormalization - for every player whose `pir_avg` is not 0; a player at
exactly 0 is blank, and a negative `pir_avg` flips every sign but they still sum to 100. Players with a
`pir_avg` near 0 (a handful of small-sample players) get very large shares. The per-game
`{prefix}_share_of_pir` columns of `player-game-stats` are a per-game view only; nothing here reads them.

Players who only have DNP rows keep a row (`games_played` 0, blank KPIs). A player traded mid-season has
all games in the KPIs and the most recent team in `team_id`. The value KPIs (PIR/credit, PIR/min/credit)
need the fantasy price and are computed in the master stage, not here.

## Column order

Columns are grouped by base metric (PIR, PIR/min, minutes, one group per contribution component, fouls
drawn rate, shooting, usage), identity columns first, in `player_kpis_guide()` (`src/kaggle_column_guide.py`) -
the KPI builder takes its column order from that dict's key order. Within a group, follow: recent-window
average before season average, then any trend/median, then percentiles low-to-high (p10, p50, p90, range), then
spread (sd, cv). A new KPI slots into the group of the metric it describes, in that position, not appended at
the end. (The master table has its own order, see `player-master-table`.)

## Verification

Recompute several KPIs for several players with independent code from the game rows (or the raw csv) and
compare; check that the 11 `_contribution_pct` sum to 100 for every player with `pir_avg` != 0 and the 11 `_contribution_avg` add up to `pir_avg`; also compare `pir_avg` with `valuation_per_game` of `euroleague_players.csv` (same Kaggle folder;
matches within rounding for every 2025-26 player). Column Guide texts live in `src/kaggle_column_guide.py`
(one row per column, marked "(unverified)" when unsure). Unit tests with tiny frames: `tests/test_player_kpis.py`.

## Committing

```bash
git add data/curated/player_kpis.xlsx
git commit -m "chore(data): refresh player KPIs"
```
