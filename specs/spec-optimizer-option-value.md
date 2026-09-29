# Spec: option value — bench-swap and captain-move pairwise terms (ticket 3 of 3)

> **STATUS: deferred, not implemented.** The per-pair constraint shapes below (`a[i,j] <=
> y_active[i]`, `a[i,j] <= y_bench[j]`, plus separate at-most-one constraints) produce ~460k
> constraint rows at realistic scale (~700 candidates, ~115k pairs) and did not solve in 10+
> minutes with HiGHS — killed as not viable. Do not implement as literally written below without
> first reformulating the constraint shapes. See `docs/next-steps.md` ("Squad optimizer" section)
> for the performance findings and a promising, mathematically-verified-equivalent alternative
> (aggregated per-player sum constraints, ~47s solve) to start from next time. The put-value
> formula, the two option mechanics, and the accepted compounding approximation below are still
> correct and worth keeping as the starting point — only the MILP constraint encoding needs
> redoing. The Report section's claim that "`pir_std` absent means `option_value` is 0.0 and the
> term is inert" is also wrong (caught during implementation, not fixed here) — the `sd=0` limit of
> the put formula is `notional * max(strike - mean, 0)`, which formation constraints can force
> positive even with no spread data at all.

## Goal

Model the two between-turn option mechanics confirmed against real rules (not the strategy doc's
recipe): a bench↔active swap and a captain-move, each a put option on the earlier-turn player's
score. Add them to the objective as two pairwise assignment terms over the existing `TURN1`/
`TURN2`-style ordering (generalised to any `turn_i < turn_j`), finally giving those sets a purpose.

Builds on `specs/spec-optimizer-config.md` (ticket 1) and `specs/spec-optimizer-credit-value.md`
(ticket 2) — this branch already has both merged in.

**Accepted approximation (confirmed with the user):** the two terms are additive and independent.
A player can, in principle, be valued as both a bench-swap donor and a captain-move donor in the
same solve, which the real game cannot actually deliver in one round (compounding two option
legs on one player over-counts). Not fixed here — would need per-turn state (the scenario/
two-stage model explicitly deferred). Document it in the module docstring's Known limits, not
worked around.

## Scope

- `src/eupy/optimize/optimize_squad.py`: new `pir_std` input column; remove `exp_pir_adj` (see
  below — it is superseded, not complementary); the put-value formula (stdlib `math.erf`, no new
  dependency); two new pairwise assignment terms in `build_model`; `Solution` gains
  `option_value`; report updated.
- `tests/test_optimize_squad.py`: new tests per Pass criteria; `tests/data/optimizer_input_sample.csv`
  gets a `pir_std` column, loses `exp_pir_adj`.
- `docs/next-steps.md`: remove the "Option value is not modelled" section (this ticket delivers
  it); the compounding approximation moves into the module docstring's Known limits instead of
  next-steps (it's a documented design choice, not deferred work).
- Out of scope: `pir_std` estimation itself (upstream, not this script's job), scenario/two-stage
  modelling, T3 sequencing beyond the general `turn_i < turn_j` pairing, per-turn state of any kind.

## Why `exp_pir_adj` is removed, not extended

`exp_pir_adj` was a placeholder hook for "pre-baked" per-player option value (spec-squad-optimizer.md
deviation 4), explicitly kept "off by default" until real option-value modelling existed. It now
does exist, as pairwise terms below — keeping both would let a user double-count option value (once
via a hand-set `exp_pir_adj`, again via the model's own pairwise terms) with no way for the model to
know. Remove the column, its validation, its `_prepare` fill logic, and switch `active_points` to
use `exp[i]` uniformly (starters/6th/captain no longer read a separate "adjusted" score — the
adjustment is now the pairwise terms themselves).

## New input: `pir_std`

Optional float column, same convention `exp_pir_adj` had: missing column or NaN cell → `0.0`
(no modelled spread → that player's option-value terms collapse to their deterministic limit,
never negative, never a crash — see formula note below). Must be `>= 0` when present (validation
error otherwise, same pattern as `price > 0`). Only a T1-side (earlier-turn) player's `pir_std`
matters — the later-turn player enters a pair only through its mean `exp_pir`.

## Put-value formula

For `X ~ N(mu, sd^2)`, strike `K`, notional `n`:

```python
import math

def _put_value(mu: float, sd: float, strike: float, notional: float) -> float:
    if sd <= 0.0:
        return notional * max(strike - mu, 0.0)
    d = (strike - mu) / sd
    phi = math.exp(-0.5 * d * d) / math.sqrt(2.0 * math.pi)
    cdf = 0.5 * (1.0 + math.erf(d / math.sqrt(2.0)))
    return notional * ((strike - mu) * cdf + sd * phi)
```

Use `math.erf` (stdlib) — do not add `scipy`. Verified reference values (already checked earlier
against 2M-draw Monte Carlo, reuse as regression fixtures): `_put_value(12, 8, 10, 0.5) ≈ 1.145`,
`_put_value(15, 7, 10, 0.5) ≈ 0.488`, `_put_value(20, 9, 16, 1.0) ≈ 1.939`, `_put_value(20, 5, 16,
1.0) ≈ 0.601`, `_put_value(20, 9, 22, 1.0) ≈ 4.679`.

## Two pairwise terms in `build_model`

Precompute, for every ordered pair `(i, j)` with `turn_i < turn_j` in `m.I`:

```
v[i, j] = _put_value(exp[i], pir_std[i], exp[j], 0.5)   # bench-swap: i active -> bench, j bench -> active
w[i, k] = _put_value(exp[i], pir_std[i], exp[k], 1.0)   # captain-move: i's captaincy -> k
```

New continuous variables (bounded [0, 1] suffices; the constraints below make them behave as an
assignment, no need for `Binary`):

```
m.a[i, j]  for (i, j) with turn_i < turn_j        # bench-swap assignment
m.z[i, k]  for (i, k) with turn_i < turn_k        # captain-move assignment

m.a[i, j] <= y_start[i] + y_6th[i]     # i must be active to donate a bench-swap
m.a[i, j] <= y_bench[j]                # j must be bench to receive it
sum_j m.a[i, j] <= 1   for each i      # i donates to at most one j
sum_i m.a[i, j] <= 1   for each j      # j receives from at most one i

m.z[i, k] <= c[i]                      # i must be captain to donate the captain-move
m.z[i, k] <= y_start[k]                # k must be a starter to receive it (rules.md: captain
                                        # must be one of the starting five)
sum_k m.z[i, k] <= 1   for each i      # (redundant given sum(c)=1, harmless to keep for clarity)
sum_i m.z[i, k] <= 1   for each k      # k receives from at most one i
```

Objective gains `+ Σ v[i,j]·a[i,j] + Σ w[i,k]·z[i,k]`; `Solution.option_value` is that sum, added
to `objective = active_points + growth_term + option_value`.

**Performance risk — flag, do not silently mitigate.** Pair count is `O(n^2)` in the candidate
pool size (bounded by `turn_i < turn_j`, not by position or squad membership, since squad
membership is itself a decision the MILP is making jointly). At the ~700-row realistic-size scale
from `spec-squad-optimizer.md` pass criterion 4, this can run to hundreds of thousands of
continuous variables. Measure actual solve time on that scale as part of Pass criterion 6 below;
if it blows past a reasonable budget, stop and report back with the numbers and a proposed
mitigation (e.g. capping pair generation to top-K candidates per position/turn) — do not decide
that scope change unilaterally.

## Pass criteria

1. `_put_value` matches every reference value above to 1e-3, including the `sd <= 0` deterministic
   limit (test both `sd == 0` and a case where `strike < mu` at `sd == 0`, i.e. value `0.0`).
2. `pir_std` missing/NaN defaults to `0.0`; present-but-negative raises `ValueError`.
3. `exp_pir_adj` has zero remaining references anywhere in `src/`, `tests/`, docstrings, or sample
   data (grep-clean) — removed, not just unused.
4. Assignment constraints hold on a solved instance: `a[i,j] > 0` only where `turn_i < turn_j` and
   `i` is active/`j` is bench in that solution; same shape check for `z[i,k]` against `c[i]`/
   `y_start[k]`; each `i`/`j`/`i`/`k` used at most once.
5. **Correctness on a small crafted instance**: independently compute, by brute force (enumerate
   or use `scipy`-free small matching search — e.g. `itertools.permutations` is fine at this size),
   the optimal `Σ v·a + Σ w·z` for one *fixed* squad/role/captain assignment, and confirm it
   matches what the MILP's `a`/`z` values give for that same fixed assignment. This does not need
   to re-run the full brute-force squad-equivalence machinery from `spec-squad-optimizer.md` — a
   fixed-assignment matching check is enough to prove the pairwise terms are wired correctly.
6. **Realistic size**: solve time on a ~700-row synthetic universe (reuse/extend the existing
   fixture) is measured and reported in the test output or a comment; if it doesn't finish in a
   reasonable time (judgment call — flag rather than silently extend a hard timeout), stop and
   report per the "Performance risk" note above instead of proceeding.
7. **The terms actually change the answer**: at least one crafted instance where adding the
   bench-swap term changes which player is rostered (a high-variance backup becomes worth it only
   because of option value), and one where it changes the captain choice.
8. **Compounding is real, not accidentally prevented**: one crafted instance shows the same player
   contributing to both an `a[i,j]` and a `z[i,k]` simultaneously in the optimal solution —
   confirms the accepted approximation behaves as documented, not as an accidental extra
   constraint that silently forbids it.
9. `ruff check src tests` / `ruff format --check src tests` clean; `uv run pytest` green (whole
   suite).
10. Tests adversarially reviewed before writing; none exist only for coverage.

## Report

Extend `format_report`'s objective line to `active {…} + growth {…} + option {…}` (three terms,
was two). No new CLI flag — `pir_std` absent means `option_value` is `0.0` and the term is inert,
same "degrades gracefully with no flag" pattern as `credit_value=0.0`.

## Known limits (module docstring — update, don't defer to next-steps)

- Bench-swap and captain-move option value are additive, independent approximations; a player can
  be valued for both in one solve, which the real game's turn sequencing cannot actually realise
  simultaneously in general. Fixing this needs per-turn state (a scenario/two-stage model), out of
  scope here.
- No modelling of a between-turn *formation* change beyond the captain-move and bench-swap
  mechanics themselves (e.g. reshuffling which position slots are filled).
- `pir_std` is not produced by any upstream script yet (same status `exp_pir` had before an ML
  model exists) — the caller supplies an estimate.
- Still myopic: one round, no look-ahead.
