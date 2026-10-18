# Spec: `credit_value` with a measured exchange rate (ticket 2 of 3)

Status: delivered (was `w_budget`).

## Goal

`credit_value` is the number of objective points one expected credit of price growth is worth. The run reports what
that exchange rate buys instead of asking the user to trust it. The model's constraints and the growth formula
`(target - 1.1*price)/25` are unchanged.

## Behaviour

- `growth_credits` (raw, independent of `credit_value`) and `growth_term = credit_value * growth_credits` are separate
  on `Solution`.
- `explain_credit_value(df, cfg, actual)` re-solves at `credit_value = 0` (skipped when it already is 0) and at
  `cash + 1`, and returns a `CreditValueReport`: `lambda_shadow` (PIR gained per extra credit of cash),
  `baseline_active` (best pure-PIR active points) and `baseline_growth_credits`.
- `main()` always prints it: the measured rate, then either the PIR given up for the extra credits or a line saying
  `credit_value` did not change the squad. The shadow price is also a row of the `summary` sheet.

## Pass criteria (in `tests/test_optimize_squad.py`)

1. `growth_credits` is the same for two `credit_value`s that pick the same squad.
2. `lambda_shadow >= 0`, and strictly positive on a crafted instance; `baseline_active >= actual.active_points`, and
   strictly greater on seed 6.
3. The `credit_value = 0` case skips the baseline re-solve (one solve instead of two).
4. `main()` prints both report variants.

## Known limits

- The growth formula and its cap are unconfirmed; this only measures the rate against that formula.
- `lambda_shadow` is per run (candidate pool, cash, trade limit), not a constant, and ignores how many later rounds a
  credit keeps paying off.
