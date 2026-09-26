# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Code lives only in `src/` (currently the fantasy price fetcher `src/fetch_basketballsphere_prices.py`).
- Data: raw downloads live in `data_source/<source>/` (`fantasy_prices/`, `kaggle_data/`, ...) and are git-ignored; produced tables live in `data_curated/`, the only tracked data folder. Only the basketballsphere prices have a fetcher in `src/`; the basketnews and dunkest csvs that used to be tracked are recoverable from the `archive/v2-2026-09` tag.
- Earlier implementations are archived as tags: `archive/v2-2026-09` holds the fetchers, Kaggle/master-table pipelines, curated workbooks and skills; `archive/v1-2026-09` the older `src/elfantasy/` and `flow/` code. Recover a file with `git show <tag>:<path>`.
- Skills (`.claude/skills/<name>/SKILL.md`) are documentation only: no code, no script copies; they reference scripts by `src/` path.
- A skill's run command is `uv run python src/<script>.py` (repo root as cwd).
- `tests/test_skill_script_paths.py` enforces both rules: every `src/*.py` path named in a SKILL.md exists, and no `.py` files sit under `.claude/skills/`.
- Lint/format: `ruff check src tests` and `ruff format --check src tests`; tests: `uv run pytest`.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
