# AGENTS.md — EuroLeague Fantasy

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
- Tidy leftovers after PRs merge (worktrees, branches, stale locks): `make tidy` (summary, then asks) · `make tidy ARGS=--yes` (delete all safe items, report the rest) · `make tidy ARGS=--dry-run`

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
3. Per ticket: implement → verify against pass criteria → PR → merge → `make tidy`.

## Definition of done

- [ ] Pass criteria verified
- [ ] `ruff check` + `ruff format --check` clean
- [ ] `uv run pytest` green; new/changed logic has meaningful tests
- [ ] Reproducible: re-running from raw data gives the same outputs
- [ ] Script I/O headers current; catalogue + graph regenerated
- [ ] Docs/specs updated; deferrals proposed and, once confirmed, logged in `docs/next-steps.md`
- [ ] New functionality noted (briefly) in the relevant `docs/quickstart.md` section
- [ ] Once the PR is merged: `make tidy` reports nothing left for this task (until then, the branch/worktree is "pending cleanup")

## Subagents

**Delegation**
- Delegate only when the task calls for it (multi-file, parallelizable, or context-heavy). If the main agent can do it without meaningful context cost, do it inline.
- Also inline when: I ask or imply it, or I'm doing a quick thing rather than a spec. Unsure → ask.
- Model: haiku for trivial, well-defined tasks; sonnet when implementation is uncertain.
- Every brief includes: goal, pass criteria, branch/worktree, files in scope, decisions already made.

**Authority & scope**
- I talk to subagents only through the main agent. Every decision needing my input (incl. module structure) is resolved with me **before** delegation. A subagent hitting an unresolved decision stops and reports back; main agent relays to me.
- Subagents don't devise their own work — scope is the work-package/spec agreed between me and the main agent. Deviating (adding, dropping, reshaping tasks) needs a near-blocking reason; even then, the subagent stops and reports up rather than acting on it. A scope change ships only after I confirm; the main agent then updates the spec and its pass criteria, and the merge into main carries that updated spec file along with the code.
- Budget guard: if a subagent or task runs well beyond expectation (default: >30 tool calls, repeated failed attempts, or main context getting heavy), pause and notify me with status + options (continue / narrow scope / stop).
- Subagents never commit. Main agent verifies against Definition of done, then commits.
- Parallel work: hold back items touched by >1 process; tell me and propose a workaround.

**Shared docs — single writer (main agent), after merge**
- Catalogue, graph: generated only (banner "GENERATED — do not edit"). On conflict, regenerate.
- `next-steps.md`: subagents list deferrals in their final report; main agent proposes them to me and appends only once I confirm.
- Specs: read-only for subagents; status changes by main agent.
- Enforcement: briefs list shared docs as out of scope; main agent rejects any subagent diff touching them.

## Git

- One branch + worktree per task. Naming is strict and identical everywhere:
  - Branch: `<type>/<short-name>` — `type` ∈ `feat`/`fix`/`chore`/`refactor`/`test`/`docs`; `short-name` lowercase kebab-case (e.g. `feat/optimizer-config`).
  - Worktree dir: `.claude/worktrees/<type>+<short-name>` (the `/` becomes `+`).
  - Always call `EnterWorktree` with an explicit `name` of `<type>/<short-name>`, then **immediately** rename the auto-generated branch: `git branch -m worktree-<type>+<short-name> <type>/<short-name>`. No `worktree-*` branch ever gets committed to or pushed.
  - `make tidy` flags any branch or worktree dir that breaks this.
- Conventional commits (`feat`/`fix`/`chore`/`refactor`/`test`/`docs`). Flag when splitting into more commits would give cleaner history.
- Merging into main always needs my explicit go-ahead beforehand — not a report that it already happened — unless I've told you upfront to go straight through to merge.
- Delivery mode — **default: remote** (overrides any global local-merge default), for interactive and background sessions alike.
  - `remote` (default): push the task branch, open a GitHub PR directly against `main`. Never stage a local merge unless I ask for `local` for that task.
  - `local` (only when I say so, per task): main agent merges into local `main` directly, only from an interactive session in the primary checkout, via `make merge-worktree` or the `merge-worktree` skill.
- Before merging, or opening/updating a PR: check the target checkout's `git status`. Foreign uncommitted work (not part of this task) → stop and ask; never stash, discard, or merge over it.
- A branch that's drifted from main (e.g. a rename/refactor landed on main after the branch forked) merges main into itself first and resolves conflicts there — including redoing any mechanical refactor main introduced — before merging into main or opening/updating the PR.
- After merge: run `make tidy` — it deletes the task branch (local + remote) and its worktree, plus stale locks, only when a merged PR matches the branch tip and it is clean, holds no irreplaceable git-ignored files (e.g. `data/raw_data/`) and is not in use (needs authenticated `gh`). Safe items come with manual commands; anything blocked is reported with a reason (dirty/ignored-data worktrees get a `--force` hint). Never force-delete around it without my go-ahead.
  - `remote`: the merge happens on GitHub, so cleanup is a separate later step; `local`: `merge-worktree` already cleans up its own branch/worktree, `make tidy` catches the rest.
  - Start of an interactive session: run `make tidy ARGS=--dry-run` and surface leftovers (background jobs can't clean up after themselves).

## Background jobs

- Isolate in a worktree. Sandbox reaches the worktree and the remote (commit and push both work; opening a PR too, under `remote`), but never the shared main checkout — not before isolating, not after, not even for the merge step itself.
- Finish by pushing the branch, then: under `remote` (default), open the PR and report its URL; under `local`, report the branch as ready for `merge-worktree`.
- Final report also lists the branch + worktree as **pending cleanup** (run `make tidy` once the PR is merged, from an interactive session).
- Only an interactive session can run `merge-worktree` (a background job never can) — via `make merge-worktree` from a terminal, or the `merge-worktree` skill in an interactive Claude Code session; both prompt for a branch when more than one task worktree exists, stage the merge for review, and — once approved — commit and clean up the branch/worktree automatically.
- No `git fetch` needed first: worktrees of the same repo share one object store, so a branch committed in any worktree is already visible from the primary checkout.

## Code

- Source in `src/` only; tests in `tests/`.
- Formatted and documented: module summary at top, docstrings, comments where non-obvious.
- Reproducible by design: deterministic runs (fixed seeds), no hidden state or manual steps, outputs fully derivable from raw data + code.
- On every change, consider whether module structure should change (new/split/merge). Propose; main agent confirms with me before doing or delegating it.
- New/changed scripts need tests — adversarially review each test idea first; write it only if it tests something meaningful. No tests for coverage's sake.

## Skills

- Skills created under, or for the purpose of, this project live in this repo's `.claude/skills/` — never the user/global `~/.claude/skills/` — unless I explicitly say otherwise.
- Scripts a skill drives live in `src/`, in whatever module they belong to (per Code's module-structure rule) — not inlined in the skill file, not scattered elsewhere.

## Data

- Raw: `data/raw_data/<source>/` — git-ignored.
- Every script declares its inputs, outputs and `final` flag in its header.
- Produced: `data/stage_XX/` (01, 02, …). Stage = max(input stages) + 1 — no dependencies within a stage.
- Stage is computed from script I/O headers, never hand-assigned. Refer to datasets by **name**; stage numbers are display-only and may shift.
- Code resolves data paths via the registry by dataset name — never hardcode `stage_XX` paths.
- Inserting a step between stages (temp measure): letter suffix, e.g. `stage_01a`, `stage_01b` (sorts correctly, no downstream moves). A later renumber pass (registry recompute + rerun) normalizes to integers.
- `data/stage_99/`: consumption layer. Datasets flagged `final: true` in their header are exposed here as **relative symlinks** to the real file. No copies.
- Orchestration will move to DVC: script headers are the source of truth for deps/outs (future `dvc.yaml` is generated from them). Never declare `data/stage_99/` symlinks as DVC outs — DVC deps point at real stage paths.
- Before every commit, the agent regenerates `docs/data-catalogue.md` and `docs/data-graph.md` from the headers via the `update-data-docs` skill — never hand-edited.

### Manual symlink into `data/stage_99/`

```bash
cd data/stage_99
ln -s ../stage_03/<dataset>.parquet <dataset>.parquet   # relative target, run from data/stage_99
ls -l <dataset>.parquet                                 # verify it points at the stage file
ln -sfn ../stage_04/<dataset>.parquet <dataset>.parquet # repoint after a renumber
rm <dataset>.parquet                                    # remove link only; target untouched
```

- Windows: needs Developer Mode, or `mklink <dataset>.parquet ..\stage_03\<dataset>.parquet` (admin cmd).
