---
name: basketballsphere-fantasy-prices
description: Refresh EuroLeague Fantasy player and head coach prices scraped from basketballsphere.com's "EuroLeague Fantasy Player Prices" page. Use this whenever the user asks to update, rebuild, refresh, or re-scrape EuroLeague Fantasy prices/credits, mentions basketballsphere.com, or references data/source_data/euroleague-fantasy/basketballsphere_prices.csv going stale after new rounds are played (prices move every round).
---

# basketballsphere.com EuroLeague Fantasy prices

Rebuilds `data/source_data/euroleague-fantasy/basketballsphere_prices.csv`: the opening/current
EuroLeague Fantasy credit price for every player (guards, forwards, centers) and every
head coach, as shown on basketballsphere.com's player-prices page.

## When to use this

Run this whenever the dataset needs refreshing - e.g. after a new round updates prices,
or if the user just wants current EuroLeague Fantasy prices. The script re-fetches the
whole page each time and overwrites the CSV, so it's safe to re-run at any point in the
season.

## How it works

The page (`https://basketballsphere.com/en/euroleague-fantasy-player-prices/`) has
position chips (All players / Guards / Forwards / Centers / Head coaches), a club
dropdown, a name search box, and a "Show More" button that only reveals 25 rows at a
time - it looks like it needs per-position scraping and pagination. It doesn't: all of
that is pure client-side JS/CSS filtering over rows that are already present in the
initial HTML response. No XHR/fetch call for the table data fires at any point
(confirmed by capturing network requests while loading the page and clicking through
the filters/Show More). So a single plain `GET` of the page URL (with a normal desktop
`User-Agent` - the site 403s a bare urllib UA) already returns every player and every
head coach; no browser rendering or pagination loop is needed.

Each row is a `<tr>` with the data already on it:

```html
<tr data-pos="F" data-club="Olympiacos" data-price="17.0">
  <td class="n r">1</td>
  <td class="nm"><a href="https://basketballsphere.com/en/players/sasha-vezenkov/">Sasha Vezenkov</a></td>
  <td class="c">Olympiacos</td>
  <td class="ps">F</td>
  <td class="n p">17.0</td>
</tr>
```

`data-pos` (and the `ps` cell) is `G`/`F`/`C` for outfield players and `HC` for head
coaches - there's no separate coaches page or endpoint, just rows with `pos="HC"` mixed
into the same table. That's how the script derives the `role` column. The name cell
sometimes links to a per-player/coach profile page and sometimes doesn't (no link) -
`src/fetch_basketballsphere_prices.py`'s regex handles both.

## Running it

```bash
cd <repo root>
uv run python src/fetch_basketballsphere_prices.py
```

No dependencies beyond the Python standard library. This overwrites
`data/source_data/euroleague-fantasy/basketballsphere_prices.csv` with the current prices. Pass
`--out PATH` to write elsewhere instead.

## Output

`data/source_data/euroleague-fantasy/basketballsphere_prices.csv` - one row per player or head
coach:

- `rank` - the page's own ranking (by price, descending)
- `name`
- `club`
- `position` - `G`, `F`, `C` for players, `HC` for head coaches
- `price` - fantasy credit value
- `role` - `player` or `head_coach` (derived from `position == "HC"`)

## Sanity-checking a refresh

Prices and even the player pool shift over the season, so don't hardcode exact
row counts as a pass/fail check - use them as a ballpark. At the time this skill was
written there were 346 rows total: 326 players (145 G / 118 F / 63 C) + 20 head
coaches, with player prices ranging 4.0-17.0 and head coach prices 5.0-10.0. After a
refresh, a reasonable check is: `role` splits into two non-trivial groups, `position`
only ever takes the 4 expected values, and prices are numeric and roughly in that
range - a big deviation (e.g. `head_coach` rows disappearing, or one club missing
entirely) likely means the page's markup changed and the regex in
`src/fetch_basketballsphere_prices.py` needs updating to match.

## Committing the refreshed data

Nothing to commit: `data/source_data/` is git-ignored (only `data/curated_data/` is
tracked), so a refresh stays local.
