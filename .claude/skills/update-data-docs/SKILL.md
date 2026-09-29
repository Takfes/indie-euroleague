---
name: update-data-docs
description: |
  Regenerate indie-euroleague's docs/data-catalogue.md and docs/data-graph.md
  from every script's Inputs/Outputs/Final module-docstring header under
  src/eupy/ via the registry (`python -m eupy.registry docs`). Use after
  adding, renaming, or removing a script, a raw data source, or a produced
  dataset -- or whenever the two docs look stale or out of sync with the
  actual headers. AGENTS.md's Definition of done requires this before every
  commit that touches a script's I/O header.
---

# Update Data Docs

## Overview

`docs/data-catalogue.md` and `docs/data-graph.md` are generated, deterministically, from the script
headers (plus `src/eupy/registry/raw_sources.toml` for raw data no script produces) by the registry
(`src/eupy/registry/`). Never edit them by hand and never derive names, stages or edges yourself: fix the
header (or `raw_sources.toml`) and regenerate.

## When to Use

- A script's `Inputs:` / `Outputs:` / `Final:` header changed (new script, renamed dataset, new consumer).
- Before any commit whose diff touches a script header (`AGENTS.md` Definition of done).
- Either doc looks stale or was hand-edited by mistake.

## Procedure

1. Lint the headers; fix every reported problem in the header itself, then re-run until it exits 0:

   ```bash
   uv run python -m eupy.registry check
   ```

2. Regenerate both docs (catalogue + graph, one pass):

   ```bash
   uv run python -m eupy.registry docs
   ```

   Individually: `... catalogue` or `... graph`; add `--stdout` to print instead of writing.
   A `GraphError` (id collision, stage beyond the color palette, coverage violation) is reported on stderr:
   fix the cause (rename, or extend `PALETTE` in `render_graph.py`), don't work around it.

3. Review the diff. It should contain exactly the consequences of the header change you made:

   ```bash
   git diff -- docs/data-catalogue.md docs/data-graph.md
   ```

   Unexpected churn means a bad header or a generator bug -- investigate, don't accept it.

4. Verify: `uv run pytest` (includes the determinism, graph/catalogue agreement and coverage tests).
   Optional spot check: `uv run python -m eupy.registry docs --stdout | md5sum` twice gives the same hash.

## Common Mistakes

- Hand-editing either doc, or regenerating only one of them -- use `docs` so they stay in step.
- Fixing a wrong doc row by editing the doc instead of the header / `raw_sources.toml`.
- Committing without step 3: a surprising diff is the only signal that a header was misread.
