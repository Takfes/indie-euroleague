---
name: basketnews-team-stats
description: Fetches EuroLeague team-level advanced stats (offensive/defensive rating, points, 3P%/2P%/FT%/TS%, attempts, assists, turnovers, rebounds, fouls, blocks, possessions, and the opponent-side equivalents) for all 20 teams, split into all games / home / away, from basketnews.com's team Advanced Stats overview page, and writes them to data/basketnews-team-stats/basketnews_team_stats.csv (one row per team). Use this whenever the user wants team advanced stats, team offense/defense ratings, home vs away team splits, or basketnews.com team overview data refreshed or re-pulled -- even if they just say "update the basketnews team stats", "refresh team offensive/defensive ratings", or mention basketnews.com's advanced-stats/overview page. Do NOT use this for per-player stats (use basketnews-player-advanced-stats), on/off stats (basketnews-onoff-stats), or EuroLeague Fantasy data.
---

# Basketnews EuroLeague Team Advanced Stats

## What this fetches

The basketnews.com team overview page, e.g.:

```
https://basketnews.com/advanced-stats/overview/25-euroleague/2025/368-real-madrid
```

It has a team selector across the top, an OFFENSE / DEFENSE toggle and
Total / Home / Away columns. That looks like 20 teams x 2 modes of scraping. It
isn't: the page is an Alpine.js app that loads **every team's** stat line with a
single POST on page load, and the team selector, toggle and split columns are pure
client-side views over that one payload. One API call gets everything; there is
nothing to click through. (Same pattern as the sibling
`basketnews-player-advanced-stats` skill.)

## How to run it

From the repo root:

```bash
python src/fetch_basketnews_team_stats.py
```

Writes `data/basketnews-team-stats/basketnews_team_stats.csv` (20 rows x 128
columns for the 2025/26 EuroLeague season at build time). Stdlib only. Re-running
overwrites the CSV with current data; that is the intended way to refresh it, and
the output is deterministic (rows sorted by team name).

Options (see `--help`):

```bash
python src/fetch_basketnews_team_stats.py --league-id 25 --season 2025 --out path/to/out.csv
```

- `--league-id` (default 25 = EuroLeague). Other competitions use other ids: open the
  equivalent `advanced-stats/overview/` page and read the `<id>-<slug>` in the URL.
- `--season` start year (default 2025 = 2025/26).
- `--out` output path (default the committed csv location).

## The underlying API (for reference / debugging)

```
POST https://basketnews.com/advanced-stats/team-profile/overview.json
Content-Type: application/x-www-form-urlencoded

ajax=true&league_id=25&season=2025
```

No auth, cookies or login. Response `{"success": true, "data": {...}}`:

- `data.stats`: one entry per team. Each KPI is `{"home": {"value", "rank"}, "away": {...}, "total": {...}}`.
  `games_played` has the same shape (value only).
- `data.teams`: `{id, name_en, short_name_en, slug_en, ...}`. This is the list the page's team selector renders.
- `data.extra`: applied filters (`total_games`, `sequence_from/to`, `stage_id`).
- `data.stages`, `data.summaries`: stage list and text blurbs; unused.

No stage / period filter is sent, so the numbers cover all stages of the season
(regular season, play-in, playoffs) combined, which is what the page shows by default.
`total` in the API is "all games". Ranks are dropped; only values are written.

## Output shape

One row per team. Identity columns first:
`team_id, team_name, team_short_name, season, league_id, games_played_all,
games_played_home, games_played_away`. `team_id` is the basketnews id, the same as
`team_id` in `data/basketnews-players-stats/basketnews_players_advanced_stats.csv`.

Then six blocks of 20 KPI columns, in this order, named `<block>_<kpi>`:
`offense_all`, `offense_home`, `offense_away`, `defense_all`, `defense_home`,
`defense_away`. KPI order inside each block is the page's order.

- Offense KPIs: `offensive_rating, points, 3p_percentage, 2p_percentage,
  ft_percentage, ts_percentage, 3p_attempted, 2p_attempted, ft_attempted,
  3p_attempted_rate, assists, assist_percentage, turnovers, turnover_percentage,
  steals_opponent, offensive_rebounds, offensive_rebound_percentage,
  fouls_received, blocks_received, possessions`. (The page lists opponent steals
  under Offense; kept as shown.)
- Defense KPIs: `defensive_rating, points_opponent, 3p_percentage_opponent,
  2p_percentage_opponent, ft_percentage_opponent, ts_percentage_opponent,
  3p_attempted_opponent, 2p_attempted_opponent, ft_attempted_opponent,
  3p_attempted_rate_opponent, assists_opponent, assist_percentage_opponent,
  turnovers_opponent, turnover_percentage_opponent, steals, defensive_rebounds,
  defensive_rebound_percentage, fouls, blocks, possessions_opponent`.

Values are per-game averages or percentages as shown on the page (percentages are
plain numbers, e.g. `37.8` for 37.8%; the page's `%` sign is dropped). A missing
value is written as an empty cell (none in the current data).

## Coverage

All 20 EuroLeague 2025/26 teams, i.e. everything in `data.teams`, which is exactly
what the page's team selector lists. `games_played_*` differ per team (38 to 44 at
build time) because of play-in / playoff participation.

## Verifying a refresh

Open a team page, e.g. `.../overview/25-euroleague/2025/368-real-madrid`, and compare
the OFFENSE / DEFENSE tables (columns are Games / Home / Away) with that team's row:
the page's Games column is `*_all`, Home is `*_home`, Away is `*_away`.
