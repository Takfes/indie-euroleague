# Spec: Team name linking — basketballsphere prices ↔ EuroLeague box score

## Goal

Produce a left-join **name map** from `basketballsphere_prices.csv`'s `club` column (master,
current-season EuroLeague Fantasy prices) to a EuroLeague box-score team display name, so
downstream feature work can attach historical team-level stats to current fantasy clubs. Same
high-level design as `spec-player-name-linking.md` (exact → fuzzy → agent, name-to-name only, no
ids in the artifact) — this ticket applies it to teams, adding one extra raw-data source because
the box-score dataset itself carries no team display name.

**The artifact's sole purpose is a name map**: `basketballsphere_prices.csv`'s `club` ↔ a
EuroLeague box-score team display-name spelling. It carries no other attribute from either
source — not `team_id`, not the fantasy side's `position`/`price`/`role`. Consumers who need
`team_id` resolve it themselves from `euroleague_header.csv` (`team_a`/`team_b` ↔
`team_id_a`/`team_id_b` are in the same row, a trivial reverse lookup) — same pattern as the
player crosswalk, where consumers resolve `player_id` themselves from `euroleague_box_score.csv`.

## Scope

- Left side (master): distinct `club` values in `data/raw_data/fantasy_prices/basketballsphere_prices.csv`
  (20 distinct clubs across 346 rows — dedupe to one crosswalk row per club, not one per player row).
- Right side: `data/raw_data/kaggle_data/euroleague_box_score.csv` has **no team display-name
  column**, only a stable 3-letter `team_id` (e.g. `MAD`, `TEL`). Team display names live in
  `data/raw_data/kaggle_data/euroleague_header.csv` (`team_a`/`team_b` + `team_id_a`/`team_id_b`,
  one row per game E2007–E2025). Build the box-score name pool from this file: every distinct
  `(team_a, team_id_a)` / `(team_b, team_id_b)` pair.
- Out of scope: a `data/stage_XX` dataset-path registry (same pre-existing gap as the player
  ticket). Actually joining/using the map in downstream feature scripts beyond the lookup class
  described below.

## Analysis (done 2026-09-27 — don't re-derive, use these numbers to sanity-check your own run)

- `euroleague_header.csv` yields 157 distinct team-name spellings across 66 distinct `team_id`s.
  **Zero spellings are shared by more than one `team_id`** (unlike the player side, there is no
  `exact_collision` case to handle for teams — still implement the check via the shared
  `matching.py` engine, it just won't fire on real data today).
- Team names churn heavily by sponsor/season, far more than player names: e.g. `team_id=BAS`
  (Baskonia) has 16 historical spellings (`TAU CERAMICA`, `CAJA LABORAL`, `KIROLBET BASKONIA
  VITORIA-GASTEIZ`, `BASKONIA`, ...), several sharing no token overlap with each other at all.
  `team_id=TEL` (Maccabi Tel Aviv) has 7 (`MACCABI ELITE TEL AVIV`, `MACCABI ELECTRA`, `MACCABI
  FOX TEL AVIV`, ...). This is expected and is exactly the kind of judgment call the agent stage
  exists for — a fuzzy string match cannot know `TAU CERAMICA` became `BASKONIA`.
- Baseline check: normalize both sides (strip accents, upper-case, letters/spaces only) and
  exact-match the master `club` string against the pool.
  - Result: 10/20 exact (`Anadolu Efes`, `Baskonia`, `Maccabi Tel Aviv`, `Milano`, `Olympiacos`,
    `Panathinaikos`, `Partizan`, `Real Madrid`, `Virtus Bologna`, `Zalgiris`).
  - 10 remain open: `ASVEL`, `Barcelona`, `Bayern Munich`, `Besiktas`, `Crvena Zvezda`, `Dubai`,
    `Fenerbahce`, `Hapoel Tel Aviv`, `Paris`, `Valencia`. Expect most to resolve via fuzzy
    (`Barcelona` → `FC BARCELONA` variants, `Fenerbahce` → `FENERBAHCE ...` variants) or agent
    domain knowledge (sponsor-prefixed historical names). `Dubai` (Dubai Basketball) is a recent
    EuroLeague entrant — may have thin or zero box-score history in this dataset; a `no_match`
    verdict is a legitimate, correct outcome here, not a failure to close the row.

## Design

### Pipeline stages

Identical structure to the player pipeline, reusing `src/eupy/entity/matching.py`:

1. **Exact stage** — normalize both sides, exact-match the master `club` string against the
   distinct box-score team-name spellings pooled from `euroleague_header.csv`.
2. **Fuzzy stage** — for still-unmatched clubs, `rapidfuzz` top-3 candidates ≥ the same 80 cutoff
   used for players (re-tune only if this ticket's real 10 misses show the floor is wrong for
   team names — check before assuming it transfers as-is).
3. **Agent stage (skill)** — a dedicated Sonnet subagent (see `resolve-team-names` skill below)
   reviews every `needs_review` / `no_candidate` row and writes verdicts.

### Idempotency requirement

Same contract as the player pipeline: rows already resolved (`confirmed` / `rejected` /
`no_match`) are carried over to the new output completely unchanged, keyed on `name` (the club
string). Everything else is recomputed fresh on every run.

### Module structure

Same `src/eupy/entity/` subpackage:

- `src/eupy/entity/resolve_team_names.py` — thin domain script: load
  `basketballsphere_prices.csv` (dedupe to distinct `club` values), build the team-name pool from
  `euroleague_header.csv`, call `matching.py`, write the crosswalk.
- `src/eupy/entity/apply_team_name_verdicts.py` — verdict-merge script/CLI, same shape as
  `apply_player_name_verdicts.py`.
- `src/eupy/entity/team_crosswalk.py` — `TeamNameCrosswalk`, read-only consumption class,
  mirroring `PlayerNameCrosswalk`.
- `tests/test_resolve_team_names.py`, `tests/test_apply_team_name_verdicts.py`,
  `tests/test_team_name_crosswalk.py` — fixture-based, no network calls, no dependency on the
  full real CSVs.

### Data output

- `data/stage_01/team_name_crosswalk.csv` — plain CSV via stdlib `csv`.
- Columns, and *only* these: `name, boxscore_team_name, match_status, match_score, matched_by,
  notes`. `match_status` ∈ `exact | needs_review | confirmed | rejected | no_candidate | no_match`.
  `boxscore_team_name` is the matched `euroleague_header.csv` display-name spelling, empty
  otherwise. `name` is the master's `club` value.
- Script header: inputs = `basketballsphere_prices.csv` + `euroleague_header.csv`; output = this
  file; `final: true` -> symlink into `data/stage_99/`.

### Consumption class

`TeamNameCrosswalk` (`src/eupy/entity/team_crosswalk.py`), mirroring `PlayerNameCrosswalk`:

- `TeamNameCrosswalk.load(path: Path | None = None) -> TeamNameCrosswalk` — classmethod,
  `path=None` defaults to `data/stage_99/team_name_crosswalk.csv`. Loads into a `name -> row` dict.
- `boxscore_team_name_for(name: str) -> str | None` — returns the matched box-score team-name
  spelling for `exact`/`confirmed` rows, `None` otherwise.
- No refresh method, by design — same as `PlayerNameCrosswalk`.
- Clear error if the file doesn't exist yet, pointing at the skill/script to run first.

### Skill

New `~/.claude/skills/resolve-team-names/SKILL.md` (user-level, same location as the other
entity-resolution skills). Same shape as `resolve-player-names`: run the exact+fuzzy script,
dispatch a Sonnet subagent to review open rows (briefed with the rows, `euroleague_header.csv`
access, and team-specific judgment-call guidance — sponsor-name churn, club rebrands/relocations,
recent-entrant no-history cases), apply verdicts, re-run to confirm closure.

## Pass criteria

- `uv run ruff check src tests` and `uv run ruff format --check src tests` clean.
- `uv run pytest` green, including tests for: team-name pool construction from
  `euroleague_header.csv`, exact match, fuzzy candidate generation, full-row idempotent carryover
  on re-run, verdict-apply merge, and `TeamNameCrosswalk` (load, lookup hits/misses, missing-file
  error).
- Running `resolve_team_names.py` live against the real CSVs produces
  `data/stage_01/team_name_crosswalk.csv` with all 20 distinct clubs accounted for, columns
  exactly `name, boxscore_team_name, match_status, match_score, matched_by, notes`.
- Invoking the skill end-to-end (subagent dispatch included) resolves every `needs_review` /
  `no_candidate` row to `confirmed` / `rejected` / `no_match`, with a rationale in `notes` for
  every non-exact row.
- Re-running `resolve_team_names.py` after a verdict pass changes nothing (byte-identical
  already-resolved rows) — verified live, not just in tests.
- Script I/O headers present and accurate.
