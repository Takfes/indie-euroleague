# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Code lives only in `src/` (data-acquisition scripts `src/fetch_*.py`, plus the master-table builders `src/build_*_master_table.py` and their shared helpers).
- Data: raw source datasets live in `data/<source>/`; produced master tables live in `data/curated/`.
- Player pipeline: raw csvs (incl. the git-ignored `data/kaggle-euroleague-data/`, see the `player-game-stats` skill) -> `src/build_game_player_stats.py` -> `src/build_player_kpis.py` -> `src/build_player_master_table.py`; outputs in `data/curated/`.
- Team pipeline: raw csvs -> `src/build_team_kpis.py` -> `src/build_team_master_table.py`; outputs in `data/curated/`.
- Player identity: `src/build_player_master_table.py` joins its five sources by a stable `player_key` (`src/player_identity.py`), not by the `name_key` normalisation helper (`src/player_name_matching.py`) directly. `player_key` is minted once and looked up from the checked-in `data/curated/player_alias_table.xlsx` (Alias Table + Identity View sheets) on every later build, never re-derived. Small manually-curated overrides (team-name/player-identity crosswalks the automatic tiers can't resolve) follow one convention across the codebase: a plain dict, keyed by the ambiguous raw value, with a `ValueError` on the automatic tiers' failure naming the unmapped value and how to add an entry (see `TEAM_NAME_CROSSWALK`/`KAGGLE_TEAM_CROSSWALK` in `src/build_team_master_table.py`/`src/build_player_master_table.py`, and `TEAM_NAME_OVERRIDES`/`KAGGLE_PLAYER_IDENTITY_OVERRIDES` in `src/build_player_master_table.py`).
- Skills (`.claude/skills/<name>/SKILL.md`) are documentation only: no code, no script copies; they reference scripts by `src/` path.
- A skill's run command is `uv run python src/<script>.py` (repo root as cwd).
- `tests/test_skill_script_paths.py` enforces both rules: every `src/*.py` path named in a SKILL.md exists, and no `.py` files sit under `.claude/skills/`.
- Lint/format: `ruff check src tests` and `ruff format --check src tests`; tests: `uv run pytest`.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
