---
name: resolve-player-names
description: |
  Use when a new basketballsphere fantasy-price snapshot has landed and the
  indie-euroleague player-name crosswalk needs refreshing, or when re-verifying
  unresolved player-name matches (needs_review / no_candidate rows) in
  data/stage_01/player_name_crosswalk.csv. Covers mapping
  basketballsphere_prices.csv names to euroleague_box_score.csv display names
  and recording confirm/reject/no_match verdicts as append-only batches.
---

# Resolve Player Names

## Overview

Rebuilds `data/stage_01/player_name_crosswalk.csv`, a left-join
**name map** from current-season EuroLeague Fantasy prices
(`data/raw_data/fantasy_prices/basketballsphere_prices.csv`) to historical
box-score display names (`data/raw_data/kaggle_data/euroleague_box_score.csv`,
seasons E2007-E2025), so downstream feature work can attach box-score stats
to fantasy players. The map is purely `name <-> boxscore_name` plus the
resolution process's own metadata -- no `club`/`position`/`price`/`player_id`
from either source file. Consumers needing those read them from their own
source files directly.

The resolver (a DVC step in the `entity` pipeline) does the mechanical work
(exact-match, then fuzzy-candidate generation via rapidfuzz); a dispatched Sonnet subagent does the judgment
calls the fuzzy stage can't make on its own — that's the point of this skill.

## When to Use

- A new `basketballsphere_prices.csv` snapshot has landed (new round, new
  season, transfers, rookies) and the crosswalk needs refreshing.
- Ad-hoc: someone wants the crosswalk's `needs_review` / `no_candidate` rows
  re-verified.

## Procedure

### 1. Rebuild the crosswalk with the current verdict batches

```bash
uv run eupy run entity
```

Runs `resolve_player_names.py` (plus the other entity steps if their inputs changed;
unchanged steps are skipped). The crosswalk is a pure function of the raw
data + the append-only batches in `data/curated/player_name_verdicts/`:
everything a batch covers comes out `confirmed` / `rejected` / `no_match`,
the rest is `exact`, `needs_review` or `no_candidate`. A changed price
snapshot legitimately reruns this step. Verdicts whose key is no longer in
the snapshot are skipped and counted as stale in the run summary — not an
error. Check the `match_status` summary before moving on.

### 2. Dispatch a subagent to review every `needs_review` and `no_candidate` row

```python
import csv
rows = list(csv.DictReader(open("data/stage_01/player_name_crosswalk.csv", encoding="utf-8")))
review = [r for r in rows if r["match_status"] in ("needs_review", "no_candidate")]
```

If `review` is empty, you are done. Otherwise, use the Agent tool to
dispatch one subagent (a general implementation/analysis agent, `model:
sonnet`) with a self-contained brief covering:

- **The open rows**: each row's `name` plus, for `needs_review` rows, the
  candidates already listed in `notes` (`NAME (score)`) and the `matched_by`
  reason (`fuzzy` / `exact_ambiguous_spelling` / `exact_collision`).
  `no_candidate` rows have no candidates — the fuzzy stage found nothing
  above its score floor.
- **Raw-data access**: the subagent must cross-check
  `data/raw_data/kaggle_data/euroleague_box_score.csv` directly for every
  row, not just trust the candidate list — the candidate list isn't
  exhaustive below the score floor, and reading the real data beats
  guessing. Give it this snippet (do **not** use `grep | cut -d,` — the
  `player` field is itself `"LAST, First"` with an internal comma, so naive
  `cut` misaligns columns):

  ```python
  import csv
  names = {r["player"] for r in csv.DictReader(open("data/raw_data/kaggle_data/euroleague_box_score.csv", encoding="utf-8")) if r["dorsal"] != "TOTAL"}
  print(sorted(n for n in names if "SURNAME" in n.upper()))
  ```

- **Judgment-call categories** it will need to apply, one per row:
  - **Nicknames / short forms** ("Nikos" for "Nikolaos") — confirm if the
    rest of the name lines up.
  - **Suffixes** (`Jr.`, `IV`) — fantasy sites often drop these; box score
    often keeps them. Confirm if everything else matches.
  - **Transliteration variants** (e.g. Hebrew/Slavic names spelled two ways,
    "Lavy" / "Lavi") — confirm if it's clearly the same surname rendered
    differently.
  - **First-name-only or surname-only false positives** — the fuzzy stage's
    most common false positive (e.g. "Marcus Bingham" matching "Marcus
    Foster" at 85+ purely on the shared first name). Reject unless the
    *other* name part also lines up.
  - **`exact_ambiguous_spelling`** — one normalized name, more than one
    distinct raw box-score spelling (e.g. "MARÍ" / "MARI"). Usually the same
    real player recorded two ways upstream; pick the spelling that looks
    canonical (or the more recent one if seasons disambiguate).
  - **`exact_collision`** — one raw box-score spelling shared by more than
    one distinct `player_id`. Can be a genuine two-different-real-players
    collision, not just a formatting quirk. If it can't tell them apart from
    the data available, `reject` rather than guess.
  - Most `no_candidate` rows are genuine: a player with zero EuroLeague
    box-score history (rookie, or an NBA/G-League/NCAA newcomer to Europe).
    `no_match` is a correct, complete answer, not a failure to close the row.
- **The exact verdicts-JSON shape** `apply_player_name_verdicts.py` expects
  (see step 3) and the instruction that its job is to **return that JSON**
  as its final report — it does not apply the verdicts itself.

### 3. Apply the subagent's verdicts

The subagent's final report is a JSON list of verdict records, one per row
it resolved:

```json
[
  {
    "name": "Wade Baldwin",
    "match_status": "confirmed",
    "boxscore_name": "WADE BALDWIN IV", "match_score": 95.0,
    "notes": "Fantasy site drops the 'IV' suffix; only one Wade Baldwin in box score."
  },
  {
    "name": "Marcus Bingham",
    "match_status": "rejected",
    "notes": "Candidate only shares first name 'Marcus'; no 'Bingham' surname in box score."
  }
]
```

Rules: `match_status` must be `confirmed` / `rejected` / `no_match`.
`confirmed` requires `boxscore_name` (plus ideally `match_score`); `rejected`
/ `no_match` must NOT carry a `boxscore_name`. `notes` is required on every
verdict — a one-sentence rationale. Convention: `needs_review` rows resolve
to `confirmed` or `rejected`; `no_candidate` rows resolve to `no_match`.

Save the subagent's JSON to a file (e.g. under `/tmp`), then ingest it:

```bash
uv run python src/eupy/entity/apply_player_name_verdicts.py --verdicts /path/to/verdicts.json [--label SLUG]
```

This validates the records and appends a new batch file
`data/curated/player_name_verdicts/NNNN_<slug>.json`. It never touches the
crosswalk or earlier batches.

### 4. Rebuild, verify, commit

```bash
uv run eupy run entity
```

Confirm the summary shows zero `needs_review` / `no_candidate`, and that a
second run does nothing (all steps up to date). Review `git diff` (the new
batch file + the changed `data/stage_01/player_name_crosswalk.csv`), then commit
both together.

## Quick Reference

| Step | Command |
|---|---|
| Rebuild crosswalk | `uv run eupy run entity` |
| Record verdicts (new batch) | `uv run python src/eupy/entity/apply_player_name_verdicts.py --verdicts PATH [--label SLUG]` |
| Batches | `data/curated/player_name_verdicts/` (append-only, tracked in git) |
| Crosswalk location | `data/stage_01/player_name_crosswalk.csv` (symlinked into `data/stage_99/`) |

`match_status` values: `exact` / `needs_review` / `confirmed` / `rejected` /
`no_candidate` / `no_match`.

## Common Mistakes

- **Confirming a candidate on first-name-only or surname-only overlap.** The
  fuzzy stage's WRatio scorer often surfaces "Kevin X" for "Kevin Y" at 80+.
  Always check the *other* name part matches too.
- **Confirming an `exact_collision` row without checking whether it's really
  two different people.** A shared box-score spelling can be a genuine
  namesake collision, not just a data-entry variant.
- **Forcing a match to avoid leaving a row unresolved.** `no_match` /
  `rejected` are correct, complete answers. A rookie with no EuroLeague
  history should end as `no_match`, not a guessed candidate.
- **Hand-editing the crosswalk CSV or an old batch.** The crosswalk is
  regenerated output and batches are append-only; always go through
  `apply_player_name_verdicts.py` — it validates the verdict shape (required
  `notes`, `boxscore_name` only on `confirmed`) and keeps the result
  reproducible and testable.
- **Skipping the surname check before rejecting/no-matching.** The fuzzy
  candidate list is not exhaustive below its score floor; a real match can
  still be sitting in the raw data under a name the scorer didn't rank
  highly enough.
- **`cut -d,` on the box score CSV.** The `player` column contains a comma
  (`"LAST, First"`) and is quoted — naive `cut` misaligns columns. Use
  `csv.DictReader` instead.
- **Letting the subagent apply its own verdicts.** It returns the JSON; the
  invoking session runs `apply_player_name_verdicts.py`, so the batch stays
  reproducible and testable.
