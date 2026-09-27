# Spec: Player name linking — basketballsphere prices ↔ EuroLeague box score

## Goal

Produce a left-join **name map** from `basketballsphere_prices.csv` (master, current-season
EuroLeague Fantasy prices) to the distinct player names in `euroleague_box_score.csv` (historical
box scores, E2007–E2025), so downstream feature work can attach historical box-score stats to
current fantasy players. The process must be re-runnable as new price snapshots arrive (new
season, transfers, rookies) without re-reviewing rows already resolved.

**The artifact's sole purpose is a name map**: `basketballsphere_prices.csv`'s `name` ↔
`euroleague_box_score.csv`'s `player` display name. It carries no other dataset's attributes
(no `club`/`position`/`price` from the master, no `player_id` from the box score) — only the two
names themselves plus the resolution process's own metadata (status/score/method/rationale).
Consumers who need `player_id`, `club`, `price`, etc. read them from their respective source
files directly; this artifact only tells you which two name strings refer to the same person.

## Scope

- Left side (master): `data/raw_data/fantasy_prices/basketballsphere_prices.csv`, `role=player`
  rows only (326 of 346 rows).
- Right side: distinct player names in `data/raw_data/kaggle_data/euroleague_box_score.csv`
  (129k event rows, `dorsal=TOTAL` rows excluded as synthetic team totals).
- Out of scope: `role=head_coach` rows (20). The box-score dataset has no coach data, so
  matching them is structurally impossible — they are filtered out before the pipeline runs and
  never appear in the crosswalk at all (not even as a `not_applicable` row; that status has been
  retired — see 2026-09-27 revision below). A `data/stage_XX` dataset-path registry (none exists
  yet in this repo). Actually joining/using the map in downstream feature scripts beyond the
  lookup class described below (real feature work is a future ticket).

## Revision — 2026-09-27

Three adjustments made after the first implementation landed (see `spec-team-name-linking.md`
for the sibling artifact added in the same pass):

- **Coaches dropped entirely.** `role=head_coach` rows are filtered out of the master rows before
  building the crosswalk (previously: passed through as `match_status=not_applicable`, 20 rows of
  pure noise in an artifact that exists to answer a player-matching question). `not_applicable` is
  retired from the `match_status` vocabulary.
- **`role` column dropped.** With `head_coach` gone, every remaining row has `role=player` —
  a constant column carrying no information — and `basketballsphere_prices.csv` has no duplicate
  `name` values among `role=player` rows, so the crosswalk key simplifies from `(name, role)` to
  `name` alone. `PlayerNameCrosswalk.boxscore_name_for` drops its `role` parameter accordingly.
  Columns are now exactly: `name, boxscore_name, match_status, match_score, matched_by, notes`.
- **Shared matching engine.** The normalize/exact/fuzzy logic (`normalize_name`, `find_candidates`,
  `format_candidates_note`) moves out of `resolve_player_names.py` into `src/eupy/entity/matching.py`,
  since `resolve_team_names.py` (the new sibling pipeline) needs the identical exact→fuzzy
  machinery, just with a different data source, key column, and output column name.
  `resolve_player_names.py` becomes a thin domain script: load the two raw CSVs, filter to
  `role=player`, build the box-score name pool, call the shared engine, write the crosswalk.
- **Agent stage is now a real subagent dispatch.** The `resolve-player-names` skill's review step
  (previously: instructions for whoever invokes the skill to do the judgment calls inline) now
  dispatches a dedicated Sonnet subagent — via the Agent tool — that receives the open rows plus
  raw-data access, does the review, and returns a verdicts JSON for `apply_player_name_verdicts.py`
  to apply. This was the original design intent ("offload the job to an agent to verify the
  partial matchings") that the first implementation didn't actually wire up.

## Analysis (done 2026-09-27 — don't re-derive, use these numbers to sanity-check your own run)

- Name formats differ: basketballsphere = `"First Last"`; box_score = `"LAST, First"`
  (upper-case last name, comma-separated).
- Baseline check: normalize both (strip accents, upper-case, letters/spaces only, reorder
  box_score's `LAST, First` -> `First Last`) and exact-match the normalized string.
  - Result: 252/346 exact matches. Of the 94 unmatched, 20 are `head_coach` rows (structurally
    unmatchable) -> ~74 real player misses out of 326 `role=player` rows (~77% exact-match rate
    among players).
  - Misses are a mix of: (a) genuinely new-to-EuroLeague players with zero box-score history (no
    possible match — the correct output is `no_match`, not a forced fuzzy hit), and (b)
    name-formatting drift a fuzzy pass should catch — nicknames/initials (e.g. "TJ Warren"),
    suffixes ("Jr.", "IV"), hyphenated surnames, accented characters, spelling variants.
- `player_id` matters for *internal correctness* even though it's never written to the output:
  verified no `player_id` maps to more than one name spelling, but the reverse isn't guaranteed —
  two different real players could in principle share one box-score display-name string. Use
  `player_id` internally to detect that kind of true collision (flag as `needs_review` rather than
  silently merging), even though the persisted row only ever carries the name string. Separately,
  the same *normalized* name can be spelled two different raw ways in the box score (accents,
  e.g. "MARÍ" vs "MARI") — also a `needs_review` case, for the same silent-guess reason.

## Design

### Pipeline stages

1. **Exact stage** — normalize both sides, exact-match the normalized `"first last"` string
   against the distinct set of box-score names. Exactly one distinct raw box-score spelling for
   that normalized value -> `exact`. More than one distinct raw spelling (accent variants) or a
   collision across different `player_id`s sharing a name -> `needs_review` (list every
   candidate spelling in `notes`; picking one silently would be a guess).
2. **Fuzzy stage** — for `role=player` rows still unmatched, score against every distinct box-score
   name using `rapidfuzz` (the one new dependency — `uv add rapidfuzz`; everything else stays on
   stdlib `csv`, no pandas/pyarrow). Keep the top few candidates (e.g. top 3) above a score floor
   (80, tuned against the real ~74 misses — see Analysis). Below the floor -> `no_candidate`.
   **Fuzzy never auto-accepts** — every fuzzy candidate is a proposal with status `needs_review`;
   only the agent stage turns it into a decision.
3. **Agent stage (skill)** — a Claude Code skill that, when invoked:
   - Runs the exact+fuzzy script to (re)generate/update the map.
   - For every `needs_review` row, judges the candidate(s) and sets `confirmed` or `rejected`
     (trying the next candidate, or falling back to `no_match` if none hold up). Judgment calls:
     nicknames, suffixes, transliteration, first-name-only/surname-only false positives.
   - For every `no_candidate` row, does a second pass looking for a match the fuzzy stage missed;
     confirms genuine no-history cases as `no_match` rather than leaving them ambiguous.
   - Writes verdicts back through a small dedicated script (not ad-hoc CSV edits — the merge must
     be reproducible and testable), updating `match_status` / `boxscore_name` / `notes` /
     `matched_by` for the rows it resolved.

### Idempotency requirement

Re-running the exact+fuzzy script must **not regress rows the agent stage already resolved**
(`confirmed` / `rejected` / `no_match`) — those rows are carried over to the new output
**completely unchanged**, keyed on `(name, role)`. Only rows not yet resolved by the agent
(new, `exact`, `needs_review`, `no_candidate`) are recomputed fresh. No partial field-level
refresh of any kind — there's nothing on the master side (other than `name`/`role`, the key
itself) that this artifact carries, so there's nothing to refresh.

### Module structure

Subpackage `src/eupy/entity/` (name-entity-resolution — matching records across
datasets that refer to the same real-world entity despite different spellings/identifiers), same
one-file-per-script pattern as `fetchers/`:

- `src/eupy/entity/__init__.py`
- `src/eupy/entity/matching.py` — shared exact→fuzzy engine (`normalize_name`,
  `find_candidates`, `format_candidates_note`), generic over a `{spelling: {owner_id}}` pool.
  Used by both `resolve_player_names.py` and `resolve_team_names.py`.
- `src/eupy/entity/resolve_player_names.py` — thin domain script: load the two raw CSVs,
  filter to `role=player`, build the box-score player-name pool, call `matching.py`, write the
  crosswalk.
- `src/eupy/entity/apply_player_name_verdicts.py` — verdict-merge script/CLI used by
  the skill stage.
- `src/eupy/entity/crosswalk.py` — `PlayerNameCrosswalk`, the read-only consumption
  class (see below).
- `tests/test_resolve_player_names.py`, `tests/test_apply_player_name_verdicts.py`,
  `tests/test_player_name_crosswalk.py`, `tests/test_matching.py` — fixture-based, no network
  calls, no dependency on the full real CSVs.

### Data output

- `data/stage_01/player_name_crosswalk.csv` — plain CSV via stdlib `csv`.
- Columns, and *only* these: `name, boxscore_name, match_status, match_score, matched_by,
  notes`. `match_status` ∈ `exact | needs_review | confirmed | rejected | no_candidate | no_match`.
  `boxscore_name` is the box-score display name string when matched, empty otherwise.
- Script header: inputs = the two raw CSVs; output = this file; `final: true` -> symlink into
  `data/stage_99/` per the project's manual-symlink convention.
- No `data/stage_XX` path registry exists yet — hardcode the output path for now, same as the
  existing fetchers hardcode their `raw_data` paths (already flagged in `docs/next-steps.md`).

### Consumption class

`PlayerNameCrosswalk` (`src/eupy/entity/crosswalk.py`) — read-only accessor over the
published map, decoupled from how it gets regenerated:

- `PlayerNameCrosswalk.load(path: Path | None = None) -> PlayerNameCrosswalk` — classmethod.
  `path=None` (the normal case) defaults to **`data/stage_99/player_name_crosswalk.csv`** — the
  project's stable, dataset-named consumption path (survives a future stage renumber), not
  `stage_01` (the pipeline's own internal working path). Loads the CSV into a `name -> row` dict.
- A lookup method, `boxscore_name_for(name: str) -> str | None` — returns the mapped box-score
  display name only when `match_status` is `exact` or `confirmed`; every other status (including
  `needs_review`/`no_candidate` — still-open rows) resolves to `None`, so callers never need to
  know the status vocabulary.
- Refresh is never a method on this class — regenerating the artifact means re-running
  `resolve_player_names.py` / invoking the skill, same as always. The class only reads whatever's
  currently on disk, no hidden network/subprocess calls.
- Clear error (what failed / next step) if the file doesn't exist yet, pointing at the skill/script
  to run first.

### Skill

`~/.claude/skills/resolve-player-names/SKILL.md` (user-level location, same as the existing
`~/.claude/skills/euroleague-fantasy-roster/` skill). Its review step (open `needs_review` /
`no_candidate` rows) dispatches a dedicated Sonnet subagent via the Agent tool, briefed with: the
open rows, the raw box-score CSV path, the judgment-call guidance (nicknames, suffixes,
transliteration, first-name-only/surname-only false positives — content carried over from the
existing skill), and the required verdicts-JSON shape. The subagent returns the verdicts JSON;
the skill then runs `apply_player_name_verdicts.py --verdicts <path>` and re-runs
`resolve_player_names.py` to confirm zero rows remain open.

## Pass criteria

- `uv run ruff check src tests` and `uv run ruff format --check src tests` clean.
- `uv run pytest` green, including tests for: name normalization, exact match (incl. the
  ambiguous-spelling/collision case), fuzzy candidate generation + threshold behavior, full-row
  idempotent carryover on re-run, verdict-apply merge, and `PlayerNameCrosswalk` (load, lookup
  hits/misses, missing-file error).
- Running `resolve_player_names.py` live against the two real CSVs produces
  `data/stage_01/player_name_crosswalk.csv` with all 326 `role=player` rows accounted for (zero
  `head_coach` rows present), columns exactly `name, boxscore_name, match_status, match_score,
  matched_by, notes` — no `role`, `player_id`, `club`, `position`, or `price`.
- Invoking the skill end-to-end (subagent dispatch included) resolves every `needs_review` /
  `no_candidate` row to `confirmed` / `rejected` / `no_match`, with a rationale in `notes` for
  every non-exact row.
- Re-running `resolve_player_names.py` after a verdict pass changes nothing (byte-identical
  already-resolved rows) — verified live, not just in tests.
- Script I/O headers present and accurate.
