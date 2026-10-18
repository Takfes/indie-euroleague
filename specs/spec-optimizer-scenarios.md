# Spec: optimizer scenarios

Status: delivered.

## Goal

Run the squad optimizer for several starting teams, each with its own config overrides, in one command, and keep a
separate output per scenario. A scenario is data in one tracked file, so an agent (the `optimize-scenarios` skill)
can maintain it from natural language.

## Scope

- `config/scenarios.toml`: one table per scenario. `roster = [player names]` (`[]` = a fresh squad, key left out =
  the workbook's own `in_prev_roster`), plus any `[optimization]` key as an override. Keys use letters, digits and
  underscores because they name the output file. Scenarios stay in the file until someone removes them.
- `src/eupy/optimize/scenarios.py` (pure library module): `load_scenarios`, `select_scenarios`, `apply_scenario`.
  `apply_scenario` returns a copy of the input table with `in_prev_roster` set and the run parameters with the
  overrides applied (defaults < `config.toml` < scenario). No temporary Excel file: the optimizer already works on a
  DataFrame, and each output's `selection` sheet shows the roster that was used.
- `optimize_squad.main(--scenarios [KEYS])`: validates every selected scenario first (names, override keys, the
  input rules, turn minimums), lists all problems together and solves nothing if any exists; then solves each into
  `data/stage_99/optimizer_output_<key>.xlsx`. A solver failure in one scenario does not stop the others and ends in
  a non-zero exit. `scripts/optimize.py` carries the same code (drift test).
- Make: `make optimize` is unchanged (plain run, `optimizer_output.xlsx`); `make optimize-scenario` runs all
  scenarios, `make optimize-scenario SCENARIO=a,b` only those.
- Summary sheet: always lists every `Optimization` parameter (default or not), plus `scenario`, `roster source` and
  `parameters overridden`.
- Skill `.claude/skills/optimize-scenarios/`: edits only `config/scenarios.toml`, runs `make optimize-scenario`,
  reports. It never edits `config.toml` and never rebuilds the eubook.

## Pass criteria

- The plain run is unchanged apart from the extra summary rows.
- One output per scenario key; the roster and every override are applied; the inputs (workbook table, base config)
  are not mutated.
- A typo in a roster name or an override key fails before any solve and names the scenario and close matches.
- Tests: file parsing and its rejections, selection, apply (roster, `[]`, no roster, overrides, no mutation),
  end-to-end outputs and summary, the selector, fail-fast, and the "every parameter listed" check.

## Not in scope

A comparison workbook across scenarios; scenario-specific input workbooks; DVC ownership of the outputs
(`stage_99` is never a DVC out).
