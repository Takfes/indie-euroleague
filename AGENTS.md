# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Code lives only in `src/` (data-acquisition scripts `src/fetch_*.py`, plus the master-table builders `src/build_*_master_table.py` and their shared helpers).
- Data: raw source datasets live in `data/<source>/`; produced master tables live in `data/curated/`.
- Skills (`.claude/skills/<name>/SKILL.md`) are documentation only: no code, no script copies; they reference scripts by `src/` path.
- A skill's run command is `uv run python src/<script>.py` (repo root as cwd).
- `tests/test_skill_script_paths.py` enforces both rules: every `src/*.py` path named in a SKILL.md exists, and no `.py` files sit under `.claude/skills/`.
- Lint/format: `ruff check src tests` and `ruff format --check src tests`; tests: `uv run pytest`.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
