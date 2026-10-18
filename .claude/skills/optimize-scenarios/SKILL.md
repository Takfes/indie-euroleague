---
name: optimize-scenarios
description: |
  Maintain config/scenarios.toml and run the squad optimizer for several starting teams in indie-euroleague. Use when
  the user gives a roster (typed, a file or a screenshot) and config tweaks per team ("team 2 has 3.5 cash and
  max 2 trades") and wants the optimizer run for them, or asks to add, change, remove or rerun scenarios.
---

# Optimize scenarios

The logic lives in `src/eupy/optimize/scenarios.py` and `optimize_squad.py`; this skill only edits the scenarios
file and calls the make target. Format and rules: `docs/optimizer.md` ("Scenarios") and `config/scenarios.toml`.

## Limits

- Edit **only** `config/scenarios.toml`. Never `config/config.toml`, never the pipeline, never the eubook.
- Keep scenarios the user did not mention. Remove one only when asked.
- Keys: letters, digits and underscores (they name `data/stage_99/optimizer_output_<key>.xlsx`).

## Steps

1. Check `data/stage_03/optimizer_input.xlsx` exists. If not, say it comes from `build_eubook` and stop.
2. Get the roster: a typed list, a file, or a screenshot (read the names off the image). Players must be spelled as in
   the input's `player` column. Check each name against it, e.g.:
   `uv run python -c "import pandas as pd; print(pd.read_excel('data/stage_03/optimizer_input.xlsx')['player'].tolist())"`
   Fix near-misses with the user's intent (accents, order, `Jr`), and ask when two players could match.
3. Write the scenario table: `roster = [...]` (10 names, 4 G / 4 F / 2 C; `[]` for a fresh squad) plus the overrides
   the user named. Overrides are `[optimization]` keys: `target`, `robustness_lambda`, `upside_beta`,
   `downside_gamma`, `credit_value`, `cash`, `max_trades`, `unlimited_trades`, `min_players_per_turn`, `must_have`, `avoid`,
   `solver`, `solver_options`. `must_have = [...]` forces players into the squad, `avoid = [...]` keeps them out (an
   owned one is sold); spell names as in the input, same check as the roster. Do not add keys the user did not ask for.
4. Run: `make optimize-scenario` (all) or `make optimize-scenario SCENARIO=team_1,team_2` (only those). Validation
   runs first and lists every problem across scenarios; fix the file and rerun.
5. Report per scenario: objective, expected PIR, mean PIR and PIR per credit of the line-up, trades used, cash after, captain, who is bought and sold against the roster, and
   any parameter the scenario overrode (the `scenario` rows and `parameters` of the output's `summary` sheet). Say
   where the files are: `data/stage_99/optimizer_output_<key>.xlsx`.
