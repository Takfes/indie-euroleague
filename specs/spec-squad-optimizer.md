# Spec: single-round squad optimizer (Pyomo MILP)

## Goal

Given one tidy table (one row per player / head coach for the upcoming round) plus three scalars,
pick the best 10-player + 1-coach squad, starting five, 6th man, bench and captain for that
round, respecting every Classic-mode rule that matters (budget, trades, composition, formation,
club cap). Deterministic, solved to proven optimality, result written as a CSV.

Origin: `contx/EuroLeague-Fantasy-AI-&-OR-Strategy.md` (last part: "Complete MILP Formulation" +
"Complete Pyomo Implementation Script"), corrected against `docs/rules.md` (rules win on conflict).

## Scope

- One script: `src/eupy/optimize/optimize_squad.py` (+ empty `src/eupy/optimize/__init__.py`).
  Pure functions + a `main()` CLI; no I/O inside the model builder.
- Input: a DataFrame (the CLI loads it from CSV) obeying the **Input contract** below.
- Scalars (function args / CLI flags, not columns): `cash`, `max_trades`, `w_budget`.
- Single round, myopic. **Out of scope:** multi-period / rolling horizon, real option-value
  modelling, producing `exp_pir` (upstream ML/heuristics), price-cap or injury adjustments,
  Round-1 late-joiner budget maths (caller passes the resulting `cash`), team-composition
  presets, plotting.
- New deps (`uv add`): `pyomo`, `highspy` (solver, pip-installable => reproducible without
  brew), `pandas` (not currently declared in `pyproject.toml`).

## Deviations from the strategy doc (all deliberate, rules-driven)

| # | Doc | Here | Why |
| - | --- | ---- | --- |
| 1 | No head coach | Coach is a roster member: exactly 1 `HC`, scores 100%, counts toward the trade limit, costs credits, cannot be starter/6th/bench/captain | `rules.md`: 10 players + 1 coach share the 100cr; coach counts toward the 4 trades |
| 2 | No club cap | `<= 6` players per `team` | `rules.md`: max 6 from one EuroLeague team |
| 3 | `MAX_TRADES = 10` for "unlimited" | `max_trades: int \| None`; `None` = no limit constraint | 10 is wrong once the coach is tradeable (11 slots) |
| 4 | Objective uses one PIR column; option value pre-baked | `exp_pir` (mean) + optional `exp_pir_adj` (option-adjusted, defaults to `exp_pir`) | Doc's option-value recipe hardcodes a cutoff tau=10 and replacement=10 (the arbitrary threshold we wanted to avoid), ignores the 50% partial credit and does not require a T2 bench player to exist. Default = off; column kept as a plug-in point |
| 5 | Turns are 1/2 | `turn` is any int >= 1 (real rounds can have 3 game dates); sets `TURN1`, `TURN2` built, T3+ in neither | Verified against `schedule_E2026.csv` |
| 6 | "T1 starters >= 3" constraint | Dropped (as the doc itself concluded) | Arbitrary threshold |
| 7 | Price-growth term for everyone | Players only; coach term = 0 | Community formula is unvalidated for coaches |

## Input contract

One row per candidate. Players **and** head coaches in the same table. CSV, UTF-8, header row,
no index column. Extra columns are ignored (kept out of the model). Column names are exact.

| column | type | required | meaning |
| ------ | ---- | -------- | ------- |
| `player_id` | str / int | yes | unique key; same id as `euroleague_fantasy_stats/players.csv` / `head_coaches.csv` |
| `name` | str | yes | display only (report/output) |
| `team` | str | yes | club code (e.g. `PAR`, `OLY`), used for the 6-per-club cap; same code space as the fantasy-stats `team` column |
| `position` | `G` / `F` / `C` / `HC` | yes | `HC` = head coach |
| `turn` | int >= 1 | yes | turn of the round in which the player's (coach's) team plays |
| `price` | float > 0 | yes | current quotation in credits (after the last price update); buy and sell price |
| `exp_pir` | float | yes | projected fantasy score this round (coach: projected coach points). Used for bench (x0.5), the price-growth term, and as default scoring value |
| `exp_pir_adj` | float | no | option-adjusted score for starter / 6th man / captain slots. Missing column or NaN cell => `exp_pir` |
| `in_prev_roster` | 0 / 1 | yes | 1 if currently in the user's team. Round 1 / fresh team: all 0 |

Hard validation (raise `ValueError` with what / why / next; validate before building anything):
required columns present; no NaN in required columns; `player_id` unique; `position` in the
allowed set; `price > 0`; `turn` int >= 1; `in_prev_roster` in {0,1}; every position has at
least as many rows as the squad needs (4 G, 4 F, 2 C, 1 HC); `in_prev_roster` sum is 0 or 11 and,
if 11, composition is exactly 4 G / 4 F / 2 C / 1 HC.

Scalars: `cash` (float >= 0; credits in the bank *before* trading — for Round 1 this is the whole
budget, 100.0), `max_trades` (int >= 0 or `None`; 4 standard, `None` in unlimited windows / Round 1),
`w_budget` (float >= 0; capital-growth weight, ~3.0 early season, 0.0 late; default 0.0).

## Model (per round; index `i` over rows, `P` = players G/F/C)

Sets: `G`, `F`, `C`, `HC`, `P = G ∪ F ∪ C`, `TURN1`, `TURN2` (subsets of `P`), `TEAMS`.

Params: `price_i`, `prev_i`, `exp_i`, `adj_i` (default `exp_i`), `delta_i = (exp_i - 1.1*price_i)/25`
for `i ∈ P`, `K = max_trades`, `cash`, `w = w_budget`.

Binary vars: `x_i, s_i, b_i` for all `i` (in squad / sold / bought); `y_start_i, y_6th_i, y_bench_i, c_i`
for `i ∈ P`.

Constraints (each gets a short comment stating its purpose in the code):
1. Transition: `x_i = prev_i - s_i + b_i`.
2. Valid actions: `s_i <= prev_i`; `b_i <= 1 - prev_i` (no phantom cash without them — `x` is binary, so buy+sell of one player changes neither cost nor score — but they rule out ambiguous sell-and-rebuy output and wasted trades).
3. Trade limit: `sum_i b_i <= K` (skipped if `K is None`; coach included).
4. Budget: `sum price_i b_i <= cash + sum price_i s_i`.
5. Composition: `sum_G x = 4`, `sum_F x = 4`, `sum_C x = 2`, `sum_HC x = 1`.
6. Club cap: for each team `t`: `sum_{i ∈ P, team_i = t} x_i <= 6`.
7. Roles: `x_i = y_start_i + y_6th_i + y_bench_i` (`i ∈ P`); `sum y_start = 5`, `sum y_6th = 1`, `sum y_bench = 4`.
8. Formation (starters): `1 <= sum_G y_start <= 3`, `1 <= sum_F y_start <= 3`, `1 <= sum_C y_start <= 2`. Together with `sum y_start = 5` this is exactly the five legal formations (2-2-1, 1-2-2, 2-1-2, 1-3-1, 3-1-1) — a test must prove that equivalence by enumeration.
9. Captain: `sum c = 1`; `c_i <= y_start_i`.

Objective (maximise):

```
sum_{i ∈ P} [ adj_i * (y_start_i + y_6th_i + c_i) + 0.5 * exp_i * y_bench_i ]     # active points, captain = +1x extra
+ sum_{i ∈ HC} exp_i * x_i                                                       # coach always 100%
+ w * sum_{i ∈ P} delta_i * x_i                                                  # expected capital growth (whole squad)
```

Constants (module-level, named): `SQUAD = {G: 4, F: 4, C: 2, HC: 1}`, `MAX_PER_CLUB = 6`,
`BENCH_FACTOR = 0.5`, `PRICE_BREAKEVEN = 1.1`, `PRICE_DIVISOR = 25.0`.

Solver: `appsi_highs`, single thread, fixed seed, must terminate `optimal` — anything else raises
with the termination condition. Ties between equally good squads can exist; output is sorted
deterministically (role order, then `player_id`) so a rerun on the same input + same solver
version is byte-identical.

## Output

`data/stage_01/squad_solution.csv` (CLI `--out` overrides): one row per player/coach in
`prev roster ∪ new squad`, columns: `player_id, name, team, position, turn, price, exp_pir, action
(keep|buy|sell), role (starter|sixth|bench|coach|none), is_captain (0/1)`. Sold players have
`role=none`. The CLI also prints a terse report: objective breakdown (active points / coach /
growth term), trades used vs limit, cash before -> after, starting five + captain, 6th man, bench.

CLI: `uv run python src/eupy/optimize/optimize_squad.py --input PATH [--cash 100.0]
[--max-trades N | --unlimited-trades] [--w-budget 0.0] [--out PATH]`.
Default `--input`: `data/raw_data/optimizer/optimizer_input.csv` (git-ignored raw layer; the user
prepares it; a future upstream script will replace the manual step).

Script header (module docstring) in the repo format: Inputs `optimizer_input` (raw,
`data/raw_data/optimizer/optimizer_input.csv`), Outputs `squad_solution`
(`data/stage_01/squad_solution.csv`), `Final: true`.

## Pass criteria

1. **Brute-force equivalence:** on small random universes (fixed seeds; e.g. 6G/5F/3C/2HC over 3
   clubs, non-trivial `prev` roster, binding budget and trade limit), the model's optimal
   objective equals an independent brute-force enumeration of all legal squads x role
   assignments x captain. At least one instance where each of budget, trade limit, `w_budget > 0`
   and `exp_pir_adj` actually changes the answer vs. leaving it off.
2. **Rule tests on crafted scenarios:** club cap binds (one club's players dominate `exp_pir` ->
   never > 6); coach is bought/sold/counted in the trade limit; `max_trades=None` lifts the limit;
   captain is a starter with the max `adj` among starters; formation is one of the five legal
   ones (enumeration proof of the bounds equivalence); `s`/`b` cannot both be 1 for one player.
3. **Validation:** every rule in "Hard validation" has a failing case that raises a readable
   `ValueError`; a valid input does not.
4. **Realistic size:** a synthetic ~700-row universe (~20 clubs) solves to optimality in
   < 30 s on this machine; two consecutive runs give a byte-identical output CSV.
5. **Output/CLI:** output CSV has the columns above, sorted deterministically; CLI smoke test on
   the sample file writes the file and prints the report (no traceback).
6. **Sample input:** `tests/data/optimizer_input_sample.csv` — small, valid, hand-readable
   (Round-1 style all-zero prev roster is fine), doubles as the format reference; the input
   contract above is reproduced in the module docstring.
7. `ruff check src tests` and `ruff format --check src tests` clean; `uv run pytest` green
   (whole suite, not just the new file).
8. Tests were adversarially reviewed before writing; none exist only for coverage.

## Known limits (state them, do not "fix" them here)

- Myopic: no look-ahead over rounds, no transfer planning beyond the current window.
- `w_budget` is a heuristic exchange rate between credits and PIR points; the price formula and
  its +-1.5cr cap are unconfirmed (see `docs/rules.md`).
- Option value is not modelled; `exp_pir_adj` is a hook only.
- Sell price = current price (no purchase-price memory), per `docs/rules.md` example.
- No solver time limit: proven optimality (`mip_rel_gap=0`) is required; fine at the realistic size (~0.6 s for ~700 rows), would need revisiting for a multi-period model.
- `w_budget` is weak at the suggested ~3: one credit of price is worth ~0.13 points through the `/25` scaling, and it changed the chosen squad in only ~15% of random test universes. Calibrate before relying on it.
