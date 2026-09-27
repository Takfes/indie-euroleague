# Spec: EuroLeague Fantasy Challenge stats-table fetcher

## Goal

Acquire the full round-by-round Stats table (players and head coaches) from the
EuroLeague Fantasy Challenge app's backing API and produce two long-format
datasets, one row per player/coach per round, rounds appended as they complete.

## Scope

- One script: `src/eupy/fetchers/fetch_euroleague_fantasy_stats.py`.
- Reverse-engineered API on `fantaking-api.dunkest.com`, the same backend the
  live web app at `https://euroleaguefantasy.euroleaguebasketball.net/10/stats`
  calls (confirmed via live network inspection, 2026-09-28).
- Two output datasets, both long-format:
  - `data/raw_data/euroleague_fantasy_stats/players.csv` (positions Guard,
    Forward, Center)
  - `data/raw_data/euroleague_fantasy_stats/head_coaches.csv` (position Head
    Coach)
- Auth: a static bearer token, captured once via a one-time manual browser
  step (documented below) and stored as `EUROLEAGUE_FANTASY_AUTH_TOKEN` in
  `.env` (git-ignored). No refresh flow — if/when the token stops working,
  redo the manual capture.
- Not incremental: every run re-derives which rounds are complete from the
  live API and rebuilds both CSVs from scratch (full overwrite), matching
  `fetch_basketballsphere_prices.py`'s convention. No state file.
- Out of scope: any round not yet complete (see "Completeness check" below);
  reconciling player IDs with other datasets (basketballsphere / Kaggle) —
  that's separate crosswalk work per `spec-player-name-linking.md`.

## Empirical findings (verified live, 2026-09-28)

### Auth

- The web app (Flutter/CanvasKit) loads Firebase SDKs, but only for
  analytics/messaging/remote-config — **not** for its API session. This
  contradicts an earlier hypothesis (Firebase ID token via Google Sign-In);
  `getAuth().currentUser` is `null` throughout a fully-authenticated session.
- Login instead goes through a custom flow
  (`localStorage['flutter.authProvider'] == "euroLeagueSSO"`). The resulting
  session credential is a static, opaque bearer token in
  `{numeric_id}|{48-char string}` format — a Laravel Sanctum
  personal-access-token shape — persisted directly in
  `localStorage['flutter.authToken']`. Confirmed **not** a JWT (0 dots, 63
  chars including the `"Bearer "` prefix).
- No refresh endpoint exists or is needed: the captured token was verified
  with a plain `curl` from outside the browser (no cookies, no headers beyond
  `Authorization: Bearer <token>`) — HTTP 200. It's a bare, freestanding
  credential, not tied to a browser session.
- **One-time bootstrap** (manual, not part of the script): log into the app in
  a real browser, open devtools, run
  `localStorage.getItem('flutter.authToken')`, and store the result as
  `EUROLEAGUE_FANTASY_AUTH_TOKEN` in `.env`. Sanctum tokens are typically
  valid indefinitely (no fixed TTL) until revoked/rotated server-side; a 401
  from the script means redo this step.

### Endpoints

- `GET https://fantaking-api.dunkest.com/api/v1/leagues/10/config` — static
  per-competition config. Returns the full season's `matchdays` array
  (`{id, number, display_name}` × 38 for a 34-round + playoffs season — do not
  assume `id == 1527 + number`, always resolve from this list), plus
  `previous_matchday.number` (the last **completed** round — currently `1`,
  matchday id `1528`; round 2 / matchday id `1529` is `current_matchday`, i.e.
  not yet played, matching the disabled "Round 2" checkbox seen in the UI),
  plus the canonical position names/ids (`Guard`=28, `Forward`=29,
  `Center`=30, `Head Coach`=31) and `current_competition_id` (currently `49`).
- `GET https://fantaking-api.dunkest.com/api/v1/competitions/{competition_id}/stats/players/table`
  — the stats table itself. Read `competition_id` from the config response
  each run rather than hardcoding `49` in two places.
  - Query params used: `stats_type=tot` (per-round totals — using `tot`
    rather than `avg` for a single-matchday request sidesteps any per-round
    averaging ambiguity), `matchdays=<single id>` (**one matchday per call**
    — passing multiple ids returns stats aggregated across all of them with
    no way to attribute rows back to a round, since the response carries no
    round/matchday column), `positions=Guard,Forward,Center` or
    `positions=Head Coach`, `page`, `per_page` (max **100** — `-1` is
    rejected with `422 "per page must be at least 1"`; must paginate for
    real, `last_page` in the response tells you when to stop).
  - No `active_players` filter is passed: with `active_players=true` the API
    silently drops 15 of 365 players (350 remain); those 15 are presumably
    free agents / transferred-out players who may still hold stats for
    already-completed rounds, so they're needed for historical completeness.
  - Response shape:
    ```json
    {"data": {"columns": [...26 names...],
              "players": [{"id": <int>, "row": [...26 values, positional...]}]},
     "meta": {"current_page": 1, "per_page": 100, "total": 350, "last_page": 4}}
    ```
    Zip `columns` with each row's `row` array to get a named record. `id` is
    the platform's own player/coach id (distinct from `name`).
  - **Unplayed rounds do not error and do not come back empty** — requesting
    `matchdays=1529` (Round 2, not yet played) still returns the full 20-row
    head-coach roster, just with placeholder values (`"-"` for most stat
    columns, `0`/`"0"` for fpt/win-loss buckets). The API will happily hand
    back junk for a future round if asked. The fetcher must gate on
    `previous_matchday.number` from the config call, never on response shape
    — same "completeness check" discipline as
    `spec-euroleague-live-fetcher.md`'s polling loop.
  - Head-coach rows always carry `"-"` for box-score columns
    (reb/ast/stl/tov/blk/blka/fd/pf/fg_missed/ft_missed) — coaches don't have
    those stats; only fpt/quotation/plus/win-loss buckets are real values.
    Expected, not a data-quality issue — preserve `"-"` as-is, don't zero-fill.

## Design

### Per run

1. Fetch `/leagues/10/config`. Build `{round_number: matchday_id}` from the
   `matchdays` array. Set `last_completed_round = previous_matchday.number`.
2. For `round_number` in `1..last_completed_round`:
   - Fetch all pages (`per_page=100`, loop until `page > last_page`) of
     `/stats/players/table?stats_type=tot&matchdays={matchday_id}&positions=Guard,Forward,Center`
     → rows tagged `round=round_number`, appended to the players accumulator.
   - Same call with `positions=Head Coach` → head_coaches accumulator.
3. Write both accumulators to CSV (full overwrite each run). Columns:
   `round`, `player_id` (the API's `id`), then the response's own `columns`
   names in order (already snake_case-ish — `win_1-10` etc. sanitized to
   valid CSV headers, e.g. `win_1_10`).

### Auth

- Read `EUROLEAGUE_FANTASY_AUTH_TOKEN` from `.env` via a small manual parser
  (current dependencies are stdlib + `rapidfuzz` only — no `python-dotenv`;
  don't add it for a two-line `KEY=value` read).
- Send as `Authorization: Bearer <token>` on every request via
  `urllib.request`, matching the existing two fetchers (no new HTTP
  dependency, e.g. no `requests`/`httpx`).

## Pass criteria

- `uv run ruff check src tests` and `uv run ruff format --check src tests`
  clean.
- `uv run pytest` green: unit tests for response-row → record mapping
  (columns+row zip), pagination looping (mocked multi-page response), and the
  completed-round cutoff logic (config fixture → correct round list). No
  live network calls in tests.
- Running the script live produces `players.csv` and `head_coaches.csv` under
  `data/raw_data/euroleague_fantasy_stats/`, currently 1 round × 350 players
  + 1 round × 20 head coaches, matching the counts independently verified via
  `curl` above. Re-running produces identical output (idempotent, no state
  file).
- Script header documents inputs/outputs/`final` flag (`false` — raw fetch,
  not yet exposed under `data/stage_99/`) per repo convention.
