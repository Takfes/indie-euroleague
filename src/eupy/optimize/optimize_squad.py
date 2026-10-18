#!/usr/bin/env python3
"""Pick the optimal single-round EuroLeague Fantasy (Classic mode) squad with a Pyomo MILP.

Reads `data/stage_04/optimizer_input.xlsx` (one row per player candidate for the upcoming round),
chooses the 10 players (4 G / 4 F / 2 C), the starting five, the 6th man, the bench and the captain
that maximise the objective below, subject to the Classic-mode rules in `docs/rules.md`: budget,
trade limit, squad composition, max 6 players per club, the five legal starting formations and an
optional minimum number of squad players per turn. The head coach is not modelled. Myopic: one
round, no look-ahead. Method and parameters in plain words: `docs/optimizer.md`.

Objective -- one shared, configurable value per player, then the game's role weights on top:

    score_i  = target_i + risk_adjustment_i                  (one of the modes below)
    growth_i = credit_value * (target_i - 1.1 * price_i) / 25   (expected credit growth, in points)
    maximise   sum over starters, 6th man, captain bonus of score_i  +  0.5 * sum over bench of score_i
               + sum over the squad of growth_i

    objective_mode   risk_adjustment_i
    vanilla          0
    robustness       - robustness_lambda * std_i
    upside           + upside_beta * max(0, p90_i - target_i)
    downside         - downside_gamma * max(0, target_i - p10_i)

Only the selected mode's parameter and workbook column are used (and required). The growth formula
assumes `target` is on the PIR scale; with another target keep `credit_value` at 0.

All run parameters live in the `[optimization]` section of `config/config.toml` (`target`,
`objective_mode`, `robustness_lambda`, `upside_beta`, `downside_gamma`, `credit_value`, `cash`,
`max_trades`, `unlimited_trades`, `min_players_per_turn`, `solver`, `solver_options`); defaults are in
`eupy.config.defaults.Optimization`. There are no CLI flags: edit the config, then run

    uv run python -m eupy.optimize.optimize_squad        (or: make optimize, which runs scripts/optimize.py)

Input contract -- first sheet of the workbook, header row, extra columns ignored. The workbook's
columns are mapped to what the model needs in ONE place, `COLUMNS` below (plus `target` in the
config); from `model_frame` on, the script works with the model's own names and never with workbook
column names:

    model name      type               required  meaning                                  mapped from
    player          str                yes       unique key and display name              COLUMNS["player"]
    team            str                yes       club code, used for the 6-per-club cap   COLUMNS["team"]
    position        G / F / C          yes       playing position                         COLUMNS["position"]
    turn            int >= 1           yes       turn of the round the team plays in      COLUMNS["turn"]
    price           float > 0          yes       current quotation in credits             COLUMNS["price"]
    in_prev_roster  0 / 1              yes       1 if currently in the user's team        COLUMNS["in_prev_roster"]
    target          float              yes       expected PIR, the value to maximise      config `target`
    std             float              robustness  standard deviation of the outcome      COLUMNS["std"]
    p90             float              upside    90th percentile of the outcome           COLUMNS["p90"]
    p10             float              downside  10th percentile of the outcome           COLUMNS["p10"]

Hard validation (ValueError before any model is built): mapped columns present, no empty cell in any
column the run uses, no infinities, `player` unique and non-empty, known `position`, `price > 0`,
integer `turn >= 1`, `in_prev_roster` in {0, 1}, enough rows per position to fill the squad, and a
previous roster that is either empty or a full 4 G / 4 F / 2 C squad.

Output -- `data/stage_99/optimizer_output.xlsx`, two sheets:

- `selection`: every input row with its original columns plus `selected` (0/1), `role`
  (starter|sixth|bench|none), `action` (keep|buy|sell, blank when untouched), `is_captain` and the
  objective components `opt_target`, `opt_risk_adjustment`, `opt_score`, `opt_credit_growth` (credits)
  and `opt_growth_value` (points). Selected players first, then sold players, then the rest.
- `summary`: objective value and its split, the benefit (+) over keeping the current squad, the
  comparison with the vanilla squad, trades, cash, solver status and time, the parameters of the run.

`scripts/optimize.py` is the working copy of this module (same code, `REPO_ROOT` one level up); the two are
maintained together, like `scripts/dataprep.py` and `eupy.transform.build_eubook`.

Inputs: none
Sources:
  - data/stage_04/optimizer_input.xlsx: prepared by hand from the eubook (scripts/dataprep.py), not catalogued
Outputs: none
Final: false
Impure: false
Notes: Reads and writes outside the registry on purpose. The input is a manual preparation step with no producing
  script, and the output is a real file in data/stage_99/ (DVC must never own stage_99 paths), written
  to data/stage_99/optimizer_output.xlsx and overwritten each run. Run parameters are the [optimization]
  section of config/config.toml, which is not a catalogued dataset.
"""

from __future__ import annotations

import math
import numbers
import time
from dataclasses import dataclass, replace
from pathlib import Path

import pandas as pd
import pyomo.environ as pyo

from eupy.config import get_config
from eupy.config.defaults import Optimization

REPO_ROOT = Path(__file__).resolve().parents[3]
INPUT_PATH = REPO_ROOT / "data" / "stage_04" / "optimizer_input.xlsx"
OUTPUT_PATH = REPO_ROOT / "data" / "stage_99" / "optimizer_output.xlsx"
# Model name -> workbook column. The only place that knows the workbook's spelling (besides the
# config's `target`). `std`, `p90`, `p10` are needed only by the objective mode that uses them.
# NOTE: the input has no 10th-percentile column yet; `downside` mode needs one named here.
COLUMNS = {
    "player": "player",
    "team": "team",
    "position": "position",
    "turn": "turn",
    "price": "crd_last",
    "in_prev_roster": "in_prev_roster",
}
RISK_COLUMNS = {"std": "pir_std", "p90": "pir_p90", "p10": "pir_p10"}
# objective_mode -> the risk column it needs (vanilla needs none).
MODE_COLUMN = {"vanilla": None, "robustness": "std", "upside": "p90", "downside": "p10"}

# Squad composition (rules.md "Team composition").
SQUAD = {"G": 4, "F": 4, "C": 2}
PLAYER_POSITIONS = ("G", "F", "C")
MAX_PER_CLUB = 6
N_STARTERS = 5
N_SIXTH = 1
N_BENCH = 4
# Per-position (min, max) starters. With exactly 5 starters these bounds admit exactly the five
# legal formations 2-2-1, 1-2-2, 2-1-2, 1-3-1, 3-1-1 (proved by enumeration in the tests).
FORMATION_BOUNDS = {"G": (1, 3), "F": (1, 3), "C": (1, 2)}
BENCH_FACTOR = 0.5
# Community price formula (rules.md "Estimated price formula"): delta = (PIR - 1.1 * price) / 25.
PRICE_BREAKEVEN = 1.1
PRICE_DIVISOR = 25.0

REQUIRED_COLUMNS = ("player", "team", "position", "turn", "price", "target", "in_prev_roster")
NUMERIC_COLUMNS = ("turn", "price", "target", "in_prev_roster", *RISK_COLUMNS)
# Columns that enter the objective/budget as coefficients: inf would leave the solver at `unknown`.
FINITE_COLUMNS = ("price", "target", *RISK_COLUMNS)
OUTPUT_COLUMNS = [
    "player",
    "team",
    "position",
    "turn",
    "price",
    "target",
    "action",
    "role",
    "is_captain",
]
ROLE_ORDER = ("starter", "sixth", "bench", "none")


@dataclass(frozen=True)
class Solution:
    """Optimal squad plus the numbers the report needs.

    Attributes:
        table: One row per player in `previous roster U new squad` (`OUTPUT_COLUMNS`), sorted.
        objective: Optimal objective value.
        active_points: Player part of the objective (starters, 6th man, captain bonus, 50% bench).
        growth_credits: Squad's expected credit gain this round, raw and independent of `credit_value`.
        growth_term: `credit_value * growth_credits`.
        trades: Number of buys.
        cash_before: Credits in the bank before trading.
        cash_after: Credits in the bank after trading.
    """

    table: pd.DataFrame
    objective: float
    active_points: float
    growth_credits: float
    growth_term: float
    trades: int
    cash_before: float
    cash_after: float


def load_input(path: Path) -> pd.DataFrame:
    """Read the optimizer workbook (first sheet), untouched.

    Args:
        path: Excel file path.

    Returns:
        The table as in the workbook; `model_frame` maps it, `validate_input` checks it.

    Raises:
        FileNotFoundError: If `path` does not exist.
    """
    if not path.is_file():
        raise FileNotFoundError(
            f"Optimizer input {path} not found. Build the workbook (stage_04/optimizer_input.xlsx) first; "
            f"see the input contract in scripts/optimize.py."
        )
    return pd.read_excel(path, sheet_name=0)


def model_frame(df: pd.DataFrame, cfg: Optimization) -> pd.DataFrame:
    """Map the workbook table to the model's columns (`COLUMNS`, the config's `target`, the mode's risk column).

    Args:
        df: Table as read from the workbook; left unchanged.
        cfg: Run parameters; `target` names the value column, `objective_mode` the risk column needed.

    Returns:
        A new table with the model names (`REQUIRED_COLUMNS`, plus `std`/`p90`/`p10` if the mode uses one).

    Raises:
        ValueError: If the mode is unknown or a mapped workbook column is missing.
    """
    if cfg.objective_mode not in MODE_COLUMN:
        raise ValueError(
            f"objective_mode must be one of {sorted(MODE_COLUMN)}, got {cfg.objective_mode!r}. "
            f"Fix [optimization] objective_mode in config/config.toml."
        )
    mapping = {**COLUMNS, "target": cfg.target}
    risk = MODE_COLUMN[cfg.objective_mode]
    if risk:
        mapping[risk] = RISK_COLUMNS[risk]
    missing = {role: col for role, col in mapping.items() if col not in df.columns}
    if missing:
        raise ValueError(
            f"Input workbook lacks column(s) {missing} (model name: workbook column). Fix COLUMNS / RISK_COLUMNS "
            f"in scripts/optimize.py, [optimization] target in config/config.toml, or add the column to the "
            f"workbook. Columns present: {list(df.columns)}"
        )
    empty = {col: df.loc[df[col].isna(), COLUMNS["player"]].astype(str).tolist() for col in mapping.values()}
    empty = {col: players for col, players in empty.items() if players}
    if empty:
        detail = "; ".join(f"{col}: {len(p)} row(s), e.g. {p[:3]}" for col, p in empty.items())
        raise ValueError(
            f"Empty cells in workbook column(s) used by this run ({detail}). Every candidate needs a value: fill "
            f"them in the workbook, or pick an objective_mode / target that does not use the column."
        )
    return pd.DataFrame({role: df[col] for role, col in mapping.items()})


def _fail(what: str, why: str, fix: str) -> ValueError:
    """Build a ValueError whose message says what is wrong, why it matters and what to do."""
    return ValueError(f"Invalid optimizer input: {what}. {why}. {fix}.")


def validate_input(df: pd.DataFrame) -> None:  # noqa: C901 -- a flat list of independent checks
    """Enforce the input contract's hard validation rules; do nothing on a valid table.

    Args:
        df: Candidate table.

    Raises:
        ValueError: On the first violated rule, with what / why / how to fix.
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise _fail(f"missing required column(s) {missing}", "The model needs all of them", "Add the columns")
    # Every column of the frame is one the run uses (model_frame maps only those), so none may be empty.
    nan_cols = [c for c in df.columns if df[c].isna().any()]
    if nan_cols:
        raise _fail(f"NaN/empty cells in column(s) {nan_cols}", "Every candidate needs them", "Fill them")
    non_numeric = [c for c in NUMERIC_COLUMNS if c in df.columns and not pd.api.types.is_numeric_dtype(df[c])]
    if non_numeric:
        raise _fail(f"non-numeric column(s) {non_numeric}", "They enter the model as numbers", "Fix the values")
    infinite = [c for c in FINITE_COLUMNS if c in df.columns and df[c].isin([math.inf, -math.inf]).any()]
    if infinite:
        raise _fail(f"infinite values in column(s) {infinite}", "The solver needs finite numbers", "Fix the values")
    blank = df["player"].astype(str).str.strip() == ""
    if blank.any():
        raise _fail("empty player(s)", "player is the unique row key", "Give every candidate a non-empty player")
    dupes = sorted(df.loc[df["player"].duplicated(), "player"].astype(str).unique())
    if dupes:
        raise _fail(f"duplicate player(s) {dupes}", "player is the row key", "Keep one row per candidate")
    bad_pos = sorted(set(df["position"].astype(str)) - set(SQUAD))
    if bad_pos:
        raise _fail(f"unknown position(s) {bad_pos}", f"Allowed: {sorted(SQUAD)}", "Map them to G/F/C")
    if (df["price"] <= 0).any():
        players = df.loc[df["price"] <= 0, "player"].tolist()
        raise _fail(f"price <= 0 for player(s) {players}", "Prices are positive credits", "Fix the prices")
    bad_turn = (df["turn"] < 1) | (df["turn"] != df["turn"].round())
    if bad_turn.any():
        players = df.loc[bad_turn, "player"].tolist()
        raise _fail(f"turn is not an integer >= 1 for player(s) {players}", "Turns are T1, T2, ...", "Fix the turns")
    if not df["in_prev_roster"].isin([0, 1]).all():
        raise _fail("in_prev_roster has values outside {0, 1}", "It is a membership flag", "Use 0 or 1")
    counts = df["position"].value_counts()
    short = {pos: int(counts.get(pos, 0)) for pos, need in SQUAD.items() if counts.get(pos, 0) < need}
    if short:
        raise _fail(
            f"too few candidates per position {short} (need {SQUAD})",
            "A full squad cannot be built",
            "Add candidates",
        )
    prev = df[df["in_prev_roster"] == 1]
    if len(prev) not in (0, sum(SQUAD.values())):
        raise _fail(
            f"in_prev_roster marks {len(prev)} rows",
            f"The previous roster is either empty (fresh team) or a full squad of {sum(SQUAD.values())}",
            "Mark all 10 current members, or none",
        )
    prev_counts = prev["position"].value_counts().to_dict()
    if len(prev) and prev_counts != SQUAD:
        raise _fail(
            f"previous roster composition {prev_counts} != {SQUAD}",
            "A current squad always has 4 G / 4 F / 2 C",
            "Fix in_prev_roster or position",
        )


def max_trades_of(cfg: Optimization) -> int | None:
    """The trade limit the model uses: None when `unlimited_trades` is set, else `cfg.max_trades`."""
    return None if cfg.unlimited_trades else cfg.max_trades


def _validate_params(cfg: Optimization) -> None:
    """Reject out-of-range parameters (cash >= 0, max_trades int >= 0, credit_value >= 0, turn minimums)."""
    if not (math.isfinite(cfg.cash) and cfg.cash >= 0):
        raise ValueError(
            f"cash must be a finite number >= 0 credits, got {cfg.cash}. Set [optimization] cash to the bank balance."
        )
    # bool is an Integral too; True/False as a trade limit is almost certainly a config bug.
    trades = max_trades_of(cfg)
    if trades is not None and (isinstance(trades, bool) or not isinstance(trades, numbers.Integral) or trades < 0):
        raise ValueError(f"max_trades must be an int >= 0, got {trades!r}. Fix [optimization] max_trades.")
    if not (math.isfinite(cfg.credit_value) and cfg.credit_value >= 0):
        raise ValueError(
            f"credit_value must be a finite number >= 0, got {cfg.credit_value}. Use 0.0 to ignore price growth."
        )
    for name in ("robustness_lambda", "upside_beta", "downside_gamma"):
        value = getattr(cfg, name)
        if not (math.isfinite(value) and value >= 0):
            raise ValueError(f"{name} must be a finite number >= 0, got {value}. Fix [optimization] {name}.")
    mins = cfg.min_players_per_turn
    if any(isinstance(k, bool) or not isinstance(k, numbers.Integral) or k < 0 for k in mins):
        raise ValueError(
            f"min_players_per_turn must be a list of ints >= 0 (entry n = turn n), got {mins!r}. "
            f"Fix [optimization] min_players_per_turn."
        )
    if sum(mins) > sum(SQUAD.values()):
        raise ValueError(
            f"min_players_per_turn {mins} asks for {sum(mins)} players, but a squad has {sum(SQUAD.values())}. "
            f"Lower the minimums."
        )


def _validate_turn_minimums(df: pd.DataFrame, cfg: Optimization) -> None:
    """Fail early when a turn has fewer candidates than `min_players_per_turn` asks for."""
    available = df["turn"].value_counts()
    short = {
        t: (int(available.get(t, 0)), k)
        for t, k in enumerate(cfg.min_players_per_turn, start=1)
        if available.get(t, 0) < k
    }
    if short:
        raise ValueError(
            f"min_players_per_turn needs more candidates than the input has per turn (turn: (have, need)) {short}. "
            f"Add candidates or lower [optimization] min_players_per_turn."
        )


def objective_components(df: pd.DataFrame, cfg: Optimization) -> pd.DataFrame:
    """Per-player objective components, one row per row of `df` (index kept).

    Args:
        df: Model frame (`model_frame` output).
        cfg: Run parameters.

    Returns:
        Columns `target`, `risk_adjustment`, `score` (= target + risk_adjustment), `credit_growth`
        (expected credits gained, price formula applied to `target`) and `growth_value`
        (= credit_value * credit_growth, in objective points). Only the selected mode's risk term is non-zero.
    """
    target = df["target"].astype(float)
    risk = pd.Series(0.0, index=df.index)
    if cfg.objective_mode == "robustness":
        risk = -cfg.robustness_lambda * df["std"].astype(float)
    elif cfg.objective_mode == "upside":
        risk = cfg.upside_beta * (df["p90"].astype(float) - target).clip(lower=0)
    elif cfg.objective_mode == "downside":
        risk = -cfg.downside_gamma * (target - df["p10"].astype(float)).clip(lower=0)
    growth = (target - PRICE_BREAKEVEN * df["price"].astype(float)) / PRICE_DIVISOR
    return pd.DataFrame({
        "target": target,
        "risk_adjustment": risk,
        "score": target + risk,
        "credit_growth": growth,
        "growth_value": cfg.credit_value * growth,
    })


def _prepare(df: pd.DataFrame, cfg: Optimization) -> pd.DataFrame:
    """Keep the model columns, add `score` and `credit_growth`, sort by `player` (row order must not matter)."""
    out = df[list(REQUIRED_COLUMNS)].copy()
    comps = objective_components(df, cfg)
    out["score"] = comps["score"]
    out["credit_growth"] = comps["credit_growth"]
    out["turn"] = out["turn"].astype(int)
    out["in_prev_roster"] = out["in_prev_roster"].astype(int)
    return out.sort_values("player", kind="stable").reset_index(drop=True)


def build_model(df: pd.DataFrame, cfg: Optimization) -> pyo.ConcreteModel:
    """Build the single-round squad MILP. Pure: no I/O, no solving.

    Args:
        df: Prepared candidate table (`_prepare` output); row position = model index.
        cfg: Run parameters (`cash`, trade limit, `credit_value`, `min_players_per_turn`).

    Returns:
        The Pyomo model (maximisation).
    """
    cash, max_trades, credit_value = cfg.cash, max_trades_of(cfg), cfg.credit_value
    m = pyo.ConcreteModel(name="squad_optimizer")
    rows = list(range(len(df)))
    pos = df["position"].tolist()
    price = df["price"].astype(float).tolist()
    prev = df["in_prev_roster"].tolist()
    score = df["score"].astype(float).tolist()
    growth = df["credit_growth"].astype(float).tolist()
    team = df["team"].tolist()
    turn = df["turn"].tolist()

    # ---- Sets
    m.I = pyo.Set(initialize=rows, ordered=True)
    m.G = pyo.Set(initialize=[i for i in rows if pos[i] == "G"], ordered=True)
    m.F = pyo.Set(initialize=[i for i in rows if pos[i] == "F"], ordered=True)
    m.C = pyo.Set(initialize=[i for i in rows if pos[i] == "C"], ordered=True)
    teams = sorted({team[i] for i in m.I})
    m.TEAMS = pyo.Set(initialize=teams, ordered=True)
    by_pos = {"G": m.G, "F": m.F, "C": m.C}

    # ---- Variables
    m.x = pyo.Var(m.I, domain=pyo.Binary)  # in the squad after trading
    m.s = pyo.Var(m.I, domain=pyo.Binary)  # sold
    m.b = pyo.Var(m.I, domain=pyo.Binary)  # bought
    m.y_start = pyo.Var(m.I, domain=pyo.Binary)
    m.y_6th = pyo.Var(m.I, domain=pyo.Binary)
    m.y_bench = pyo.Var(m.I, domain=pyo.Binary)
    m.c = pyo.Var(m.I, domain=pyo.Binary)  # captain

    # 1. Transition: the new squad is the old one minus sales plus purchases.
    m.transition = pyo.Constraint(m.I, rule=lambda m, i: m.x[i] == prev[i] - m.s[i] + m.b[i])
    # 2. Valid actions: sell only current members, buy only non-members. Stops a wasted
    #    "sell and re-buy" of the same player (s = b = 1) from eating a trade.
    m.sell_only_owned = pyo.Constraint(m.I, rule=lambda m, i: m.s[i] <= prev[i])
    m.buy_only_new = pyo.Constraint(m.I, rule=lambda m, i: m.b[i] <= 1 - prev[i])
    # 3. Trade limit: at most K buys per round (rules.md "Trades").
    if max_trades is not None:
        m.trade_limit = pyo.Constraint(expr=sum(m.b[i] for i in m.I) <= max_trades)
    # 4. Budget: purchases are paid from the bank plus the proceeds of sales (at current price).
    m.budget = pyo.Constraint(expr=sum(price[i] * m.b[i] for i in m.I) <= cash + sum(price[i] * m.s[i] for i in m.I))
    # 5. Composition: exactly 4 G / 4 F / 2 C.
    m.composition = pyo.Constraint(list(SQUAD), rule=lambda m, p: sum(m.x[i] for i in by_pos[p]) == SQUAD[p])
    # 6. Club cap: at most 6 players from one EuroLeague club.
    m.club_cap = pyo.Constraint(m.TEAMS, rule=lambda m, t: sum(m.x[i] for i in m.I if team[i] == t) <= MAX_PER_CLUB)
    # 6b. Turn distribution: at least min_players_per_turn[t - 1] squad players whose team plays in turn t.
    turn_mins = {t: k for t, k in enumerate(cfg.min_players_per_turn, start=1) if k > 0}
    m.TURNS = pyo.Set(initialize=sorted(turn_mins), ordered=True)
    m.turn_min = pyo.Constraint(m.TURNS, rule=lambda m, t: sum(m.x[i] for i in m.I if turn[i] == t) >= turn_mins[t])
    # 7. Roles: every squad player gets exactly one of starter / 6th man / bench; non-members none.
    m.one_role = pyo.Constraint(m.I, rule=lambda m, i: m.x[i] == m.y_start[i] + m.y_6th[i] + m.y_bench[i])
    m.n_start = pyo.Constraint(expr=sum(m.y_start[i] for i in m.I) == N_STARTERS)
    m.n_6th = pyo.Constraint(expr=sum(m.y_6th[i] for i in m.I) == N_SIXTH)
    m.n_bench = pyo.Constraint(expr=sum(m.y_bench[i] for i in m.I) == N_BENCH)
    # 8. Formation: per-position starter bounds; with 5 starters these are exactly the legal formations.
    m.formation_min = pyo.Constraint(
        PLAYER_POSITIONS, rule=lambda m, p: sum(m.y_start[i] for i in by_pos[p]) >= FORMATION_BOUNDS[p][0]
    )
    m.formation_max = pyo.Constraint(
        PLAYER_POSITIONS, rule=lambda m, p: sum(m.y_start[i] for i in by_pos[p]) <= FORMATION_BOUNDS[p][1]
    )
    # 9. Captain: exactly one, and he must be in the starting five.
    m.one_captain = pyo.Constraint(expr=sum(m.c[i] for i in m.I) == 1)
    m.captain_starts = pyo.Constraint(m.I, rule=lambda m, i: m.c[i] <= m.y_start[i])

    # ---- Objective pieces (kept as expressions so the report can split the optimum).
    # Active points: starters and 6th man count 100% of their score, the captain counts a second
    # time, bench players count 50%.
    m.active_points = pyo.Expression(
        expr=sum(score[i] * (m.y_start[i] + m.y_6th[i] + m.c[i]) + BENCH_FACTOR * score[i] * m.y_bench[i] for i in m.I)
    )
    # Expected capital growth of the whole squad, in credits -- raw, independent of credit_value.
    m.growth_credits = pyo.Expression(expr=sum(growth[i] * m.x[i] for i in m.I))
    # Same, in points via the credit_value rate.
    m.growth_term = pyo.Expression(expr=credit_value * m.growth_credits)
    m.objective = pyo.Objective(expr=m.active_points + m.growth_term, sense=pyo.maximize)
    return m


def solve(m: pyo.ConcreteModel, cfg: Optimization) -> None:
    """Solve `m` in place with HiGHS and load the optimal values into its variables.

    Args:
        m: Model from `build_model`.
        cfg: Run parameters; `solver` and `solver_options` are used here.

    Raises:
        RuntimeError: If the solver does not prove optimality (e.g. infeasible budget/trade limit).
    """
    opt = pyo.SolverFactory(cfg.solver)
    # appsi_highs takes its options as `highs_options`; other Pyomo solvers use the generic `options`.
    if cfg.solver == "appsi_highs":
        opt.highs_options = dict(cfg.solver_options)
    else:
        opt.options.update(cfg.solver_options)
    results = opt.solve(m, load_solutions=False)
    tc = results.solver.termination_condition
    if tc != pyo.TerminationCondition.optimal:
        hint = (
            " No legal squad fits the constraints (e.g. a fresh team with max_trades < 10, too little cash, "
            "or too few candidates in a turn for min_players_per_turn); check [optimization] in config/config.toml."
            if tc == pyo.TerminationCondition.infeasible
            else ""
        )
        raise RuntimeError(f"Squad optimizer did not reach a proven optimum: termination condition {tc}.{hint}")
    opt.load_vars()


def extract_solution(df: pd.DataFrame, m: pyo.ConcreteModel, cash: float) -> Solution:
    """Turn solved variable values into the output table and report numbers.

    Args:
        df: The prepared table the model was built from.
        m: Solved model.
        cash: Credits in the bank before trading.

    Returns:
        The `Solution`.
    """

    def on(var: pyo.Var, i: int) -> bool:
        return var[i].value is not None and var[i].value > 0.5

    rows = []
    for i in m.I:
        x, s, b = on(m.x, i), on(m.s, i), on(m.b, i)
        if not (x or s):
            continue
        role = "starter" if on(m.y_start, i) else "sixth" if on(m.y_6th, i) else "bench" if x else "none"
        captain = int(on(m.c, i))
        action = "sell" if s else "buy" if b else "keep"
        rows.append({**df.loc[i, OUTPUT_COLUMNS[:6]].to_dict(), "action": action, "role": role, "is_captain": captain})

    table = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    table["_role_rank"] = table["role"].map(ROLE_ORDER.index)
    table = table.sort_values(["_role_rank", "player"], kind="stable").drop(columns="_role_rank")
    table = table.reset_index(drop=True)

    bought = table.loc[table["action"] == "buy", "price"].sum()
    sold = table.loc[table["action"] == "sell", "price"].sum()
    return Solution(
        table=table,
        objective=float(pyo.value(m.objective)),
        active_points=float(pyo.value(m.active_points)),
        growth_credits=float(pyo.value(m.growth_credits)),
        growth_term=float(pyo.value(m.growth_term)),
        trades=int((table["action"] == "buy").sum()),
        cash_before=cash,
        cash_after=float(cash + sold - bought),
    )


def optimize_squad(df: pd.DataFrame, cfg: Optimization) -> Solution:
    """Validate, build, solve and extract in one call.

    Args:
        df: Candidate table as read from the workbook (input contract above).
        cfg: Run parameters.

    Returns:
        The optimal `Solution`.

    Raises:
        ValueError: If the table or a parameter violates the contract.
        RuntimeError: If the solver does not prove optimality.
    """
    frame = model_frame(df, cfg)
    validate_input(frame)
    _validate_params(cfg)
    _validate_turn_minimums(frame, cfg)
    prepared = _prepare(frame, cfg)
    m = build_model(prepared, cfg)
    solve(m, cfg)
    return extract_solution(prepared, m, cfg.cash)


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


def explain_credit_value(df: pd.DataFrame, cfg: Optimization, actual: Solution) -> CreditValueReport:
    """Measure what `credit_value` is buying: a shadow price and a pure-PIR baseline, via re-solves.

    Args:
        df: Candidate table obeying the input contract (the same one `actual` was solved from).
        cfg: The parameters `actual` was solved with.
        actual: The already-solved squad to compare against.

    Returns:
        The `CreditValueReport`. When `credit_value == 0`, `actual` already *is* the pure-PIR
        baseline, so the baseline re-solve is skipped (one fewer solve than the general case).
    """
    baseline = actual if cfg.credit_value == 0 else optimize_squad(df, replace(cfg, credit_value=0.0))
    shadow = optimize_squad(df, replace(cfg, cash=cfg.cash + 1, credit_value=0.0))
    return CreditValueReport(
        lambda_shadow=shadow.active_points - baseline.active_points,
        baseline_active=baseline.active_points,
        baseline_growth_credits=baseline.growth_credits,
    )


def current_squad_objective(df: pd.DataFrame, cfg: Optimization) -> float | None:
    """Objective of the best line-up that keeps the current squad (no trades), or None if there is none.

    Args:
        df: Candidate table obeying the input contract.
        cfg: Run parameters; the trade limit is forced to 0.

    Returns:
        The no-trade optimum, or None when no squad is marked `in_prev_roster` (fresh team) or the
        current squad cannot satisfy the constraints (e.g. `min_players_per_turn`).
    """
    if not (df[COLUMNS["in_prev_roster"]] == 1).any():
        return None
    try:
        return optimize_squad(df, replace(cfg, max_trades=0, unlimited_trades=False)).objective
    except RuntimeError:  # infeasible with the current squad
        return None


def format_report(sol: Solution, max_trades: int | None, benefit: float | None = None) -> str:
    """Render the terse stdout report.

    Args:
        sol: Solved squad.
        max_trades: The trade limit used (None = unlimited).
        benefit: Objective gain over keeping the current squad, or None when not available.

    Returns:
        Multi-line report text.
    """
    t = sol.table

    def line(r: pd.Series) -> str:
        cap = " (C)" if r["is_captain"] else ""
        return f"  {r['position']:<2} {r['player']}{cap} [{r['team']}, T{r['turn']}] {r['price']:.1f}cr target {r['target']:.1f}"

    limit = "unlimited" if max_trades is None else str(max_trades)
    out = [
        f"Objective {sol.objective:.2f} = active {sol.active_points:.2f} + growth {sol.growth_term:.2f}",
        f"Trades {sol.trades} / {limit}; cash {sol.cash_before:.2f} -> {sol.cash_after:.2f}",
    ]
    if benefit is not None:
        out.append(f"Benefit over the current squad: {benefit:+.2f}")
    for title, role in (("Starting five:", "starter"), ("6th man:", "sixth"), ("Bench:", "bench")):
        out.append(title)
        out.extend(line(r) for _, r in t[t["role"] == role].iterrows())
    moves = t[t["action"] != "keep"]
    if len(moves):
        out.append("Trades:")
        out.extend(
            f"  {r['action']:<4} {r['player']} ({r['position']}, {r['price']:.1f}cr)" for _, r in moves.iterrows()
        )
    return "\n".join(out)


def format_credit_value_report(report: CreditValueReport, actual: Solution) -> str:
    """Render the credit_value exchange-rate report: the measured shadow price and its trade-off.

    Args:
        report: Output of `explain_credit_value`.
        actual: The squad actually solved for, at the run's `credit_value`.

    Returns:
        Two-line report text.
    """
    lines = [f"Credit value: {report.lambda_shadow:.2f} PIR/credit measured (re-solved at cash+1, credit_value=0)"]
    if actual.active_points != report.baseline_active:
        lines.append(
            f"Growth trade-off: gave up {report.baseline_active - actual.active_points:.2f} PIR for "
            f"+{actual.growth_credits - report.baseline_growth_credits:.3f} credits"
        )
    else:
        lines.append("credit_value did not change the squad at this cash/trade limit")
    return "\n".join(lines)


ROLE_RANK = {role: n for n, role in enumerate(ROLE_ORDER)}


COMPONENT_COLUMNS = {
    "target": "opt_target",
    "risk_adjustment": "opt_risk_adjustment",
    "score": "opt_score",
    "credit_growth": "opt_credit_growth",
    "growth_value": "opt_growth_value",
}


def selection_sheet(raw: pd.DataFrame, sol: Solution, cfg: Optimization) -> pd.DataFrame:
    """The input table plus the solution and each player's objective components.

    Args:
        raw: The input as read (all original columns).
        sol: The solved squad.
        cfg: Run parameters (for the components).

    Returns:
        One row per input row: the original columns, `selected`, `role`, `action`, `is_captain`, then
        the `opt_*` components. Order: starters, 6th man, bench, sold players, everyone else; `player`
        within a group.
    """
    chosen = sol.table[["player", "action", "role", "is_captain"]]
    player = COLUMNS["player"]
    out = raw.merge(chosen.rename(columns={"player": player}), on=player, how="left")
    out["role"] = out["role"].fillna("none")
    out["action"] = out["action"].fillna("")
    out["is_captain"] = out["is_captain"].fillna(0).astype(int)
    out["selected"] = out["role"].isin(["starter", "sixth", "bench"]).astype(int)
    comps = objective_components(model_frame(raw, cfg), cfg).rename(columns=COMPONENT_COLUMNS)
    out = out.join(comps)  # same row order as raw: merge(how=left) keeps the left index
    # Sold players carry role "none" and action "sell": keep them right under the squad.
    out["_rank"] = out["role"].map(ROLE_RANK) - (out["action"] == "sell") * 0.5
    out = out.sort_values(["_rank", player], kind="stable").drop(columns="_rank").reset_index(drop=True)
    return out[[*raw.columns, "selected", "role", "action", "is_captain", *COMPONENT_COLUMNS.values()]]


def lineup_sum(sheet: pd.DataFrame, column: str) -> float:
    """Sum of a `selection` column over the line-up with the game's weights (captain x2, bench x0.5)."""
    weight = sheet["role"].map({"starter": 1.0, "sixth": 1.0, "bench": BENCH_FACTOR}).fillna(0.0) + sheet["is_captain"]
    return float((weight * sheet[column]).sum())


def vanilla_config(cfg: Optimization) -> Optimization | None:
    """The same run with the plain expected-PIR objective, or None when `cfg` already is that."""
    if cfg.objective_mode == "vanilla" and cfg.credit_value == 0:
        return None
    return replace(cfg, objective_mode="vanilla", credit_value=0.0)


def summary_sheet(
    sol: Solution,
    sheet: pd.DataFrame,
    cfg: Optimization,
    n_candidates: int,
    solve_seconds: float,
    baseline: float | None,
    cv_report: CreditValueReport,
    vanilla: pd.DataFrame | None,
) -> pd.DataFrame:
    """High-level optimizer output as `section | metric | value` rows.

    Args:
        sol: The solved squad.
        sheet: Its `selection_sheet`.
        cfg: Parameters of the run.
        n_candidates: Number of input rows.
        solve_seconds: Wall time of the main solve.
        baseline: Objective of keeping the current squad (`current_squad_objective`), or None.
        cv_report: Output of `explain_credit_value`.
        vanilla: `selection_sheet` of the vanilla-objective squad (`vanilla_config`), or None when the
            run is already vanilla.

    Returns:
        The summary table.
    """
    limit = max_trades_of(cfg)
    rows = [
        ("objective", "objective value", sol.objective),
        ("objective", "active points (starters, 6th man, captain bonus, bench)", sol.active_points),
        ("objective", "growth term (credit_value x expected credit growth)", sol.growth_term),
        ("objective", "expected credit growth of the squad", sol.growth_credits),
        ("objective", "objective keeping the current squad (no trades)", baseline if baseline is not None else "n/a"),
        (
            "objective",
            "benefit (+) over the current squad",
            sol.objective - baseline if baseline is not None else "n/a",
        ),
        ("objective", "PIR per extra credit of cash (shadow price)", cv_report.lambda_shadow),
        ("line-up", "expected target of the line-up (weighted)", lineup_sum(sheet, "opt_target")),
        ("line-up", "risk adjustment of the line-up (weighted)", lineup_sum(sheet, "opt_risk_adjustment")),
    ]
    if vanilla is not None:
        rows += [
            ("vs vanilla", "expected target of the vanilla line-up", lineup_sum(vanilla, "opt_target")),
            (
                "vs vanilla",
                "expected credit growth of the vanilla squad",
                float(vanilla.loc[vanilla["selected"] == 1, "opt_credit_growth"].sum()),
            ),
            (
                "vs vanilla",
                "players different from the vanilla squad",
                len(
                    set(sheet.loc[sheet["selected"] == 1, COLUMNS["player"]])
                    - set(vanilla.loc[vanilla["selected"] == 1, COLUMNS["player"]])
                ),
            ),
        ]
    rows += [
        ("squad", "trades used", sol.trades),
        ("squad", "trade limit", "unlimited" if limit is None else limit),
        ("squad", "cash before", sol.cash_before),
        ("squad", "cash after", sol.cash_after),
        ("solver", "candidates", n_candidates),
        ("solver", "solver", cfg.solver),
        ("solver", "status", "optimal"),  # anything else raises before we get here
        ("solver", "solve time (s)", round(solve_seconds, 3)),
        ("parameters", "target", cfg.target),
        ("parameters", "objective_mode", cfg.objective_mode),
        ("parameters", "robustness_lambda", cfg.robustness_lambda),
        ("parameters", "upside_beta", cfg.upside_beta),
        ("parameters", "downside_gamma", cfg.downside_gamma),
        ("parameters", "cash", cfg.cash),
        ("parameters", "credit_value", cfg.credit_value),
        ("parameters", "min_players_per_turn", str(list(cfg.min_players_per_turn))),
        ("parameters", "solver_options", str(dict(cfg.solver_options))),
    ]
    return pd.DataFrame(rows, columns=["section", "metric", "value"])


def write_output(selection: pd.DataFrame, summary: pd.DataFrame, path: Path) -> None:
    """Write the two-sheet output workbook, creating the parent directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path) as writer:
        selection.to_excel(writer, sheet_name="selection", index=False)
        summary.to_excel(writer, sheet_name="summary", index=False)


def main() -> None:
    """Read the config and the input workbook, solve, write the output workbook and print the report.

    Raises:
        FileNotFoundError: If the input workbook is missing.
        ValueError: If the config or the input violates its contract.
        RuntimeError: If the solver does not prove optimality.
    """
    cfg = get_config().optimization
    df = load_input(INPUT_PATH)
    started = time.perf_counter()
    sol = optimize_squad(df, cfg)
    elapsed = time.perf_counter() - started
    baseline = current_squad_objective(df, cfg)
    benefit = None if baseline is None else sol.objective - baseline
    print(format_report(sol, max_trades_of(cfg), benefit))
    cv_report = explain_credit_value(df, cfg, sol)
    print(format_credit_value_report(cv_report, sol))
    sheet = selection_sheet(df, sol, cfg)
    plain = vanilla_config(cfg)
    vanilla = None if plain is None else selection_sheet(df, optimize_squad(df, plain), plain)
    summary = summary_sheet(sol, sheet, cfg, len(df), elapsed, baseline, cv_report, vanilla)
    write_output(sheet, summary, OUTPUT_PATH)
    print(f"Solved {len(df)} candidates in {elapsed:.2f}s; wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
