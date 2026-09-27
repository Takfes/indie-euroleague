# Spec: EuroLeague Fantasy Challenge stats-table fetcher

## Goal

Acquire the full round-by-round Stats table (players and head coaches) from the
EuroLeague Fantasy Challenge app's backing API and produce two long-format
datasets, one row per player/coach per round, rounds appended through the
round currently in progress.

## Scope

- One script: `src/eupy/fetchers/fetch_euroleague_fantasy_stats.py`.
- Reverse-engineered API on `fantaking-api.dunkest.com`, the same backend the
  live web app at `https://euroleaguefantasy.euroleaguebasketball.net/10/stats`
  calls (confirmed via live network inspection, 2026-09-28).
- Two output datasets, both long-format, each keeping only the columns that
  are ever real for that entity (see "Column split" below) — no column that's
  always placeholder for one dataset leaks in from the other:
  - `data/raw_data/euroleague_fantasy_stats/players.csv` (positions Guard,
    Forward, Center)
  - `data/raw_data/euroleague_fantasy_stats/head_coaches.csv` (position Head
    Coach)
- Auth: a static bearer token, captured once via a one-time manual browser
  step (documented below) and stored as `EUROLEAGUE_FANTASY_AUTH_TOKEN` in
  `.env` (git-ignored). No refresh flow — if/when the token stops working,
  redo the manual capture.
- Not incremental: every run re-derives which round to fetch through from the
  live API and rebuilds both CSVs from scratch (full overwrite), matching
  `fetch_basketballsphere_prices.py`'s convention. No state file.
- Fetches through the round currently in progress (`current_matchday`), not
  just completed ones (`previous_matchday`) — see "Price data leads box-score
  completion" below for why. Out of scope: any round beyond that (not yet
  started); reconciling player IDs with other datasets (basketballsphere /
  Kaggle) — that's separate crosswalk work per `spec-player-name-linking.md`.

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
    head-coach roster, with box-score columns placeholder-valued (`"-"` for
    most stat columns, `0`/`"0"` for fpt/win-loss buckets) — see "Price data
    leads box-score completion" below for the one exception (`quotation`).
    The fetcher must never gate on response shape to decide whether a round's
    box-score data is real — same "completeness check" discipline as
    `spec-euroleague-live-fetcher.md`'s polling loop.
  - Head-coach rows always carry `"-"` for box-score columns
    (reb/ast/stl/tov/blk/blka/fd/pf/fg_missed/ft_missed) — coaches don't have
    those stats; only fpt/quotation/plus/win-loss buckets are real values.
    Symmetrically, player rows always carry `"-"` for the win/loss-bucket
    columns (win_1-10/win_11-20/win_20/win_ot/loss_1-10/loss_11-20/loss_20/
    loss_ot) — those are coach-only (presumably the coach's team's game
    margin buckets). Expected, not a data-quality issue in the API response
    itself — but see "Column split" below for why each CSV drops the columns
    that are always placeholder for its own entity type.

### Price data leads box-score completion

- Verified live 2026-09-28 (round 1 complete, round 2 in progress): querying
  `matchdays=1529` (round 2, `current_matchday`, box-score stats still `0`)
  already returns a **different `quotation` value than the round-1 query**
  for 265 of 340 players — e.g. S. Vezenkov: `17` (round 1) → `17.6` (round
  2). `quotation` is "price entering this round," published as soon as a
  round becomes `current_matchday`, well before that round's games are
  played. `plus` was `0.0` for every player in both rounds at verification
  time (round 1 is the season opener, so there's no prior round for it to
  diff against yet — not enough completed rounds have passed to confirm
  whether/when it becomes nonzero for a later round).
- Consequence: gating fetch on `previous_matchday.number` (completed rounds
  only, the original design) silently skips capturing the in-progress
  round's price snapshot. That snapshot is not recoverable later — by the
  time the round closes and becomes `previous_matchday`, `quotation` moves
  again (entering the *next* round), so the entering-price value for the
  round that just closed is gone. The fetcher therefore fetches through
  `current_matchday.number` instead. This assumes the script runs at least
  once per round while that round is still current — skipping a round's
  entire in-progress window means only its post-close price is ever
  captured, not its entering price. Acceptable: a missed entering-price
  snapshot degrades one column for one round, not a hard failure.

## Design

### Per run

1. Fetch `/leagues/10/config`. Build `{round_number: matchday_id}` from the
   `matchdays` array. Set `max_round = current_matchday.number` (the
   in-progress round, not just the last completed one — see "Price data
   leads box-score completion").
2. For `round_number` in `1..max_round`:
   - Fetch all pages (`per_page=100`, loop until `page > last_page`) of
     `/stats/players/table?stats_type=tot&matchdays={matchday_id}&positions=Guard,Forward,Center`
     → rows tagged `round=round_number`, appended to the players accumulator.
   - Same call with `positions=Head Coach` → head_coaches accumulator.
3. Write both accumulators to CSV (full overwrite each run). Both start from
   `round`, `player_id` (the API's `id`), then shared identifying columns
   (`rank`/`name`/`position`/`team`/`fpt`/`quotation`/`plus`), then diverge —
   see "Column split" below. Column names are the response's own `columns`
   names (already snake_case-ish — `win_1-10` etc. sanitized to valid CSV
   headers, e.g. `win_1_10`).

### Column split

- Both position filters return the same 26-column response shape (see
  "Endpoints" above), but only half the columns are ever real for each
  entity: box-score columns for players, win/loss-bucket columns for head
  coaches. Each row dict built by `fetch_round_rows` keeps all 26 keys
  regardless of entity type (it doesn't know or care which filter produced
  it); `write_csv` takes an explicit `fieldnames` list per output
  (`PLAYER_CSV_FIELDNAMES` / `HEAD_COACH_CSV_FIELDNAMES`, both sharing a
  common identifying-column prefix) and writes with `extrasaction="ignore"`
  so each CSV only ever carries the columns that can hold real data for that
  entity — no columns that are always `"-"` for that dataset.

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
  (columns+row zip), pagination looping (mocked multi-page response), the
  round-cutoff logic (config fixture → `max_round` includes the in-progress
  round), and the column split (`write_csv` drops head-coach-only columns
  from `players.csv` and player-only columns from `head_coaches.csv`). No
  live network calls in tests.
- Running the script live produces `players.csv` and `head_coaches.csv` under
  `data/raw_data/euroleague_fantasy_stats/`, currently 2 rounds × 345 players
  + 2 rounds × 20 head coaches (round 1 complete, round 2 in progress),
  matching the counts independently verified live above. `players.csv`
  carries no `win_*`/`loss_*` columns; `head_coaches.csv` carries no
  `pts`/`reb`/`ast`/`stl`/`tov`/`blk`/`blka`/`fd`/`pf`/`fg_missed`/`ft_missed`
  columns. Re-running is idempotent for completed rounds (no state file); the
  in-progress round's row count and `quotation` values may shift between runs
  as the API's live price data updates — expected, not a bug.
- Script header documents inputs/outputs/`final` flag (`false` — raw fetch,
  not yet exposed under `data/stage_99/`) per repo convention.
