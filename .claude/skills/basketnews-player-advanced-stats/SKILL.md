---
name: basketnews-player-advanced-stats
description: Fetches EuroLeague player advanced stats (offensive rating, defensive rating, usage%, true shooting%, rebound%, assist%, steal%, block%, and more) for every player and every position from basketnews.com's Advanced Stats leaderboard, and writes them to data/basketnews-players-stats/basketnews_players_advanced_stats.csv. Use this whenever the user wants EuroLeague advanced/efficiency stats, per-100-possession-style ratings, or basketnews.com leaderboard data refreshed or re-pulled -- even if they just say "update the basketnews data", "refresh player advanced stats", "get offensive/defensive ratings for EuroLeague players", or mention basketnews.com's advanced-stats/leaders page. Do NOT use this for basic box-score stats (points/rebounds/assists) available from other sources in this repo, or for EuroLeague Fantasy data -- this skill is specifically for basketnews.com's advanced/efficiency metrics.
---

# Basketnews EuroLeague Player Advanced Stats

## What this fetches

The basketnews.com Advanced Stats leaderboard, e.g.:

```
https://basketnews.com/advanced-stats/leaders/players/25-euroleague/2025?leaders_mode=offensive&leaders_sort=offensive_rating_lineup&leaders_sort_desc=0&leaders_min_gp=5
```

That page *looks* like it needs UI scraping (an OFFENSE/DEFENSE toggle, ~12
pages of results per mode). It doesn't. The page is an Alpine.js app that
loads the **entire** season's per-player stat line -- every offensive and
defensive metric, for every player, in every position -- with a single POST
call on page load. The toggle and pagination are pure client-side
filter/sort/slice over that one payload. So one API call gets everything;
there's nothing to click through.

## How to run it

From the repo root:

```bash
python .claude/skills/basketnews-player-advanced-stats/scripts/fetch_player_advanced_stats.py
```

This writes `data/basketnews-players-stats/basketnews_players_advanced_stats.csv`
(682 rows / 45 columns for the 2025/2026 EuroLeague regular season+playoffs as
of when this skill was built -- row count grows as the season progresses).

To target a different season or league, pass flags (see `--help`):

```bash
python .claude/skills/basketnews-player-advanced-stats/scripts/fetch_player_advanced_stats.py \
    --league-id 25 --season 2024 --out data/basketnews-players-stats/basketnews_players_advanced_stats_2024.csv
```

`--league-id 25` is EuroLeague. Other basketnews competitions use different
ids -- find one by opening the equivalent `advanced-stats/leaders/players/`
page for that competition and reading the `<id>-<slug>` in the URL path.

Re-running the script simply overwrites the CSV with the latest data --
that's the intended way to "refresh" this dataset.

## The underlying API (for reference / debugging)

```
POST https://basketnews.com/advanced-stats/team-profile/players.json
Content-Type: application/x-www-form-urlencoded

ajax=true&league_id=25&season=2025
```

No auth, cookies, or login wall -- a plain POST works from `curl` or
`urllib`. Optional params `stage_id`, `period_start`, `period_end`,
`sequence_from`, `sequence_to` narrow the window (see `data.stages` in the
response for valid stage ids); omitting them all is what the target
leaderboard URL does by default, i.e. "all stages" of the season combined.

The response is `{"success": true, "data": {...}}` with:

- `data.stats` -- one entry per player+team stint (players traded mid-season
  get more than one), each stat field shaped like `{"value": ..., "pct": ...,
  "rank": ...}` (and `time_played` additionally has a `formatted` "MM:SS"
  string).
- `data.players` -- `{id, full_name, full_name_short, positions: [...]}` (a
  player can list multiple eligible positions).
- `data.teams` -- `{id, name_en, short_name_en, ...}`.
- `data.positions` -- `{id, short_name}` lookup, e.g. `1 -> "PG"`, `5 -> "C"`.
- `data.extra` -- echoes back the applied filters (`total_games`, etc).

The script pulls the `value` out of each stat object (plus `time_played`'s
`formatted` string) since that's what the page itself displays.

## Output shape

Long format: one row per (player, team-stint, mode), where `mode` is
`"offensive"` or `"defensive"`. Columns belonging to the other mode are left
blank on a given row -- this mirrors toggling OFFENSE/DEFENSE on the page
without actually needing to toggle anything.

Common columns: `season, league_id, mode, player_id, player_name,
player_name_short, team_id, team_name, team_short_name, positions,
games_played, minutes_per_game, minutes_per_game_raw`.

Offensive-mode columns (exactly what the site's OFFENSE leaderboard shows):
`offensive_rating_lineup, offensive_rating_ind, points, 3p_percentage,
2p_percentage, ft_percentage, ts_percentage, 3p_attempted, 2p_attempted,
ft_attempted, 3p_attempted_rate, created_points, assists, assist_percentage,
turnovers, turnover_percentage, offensive_rebounds,
offensive_rebound_percentage, fouls_received, blocks_received,
usage_percentage`.

Defensive-mode columns (exactly what the site's DEFENSE leaderboard shows):
`defensive_rating_lineup, defensive_rating_ind, stops, stop_percentage,
defensive_rebounds, defensive_rebound_percentage, steals, steal_percentage,
blocks, block_percentage, foul_stops`.

## A note on "min games" and position filters

The live page defaults to a "min games played >= 20" filter (or >= 1 if the
season has fewer than 20 total games yet) and lets you filter by position --
both applied purely client-side over the same full dataset. This script
intentionally ignores both: it dumps every stat line the API returns, so the
CSV always covers every player regardless of games played and every
position, which is a strict superset of anything the UI's leaderboard pages
would show you (and avoids re-deriving which UI page a given low-minutes
player would even land on).

## Verifying a refresh

Spot-check a couple of rows against the live leaderboard page after
re-running: pick a player near the top of
`.../leaders/players/25-euroleague/<season>?leaders_mode=offensive&...` and
confirm their `points`, `offensive_rating_lineup`, etc. in the CSV match what
the page displays (and likewise for `leaders_mode=defensive`).
