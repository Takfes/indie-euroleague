---
description: Data layout (raw, staged, curated, stage_99), the script I/O header grammar, stage computation and DVC. Use when touching data/, src/eupy scripts, headers, dvc.yaml or the generated docs.
alwaysApply: true
---

# Data and script headers

- Raw: `data/raw_data/<source>/`.
- Git versions only the `data/` skeleton (`.gitkeep`) plus an explicit allow-list in `.gitignore` (today: `data/curated/**`, the verdict batches — judgements not derivable from raw data). Everything else under `data/` is git-ignored; derived outputs are fingerprinted by the committed `dvc.lock` and rebuilt with `eupy run`.
- Every script declares its inputs, outputs and `final` flag in its header.
- Produced: `data/stage_XX/` (01, 02, …). Stage = max(input stages) + 1 — no dependencies within a stage.
- Stage is computed from script I/O headers, never hand-assigned. Refer to datasets by **name**; stage numbers are display-only and may shift.
- Code resolves data paths via the registry by dataset name — never hardcode `stage_XX` paths.
- Inserting a step between stages (temp measure): letter suffix, e.g. `stage_01a`, `stage_01b` (sorts correctly, no downstream moves). A later renumber pass (registry recompute + rerun) normalizes to integers.
- `data/stage_99/`: consumption layer. Datasets flagged `final: true` in their header are exposed here as **relative symlinks** to the real file. No copies.
- Orchestration will move to DVC: script headers are the source of truth for deps/outs (future `dvc.yaml` is generated from them). Never declare `data/stage_99/` symlinks as DVC outs — DVC deps point at real stage paths.
- Before every commit, the agent regenerates `docs/data-catalogue.md` and `docs/data-graph.md` from the headers via the `update-data-docs` skill — never hand-edited.
- Header grammar: a strict key-value block at the **end** of each pipeline script's module docstring (line-based, hand-parseable, no YAML). Keys in this order; `Inputs`/`Sources`/`Outputs` may be `none`:

  ```
  Inputs:
    - <dataset-name>: <repo-relative path>
  Sources:
    - <free text: live URL, verdicts JSON, ...>
  Outputs:
    - <dataset-name>: <repo-relative path>
  Final: true|false
  Impure: true|false
  Refresh: <free text>     # optional
  Notes: <free text>       # optional; may continue on 2-space-indented lines
  ```

  - Dataset name: raw = `<source-dir>/<logical-name>`, produced = file stem. A directory of per-run files ends in `/`; no templated file names in paths.
  - `Impure: true` = hits a live source / can't be re-derived from disk (all fetchers). External origins and CLI-given files (verdicts JSON) go under `Sources`, not `Inputs`. A verdict applier that reads and rewrites an existing file lists it in both `Inputs` and `Outputs`; resolvers that merge into their own previous output list only their real inputs (the merge goes in `Notes`). `Refresh` and `Notes` are optional; `Notes` may continue on 2-space-indented lines. Paths are directories (ending `/`) where files are templated — the file-name pattern goes in `Notes`.
  - No `Pipeline:` key. Prose above the block stays for humans; `Final:` is bare `true`/`false`.

### Manual symlink into `data/stage_99/`

```bash
cd data/stage_99
ln -s ../stage_03/<dataset>.parquet <dataset>.parquet   # relative target, run from data/stage_99
ls -l <dataset>.parquet                                 # verify it points at the stage file
ln -sfn ../stage_04/<dataset>.parquet <dataset>.parquet # repoint after a renumber
rm <dataset>.parquet                                    # remove link only; target untouched
```

- Windows: needs Developer Mode, or `mklink <dataset>.parquet ..\stage_03\<dataset>.parquet` (admin cmd).
