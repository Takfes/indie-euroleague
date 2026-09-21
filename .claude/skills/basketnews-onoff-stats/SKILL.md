---
name: basketnews-onoff-stats
description: Refresh the EuroLeague team on/off-court stats dataset scraped from basketnews.com's advanced-stats pages (per-player offensive/defensive/net rating with the player on vs. off the court, for every EuroLeague team). Use this whenever the user asks to update, rebuild, refresh, or re-scrape the basketnews on/off dataset, mentions "on/off stats", "on-off court stats", "lineup impact" for EuroLeague teams/players, or references data/basketnews-onoff-stats/onoff_stats.csv going stale after new rounds are played.
---

# basketnews.com EuroLeague on/off-court stats

Rebuilds `data/basketnews-onoff-stats/onoff_stats.csv`: full-season on/off-court
splits (offensive/defensive/net rating and related KPIs, with the player on
the court vs. off it) for every player on every EuroLeague team, as shown on
basketnews.com's advanced-stats "on/off" pages.

## When to use this

Run this whenever the dataset needs refreshing - e.g. after more EuroLeague
rounds have been played, or if the user just wants current on/off numbers.
The script re-fetches the full current season each time and overwrites the
CSV, so it's safe to re-run at any point in the season.

## How it works

The UI page (e.g.
`https://basketnews.com/advanced-stats/on-off/25-euroleague/2025/33-panathinaikos-aktor-athens`)
has a team-logo selector across the top and a TOTAL / OFFENSE / DEFENSE /
COMBO toggle. Both look like they'd need per-team, per-toggle scraping, but
they're both pure client-side filters: the page loads ALL teams' and ALL
players' on/off stats in one JSON payload up front, then the team selector
and toggle just change what's displayed from that same payload. This was
confirmed by reading the page's inline `<script>` (the `loadStats()`
function and the `types.onoff.data_url` config inside the Alpine.js
`advancedStatsData()` component).

So the whole dataset comes from a single API call:

```
POST https://basketnews.com/advanced-stats/team-profile/on-off.json
Content-Type: application/x-www-form-urlencoded
X-Requested-With: XMLHttpRequest
Referer: https://basketnews.com/advanced-stats/on-off/25-euroleague/2025/33-panathinaikos-aktor-athens
Accept: application/json, text/javascript, */*; q=0.01

ajax=true&league_id=25&season=2025
```

`league_id=25` is EuroLeague; `season=2025` means the season starting in
2025 (i.e. 2025-2026). Optional params `stage_id`, `period_start`,
`period_end`, `sequence_from`, `sequence_to` exist for narrower slices (a
specific round range or quarter) but are omitted to match the page's
full-season default view.

The response contains `data.stats` (one entry per player-team pair for the
season, with most numeric fields shaped as `{"value": ..., "pct": ...}` -
`pct` is only a percentile used for cell heat-coloring on the page, not
shown as text, so it's dropped), plus `data.teams`, `data.players`, and
`data.positions` lookup tables used to join in readable names.

Which fields belong to which toggle state (total/offensive/defensive) is
taken from the page's own `onoffStats` JS config object - see the
`ONOFF_STATS` dict at the top of `src/fetch_basketnews_onoff_stats.py`. If
basketnews changes the page's columns, search the page source for
`onoffStats:` to find the updated field lists and update that dict.

The page's fourth tab, COMBO (arbitrary multi-player lineup combinations),
uses a different endpoint (`/advanced-stats/team-profile/combo.json`) and a
different data shape (you pick specific players to combine) - it's out of
scope for this skill, which only covers the three per-player views named in
the original task (total/offense/defense).

## Running it

```bash
cd <repo root>
uv run python src/fetch_basketnews_onoff_stats.py
```

(`uv run` picks up the `requests` dependency already declared in this repo's
`pyproject.toml`/`uv.lock`; use your own Python with `requests` installed if
not using uv.)

This overwrites `data/basketnews-onoff-stats/onoff_stats.csv` with the
current full season's data. The script finds the repo root via `git
rev-parse --show-toplevel`, so it works regardless of your current
directory.

## Output

`data/basketnews-onoff-stats/onoff_stats.csv` - one row per (team, player,
view), where `view` is one of `total` / `offensive` / `defensive`.

Identity columns: `league`, `league_id`, `season`, `view`, `team_id`,
`team_name`, `team_short_name`, `player_id`, `player_name`, `position`,
`games_played`, `time_played_formatted`.

KPI columns: the union of all fields used by any of the three views (e.g.
`offensive_rating_lineup`, `offensive_rating_lineup_diff`,
`defensive_rating_lineup`, `net_rating_lineup`, `possession_percentage_lineup`,
`2p_percentage_lineup`, `assist_percentage_opponent_lineup`, etc. - see the
`ONOFF_STATS` dict in the script for the full, exact list per view). A
column is left blank on rows for a view it doesn't apply to (e.g.
`2p_attempted_lineup` is blank on `total`-view rows, since that tab doesn't
show it).

## Committing the refreshed data

This repo's `.gitignore` excludes `data/` entirely, so a normal `git add`
won't pick up the CSV. After refreshing, force-add it explicitly:

```bash
git add -f data/basketnews-onoff-stats/onoff_stats.csv
git commit -m "chore(data): refresh basketnews on-off stats"
```
