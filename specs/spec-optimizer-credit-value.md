# Spec: `w_budget` → `credit_value`, with a measured exchange rate (ticket 2 of 3)

## Goal

`w_budget` has no intuitive interpretation today — it is an unexplained multiplier on a `/25`
formula. Rename it to `credit_value` (its actual meaning: PIR points per credit of expected price
growth) and make the CLI report *show* that exchange rate instead of asking the user to trust it:
a measured shadow price (what one more credit of cash is worth this round) and a trade-off readout
(what the chosen squad gave up / gained by using `credit_value > 0` instead of pure PIR-maximizing).
No change to the model's constraints or the price-growth formula itself.

Builds on `specs/spec-optimizer-config.md` (ticket 1, must be merged/rebased onto first — this
spec assumes the coach-free, name-keyed, config-driven `optimize_squad.py`).

## Scope

- `src/eupy/optimize/optimize_squad.py`: rename `w_budget` → `credit_value` (param, CLI flag,
  config key, `Settings` field, docstrings, error messages); split the growth expression into a
  raw `growth_credits` (unweighted, independent of `credit_value`) and `growth_term =
  credit_value * growth_credits`; add `explain_credit_value()` (two extra solves) and a report
  section for it, wired into `main()` only.
- `tests/test_optimize_squad.py`, `tests/data/optimizer_config_sample.toml`: renamed field/key,
  new tests for `growth_credits` and `explain_credit_value`.
- `docs/next-steps.md`: remove the "`w_budget` is weak" bullet (delivered by this ticket).
- Out of scope: option value, `pir_std`, any change to the `(exp - 1.1*price)/25` formula or its
  unconfirmed ±1.5cr cap, any change to `build_model`'s constraints.

## Rename

Every occurrence of `w_budget` becomes `credit_value`: the `build_model`/`optimize_squad`
parameter, `_validate_scalars`, the CLI flag `--w-budget` → `--credit-value`, the config key
`w_budget` → `credit_value` (update `KNOWN_CONFIG_KEYS`, `Settings.w_budget` →
`Settings.credit_value`, `resolve_settings`, `tests/data/optimizer_config_sample.toml`), the module
docstring's scalar description and CLI usage line. `Solution.growth_term` keeps its name (it
already describes the objective contribution generically, not the old parameter name).

## `Solution` gains `growth_credits`

Split the existing growth expression in `build_model`:

```python
m.growth_credits = pyo.Expression(expr=sum(delta[i] * m.x[i] for i in m.I))   # raw, unweighted
m.growth_term = pyo.Expression(expr=credit_value * m.growth_credits)           # in the objective
```

`Solution` gets a new field `growth_credits: float` (the squad's expected credit gain this round,
independent of `credit_value`); `growth_term` is computed from it as before. This lets the report
functions below compare two solves' raw growth without re-deriving it from `delta_i * x_i`.

## `explain_credit_value()` — new function, called from `main()` only

Not part of `optimize_squad()` (which stays a single solve, unchanged for existing callers/tests).
Takes the already-solved `actual: Solution` plus `(df, cash, max_trades, credit_value)` and does up
to two extra solves:

```python
@dataclass(frozen=True)
class CreditValueReport:
    """What credit_value is buying, measured by re-solving at credit_value=0 and at cash+1.

    Attributes:
        lambda_shadow: PIR gained per +1 credit of cash, at credit_value=0 (shadow price of the
            budget constraint, isolated from any credit_value weighting).
        baseline_active: Optimal active_points at (cash, max_trades, credit_value=0) -- the
            pure-PIR-maximizing squad, for comparison against `actual`.
        baseline_growth_credits: That baseline squad's expected credit growth.
    """
    lambda_shadow: float
    baseline_active: float
    baseline_growth_credits: float

def explain_credit_value(
    df: pd.DataFrame, cash: float, max_trades: int | None, credit_value: float, actual: Solution
) -> CreditValueReport:
    baseline = actual if credit_value == 0 else optimize_squad(df, cash, max_trades, credit_value=0.0)
    shadow = optimize_squad(df, cash=cash + 1, max_trades=max_trades, credit_value=0.0)
    return CreditValueReport(
        lambda_shadow=shadow.active_points - baseline.active_points,
        baseline_active=baseline.active_points,
        baseline_growth_credits=baseline.growth_credits,
    )
```

(Signature illustrative, not prescriptive on naming internals — the three numbers and the
credit_value==0 short-circuit are the contract.)

## Report

New function `format_credit_value_report(report: CreditValueReport, actual: Solution) -> str`,
printed from `main()` right after the existing `format_report()` output. Two lines:

- Measured rate: `f"Credit value: {report.lambda_shadow:.2f} PIR/credit measured (re-solved at
  cash+1, credit_value=0)"`.
- Trade-off, only when `actual.active_points != report.baseline_active` (i.e. `credit_value`
  actually changed the squad): `f"Growth trade-off: gave up {report.baseline_active -
  actual.active_points:.2f} PIR for +{actual.growth_credits - report.baseline_growth_credits:.3f}
  credits"`. When it did not change the squad, print a line saying so instead (e.g. `"credit_value
  did not change the squad at this cash/trade limit"`), not nothing.

`main()` always computes and prints this (it is the answer to "what am I trading off", not an
opt-in flag) — two extra solves per CLI run (or one, when `credit_value == 0`), on top of the
existing sub-second solve time; no new CLI flag.

## Pass criteria

1. Every prior `w_budget` test (naming aside) still passes under the new name/contract — rewritten,
   not deleted.
2. `growth_credits` is independent of `credit_value`: two solves with different `credit_value` that
   land on the *same* squad report the same `growth_credits`.
3. Invariant test: `shadow.active_points >= baseline.active_points` (relaxing cash by 1 credit
   cannot lower the pure-PIR optimum) on at least one instance where it's strictly greater.
4. Invariant test: `baseline.active_points >= actual.active_points` for `credit_value > 0` (the
   credit_value=0 baseline is the unconstrained PIR maximum) on at least one instance where it's
   strictly greater (i.e. `credit_value` actually cost some PIR).
5. `explain_credit_value` re-solve count: a test (can inspect call count via a wrapped/mocked
   `optimize_squad` or by checking `credit_value == 0` short-circuits to one fewer solve) confirms
   the credit_value==0 case skips the baseline re-solve.
6. CLI smoke test: `format_credit_value_report` output appears in `main()`'s stdout for both a
   `credit_value == 0` run (the "did not change" line, since baseline == actual trivially) and a
   `credit_value` large enough to change the sample squad.
7. `ruff check src tests` / `ruff format --check src tests` clean; `uv run pytest` green (whole
   suite).
8. Tests adversarially reviewed before writing; none exist only for coverage.

## Known limits (carried over / updated)

- The price-growth formula and its ±1.5cr cap remain unconfirmed (`docs/rules.md`) — this ticket
  only measures the *exchange rate* against that formula, it does not validate the formula itself.
- `lambda_shadow` is a per-round, per-run number (depends on the specific candidate pool, cash and
  trade limit); it is not a fixed constant to hardcode, and it does not account for how many future
  rounds a credit keeps paying off (that discounting, if wanted, stays the user's own judgment call
  when picking `credit_value`).
- Still myopic; option value still not modelled (ticket 3).
