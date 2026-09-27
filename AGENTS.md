# CLAUDE.md — EuroLeague Fantasy

## Project

Hobby pipeline: scrape/acquire data → KPIs/features → ML predictions (PIR, value) → squad optimization against objectives.

| Path                                            | Purpose                                                      |
| ----------------------------------------------- | ------------------------------------------------------------ |
| `contx/`                                        | Context: future ideas, KPIs, what others do, viz inspiration |
| `docs/resources.md`                             | External URLs                                                |
| `docs/next-steps.md`                            | Deferred work                                                |
| `specs/spec-<short-name>.md`                    | Execution specs                                              |
| `docs/data-catalogue.md` · `docs/data-graph.md` | Data inventory · lineage (agent-generated)                   |
| `data/stage_99/`                                | Consumption layer (symlinks)                                 |

Earlier implementations are archived as tags: `archive/v2-2026-09` (fetchers, Kaggle/master-table pipelines, curated workbooks, skills) and `archive/v1-2026-09` (older `src/elfantasy/` and `flow/`). Recover a file with `git show <tag>:<path>`.

## Commands (repo root)

- Run: `uv run python src/eupy/<module>.py` (e.g. `fetchers/fetch_euroleague_live_boxscores.py`)
- Test: `uv run pytest`
- Lint/format: `ruff check src tests` · `ruff format --check src tests`

## Communication

- Terse. Bullets over prose. No pleasantries.
- Challenge my views — sparring partner, not yes-man. Every release needs a clear purpose and value.
- Blocked, multiple viable paths, or my instructions could be better → present options + recommendation.
- Can't make something work → stop and tell me. Don't reinvent the wheel.
- 80/20: works-but-suboptimal and off the critical path → propose deferring it to `docs/next-steps.md`; logging it is my call, not a default action (quick, cheap tidy of the file when doing so).
- While subagents run: brief status — progress, things to be aware of, decisions I need to make.
- `update` → from git log + active specs, grouped summary of **done** vs **ongoing/remaining**. Abstract; not one line per commit/spec.

## Workflow

1. Plan → spec (`specs/spec-<short-name>.md`): goal, scope, pass criteria.
2. Spec → tickets.
3. Per ticket: implement → verify against pass criteria → merge → cleanup.

## Definition of done

- [ ] Pass criteria verified
- [ ] `ruff check` + `ruff format --check` clean
- [ ] `uv run pytest` green; new/changed logic has meaningful tests
- [ ] Reproducible: re-running from raw data gives the same outputs
- [ ] Script I/O headers current; catalogue + graph regenerated
- [ ] Docs/specs updated; deferrals proposed and, once confirmed, logged in `docs/next-steps.md`
- [ ] Branches/worktrees cleaned up

## Subagents & git

- Delegate only when the task calls for it (multi-file, parallelizable, or context-heavy). If the main agent can do it without meaningful context cost, do it inline.
- Also inline when: I ask or imply it, or I'm doing a quick thing rather than a spec. Unsure → ask.
- I talk to subagents only through the main agent. Every decision needing my input (incl. module structure) is resolved with me **before** delegation. A subagent hitting an unresolved decision stops and reports back; main agent relays to me.
- Subagents don't devise their own work — scope is the work-package/spec agreed between me and the main agent. Deviating (adding, dropping, reshaping tasks) needs a near-blocking reason; even then, the subagent stops and reports up rather than acting on it. A scope change ships only after I confirm; the main agent then updates the spec and its pass criteria, and the merge into main carries that updated spec file along with the code.
- Budget guard: if a subagent or task runs well beyond expectation (default: >30 tool calls, repeated failed attempts, or main context getting heavy), pause and notify me with status + options (continue / narrow scope / stop).
- Model: haiku for trivial, well-defined tasks; sonnet when implementation is uncertain.
- Every brief includes: goal, pass criteria, branch/worktree, files in scope, decisions already made.
- One branch + worktree per task, named `<type>/<short-name>`.
- Parallel work: hold back items touched by >1 process; tell me and propose a workaround.
- Shared docs — single writer (main agent), after merge:
  - Catalogue, graph: generated only (banner "GENERATED — do not edit"). On conflict, regenerate.
  - `next-steps.md`: subagents list deferrals in their final report; main agent proposes them to me and appends only once I confirm.
  - Specs: read-only for subagents; status changes by main agent.
  - Enforcement: briefs list shared docs as out of scope; main agent rejects any subagent diff touching them.
- Subagents never commit. Main agent verifies against Definition of done, then commits. Merging into main always needs my explicit go-ahead beforehand — not a report that it already happened — unless I've told you upfront to go straight through to merge.
- Merge strategy: `local` → merge into local `main`; `remote` → open GitHub PR.
- Conventional commits (`feat`/`fix`/`chore`/`refactor`/`test`/`docs`). Flag when splitting into more commits would give cleaner history.
- After merge: delete finished branches and worktrees.

## Code

- Source in `src/` only; tests in `tests/`.
- Formatted and documented: module summary at top, docstrings, comments where non-obvious.
- Reproducible by design: deterministic runs (fixed seeds), no hidden state or manual steps, outputs fully derivable from raw data + code.
- On every change, consider whether module structure should change (new/split/merge). Propose; main agent confirms with me before doing or delegating it.
- New/changed scripts need tests — adversarially review each test idea first; write it only if it tests something meaningful. No tests for coverage's sake.

## Data

- Raw: `data/raw_data/<source>/` — git-ignored.
- Every script declares its inputs, outputs and `final` flag in its header.
- Produced: `data/stage_XX/` (01, 02, …). Stage = max(input stages) + 1 — no dependencies within a stage.
- Stage is computed from script I/O headers, never hand-assigned. Refer to datasets by **name**; stage numbers are display-only and may shift.
- Code resolves data paths via the registry by dataset name — never hardcode `stage_XX` paths.
- Inserting a step between stages (temp measure): letter suffix, e.g. `stage_01a`, `stage_01b` (sorts correctly, no downstream moves). A later renumber pass (registry recompute + rerun) normalizes to integers.
- `data/stage_99/`: consumption layer. Datasets flagged `final: true` in their header are exposed here as **relative symlinks** to the real file. No copies.
- Orchestration will move to DVC: script headers are the source of truth for deps/outs (future `dvc.yaml` is generated from them). Never declare `data/stage_99/` symlinks as DVC outs — DVC deps point at real stage paths.
- Before every commit, the agent regenerates `docs/data-catalogue.md` and `docs/data-graph.md` from the headers — never hand-edited.

### Manual symlink into `data/stage_99/`

```bash
cd data/stage_99
ln -s ../stage_03/<dataset>.parquet <dataset>.parquet   # relative target, run from data/stage_99
ls -l <dataset>.parquet                                 # verify it points at the stage file
ln -sfn ../stage_04/<dataset>.parquet <dataset>.parquet # repoint after a renumber
rm <dataset>.parquet                                    # remove link only; target untouched
```

- Windows: needs Developer Mode, or `mklink <dataset>.parquet ..\stage_03\<dataset>.parquet` (admin cmd).
