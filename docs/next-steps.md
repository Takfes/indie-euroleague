# Next steps

Deferred work, off the critical path.

- **No `data/stage_XX` dataset-path registry yet.** `resolve_player_names.py` and
  `apply_player_name_verdicts.py` hardcode their `data/stage_01/player_name_crosswalk.csv` path,
  same as the existing fetchers hardcode their `raw_data` paths. Expected until a registry is
  built (tracked as a standing gap alongside the catalogue/graph generator, not a one-off here).
- **`euroleague_box_score.csv` has at least 2 known cases of one real player under two different
  `player_id`s across seasons** (e.g. Lucas Mari: `P011974`/E2022 vs `P010754`/E2023, same club —
  likely an accent-spelling-driven re-mint upstream; also Marko Simonovic, not in the current price
  snapshot). Not fixable by the name-resolution pipeline; worth knowing about for any future
  box-score analysis that assumes one `player_id` per player.

## DVC pipeline initiative (specs: `spec-header-format.md`, `spec-registry.md`, `spec-catalogue-renderer.md`, `spec-graph-renderer.md`, `spec-dvc-core.md`, `spec-dvc-wrapper.md`, `spec-html-viewer.md`, `spec-entity-verdicts.md`)

- **Control panel.** Local server (`uv run eupy panel`, 127.0.0.1 only) serving the HTML data map
  with run buttons per pipeline (`dvc repro <pipeline>`, log streaming, fresh/stale colouring from
  `dvc status`). Human-gated steps would show "waiting for verdicts" instead of a button. Deferred
  until the static viewer proves useful; a terminal `eupy run <pipeline>` covers most of the value.
- **Scripts resolve paths through the registry.** Scripts keep hardcoding their `data/...` paths;
  the registry only lints declared paths against computed stages. Migrating scripts to
  `registry.path("<dataset>")` would make a stage renumber a pure re-run (no file edits).

## Squad optimizer (`src/eupy/optimize/optimize_squad.py`, spec: `specs/spec-squad-optimizer.md`)

- **`TURN1` / `TURN2` are built but unused.** Real rounds can have a T3 (up to 3 game dates seen in
  `schedule_E2026.csv`), which sits in neither set. Superseded by the general `turn_i < turn_j`
  pairing in the option-value attempt below (not tied to exactly two named turns) — extend or drop
  once that work resumes.
- **Option value — mechanics confirmed, formulation designed, implementation attempted and
  reverted on a performance blow-up.** Full design in `specs/spec-optimizer-option-value.md`
  (status: deferred, not implemented — kept as the starting point, not as ready-to-build).
  Summary for whoever picks this up next:
  - *Confirmed mechanics (both rules questions from the previous round of this deferral are now
    answered).* Two independent between-turn option types: (1) **bench↔active swap** — a T1 active
    player (starter or 6th man, both 100%) can be swapped for a not-yet-played bench player (50%);
    value is a put on the T1 player's score, strike = the bench player's mean, notional ½. (2)
    **captain move** — the captain bonus can move to any not-yet-played starter (must remain one of
    the starting five); same put shape, notional 1 (bigger, since the full ×2 bonus relocates, not
    just a 50%→100% swing). Monte Carlo-verified formulas and reference values are in the spec.
  - *Why it's not shipped.* The natural MILP encoding — a continuous assignment variable per
    `(i, j)` pair with `turn_i < turn_j`, bounded per-pair (`a[i,j] <= y_active[i]`, `a[i,j] <=
    y_bench[j]`, plus separate at-most-one-use constraints) — produces `O(n²)` variables and
    constraint rows. At the ~700-candidate realistic-size benchmark this was ~115k pairs, ~460k
    constraint rows, and HiGHS did not finish solving in 10+ minutes (killed, not viable for a
    tool meant to run once per round).
  - *A promising alternative, not yet built.* Replacing the per-pair bounds with one aggregated sum
    per player — `sum_j a[i,j] <= y_active[i]`, `sum_i a[i,j] <= y_bench[j]` (and the equivalent
    pair for the captain-move `z[i,k]`) — is mathematically equivalent, not an approximation: with
    binary `y`/`c` capacities on both sides and non-negative objective coefficients (put values are
    never negative), this is a bipartite transportation/assignment LP, and that polytope is totally
    unimodular, so the LP optimum is automatically integral with no separate at-most-one
    constraints needed. Measured on the same ~700-row scale: ~1.4k constraint rows, solves to proven
    optimality in ~47s. Start here next time, with the realistic-size pass criterion raised from
    30s to ~60s (this cost is permanent once shipped — the pair machinery is built regardless of
    whether `pir_std` is populated, so every future run pays it, not just a stress test).
    *Exact repro parameters* (the scratch scripts themselves lived in a background job's tmp dir
    and are gone): synthetic 680-row universe, seed 7, `turn` in `{1, 2}`, `pir_std` drawn
    `U(2, 10)` per row, `cash=1.5`, `max_trades=4`, `credit_value=3.0` — spec-shaped model: 115,466
    pairs, ~460k constraint rows, build 1.7s, solve killed after 10+ min; aggregated model: same
    115,466 pairs, ~1.4k constraint rows, solved in 47.2s to objective 241.66 (active 228.20 +
    option 8.22 + growth).
  - *A lossy fallback, only if 47s ever isn't enough at larger scale.* Top-K pruning (keep only each
    donor's top-K receivers by value) — faster, but changes answers, so it's a scope decision for
    whoever revisits this, not a default.
  - *A bug in the deferred spec, for whoever fixes it up before building.* The spec's Report section
    claims "`pir_std` absent means `option_value` is 0.0 and the term is inert" — false. At `sd=0`
    the put value's deterministic limit is `notional * max(strike − mean, 0)`, which the starting
    formation's constraints can force positive even with zero spread data. Keep the formula (it's
    correct and tested), fix the wording, and scope the brute-force equivalence test to all-`turn=1`
    universes where the terms are genuinely inert.
  - *Accepted, still-standing approximation.* The two option types are modelled as independent
    additive terms; a player can be valued as both a bench-swap donor and a captain-move donor in
    one solve, which the real game's turn sequencing can't actually deliver simultaneously.
    Confirmed acceptable for now — fixing it needs per-turn state (a scenario/two-stage model).
  - *Validate first, once built.* A Monte Carlo round simulator should show the chosen approach
    beats the no-option baseline before it goes into the objective for real.
