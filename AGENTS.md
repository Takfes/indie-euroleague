# AGENTS.md — EuroLeague Fantasy

## Project

Hobby pipeline: scrape/acquire data → KPIs/features → ML predictions (PIR, value) → squad optimization against objectives.

| Path                                            | Purpose                                                      |
| ----------------------------------------------- | ------------------------------------------------------------ |
| `contx/`                                        | Context: future ideas, KPIs, what others do, viz inspiration |
| `docs/resources.md`                             | External URLs                                                |
| `docs/next-steps.md`                            | Deferred work                                                |
| `docs/run-guide.md`                             | How to run everything (Claude-maintained)                    |
| `specs/spec-<short-name>.md`                    | Execution specs                                              |
| `docs/data-catalogue.md` · `docs/data-graph.md` | Data inventory · lineage (agent-generated)                   |
| `data/stage_99/`                                | Consumption layer (symlinks)                                 |

Earlier implementations are archived as tags: `archive/v2-2026-09` (fetchers, Kaggle/master-table pipelines, curated workbooks, skills) and `archive/v1-2026-09` (older `src/elfantasy/` and `flow/`). Recover a file with `git show <tag>:<path>`.

## Commands (repo root)

- Run: `uv run python src/eupy/<module>.py` (e.g. `fetchers/fetch_euroleague_live_boxscores.py`)
- Test: `uv run pytest`
- Lint/format: `ruff check src tests` · `ruff format --check src tests`
- Cleanup: `make clean-all` = `clean-cache` (pycache/ruff/pytest/mypy caches, `.DS_Store`; skips `.venv`/`.git`/`.claude`) + `clean-git`. Git leftovers after PRs merge (worktrees, branches, stale locks): `make clean-git` (summary, then asks) · `make clean-git ARGS=--yes` (delete all safe items, report the rest) · `make clean-git ARGS=--dry-run` · Raw data: `make clean-data-raw` deletes every file under `data/raw_data/` except `.gitkeep` (`ARGS=--dry-run` lists only; not part of `clean-all`)

## Communication

- Terse. Bullets over prose. No pleasantries.
- Challenge my views — sparring partner, not yes-man. Every release needs a clear purpose and value.
- Blocked, multiple viable paths, or my instructions could be better → present options + recommendation.
- Can't make something work → stop and tell me. Don't reinvent the wheel.
- 80/20: works-but-suboptimal and off the critical path → propose deferring it to `docs/next-steps.md`; logging it is my call, not a default action (quick, cheap tidy of the file when doing so).
- While subagents run: brief status — progress, things to be aware of, decisions I need to make.
- `update` → from git log + active specs, grouped summary of **done** vs **ongoing/remaining**. Abstract; not one line per commit/spec.

## Run guide

- `docs/run-guide.md` is the Claude-maintained reference for running the project (fetchers, DVC stages, config, registry, data layout, dev loop). It is not user-managed.
- When I ask how to run, set up or configure something, answer from it, checking the claim against the code first.
- In the same commit as any change to a command, DVC stage, script path, config key or data path, update the affected section. If it is stale or missing something, fix it and say so.

## Workflow

1. Plan → spec (`specs/spec-<short-name>.md`): goal, scope, pass criteria.
2. Spec → tickets.
3. Per ticket: implement → verify against pass criteria → PR → merge → `make clean-git`.

## Definition of done

- [ ] Pass criteria verified
- [ ] `ruff check` + `ruff format --check` clean
- [ ] `uv run pytest` green; new/changed logic has meaningful tests
- [ ] Reproducible: re-running from raw data gives the same outputs
- [ ] Script I/O headers current; catalogue + graph regenerated
- [ ] Docs/specs updated; deferrals proposed and, once confirmed, logged in `docs/next-steps.md`
- [ ] `docs/run-guide.md` updated for any changed command, stage, path or config key
- [ ] Once the PR is merged: `make clean-git` reports nothing left for this task (until then, the branch/worktree is "pending cleanup")

## Rules (`.claude/rules/`)

Detailed rules live in dedicated files, loaded with this one (all `alwaysApply`):

- `.claude/rules/git-workflow.md` — branches, worktrees, commits, delivery mode, merging, cleanup, background jobs.
- `.claude/rules/data-and-headers.md` — data layout, stages, `stage_99` symlinks, script I/O header grammar.
- `.claude/rules/subagents.md` — delegation, authority and scope, shared-doc single writer.
- `.claude/rules/karpathy-behavioral-guidelines.md` — think before coding, simplicity, surgical changes, goal-driven execution.

## Code

- Source in `src/` only; tests in `tests/`.
- Formatted and documented: module summary at top, docstrings, comments where non-obvious.
- Reproducible by design: deterministic runs (fixed seeds), no hidden state or manual steps, outputs fully derivable from raw data + code.
- On every change, consider whether module structure should change (new/split/merge). Propose; main agent confirms with me before doing or delegating it.
- New/changed scripts need tests — adversarially review each test idea first; write it only if it tests something meaningful. No tests for coverage's sake.

## Skills

- Skills created under, or for the purpose of, this project live in this repo's `.claude/skills/` — never the user/global `~/.claude/skills/` — unless I explicitly say otherwise.
- Scripts a skill drives live in `src/`, in whatever module they belong to (per Code's module-structure rule) — not inlined in the skill file, not scattered elsewhere.
