# Spec: EuroLeague Advanced API schedule fetcher

## Goal

Acquire the full-season game schedule (one row per game: round/gameday, date,
tip-off time, arena, home/away teams, played flag) from the Euroleague Advanced
API and store it as a raw CSV. Gives the pipeline a season calendar keyed by a
`gamecode` that joins directly to the live box-score fetcher's `game_id`.

## Scope

- One script: `src/eupy/fetchers/fetch_game_schedule.py`.
- One endpoint: `GET https://euroleague-advanced-api.eu/Euroleague/schedule?season={year}`
  (OpenAPI operation `schedule__competition__schedule_get`; swagger at
  `https://euroleague-advanced-api.eu/docs`, machine-readable spec at
  `/openapi.json`).
- Euroleague only (hard-coded path segment). The API also serves `Eurocup`; not
  needed here.
- One season per run, selected with `--season E2026` (same code format and
  default as `fetch_euroleague_live_boxscores.py`); the script derives the API's
  integer year (`2026`) from it.
- Output: `data/raw_data/game_schedule/schedule_{season}.csv`
  (e.g. `schedule_E2026.csv`). Source-level directory, matching
  `euroleague_net/` and `kaggle_data/` — future endpoints of the same API land
  beside it.
- Not incremental: the schedule is mutable (`played`, dates, times get
  confirmed/changed), so every run refetches and fully overwrites that season's
  file. No state file. Matches `fetch_basketballsphere_prices.py`.
- Raw layer: values written verbatim as returned, columns in the API's order, no
  renaming or typing. Out of scope: normalising team codes/names to the
  crosswalks, date parsing, any stage-01+ dataset built from this, Eurocup,
  historical backfill (possible later by running with `--season E2025` etc.).
- No auth, no new dependency (stdlib `urllib`, like the other fetchers).

## Empirical findings (verified live, 2026-09-28)

- Free/unauthenticated. The API description says only the *advanced-stats*
  endpoints need a subscription; basic ones stay free. Anonymous GET returns 200.
- `season` query param is an integer year (`2025` = 2025-26), required, `>= 2000`,
  no later than the season under way. Response: JSON array of flat objects.
- `season=2025` → 402 rows (RS, playoffs, Final Four); `season=2026` → 380 rows
  (all `round == "RS"`; 10 already played at the time of writing).
- Every row has the same 20 keys: `gameday, round, arenacode, arenaname,
  arenacapacity, date, startime, endtime, group, game, gamecode, hometeam,
  homecode, hometv, awayteam, awaycode, awaytv, confirmeddate, confirmedtime,
  played`. All values are strings except `gameday` (int). Booleans arrive as the
  strings `"true"`/`"false"`. `date` is `"Sep 24, 2026"`.
- `gamecode` looks like `E2026_7` — identical in format to the live box-score
  fetcher's `game_id` (`{season}_{gamecode}`), so it is the join key.
- Swagger UI warns full-season payloads are big for browsers; ~170 KB here, fine
  programmatically. One request per run, no delay needed.

## Design

- `fetch_schedule(season_code)` → list of dicts; `write_schedule_csv(rows, path)`;
  `main()` with `--season` (default `E2026`) and `--out-dir`
  (default the repo's `data/raw_data/game_schedule`).
- Send a descriptive `User-Agent` (convention in the other fetchers).
- Fail loudly, with what/why/next, on: malformed season code (before any
  request), HTTP/network error, non-list payload, empty payload (a season with
  zero games is a red flag, not a valid result), rows whose `gamecode` does not
  start with the requested season code. Fetch and validate *before* opening the
  output file so a failure never truncates the previous good file.
- CSV header = keys of the first row, in order; every row must carry exactly that
  key set (else raise). No timestamps in the file → same API response gives a
  byte-identical file.

## Docs

- Script header declares Inputs / Outputs / `Final: false` in the fetchers'
  format.
- `docs/data-catalogue.md` and `docs/data-graph.md` are currently empty
  placeholders. They are populated (first time) by the agent from **all**
  script I/O headers in `src/` — not only the new script — with a
  "GENERATED — do not edit" banner. Stage = max(input stages) + 1, raw = stage
  0/"raw". Graph is a Mermaid `flowchart LR` (raw sources → scripts → datasets).
  No generator script is written in this task (tracked separately in memory).

## Pass criteria

1. `uv run python src/eupy/fetchers/fetch_game_schedule.py` writes
   `data/raw_data/game_schedule/schedule_E2026.csv`: header is the 20
   API fields in API order; row count equals the API's array length (380 at time
   of writing); `gamecode` unique and all prefixed `E2026_`.
2. Second run against an unchanged API produces a byte-identical file.
3. Bad `--season` (e.g. `2026`, `E26`, `X2026`) exits with a clear error and makes
   no request; an empty or non-list payload raises and leaves an existing output
   file untouched.
4. Script header (module docstring) lists Inputs / Outputs / Final.
5. Tests in `tests/test_fetch_game_schedule.py`, network mocked,
   covering: season-code parsing/validation, CSV column order + verbatim values,
   failure modes that must not clobber the existing file, deterministic
   re-write. Each test idea adversarially reviewed first; no coverage-only tests.
6. `ruff check src tests` and `ruff format --check src tests` clean;
   `uv run pytest` green.
7. `docs/data-catalogue.md` and `docs/data-graph.md` populated as described and
   consistent with every script header (new dataset present, stage computed,
   `stage_99` symlinks shown only for `final: true` datasets).
