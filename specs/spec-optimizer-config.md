# Spec: optimizer config file, input format and coach removal (ticket 1 of 3)

## Goal

Follow-up to `specs/spec-squad-optimizer.md` after a first review of `optimize_squad.py`. Three
changes, all to the same script: a config file to replace CLI-flag-only runs, CSV/Excel input with
`name` instead of `player_id` as the row key, and removal of the head coach from the optimization
(coach decisions are made offline by the user). Deviations #1 and #7 of the original spec are
reverted. `w_budget`/`credit_value` (ticket 2) and option value (ticket 3) are out of scope here.

## Scope

- `src/eupy/optimize/optimize_squad.py`: config loading, CSV/Excel input, drop `player_id`, drop
  the coach from the model/objective/output, per-constraint comments.
- `tests/test_optimize_squad.py`, `tests/data/optimizer_input_sample.csv`: updated for the new
  contract (no `player_id`, no `HC` rows, no coach in composition/budget/objective).
- `tests/data/optimizer_config_sample.toml`: new, config format reference.
- `pyproject.toml` (`uv add openpyxl`), `docs/data-catalogue.md`, `docs/data-graph.md`,
  `docs/next-steps.md` (remove the "config file" and "Round 1 flag" bullets — both land here).
- Out of scope: `w_budget` rename/report, option value, `pir_std` input, multi-round anything.

## Input contract changes vs `spec-squad-optimizer.md`

- **Drop `player_id`.** `name` is the row key: must be unique, non-empty. (The user's own
  `player_name_crosswalk` output is expected to already carry one canonical name per player.)
- **Drop all `HC` rows and the `position` value `HC`.** `position` is `G` / `F` / `C` only. Squad
  composition becomes 4 G / 4 F / 2 C = 10 (no `+1 HC`). Coach selection, pricing and trading are
  entirely the user's own, outside this script.
- **File format:** `.csv` or `.xlsx`/`.xls`, dispatched on suffix. Excel reads the first sheet
  unless `sheet` is set in the config. Same column contract either way.
- Everything else (`team`, `position`, `turn`, `price`, `exp_pir`, `exp_pir_adj`, `in_prev_roster`)
  unchanged; `in_prev_roster` now sums to 0 or 10 (was 11), and previous-roster composition check
  drops `HC`.

## Config file

- **Format:** TOML (`tomllib`, stdlib, Python >= 3.11 — this repo requires >= 3.12).
- **Location:** `data/stage_99/optimizer_config.toml` — a real tracked file (not a symlink; this
  is a hand-authored run parameter, not a script output, so it does not get a catalogue/graph
  entry). Sample/reference copy: `tests/data/optimizer_config_sample.toml`.
- **Keys** (all optional; CLI flags override the config, which overrides these defaults):

  ```toml
  input = "data/raw_data/optimizer/optimizer_input.csv"
  sheet = ""            # only used for .xlsx/.xls input; "" = first sheet
  output = "data/stage_01/squad_solution.csv"
  cash = 100.0           # bank balance before trading, coach excluded
  max_trades = 4
  unlimited_trades = false   # true = ignore max_trades (fresh squad / free-trade window)
  w_budget = 0.0
  ```

- **CLI:** `--config PATH` (default: `data/stage_99/optimizer_config.toml` if it exists, else
  built-in defaults) plus the existing individual flags (`--input`, `--cash`, `--max-trades` /
  `--unlimited-trades`, `--w-budget`, `--out`), which take precedence over the config when passed.
  A flag not passed on the CLI falls back to the config value, then the built-in default.
- **Validation:** unknown keys in the TOML file raise (`ValueError`, what/why/next) — catches typos
  early. Same scalar validation as today (`_validate_scalars`), run after the config/flag merge.
- **`unlimited_trades` note (footgun from the original spec):** a fresh squad (no `in_prev_roster`
  row set) with a finite `max_trades` is infeasible (10 buys needed) and fails with the existing
  solver-infeasible hint; the config makes `unlimited_trades = true` a one-time setting instead of
  a flag to remember every Round 1 / free-trade window.

## Minor fixes

1. **CSV or Excel input.** `load_input` dispatches on `path.suffix`: `.csv` → `pd.read_csv`,
   `.xlsx`/`.xls` → `pd.read_excel(sheet_name=...)` (needs `openpyxl`, `uv add openpyxl`). Any
   other suffix raises.
2. **Drop `player_id`; `name` is the key.** Remove `player_id` from `REQUIRED_COLUMNS`,
   `OUTPUT_COLUMNS`, validation, sort key (`name` replaces it, still deterministic), and every
   report/output reference.
3. **Constraint comments.** Each of the 9 constraints in `build_model` gets a one-line comment
   stating what it enforces and why (rules.md citation where relevant) — most already have one;
   fill the gaps.
4. **Exclude the coach entirely.**
   - Model: no `HC` set, no coach variables, no coach composition/budget/trade terms.
   - Objective: drop the `coach_points` expression and the `sum_{i∈HC}` term; `Solution.objective`
     is `active_points + growth_term` only (drop `coach_points` from the dataclass and report).
   - Output: no `role="coach"` rows; `ROLE_ORDER` drops `"coach"`.
   - Spec deviations #1 and #7 (`spec-squad-optimizer.md`) are reverted by this change — note it
     in this spec, don't edit the old one (it stays as a historical record of v1).

## Pass criteria

1. `tests/data/optimizer_input_sample.csv` has no `player_id` column and no `HC` rows; still
   Round-1-style, hand-readable.
2. `tests/data/optimizer_config_sample.toml` round-trips through `main()` (CLI smoke test) and
   matches the documented keys exactly.
3. Every existing test that referenced `player_id` or coach behaviour is rewritten, not deleted,
   for the new contract (composition tests: 4G/4F/2C = 10; trade-limit tests: 10 buys for a fresh
   squad; no coach-specific test remains, since there is no coach).
4. Brute-force equivalence (spec-squad-optimizer.md pass criterion 1) re-verified against the
   10-player model.
5. New test: a config file's values are used when no CLI flag overrides them, and a CLI flag beats
   the config for that one field (one test covering one overridden field is enough).
6. New test: an unrecognized config key raises `ValueError`.
7. New test: `.xlsx` input with the same content as the CSV sample produces the identical solution.
8. `ruff check src tests` and `ruff format --check src tests` clean; `uv run pytest` green (whole
   suite).
9. Tests adversarially reviewed before writing; none exist only for coverage.

## Known limits (carried over, unchanged by this ticket)

Myopic single round; `w_budget` calibration and option value are out of scope (tickets 2, 3); sell
price = current price; no solver time limit.
