---
name: dunkest-player-stats
description: Fetch/refresh EuroLeague player fantasy stats (points, rebounds, assists, credit value "cr", and 35+ other per-game stat columns for every player on all 20 EuroLeague teams) from dunkest.com's stats API and save them to data/dunkest-data/player_stats.csv. Use this whenever the user asks to pull, download, update, or refresh Dunkest/EuroLeague player stats or fantasy data, mentions dunkest.com, or wants current-season EuroLeague per-player averages for fantasy analysis -- even if they just say "get the latest player stats" or "refresh the dunkest data" without naming the site.
---

# Dunkest Player Stats

## What this does

Downloads current-season EuroLeague player stats (per-game averages: points, rebounds,
assists, shooting splits, fantasy credit value "cr", fantasy points "pdk", etc.) from
Dunkest's fantasy stats tool and writes them to `data/dunkest-data/player_stats.csv`,
one row per player.

## Why this is a single API call, not a scrape

The Dunkest stats page
(`https://www.dunkest.com/en/euroleague/stats/players/table?...`) displays the table
paginated 15 rows/page across ~21 pages, which looks like it needs a pagination loop to
scrape fully. It doesn't: the page's own JSON API,
`GET https://www.dunkest.com/api/stats/table`, returns *every* player matching the
filters in one response (confirmed: 305 players across all 20 teams in a single call,
no auth or cookies required). The 15-per-page UI pagination is purely client-side
rendering on top of that one full payload. So don't build a pagination loop for this --
one request already has everything.

## How to run it

```bash
python .claude/skills/dunkest-player-stats/scripts/fetch_player_stats.py
```

This overwrites `data/dunkest-data/player_stats.csv` with a fresh pull using the default
filters (full 2025-26 EuroLeague season, all 20 teams, all 3 positions, credit range
4-35 -- i.e. every rostered player). Run it again later in the season to get updated
averages; there's no need to change `--date-to` mid-season since it already spans the
full season window and the API reflects games played so far.

Useful overrides (see `--help` for the full list):

- `--out PATH` -- write somewhere other than the default CSV path
- `--stats-type tot` -- season totals instead of per-game averages
- `--teams ... ` / `--positions ...` -- narrow to specific teams (ids) or positions
  (1=Guard, 2=Forward, 3=Center)
- `--player-search NAME` -- filter to players matching a name substring

## Output columns

41 columns per player, taken verbatim from the API (values are strings as returned,
e.g. `"15.6"`): `id, gp, first_name, last_name, team_id, team_code, team_name, position,
position_id, cr, pdk, min, starter, pts, ast, reb, stl, blk, blka, fgm, fgm_tot, fga,
fga_tot, tpm, tpm_tot, tpa, tpa_tot, ftm, ftm_tot, fta, fta_tot, oreb, dreb, tov, pf,
fouls_received, plus_minus, fgp, tpp, ftp, slug`.

Key fields: `cr` = fantasy credit/salary, `pdk` = fantasy points per game, `gp` = games
played, `starter` = games started. Shooting fields come in both per-game (`fgm`, `fga`,
...) and season-total (`fgm_tot`, `fga_tot`, ...) form.

## Verification after running

- Row count should be in the low 300s (one row per rostered player across all teams) --
  a much smaller number likely means a filter param got dropped.
- `python -c "import csv; rows=list(csv.DictReader(open('data/dunkest-data/player_stats.csv'))); print(len(rows), len({r['team_id'] for r in rows}))"`
  should print roughly `3XX 20` (all 20 teams represented).
