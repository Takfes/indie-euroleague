# Squad optimizer

How `scripts/optimize.py` chooses the squad. Run it with `make optimize` (= `uv run python -m eupy.optimize.optimize_squad`; `scripts/optimize.py` is its working copy, kept identical by a test); every parameter is in `[optimization]` of `config/config.toml` (defaults in `src/eupy/config/defaults.py`). Commands and file layout: [run-guide.md](run-guide.md); game rules: [rules.md](rules.md).

## What it does

For one round it picks the 10-player squad (4 G / 4 F / 2 C), the starting five, the 6th man, the bench and the captain that maximise the objective below. The game rules are hard constraints: budget, trade limit, squad composition, at most 6 players per club, the five legal starting formations, and optionally a minimum number of players per turn. It is a mixed-integer program (Pyomo, HiGHS) and must prove optimality. It is myopic: one round, no look-ahead.

## The objective

The model maximises one number. It is built in three steps.

### Step 1: a value for every player

```
score  = target  +  risk adjustment
```

- `target` is the expected PIR: the workbook column named by `target` in the config (default `exp_pir`).
- The risk adjustment is the sum of three optional terms. Each has a weight in the config that defaults to `0` (term off), and any combination works.

| Term | Effect on the score | Weight | Column read (only when the weight is above 0) |
| --- | --- | --- | --- |
| Robustness | minus `robustness_lambda` x `std` | `robustness_lambda` | `pir_std` |
| Upside | plus `upside_beta` x how far `p90` is above `target` | `upside_beta` | `pir_p90` |
| Downside | minus `downside_gamma` x how far `target` is above `p10` | `downside_gamma` | `pir_p10` |

"How far above" counts only when positive: `max(0, p90 - target)` and `max(0, target - p10)`. A player whose `p10` is already above the target is not penalised, nor rewarded.

### Step 2: the game's role weights

Each squad member counts differently depending on the role, which is a game rule and not configurable:

| Role | Counts |
| --- | --- |
| Starter (5) and 6th man | 100% of the score |
| Captain (1, must start) | 200% of the score |
| Bench (3) | 50% of the score |

### Step 3: credit growth

Prices move after each round, so a player also has an expected credit growth, using the community price formula:

```
growth = (target - 1.1 x price) / 25
```

It is added for all 10 squad members whatever their role, scaled by `credit_value` (see below).

### All together

```
maximise    sum( role weight x score )   over the squad
          + credit_value x sum( growth ) over the squad
```

With all risk weights and `credit_value` at 0 this is the plain role-weighted expected PIR. The `summary` sheet calls that squad the "vanilla" squad and compares your run against it.

### Worked example

A starter with `target` 20, `std` 4, `p90` 30, `p10` 8, `price` 10, and the weights `robustness_lambda` 0.5, `upside_beta` 0.25, `downside_gamma` 0.1, `credit_value` 3:

| Part | Calculation | Points |
| --- | --- | --- |
| target | | 20.00 |
| robustness | 0.5 x 4 | -2.00 |
| upside | 0.25 x (30 - 20) | +2.50 |
| downside | 0.1 x (20 - 8) | -1.20 |
| **score** | | **19.30** |
| growth | (20 - 1.1 x 10) / 25 = 0.36 credits; x 3 | +1.08 |
| **value as a starter** | | **20.38** |

As the captain the score part counts twice (38.60) and as a bench player half (9.65); the growth part is the same 1.08 in every role.

### Notes

- `growth` assumes `target` is on the PIR scale; with another target keep `credit_value` at 0.
- `pir_std`, `pir_p10` and `pir_p90` come from the eubook's player analytical record, where empty cells are filled with defaults (a one-game player gets `pir_std` 0), so the optimizer never sees them empty if the input is rebuilt from it.
- The risk terms are preferences, not better strategies. Robustness penalises upside and downside volatility alike, so combining it with the percentile terms counts spread twice. The weights have no empirical default: start at 0, then compare with the vanilla squad on the summary sheet.
- The workbook column names are set in `COLUMNS` / `RISK_COLUMNS` at the top of the script.

### `credit_value`

The exchange rate from expected credit growth to objective points. With `credit_value = 3.0` a player expected to gain 0.5 credits adds 1.5 points; one expected to lose 0.5 credits subtracts 1.5. A player with target 20 and growth +0.5 is worth 21.5 in that term's units.

- `0.0` (default): ignore credit growth.
- `1.0`: one expected credit is worth one point. `3.0`: worth three points. Higher values favour future purchasing power over this round.
- It means points in the objective, not three real future PIR. Its strategic value should be calibrated later against how extra credits improve future squads.

`credit_value` is independent of the risk terms and combines with any of them.

## Parameters

| Key | Default | Meaning |
| --- | --- | --- |
| `target` | `exp_pir` | workbook column to maximise |
| `robustness_lambda`, `upside_beta`, `downside_gamma` | `0.0` | risk weights, >= 0; 0 = term off, terms combine |
| `credit_value` | `0.0` | points per expected credit of growth, >= 0 |
| `cash` | `100.0` | credits in the bank before trading |
| `max_trades`, `unlimited_trades` | `4`, `false` | buys allowed this round |
| `min_players_per_turn` | `[]` | `[3, 3]` = at least 3 turn-1 and 3 turn-2 players in the 10-man squad; `[]` or `[0, 0]` = off |
| `solver`, `solver_options` | `appsi_highs`, 1 thread, seed 42, gap 0 | `solver_options` replaces the default dict as a whole |

## Input and empty values

`data/stage_03/optimizer_input.xlsx`, first sheet, one row per candidate. `build_eubook` writes it: the `player_analytical_record` columns plus `in_prev_roster` (all 0) at the right end. Set `in_prev_roster` to 1 for the players you own before running; every rerun of the build resets it to 0. The model needs `player`, `team`, `position`, `turn`, `price` (workbook `crd_last`), `in_prev_roster`, the target, and the risk columns of the terms whose weight is above 0. Any empty cell in one of them stops the run with an error naming the columns; other columns are ignored. Further checks: unique players, known positions, `price > 0`, integer turns, a previous roster that is empty or a full 4/4/2 squad, enough candidates per position and per turn.

## Output

`data/stage_99/optimizer_output.xlsx`:

- `selection`: every input row, plus `selected`, `role`, `action`, `is_captain`, and the components of its objective value: `opt_target`, `opt_risk_adjustment`, `opt_score`, `opt_credit_growth` (credits), `opt_growth_value` (points).
- `summary`: objective value and its split, benefit (+) over keeping the current squad, the weighted target and risk adjustment of the line-up, the comparison with the vanilla squad (when the run is not already vanilla), trades, cash, solver status and time, and the parameters used.

## Not implemented yet

- `risk_metric_window` and `context_adjustment_enabled`: they shape how the input columns are built upstream (the window of `pir_std`, `pir_p90`, whether `exp_pir` includes matchup context), not this script.
- Player-specific weights (different `lambda` for core, bench or speculative picks) and a scenario value for turn-1 versus turn-2 substitution. The shared per-player value in `objective_components` is the place to extend.
