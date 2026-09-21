---
name: player-game-stats
description: Rebuild the game-level player dataset (one row per player per game, with PIR, usage proxy, shooting percentages and fouls drawn per minute) from the Kaggle EuroLeague box score into data/curated/player_game_stats.xlsx. Use this whenever the user asks to refresh or rebuild the Kaggle game-level data, PIR or usage-proxy per game, or after the Kaggle csv files changed, and before rebuilding the player KPIs (player-kpis skill) or the player master table.
---

# EuroLeague game-level player stats (Kaggle box score)

Builds `data/curated/player_game_stats.xlsx`: the first step of the Kaggle pipeline
(raw csv files -> `player_game_stats.xlsx` -> `player_kpis.xlsx` -> `player_master_table.xlsx`).
One row per player per game of the chosen season, with per-row metrics computed from the box score.

## When to use this

After the Kaggle csv files were downloaded or refreshed, or when someone wants PIR / usage proxy per game.
Nothing is fetched by the script, so the two input files must already exist:

- `data/kaggle-euroleague-data/euroleague_box_score.csv` (one row per player per game, all seasons since 2007)
- `data/kaggle-euroleague-data/euroleague_header.csv` (one row per game: date, time, round, phase, teams)

**The Kaggle folder is git-ignored** (`data/*` in `.gitignore`; only `data/curated/` and the fetched
source folders are tracked), so a fresh checkout does not have it. Get it from Kaggle with
`kaggle datasets download babissamothrakis/euroleague-datasets` (the captain's `donwload_kaggle.sh`),
unzip into `data/kaggle-euroleague-data/`. Only the two files above are needed here; do not copy the
378 MB play-by-play file. The script stops with this hint when a file is missing.

## Running it

```bash
cd <repo root>
uv run python src/build_game_player_stats.py [--season E2025] [--phases "REGULAR SEASON" PLAYOFFS ...] [--out PATH]
```

Defaults: season `E2025` (2025-26), all four phases (REGULAR SEASON, PLAY-IN, PLAYOFFS, FINAL FOUR).
`phase`, `round` and `date` stay in the output, so a phase filter later is a one-line change.
Output sheets: `Column Guide` (source dataset, column name, explanation; one row per column, texts in
`src/kaggle_column_guide.py`) and `Game Stats`.

## What the script does

- Keeps the season, drops the two team-total rows per game (`dorsal == "TOTAL"`; not players; do not rely on a
  `P<digits>` player id, real ids also include letter codes such as `PLCZ`), keeps the chosen phases.
- Parses `minutes` (`MM:SS` text or `DNP`) into decimal minutes; DNP becomes 0.
- Joins date, time, team names, opponent and home/away (first-listed team = home) from the header.
- Per row: `fgm`, `fga`, `pir`, `pir_per_min`, `usage_proxy`, `usage_per_min`, `fdr_per_min`, `fg_pct`,
  `ft_pct`, `ts_pct` (blank where the denominator is 0) and `game_number` (per player, chronological by
  date, time, then game id; games played only).
- **Definitions.** `pir` = points + total rebounds + assists + steals + blocks made + fouls drawn - missed FG -
  missed FT - turnovers - shots blocked - fouls committed (`blocks_favour` are blocks made, `blocks_against`
  the player's shots rejected, `fouls_received` fouls drawn = FDR). Usage proxy = FGA + 0.44 x FTA + TO + 0.5 x AST.
  TS% = points / (2 x (FGA + 0.44 x FTA)).
- **Played** = minutes > 0. DNP rows stay in the dataset with `played` False and blank derived values, so a
  DNP count is possible; all KPIs later ignore them.

## Verification (built into the script)

The run stops with an error if: computed `pir` differs from the official `valuation` on any played row (it
matches on every row of 2025-26), a game does not have exactly two team-total rows, (`game_id`, `player_id`)
repeats, or a game has no header row. It prints row counts after each filter. Current numbers for E2025 (all
phases): 129,168 raw rows, 10,344 in the season, 804 team totals dropped, 9,540 player rows (8,741 played,
799 DNP), 402 games, 351 players.

## Committing

`data/curated/` is tracked (`!data/curated/**`), the Kaggle csv files are not. Commit only the workbook:

```bash
git add data/curated/player_game_stats.xlsx
git commit -m "chore(data): refresh player game stats"
```

Next step: the `player-kpis` skill (`uv run python src/build_player_kpis.py`).
