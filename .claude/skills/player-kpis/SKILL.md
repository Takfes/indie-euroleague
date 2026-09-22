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

| Group       | Columns                                                                                                          |
| ----------- | ---------------------------------------------------------------------------------------------------------------- |
| Identity    | `player_id` (Kaggle), `player_name_raw` (`LAST, FIRST`), `player_name` (First Last), `team_id` (latest team), `games_played`, `games_dnp`, `dnp_rate`, `recent_games` |
| Production  | `pir_avg` (season mean), `pir_per_min` (total PIR / total minutes), `pir_avg_recent`, `pir_median_recent`        |
| Opportunity | `minutes_avg`, `minutes_avg_recent`, `minutes_trend` (recent - season mean, minutes), `minutes_sd`, `minutes_cv`, `starts_rate` (starts / games played, season) |
| Stability   | `pir_per_min_sd`, `pir_per_min_cv` (SD / mean of the per-game series), `pir_p10`, `pir_p50`, `pir_p90`, `pir_range` (P90 - P10); all over the season; SD/CV blank under 2 games |
| Profile     | `pts/reb/ast/stl/blk/fdr_contrib` (season total of the component / season total PIR; blank if total PIR <= 0; can sum above 1 because PIR subtracts negatives), `fg_pct`, `fg3_pct`, `ft_pct`, `ts_pct` (season totals), `fdr_rate` (fouls drawn / minutes), `usage_proxy_avg` (mean per game played), `usage_per_min` (total usage / total minutes) |

Players who only have DNP rows keep a row (`games_played` 0, blank KPIs). A player traded mid-season has
all games in the KPIs and the most recent team in `team_id`. The value KPIs (PIR/credit, PIR/min/credit)
need the fantasy price and are computed in the master stage, not here.

## Column order

Columns are grouped by base metric (PIR, PIR/min, minutes, one group per contribution stat, shooting,
usage), identity columns first, in `player_kpis_guide()` (`src/kaggle_column_guide.py`) - the KPI builder
takes its column order from that dict's key order. Within a group, follow: recent-window average before
season average, then any trend/median, then percentiles low-to-high, then spread (sd, cv). A new KPI slots
into the group of the metric it describes, in that position, not appended at the end.

## Verification

Recompute several KPIs for several players with independent code from the game rows (or the raw csv) and
compare; also compare `pir_avg` with `valuation_per_game` of `euroleague_players.csv` (same Kaggle folder;
matches within rounding for every 2025-26 player). Column Guide texts live in `src/kaggle_column_guide.py`
(one row per column, marked "(unverified)" when unsure). Unit tests with tiny frames: `tests/test_player_kpis.py`.

## Committing

```bash
git add data/curated/player_kpis.xlsx
git commit -m "chore(data): refresh player KPIs"
```
