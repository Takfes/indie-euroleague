# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Code lives only in `src/` (data-acquisition scripts `src/fetch_*.py`, plus the master-table builders `src/build_*_master_table.py` and their shared helpers).
- Data: raw source datasets live in `data/<source>/`; produced master tables live in `data/curated/`. The fantasy price list (`data/euroleague-fantasy/basketballsphere_prices.csv`) is the ground truth for this season's teams, rosters and player-to-team assignment; Basketnews, Dunkest and Kaggle are past-season stat sources and correctly lack a club new to the league (`load_canonical_team_names` in `src/build_player_master_table.py` derives the 20 teams from the price list).
- Player pipeline: raw csvs (incl. the git-ignored `data/kaggle-euroleague-data/`, see the `player-game-stats` skill) -> `src/build_game_player_stats.py` -> `src/build_player_kpis.py` -> `src/build_player_master_table.py`; outputs in `data/curated/`.
- Team pipeline: raw csvs + `player_game_stats.xlsx`/`player_master_table.xlsx` (for the real-PIR `funnel_actual_pir_*` KPIs) -> `src/build_team_kpis.py` -> `src/build_team_master_table.py`; outputs in `data/curated/`. The player master in turn reads `team_kpis.xlsx` for the 20 canonical team names, so rebuild order is game stats -> player master -> team KPIs -> team master (the committed workbooks break the cycle).
- Player identity: `src/build_player_master_table.py` first resolves which source rows are the same player (`name_key` matching, `src/player_name_matching.py`), and only after that completes assigns each final group one stable `player_key` (`src/player_identity.py`) - never mid-matching, so a player can't be split by source order. Keys are reused from the previous `data/curated/player_alias_table.xlsx` by (source, source_id) link, else slugged from the name; that table (Alias Table + Identity View sheets) is a build output, and matching never reads it. Small manually-curated overrides (team-name/player-identity crosswalks the automatic tiers can't resolve) follow one convention across the codebase: a plain dict, keyed by the ambiguous raw value, with a `ValueError` on the automatic tiers' failure naming the unmapped value and how to add an entry (see `TEAM_NAME_CROSSWALK`/`KAGGLE_TEAM_CROSSWALK` in `src/build_team_master_table.py`/`src/build_player_master_table.py`, and `TEAM_NAME_OVERRIDES`/`KAGGLE_PLAYER_IDENTITY_OVERRIDES` in `src/build_player_master_table.py`).
- Master sheet columns: the order, the same-stat duplicates dropped across sources (`DUPLICATE_COLUMNS`) and the last-step `EXCLUDED_COLUMNS` all live in `src/player_master_layout.py`; a source column the layout does not place makes the build raise, so place new ones there. The Column Guide sheet follows that order.
- Skills (`.claude/skills/<name>/SKILL.md`) are documentation only: no code, no script copies; they reference scripts by `src/` path.
- A skill's run command is `uv run python src/<script>.py` (repo root as cwd).
- `tests/test_skill_script_paths.py` enforces both rules: every `src/*.py` path named in a SKILL.md exists, and no `.py` files sit under `.claude/skills/`.
- Lint/format: `ruff check src tests` and `ruff format --check src tests`; tests: `uv run pytest`.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
