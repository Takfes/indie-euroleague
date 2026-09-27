# Spec: Player name linking — basketballsphere prices ↔ EuroLeague box score

## Goal

Produce a left-join crosswalk linking `basketballsphere_prices.csv` (master, current-season
EuroLeague Fantasy prices) to the distinct player roster derived from `euroleague_box_score.csv`
(historical box scores, E2007–E2025), so downstream feature work can attach historical box-score
stats to current fantasy players. The process must be re-runnable as new price snapshots arrive
(new season, transfers, rookies) without re-reviewing rows already resolved.

## Scope

- Left dataset (master): `data/raw_data/fantasy_prices/basketballsphere_prices.csv` (346 rows:
  326 `role=player`, 20 `role=head_coach`).
- Right dataset: distinct players derived from `data/raw_data/kaggle_data/euroleague_box_score.csv`
  (129k event rows -> 2,363 unique `player_id`s; verified no `player_id` maps to more than one
  name spelling, so one canonical name per id is safe to use as the match target — dedupe by
  `player_id`, excluding `dorsal=TOTAL` synthetic rows, before matching).
- In scope: `role=player` rows only. `role=head_coach` rows (20) pass through untouched with
  `match_status=not_applicable` — `euroleague_box_score.csv` has no coach data, so matching them
  is structurally impossible; don't spend fuzzy/agent effort on them.
- Out of scope: a `data/stage_XX` dataset-path registry (none exists yet in this repo — see
  "Data output" below). Joining/using the crosswalk in downstream feature scripts (future ticket).

## Analysis (done 2026-09-27 — don't re-derive, use these numbers to sanity-check your own run)

- Name formats differ: basketballsphere = `"First Last"`; box_score = `"LAST, First"`
  (upper-case last name, comma-separated).
- Baseline check: normalize both (strip accents, upper-case, strip non-letters except spaces,
  reorder box_score's `LAST, First` -> `First Last`) and exact-match the normalized string.
  - Result: 252/346 exact matches. Of the 94 unmatched, 20 are `head_coach` rows (structurally
    unmatchable) -> ~74 real player misses out of 326 `role=player` rows (~77% exact-match rate
    among players).
  - Misses are a mix of: (a) genuinely new-to-EuroLeague players with zero box-score history (no
    possible match — the correct output is `no_match`, not a forced fuzzy hit), and (b)
    name-formatting drift a fuzzy pass should catch — nicknames/initials (e.g. "TJ Warren"),
    suffixes ("Jr.", "IV"), hyphenated surnames, accented characters, spelling variants.

## Design

### Pipeline stages

1. **Exact stage** — deterministic normalization + exact match on the normalized `"first last"`
   string.
2. **Fuzzy stage** — for `role=player` rows still unmatched, score against every normalized
   distinct box_score name using `rapidfuzz` (new dependency — the *only* one to add; keep
   everything else on stdlib `csv`, matching the existing fetchers' zero-dependency style — no
   pandas/pyarrow). Keep the top few candidates (e.g. top 3) with score above a floor (start at
   80, tune against the real ~74 misses and report what you land on). Below the floor ->
   `no_candidate`. **Fuzzy never auto-accepts** — every fuzzy candidate is a proposal with status
   `needs_review`; only the agent stage turns it into a decision. This is what implements the
   requested exact -> fuzzy-candidates -> agent-verifies flow.
3. **Agent stage (skill)** — a Claude Code skill that, when invoked:
   - Runs the stage-1/2 script to (re)generate/update the crosswalk.
   - For every `needs_review` row, judges the candidate(s) and sets `confirmed` or `rejected`
     (trying the next candidate, or falling back to `no_match` if none hold up). Use judgment on
     nicknames/suffixes/transliteration — that's the point of this stage.
   - For every `no_candidate` row, does a second pass looking for a match the fuzzy stage missed
     (e.g. very different name spelling); confirms genuine no-history cases as `no_match` rather
     than leaving them ambiguous.
   - Writes verdicts back through a small dedicated script (not ad-hoc CSV edits by hand — the
     merge must be reproducible and testable), updating `match_status` / `player_id` /
     `boxscore_name` / `notes` / `matched_by` for the rows it resolved.

### Idempotency requirement

Re-running the exact+fuzzy script (e.g. next price snapshot) must **not regress rows the agent
stage already resolved** (`confirmed` / `rejected` / `no_match`) — merge into the existing
crosswalk keyed on `(name, role)`; only rows not yet resolved by the agent get fresh
exact/fuzzy treatment. This is what makes the process replicable without re-reviewing everything
each run.

### Module structure

Mirrors the existing `src/indie_euroleague/fetchers/` subpackage pattern — one file per script,
each with importable functions that tests exercise directly (same pattern as
`tests/test_fetch_basketballsphere_prices.py`):

- `src/indie_euroleague/linking/__init__.py`
- `src/indie_euroleague/linking/link_player_names.py` — exact + fuzzy stage, CLI + importable
  functions (normalization, box-score roster dedupe, candidate generation, crosswalk merge).
- `src/indie_euroleague/linking/apply_link_verdicts.py` — verdict-merge script/CLI used by the
  skill stage.
- `tests/test_link_player_names.py`, `tests/test_apply_link_verdicts.py` — fixture-based, no
  network calls, no dependency on the full real CSVs.

If, once inside the code, a different split reads cleaner (e.g. folding verdict-apply into a
subcommand of the same script), that's a minor call you can make — the two-source-file shape
above is the default, not a hard requirement.

### Data output

- `data/stage_01/player_name_crosswalk.csv` — plain CSV via stdlib `csv` (no pandas/pyarrow),
  consistent with the project's current zero-runtime-dependency convention; `rapidfuzz` is the
  only new dependency (`uv add rapidfuzz`).
- Columns: `name, role, club, position, price, match_status, player_id, boxscore_name,
  match_score, matched_by, notes`. `match_status` ∈
  `exact | needs_review | confirmed | rejected | no_candidate | no_match | not_applicable`.
- Script header: inputs = the two raw CSVs; output = this file; `final: true` (it's meant to feed
  downstream feature work) -> symlink into `data/stage_99/` per the project's manual-symlink
  convention once the file exists.
- No `data/stage_XX` path registry exists yet in this repo (nothing else writes to
  `data/stage_01` today — it's currently empty) — hardcode the output path for now, the same way
  the existing fetchers hardcode their `raw_data` output paths. List the registry gap as a
  deferral in your final report (main agent will log it in `docs/next-steps.md` — don't edit that
  file yourself).

### Skill

New skill, `~/.claude/skills/link-player-names/SKILL.md` — same location convention as the
existing `~/.claude/skills/euroleague-fantasy-roster/` skill (project-specific skills live in the
user-level skills directory in this workflow, not repo-local `.claude/skills/`). Match that
skill's frontmatter/procedure format, but follow *this repo's* current data/script conventions
(not that older skill's hardcoded-path style). Use the `superpowers:writing-skills` skill while
authoring it. Content: when to invoke (new price snapshot landed, or ad-hoc re-verification
request), the exact -> fuzzy -> agent flow, how to run the two scripts, and the judgment calls the
agent stage should apply (nicknames, suffixes, transliteration, genuine-rookie no-match cases).

## Pass criteria

- `uv run ruff check src tests` and `uv run ruff format --check src tests` clean.
- `uv run pytest` green, including new tests for: name normalization (accents/case/comma-reorder),
  exact match, fuzzy candidate generation + threshold behavior, crosswalk merge idempotency
  (re-run preserves prior confirmed/rejected/no_match rows), verdict-apply merge.
- Running `link_player_names.py` live against the two real CSVs produces
  `data/stage_01/player_name_crosswalk.csv` with all 346 rows accounted for (20
  `not_applicable`, the rest `exact` / `needs_review` / `no_candidate`).
- Running the skill (agent stage) end-to-end resolves every `needs_review` / `no_candidate` row
  to `confirmed` / `rejected` / `no_match`, with a brief rationale in `notes` for every non-exact
  row.
- Re-running `link_player_names.py` after a verdict pass changes none of the already-`confirmed` /
  `rejected` / `no_match` rows (idempotency check, exercised in tests and spot-checked live).
- Script I/O headers present and accurate.
