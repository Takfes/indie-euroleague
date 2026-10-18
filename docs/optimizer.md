# Squad optimizer

How `scripts/optimize.py` chooses the squad. Run it with `make optimize` or `uv run python scripts/optimize.py`; every parameter is in `[optimization]` of `config/config.toml` (defaults in `src/eupy/config/defaults.py`). Commands and file layout: [run-guide.md](run-guide.md); game rules: [rules.md](rules.md).

## What it does

For one round it picks the 10-player squad (4 G / 4 F / 2 C), the starting five, the 6th man, the bench and the captain that maximise the objective below. The game rules are hard constraints: budget, trade limit, squad composition, at most 6 players per club, the five legal starting formations, and optionally a minimum number of players per turn. It is a mixed-integer program (Pyomo, HiGHS) and must prove optimality. It is myopic: one round, no look-ahead.

## The objective

Every candidate gets one shared value, the same formulas for everybody:

```
score_i  = target_i + risk_adjustment_i
growth_i = (target_i - 1.1 * price_i) / 25          expected credit growth
maximise   starters + 6th man + captain bonus (score)  +  0.5 * bench (score)  +  credit_value * sum of growth over the squad
```

- `target_i` is the expected PIR (config `target`, default column `exp_pir`).
- The role weights (captain counts twice, bench counts 50%) are game rules and stay as they are.
- `growth_i` is the verified community price formula. It needs a target on the PIR scale; with another target keep `credit_value` at 0.

### Objective modes

`objective_mode` selects one risk adjustment. Only that mode's parameter and workbook column are used (and required).

| Mode | `risk_adjustment_i` | Parameter | Workbook column |
| --- | --- | --- | --- |
| `vanilla` (default) | 0 | none | none |
| `robustness` | `- robustness_lambda * std_i` | `robustness_lambda` | `pir_std` |
| `upside` | `+ upside_beta * max(0, p90_i - target_i)` | `upside_beta` | `pir_p90` |
| `downside` | `- downside_gamma * max(0, target_i - p10_i)` | `downside_gamma` | `pir_p10` |

`pir_std`, `pir_p10` and `pir_p90` come from the eubook's player analytical record, where empty cells are filled with defaults (a one-game player gets `pir_std` 0), so the optimizer never sees them empty if the input is rebuilt from it. These are alternative preferences, not better strategies. Robustness penalises upside and downside volatility alike; downside penalises only the gap between the expected value and the 10th percentile. The risk parameters are tuning choices with no empirical default: start at 0, then compare against the vanilla baseline. The workbook column names are set in `COLUMNS` / `RISK_COLUMNS` at the top of the script.

### `credit_value`

The exchange rate from expected credit growth to objective points. With `credit_value = 3.0` a player expected to gain 0.5 credits adds 1.5 points; one expected to lose 0.5 credits subtracts 1.5. A player with target 20 and growth +0.5 is worth 21.5 in that term's units.

- `0.0` (default): ignore credit growth.
- `1.0`: one expected credit is worth one point. `3.0`: worth three points. Higher values favour future purchasing power over this round.
- It means points in the objective, not three real future PIR. Its strategic value should be calibrated later against how extra credits improve future squads.

`credit_value` is independent of the mode and can be combined with any of them.

## Parameters

| Key | Default | Meaning |
| --- | --- | --- |
| `target` | `exp_pir` | workbook column to maximise |
| `objective_mode` | `vanilla` | `vanilla`, `robustness`, `upside`, `downside` |
| `robustness_lambda`, `upside_beta`, `downside_gamma` | `0.0` | risk weights, each used by its own mode, >= 0 |
| `credit_value` | `0.0` | points per expected credit of growth, >= 0 |
| `cash` | `100.0` | credits in the bank before trading |
| `max_trades`, `unlimited_trades` | `4`, `false` | buys allowed this round |
| `min_players_per_turn` | `[]` | `[3, 3]` = at least 3 turn-1 and 3 turn-2 players in the 10-man squad; `[]` or `[0, 0]` = off |
| `solver`, `solver_options` | `appsi_highs`, 1 thread, seed 42, gap 0 | `solver_options` replaces the default dict as a whole |

## Input and empty values

`data/stage_04/optimizer_input.xlsx`, first sheet, one row per candidate. The model needs `player`, `team`, `position`, `turn`, `price` (workbook `crd_last`), `in_prev_roster`, the target, and the risk column of the selected mode. Any empty cell in one of them stops the run with an error naming the columns; other columns are ignored. Further checks: unique players, known positions, `price > 0`, integer turns, a previous roster that is empty or a full 4/4/2 squad, enough candidates per position and per turn.

## Output

`data/stage_99/optimizer_output.xlsx`:

- `selection`: every input row, plus `selected`, `role`, `action`, `is_captain`, and the components of its objective value: `opt_target`, `opt_risk_adjustment`, `opt_score`, `opt_credit_growth` (credits), `opt_growth_value` (points).
- `summary`: objective value and its split, benefit (+) over keeping the current squad, the weighted target and risk adjustment of the line-up, the comparison with the vanilla squad (when the run is not already vanilla), trades, cash, solver status and time, and the parameters used.

## Not implemented yet

- `risk_metric_window` and `context_adjustment_enabled`: they shape how the input columns are built upstream (the window of `pir_std`, `pir_p90`, whether `exp_pir` includes matchup context), not this script.
- Player-specific weights (different `lambda` for core, bench or speculative picks) and a scenario value for turn-1 versus turn-2 substitution. The shared per-player value in `objective_components` is the place to extend.
