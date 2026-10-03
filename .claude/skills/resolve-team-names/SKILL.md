---
name: resolve-team-names
description: |
  Use when a new basketballsphere fantasy-price snapshot has landed and the
  indie-euroleague team-name crosswalk needs refreshing, or when re-verifying
  unresolved team-name matches (needs_review / no_candidate rows) in
  data/stage_01/team_name_crosswalk.csv. Covers mapping
  basketballsphere_prices.csv club names to euroleague_header.csv box-score
  team display names and recording confirm/reject/no_match verdicts as append-only batches.
---

# Resolve Team Names

## Overview

Rebuilds `data/stage_01/team_name_crosswalk.csv`, a left-join
**name map** from current-season EuroLeague Fantasy clubs
(`data/raw_data/fantasy_prices/basketballsphere_prices.csv`'s `club` column)
to historical box-score team display names. `euroleague_box_score.csv` has
no team display-name column itself -- only a stable 3-letter `team_id` --
so the box-score name pool comes from `data/raw_data/kaggle_data/
euroleague_header.csv`'s `team_a`/`team_b` + `team_id_a`/`team_id_b`
columns. The map is purely `name <-> boxscore_team_name` plus the
resolution process's own metadata -- no `team_id`/`position`/`price`/`role`
from either source file. Consumers needing `team_id` resolve it themselves
from `euroleague_header.csv` directly.

The resolver (a DVC step in the `entity` pipeline) does the mechanical work
(exact-match, then fuzzy-candidate generation via rapidfuzz, shared with the player-name pipeline via
`matching.py`); a dispatched Sonnet subagent does the judgment calls the
fuzzy stage can't make on its own — that's the point of this skill. Team
names churn far more than player names (sponsor changes, rebrands,
relocations), so expect more judgment calls per row than the player-name
skill, and expect some real fuzzy scores of 0 for a genuine same-club match
(e.g. `TAU CERAMICA` -> `BASKONIA` shares no tokens at all).

## When to Use

- A new `basketballsphere_prices.csv` snapshot has landed (new clubs) and
  the crosswalk needs refreshing.
- Ad-hoc: someone wants the crosswalk's `needs_review` / `no_candidate` rows
  re-verified.

## Procedure

### 1. Rebuild the crosswalk with the current verdict batches

```bash
uv run eupy run entity
```

Runs `resolve_team_names.py` (plus the other entity steps if their inputs changed;
unchanged steps are skipped). The crosswalk is a pure function of the raw
data + the append-only batches in `data/curated/team_name_verdicts/`:
everything a batch covers comes out `confirmed` / `rejected` / `no_match`,
the rest is `exact`, `needs_review` or `no_candidate`. A changed price
snapshot legitimately reruns this step. Verdicts whose key is no longer in
the snapshot are skipped and counted as stale in the run summary — not an
error. Check the `match_status` summary before moving on.

### 2. Dispatch a subagent to review every `needs_review` and `no_candidate` row

```python
import csv
rows = list(csv.DictReader(open("data/stage_01/team_name_crosswalk.csv", encoding="utf-8")))
review = [r for r in rows if r["match_status"] in ("needs_review", "no_candidate")]
```

If `review` is empty, you are done. Otherwise, use the Agent tool to
dispatch one subagent (a general implementation/analysis agent, `model:
sonnet`) with a self-contained brief covering:

- **The open rows**: each row's `name` (the fantasy `club` value) plus, for
  `needs_review` rows, the candidates already listed in `notes` (`NAME
  (score)`) and the `matched_by` reason (`fuzzy` / `exact_ambiguous_spelling`
  / `exact_collision`). `no_candidate` rows have no candidates — the fuzzy
  stage found nothing above its score floor, which for team names can
  legitimately happen even for a real match (sponsor-name churn breaks
  string similarity entirely).
- **Raw-data access**: the subagent must cross-check
  `data/raw_data/kaggle_data/euroleague_header.csv` directly for every row's
  full spelling history, not just trust the candidate list. Give it this
  snippet to pool every distinct spelling by `team_id` (a club can have many
  historical spellings sharing one `team_id`):

  ```python
  import csv
  from collections import defaultdict
  by_id = defaultdict(set)
  with open("data/raw_data/kaggle_data/euroleague_header.csv", newline="", encoding="utf-8") as f:
      for row in csv.DictReader(f):
          by_id[row["team_id_a"]].add(row["team_a"])
          by_id[row["team_id_b"]].add(row["team_b"])
  print(sorted(by_id["BAS"]))  # every historical spelling for one team_id
  ```

  Also worth pulling each spelling's `season_code` range (min/max) to judge
  which historical spelling is most current when several map to the same
  `team_id` — pick the spelling with the most recent/longest-running
  `season_code` range unless there's a clearer signal.

- **Judgment-call categories** it will need to apply, one per row:
  - **Sponsor-prefixed historical names** — a fuzzy score of 0 is common and
    expected (e.g. `TAU CERAMICA` -> `BASKONIA`, `CAJA LABORAL` ->
    `BASKONIA`). Requires knowing EuroLeague club history, not string
    similarity — cross-check the `team_id`'s full spelling list rather than
    relying on the fuzzy candidates alone.
  - **Club rebrands / relocations** — a club can have many spellings under
    one stable `team_id` across seasons (Maccabi Tel Aviv alone has 7:
    `MACCABI ELITE TEL AVIV`, `MACCABI ELECTRA`, `MACCABI FOX TEL AVIV`, ...).
    Pick the current/most-recent spelling by `season_code`, and say so in
    `notes`.
  - **Distinct-club false positives** — fuzzy string similarity can surface
    a *different* real club that merely looks similar (e.g. "Paris" fuzzy-
    matching "Aris Thessaloniki" — different clubs, different `team_id`s).
    Reject unless the `team_id` and full spelling history genuinely point to
    the same club.
  - **`exact_ambiguous_spelling`** — one normalized name, more than one
    distinct raw spelling (e.g. a trailing-whitespace or accent variant).
    Confirm the canonical-looking one if they share a `team_id`.
  - **`exact_collision`** — one raw spelling shared by more than one
    distinct `team_id` (not observed in real data as of this pipeline's
    creation, but still possible). Don't assume it's safe to confirm
    without checking; `reject` if the data can't disambiguate.
  - **Recent-entrant / thin-history clubs** — a club with zero or
    near-zero EuroLeague box-score history (e.g. a brand-new franchise) is
    a legitimate `no_match`, not a failure to close the row. Don't force a
    guess.
- **The exact verdicts-JSON shape** `apply_team_name_verdicts.py` expects
  (see step 3) and the instruction that its job is to **return that JSON**
  as its final report — it does not apply the verdicts itself.

### 3. Apply the subagent's verdicts

The subagent's final report is a JSON list of verdict records, one per row
it resolved:

```json
[
  {
    "name": "Baskonia",
    "match_status": "confirmed",
    "boxscore_team_name": "TAU CERAMICA", "match_score": 0.0,
    "notes": "team_id BAS; 'TAU CERAMICA' is Baskonia's sponsor name in this era of the box-score history."
  },
  {
    "name": "Dubai",
    "match_status": "no_match",
    "notes": "No box-score history for this team_id -- Dubai Basketball is a brand-new EuroLeague entrant."
  }
]
```

Rules: `match_status` must be `confirmed` / `rejected` / `no_match`.
`confirmed` requires `boxscore_team_name` (plus ideally `match_score` — `0.0`
is valid and expected for sponsor-name matches with no string overlap);
`rejected` / `no_match` must NOT carry a `boxscore_team_name`. `notes` is
required on every verdict — a one-sentence rationale grounded in what's
actually in `euroleague_header.csv`. Convention: `needs_review` rows resolve
to `confirmed` or `rejected`; `no_candidate` rows resolve to `no_match`.

Save the subagent's JSON to a file (e.g. under `/tmp`), then ingest it:

```bash
uv run python src/eupy/entity/apply_team_name_verdicts.py --verdicts /path/to/verdicts.json [--label SLUG]
```

This validates the records and appends a new batch file
`data/curated/team_name_verdicts/NNNN_<slug>.json`. It never touches the
crosswalk or earlier batches.

### 4. Rebuild, verify, commit

```bash
uv run eupy run entity
```

Confirm the summary shows zero `needs_review` / `no_candidate`, and that a
second run does nothing (all steps up to date). Review `git diff` (the new
batch file + the changed `data/stage_01/team_name_crosswalk.csv`), then commit
both together.

## Quick Reference

| Step | Command |
|---|---|
| Rebuild crosswalk | `uv run eupy run entity` |
| Record verdicts (new batch) | `uv run python src/eupy/entity/apply_team_name_verdicts.py --verdicts PATH [--label SLUG]` |
| Batches | `data/curated/team_name_verdicts/` (append-only, tracked in git) |
| Crosswalk location | `data/stage_01/team_name_crosswalk.csv` (symlinked into `data/stage_99/`) |

`match_status` values: `exact` / `needs_review` / `confirmed` / `rejected` /
`no_candidate` / `no_match`.

## Common Mistakes

- **Trusting the fuzzy candidate list as exhaustive for team names.** Unlike
  player names, a genuine team match can score 0 on string similarity
  (sponsor-name churn). Always cross-check the full `team_id` spelling
  history in `euroleague_header.csv` before rejecting or no-matching.
- **Confirming a fuzzy candidate that's actually a different club.** String
  similarity alone can surface a real but wrong club (e.g. "Paris" vs
  "Aris"). Verify the `team_id` and spelling history genuinely agree.
- **Picking a stale historical spelling over the current one.** When one
  `team_id` has several spellings (rebrand/relocation/sponsor change), check
  `season_code` ranges and prefer the most current spelling unless there's a
  reason to do otherwise.
- **Forcing a match to avoid leaving a row unresolved.** `no_match` /
  `rejected` are correct, complete answers. A recent entrant with no
  EuroLeague box-score history should end as `no_match`, not a guessed
  candidate.
- **Hand-editing the crosswalk CSV or an old batch.** The crosswalk is
  regenerated output and batches are append-only; always go through
  `apply_team_name_verdicts.py` — it validates the verdict shape (required
  `notes`, `boxscore_team_name` only on `confirmed`) and keeps the result
  reproducible and testable.
- **Letting the subagent apply its own verdicts.** It returns the JSON; the
  invoking session runs `apply_team_name_verdicts.py`, so the batch stays
  reproducible and testable.
