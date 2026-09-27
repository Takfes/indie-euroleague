# Spec: EuroLeague live-API box score fetcher

## Goal

Incrementally fetch EuroLeague's live-API game data for the ongoing 2026-27 season
(`seasoncode=E2026`) and produce delta CSVs shaped like the historical dataset at
`data/raw_data/kaggle_data/euroleague_box_score.csv`. Re-running the script only
fetches games that are new since the last run.

## Scope

- One script: `src/eupy/fetchers/fetch_euroleague_live_boxscores.py`.
- Two endpoints, no auth:
  - `https://live.euroleague.net/api/Header?gamecode={g}&seasoncode={s}`
  - `https://live.euroleague.net/api/BoxScore?gamecode={g}&seasoncode={s}`
- Output: delta-only CSVs, one per run, under
  `data/raw_data/euroleague_live/box_score/{season}_delta_{run_timestamp_utc}.csv`.
  No merged master file — concatenating deltas is a future staging step, out of scope.
- State file: `data/raw_data/euroleague_live/_state.json`, keyed by season code. Both
  the output directory and the state file live in the git-ignored raw zone
  (`data/raw_data/`), consistent with raw data not being tracked in git.
- Out of scope: reconciling live-API player IDs (`P011157`) with the historical
  CSV's `PBDE`-style IDs (different schemes; see Known limitations).

## Empirical findings (verified live, 2026-09-27)

- A gamecode with no game yet returns HTTP 200 with a **completely empty body**
  (confirmed for `gamecode=11..30` and `9999` on `E2026` — round 2 hasn't been
  created in the API yet). This is the primary/only observed "stop" signal.
- Round 1 (`E2026`, gamecode 1-10) is fully played and complete: `Live: false`,
  numeric `ScoreA`/`ScoreB`, and both teams' `PlayersStats` populated in BoxScore.
- **Could not verify empirically**: a gamecode that is scheduled but not yet
  played, while later/earlier gamecodes in the same run are playable (i.e. a
  non-empty-but-incomplete response). The whole of round 2 currently returns
  empty bodies rather than "valid JSON with null scores" — the API appears to
  create a gamecode's entry only close to/at game time, not when the fixture is
  merely scheduled. The design below handles this defensively anyway (see
  "Completeness check"), but that branch is untested against a real response.
- `urllib.request`'s **default User-Agent gets HTTP 403 Forbidden** from
  `live.euroleague.net`. Any non-default `User-Agent` (tested: `curl/8.4.0`,
  `Mozilla/5.0`) works. The script sets a simple identifying UA
  (`Mozilla/5.0 (compatible; euroleague-live-fetcher/1.0)`).

## Design

### Completeness check

A gamecode is treated as "ingestable" only if:
1. Header response is non-empty and `Live` is `false` (not currently in progress).
2. BoxScore response is non-empty and `Stats` has exactly 2 entries, each with a
   non-empty `PlayersStats` list.

The `Live` check guards against ingesting partial stats from an in-progress game
whose BoxScore might already have non-empty (but not final) `PlayersStats`.

### Polling loop (per run, per season)

1. Read `_state.json`; resume from `last_ingested_gamecode + 1` (default 0 if unseen).
2. For each gamecode in increasing order: fetch Header. Empty body -> stop.
   Fetch BoxScore. If not complete (per above) -> stop without advancing the
   pointer past this gamecode. Otherwise build rows, advance
   `last_ingested_gamecode` to this gamecode, continue.
3. A small delay (0.3s) between each HTTP request (polite client, sequential
   requests only — no concurrency, no retries/backoff).
4. On any non-empty Header response, sanity-check `pcom`/`CompetitionReducedName`
   (stripped) equals the requested season code; raise if not (defends against a
   mixed-up season mapping upstream).
5. If at least one game was ingested: write one delta CSV containing only the
   newly ingested games' rows, and update `last_ingested_gamecode` +
   `last_checked_at` in state.
6. If zero new games: don't create a delta file; just bump `last_checked_at` and
   print "no new data".

### Field mapping (Header + BoxScore -> target CSV row)

Per player, per game:

| CSV column | Source |
| --- | --- |
| `game_id` | `{season}_{gamecode}` |
| `game` | `{Header.CodeTeamA}-{Header.CodeTeamB}` |
| `round` | `Header.Round` |
| `phase` | `Header.Phase` |
| `season_code` | requested `--season` value |
| `player_id` | `PlayersStats[].Player_ID`, stripped |
| `game_player_id` | `{game_id}_{player_id}` |
| `team_id` | `PlayersStats[].Team` (short code) |
| `dorsal`, `player`, `minutes` | `Dorsal`, `Player`, `Minutes` direct |
| `points` .. `plus_minus` | direct/renamed fields per BoxScore keys (see code) |

Team-total row (one per team per game, `dorsal="TOTAL"`): synthesized from that
team's `totr` block + `Stats[i].Team` (full name) + the team's short code, taken
from `Header.CodeTeamA`/`CodeTeamB` matched by `Stats` array position (documented
invariant: `Stats` is in the same order as `Header`'s `TeamA`/`TeamB`).
`player_id` = `team_id` = the short code, `is_starter=0`, `is_playing=1`,
`plus_minus=0` — matching the historical CSV's TOTAL-row convention. The `tmr`
(unattributed team-rebounds pseudo-row) is not emitted as its own row; it's
already folded into `totr`'s sums, matching how the historical CSV represents it.

Row order within a game matches the historical CSV: team A's players, team A's
TOTAL, team B's players, team B's TOTAL.

### Known deviations from the historical CSV

- `is_starter`/`is_playing`: historical CSV stores these as floats (`0.0`/`1.0`);
  the live API returns ints (`0`/`1`) and this fetcher carries them through as-is
  rather than reformatting. Low risk — a future staging/merge step can normalize
  dtypes when it concatenates deltas with history.
- **Player ID scheme mismatch** (called out in the task, not fixable here): the
  live API's player IDs (`P011157`) use a different scheme than the historical
  CSV's `PBDE`-style 4-char codes. No reconciliation is attempted; live-API rows
  simply carry the live API's own IDs.

## Pass criteria

- `uv run ruff check src tests` and `uv run ruff format --check src tests` clean.
- `uv run pytest` green, including new tests for: Header+BoxScore -> row mapping,
  team-total-row synthesis, empty-response stop detection, and state
  read/update round-trip. No live network calls in tests.
- Running the script live against `seasoncode=E2026` produces a delta CSV with
  the right columns/shape, spot-checked against the raw API JSON, and updates
  `_state.json`. Running it again immediately afterward (no new completed games)
  produces no new delta file and doesn't error.
