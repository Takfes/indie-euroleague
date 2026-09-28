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

## Squad optimizer (`src/eupy/optimize/optimize_squad.py`, spec: `specs/spec-squad-optimizer.md`)

- **`TURN1` / `TURN2` are built but unused.** Kept per the strategy-doc discussion; they only earn
  their place once the option-value work below pairs T1 starters with T2 bench players. Real rounds
  can have a T3 (up to 3 game dates seen in `schedule_E2026.csv`), which sits in neither set.
  Extend or drop them when the option-value decision is made.
- **Option value is not modelled — `exp_pir_adj` is only a hook.**
  - *What the game gives you.* Between turns a field player can be swapped with a bench player who
    has not played yet; the swapped-out player keeps 50% (`docs/rules.md`). For a T1 starter with
    score `X` and a T2 bench player with score `Y`: no swap = `X + 0.5Y`, swap = `0.5X + Y`. The
    swap is decided knowing `X` but not `Y`, so you swap iff `X < E[Y]`, and the expected gain is
    `0.5 · (E[Y] − X)⁺`. Option value of a T1 starter backed by a T2 bench player is therefore a
    put on `X`: strike = the backup's mean `μ_Y`, notional ½. For `X ~ N(μ, σ²)`:
    `0.5 · [(μ_Y − μ)·Φ(d) + σ·φ(d)]`, `d = (μ_Y − μ)/σ`. Checked by Monte Carlo (2M draws):
    μ=12, σ=8, backup μ=10 → +1.14 PIR; σ=2 → +0.08. Higher σ on T1 is what buys value.
  - *Why the strategy-doc recipe is not the one to use.* It hardcodes τ = 10 as both the bench
    cutoff and the replacement score, drops the 50% haircut (a benched `X` still counts 0.5X and the
    backup only goes 50% → 100%), and gives the option to every T1 starter whether or not a T2
    bench player exists. It overstates: μ=12, σ=8 → +2.29 by its own formula (the doc prints +2.85)
    vs +1.14 under the rules; σ=2 → +0.17 vs +0.08. The cutoff is not really arbitrary — it is the
    backup's expected score, i.e. fixed by the lineup, not by a constant.
  - *Consequence for modelling.* The value belongs to a (T1 starter, T2 bench) pair, not to a
    player, so a per-player coefficient can only approximate it. Options, cheapest first:
    1. Per-player put with a fixed benchmark strike (e.g. mean of the T2 bench tier), written to
       `exp_pir_adj` upstream — no model change.
    2. Pairwise term: precompute `v_ij = 0.5·E[(μ_j − X_i)⁺]` for every T1 `i` / T2 `j`, add
       assignment vars `a_ij ≤ y_start_i`, `a_ij ≤ y_bench_j` (each `i` and `j` used at most once),
       objective `+ Σ v_ij·a_ij`. Still a MILP; conservative, since real recourse can re-match subs
       after seeing every T1 result.
    3. Scenario-based two-stage model (sample T1 outcomes, swap recourse per scenario): closest to
       the game, grows with the scenario count.
  - *New inputs.* A per-player spread (`pir_std`, or PIR p10/p50/p90 from the LightGBM plan in the
    strategy doc); not in the input contract yet.
  - *Rules to confirm before building.* (a) Swapping a starter with the 6th man: does the
    swapped-out starter drop to 50% or take the 100% 6th-man slot? If the latter the option is
    cheaper and larger and the bench is not the relevant backup. (b) The captain can be reassigned
    between turns to a starter who has not played: a T1 captain flop moved to a T2 starter is an
    extra ×2 option on the captain slot. (c) T3 rounds allow a second swap step.
  - *Validate first.* A Monte Carlo round simulator (as the strategy doc suggests) should show the
    chosen approach beats the no-option baseline before it goes into the objective.
