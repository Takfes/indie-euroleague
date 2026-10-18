# Spec: single-round squad optimizer (Pyomo MILP)

Status: implemented. Code: `src/eupy/optimize/optimize_squad.py`, mirrored by `scripts/optimize.py` (a test keeps the
two identical). User-facing description: `docs/optimizer.md`. Run: `make optimize`.

## Goal

For one round, pick the 10-player squad (4 G / 4 F / 2 C), starting five, 6th man, bench and captain that maximise the
objective, under the Classic-mode rules in `docs/rules.md` (rules win over the strategy notes in
`contx/EuroLeague-Fantasy-AI-&-OR-Strategy.md`). Deterministic, proven optimal, written to an Excel workbook.

## Scope

- Pure functions plus a `main()` without CLI flags; the model builder does no I/O. Parameters come from
  `[optimization]` in `config/config.toml` (defaults in `eupy.config.defaults.Optimization`).
- Input: `data/stage_03/optimizer_input.xlsx`, first sheet. Workbook columns are mapped to the model once (`COLUMNS`,
  `RISK_COLUMNS`, config `target`); the input is never renamed.
- Output: `data/stage_99/optimizer_output.xlsx` with sheets `selection` (input rows + `selected`, `role`, `action`,
  `is_captain`, objective components) and `summary`.
- Out of scope: multi-round planning, producing `exp_pir` and the risk columns (upstream), the head coach (the user
  handles coach selection and pricing), injuries and price caps.

## Model

Binary `x` (in squad), `s` (sold), `b` (bought), `y_start`, `y_6th`, `y_bench`, `c` (captain) per candidate.

1. Transition `x = prev - s + b`; valid actions `s <= prev`, `b <= 1 - prev`.
2. Trade limit `sum b <= max_trades` (absent when unlimited); budget `sum price*b <= cash + sum price*s`.
3. Composition 4 G / 4 F / 2 C; at most 6 players per club; optional `min_players_per_turn`.
4. Roles: each squad player has exactly one of starter / 6th man / bench (5 / 1 / 4); starter bounds per position that
   admit exactly the five legal formations 2-2-1, 1-2-2, 2-1-2, 1-3-1, 3-1-1; one captain, who starts.
5. Objective: `score` for starters, 6th man and captain bonus, 0.5 x `score` for the bench, plus
   `credit_value * sum (target - 1.1*price)/25` over the squad. `score = target + risk adjustment` by
   risk weights `robustness_lambda`, `upside_beta`, `downside_gamma` (0 = off, terms combine); see `docs/optimizer.md`.

Solver `appsi_highs` (configurable), 1 thread, fixed seed, `mip_rel_gap = 0`; anything but `optimal` raises. Output is
sorted deterministically, so reruns on the same input are identical.

## Pass criteria (all covered in `tests/test_optimize_squad.py`)

1. The MILP optimum equals an independent brute-force enumeration of squads x roles x captain on seeded random
   universes, in every config (base, no budget, no trade limit, `credit_value` 0, robustness); each of budget, trade
   limit, `credit_value` and the risk adjustment changes the answer on at least one instance.
2. Rule scenarios: club cap, unlimited trades, formation bounds equal the legal formations (enumeration), no
   sell-and-rebuy of one player, row order does not matter, a fresh team with a trade limit is infeasible and says so.
3. Every validation rule has a failing case with a readable `ValueError`, including empty cells in any used column.
4. A ~700-row universe solves in under 30 s and repeats identically; `main()` writes both sheets and prints the report.
5. `ruff`, `pytest` and `registry check` clean.

## Known limits

- Myopic: no look-ahead, no transfer planning. Sell price equals the current price.
- The price formula and its cap are unconfirmed (`docs/rules.md`); `credit_value` is a heuristic exchange rate.
- Option value between turns is not modelled (`specs/spec-optimizer-option-value.md`).
- No solver time limit; proven optimality is required.
