#!/usr/bin/env python3
"""Pick the optimal single-round EuroLeague Fantasy (Classic mode) squad with a Pyomo MILP.

Given one tidy table of candidates (players and head coaches) for the upcoming round plus three
scalars (`cash`, `max_trades`, `w_budget`), choose the 10 players + 1 head coach, the starting
five, the 6th man, the bench and the captain that maximise expected fantasy points (plus an
optional capital-growth term), subject to the Classic-mode rules in `docs/rules.md`: budget,
trade limit (coach included), squad composition 4 G / 4 F / 2 C / 1 HC, max 6 players per club
and the five legal starting formations. Solved with HiGHS (`appsi_highs`, 1 thread, fixed seed,
zero relative MIP gap) and must terminate `optimal`. Myopic: one round, no look-ahead; option
value is not modelled (`exp_pir_adj` is only a hook). Spec: `specs/spec-squad-optimizer.md`.

Input contract -- one row per candidate, players and head coaches in the same table. CSV, UTF-8,
header row, no index column. Extra columns are ignored. Column names are exact.

    column          type               required  meaning
    player_id       str / int          yes       unique key (same id as euroleague_fantasy_stats
                                                 players.csv / head_coaches.csv)
    name            str                yes       display only
    team            str                yes       club code (e.g. PAR, OLY), used for the 6-per-club cap
    position        G / F / C / HC     yes       HC = head coach
    turn            int >= 1           yes       turn of the round in which the player's team plays
    price           float > 0          yes       current quotation in credits (buy and sell price)
    exp_pir         float              yes       projected fantasy score this round (coach: projected
                                                 coach points); bench (x0.5), price growth, default score
    exp_pir_adj     float              no        option-adjusted score for starter / 6th man / captain
                                                 slots; missing column or NaN cell => exp_pir
    in_prev_roster  0 / 1              yes       1 if currently in the user's team (Round 1: all 0)

Hard validation (ValueError before any model is built): required columns present, no NaN in them,
unique `player_id`, known `position`, `price > 0`, integer `turn >= 1`, `in_prev_roster` in {0, 1},
enough rows per position to fill the squad, and a previous roster that is either empty or a full
4 G / 4 F / 2 C / 1 HC squad.

Scalars: `cash` (>= 0, credits in the bank before trading; Round 1: the whole budget, 100.0),
`max_trades` (int >= 0, or None = unlimited window / Round 1; standard 4), `w_budget` (>= 0,
credits -> points exchange rate for expected price growth; ~3.0 early season, 0.0 late).

Output: one row per player/coach in `previous roster U new squad`, columns `player_id, name, team,
position, turn, price, exp_pir, action (keep|buy|sell), role (starter|sixth|bench|coach|none),
is_captain (0/1)`, sorted by role then `player_id`. A terse report is printed to stdout.

Usage:
    python src/eupy/optimize/optimize_squad.py [--input PATH] [--cash 100.0]
        [--max-trades N | --unlimited-trades] [--w-budget 0.0] [--out PATH]

Inputs: optimizer_input -- data/raw_data/optimizer/optimizer_input.csv (raw; prepared manually for now).
Outputs: squad_solution -- data/stage_01/squad_solution.csv (overwritten each run).
Final: true -- the pipeline's end product (recommended squad); symlinked into data/stage_99/.
"""

from __future__ import annotations

import argparse
import math
import numbers
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import pyomo.environ as pyo

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_INPUT = REPO_ROOT / "data" / "raw_data" / "optimizer" / "optimizer_input.csv"
DEFAULT_OUT = REPO_ROOT / "data" / "stage_01" / "squad_solution.csv"

# Squad composition (rules.md "Team composition").
SQUAD = {"G": 4, "F": 4, "C": 2, "HC": 1}
PLAYER_POSITIONS = ("G", "F", "C")
COACH = "HC"
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

SOLVER = "appsi_highs"
# threads=1 + fixed seed => same input and solver version give the same solution (ties included).
# mip_rel_gap=0: HiGHS stops at a 0.01% gap by default; we want proven optimality.
SOLVER_OPTIONS = {"threads": 1, "random_seed": 42, "mip_rel_gap": 0.0}

REQUIRED_COLUMNS = ("player_id", "name", "team", "position", "turn", "price", "exp_pir", "in_prev_roster")
NUMERIC_COLUMNS = ("turn", "price", "exp_pir", "in_prev_roster")
# Columns that enter the objective/budget as coefficients: inf would leave the solver at `unknown`.
FINITE_COLUMNS = ("price", "exp_pir", "exp_pir_adj")
OUTPUT_COLUMNS = [
    "player_id",
    "name",
    "team",
    "position",
    "turn",
    "price",
    "exp_pir",
    "action",
    "role",
    "is_captain",
]
ROLE_ORDER = ("starter", "sixth", "bench", "coach", "none")


@dataclass(frozen=True)
class Solution:
    """Optimal squad plus the numbers the report needs.

    Attributes:
        table: One row per player in `previous roster U new squad` (`OUTPUT_COLUMNS`), sorted.
        objective: Optimal objective value.
        active_points: Player part of the objective (starters, 6th man, captain bonus, 50% bench).
        coach_points: Coach part of the objective.
        growth_term: `w_budget * sum(delta)` over the squad's players.
        trades: Number of buys (coach included).
        cash_before: Credits in the bank before trading.
        cash_after: Credits in the bank after trading.
    """

    table: pd.DataFrame
    objective: float
    active_points: float
    coach_points: float
    growth_term: float
    trades: int
    cash_before: float
    cash_after: float


def load_input(path: Path) -> pd.DataFrame:
    """Read the optimizer input CSV (see the input contract in the module docstring).

    Args:
        path: CSV file path.

    Returns:
        The raw table; validation is left to `validate_input`.

    Raises:
        FileNotFoundError: If `path` does not exist.
    """
    if not path.is_file():
        raise FileNotFoundError(
            f"Optimizer input {path} not found. Prepare it per the input contract in "
            f"src/eupy/optimize/optimize_squad.py (see tests/data/optimizer_input_sample.csv) or pass --input."
        )
    return pd.read_csv(path, encoding="utf-8")


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
    nan_cols = [c for c in REQUIRED_COLUMNS if df[c].isna().any()]
    if nan_cols:
        raise _fail(f"NaN/empty cells in required column(s) {nan_cols}", "Every candidate needs them", "Fill them")
    # exp_pir_adj is optional and may hold NaN cells, but when present it must be numeric.
    numeric = [*NUMERIC_COLUMNS, *(["exp_pir_adj"] if "exp_pir_adj" in df.columns else [])]
    non_numeric = [c for c in numeric if not pd.api.types.is_numeric_dtype(df[c])]
    if non_numeric:
        raise _fail(f"non-numeric column(s) {non_numeric}", "They enter the model as numbers", "Fix the values")
    infinite = [c for c in FINITE_COLUMNS if c in df.columns and df[c].isin([math.inf, -math.inf]).any()]
    if infinite:
        raise _fail(f"infinite values in column(s) {infinite}", "The solver needs finite numbers", "Fix the values")
    dupes = sorted(df.loc[df["player_id"].duplicated(), "player_id"].astype(str).unique())
    if dupes:
        raise _fail(f"duplicate player_id(s) {dupes}", "player_id is the row key", "Keep one row per candidate")
    bad_pos = sorted(set(df["position"].astype(str)) - set(SQUAD))
    if bad_pos:
        raise _fail(f"unknown position(s) {bad_pos}", f"Allowed: {sorted(SQUAD)}", "Map them to G/F/C/HC")
    if (df["price"] <= 0).any():
        ids = df.loc[df["price"] <= 0, "player_id"].tolist()
        raise _fail(f"price <= 0 for player_id(s) {ids}", "Prices are positive credits", "Fix the prices")
    bad_turn = (df["turn"] < 1) | (df["turn"] != df["turn"].round())
    if bad_turn.any():
        ids = df.loc[bad_turn, "player_id"].tolist()
        raise _fail(f"turn is not an integer >= 1 for player_id(s) {ids}", "Turns are T1, T2, ...", "Fix the turns")
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
            "Mark all 11 current members, or none",
        )
    prev_counts = prev["position"].value_counts().to_dict()
    if len(prev) and prev_counts != SQUAD:
        raise _fail(
            f"previous roster composition {prev_counts} != {SQUAD}",
            "A current squad always has 4 G / 4 F / 2 C / 1 HC",
            "Fix in_prev_roster or position",
        )


def _validate_scalars(cash: float, max_trades: int | None, w_budget: float) -> None:
    """Reject out-of-range scalars (spec: cash >= 0, max_trades int >= 0 or None, w_budget >= 0)."""
    if not (math.isfinite(cash) and cash >= 0):
        raise ValueError(
            f"cash must be a finite number >= 0 credits, got {cash}. Pass the bank balance before trading."
        )
    # bool is an Integral too; True/False as a trade limit is almost certainly a caller bug.
    if max_trades is not None and (
        isinstance(max_trades, bool) or not isinstance(max_trades, numbers.Integral) or max_trades < 0
    ):
        raise ValueError(f"max_trades must be an int >= 0 or None (unlimited), got {max_trades!r}.")
    if not (math.isfinite(w_budget) and w_budget >= 0):
        raise ValueError(f"w_budget must be a finite number >= 0, got {w_budget}. Use 0.0 to ignore price growth.")


def _prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Keep the contract columns, fill `exp_pir_adj` and sort by `player_id` (row order must not matter)."""
    out = df[list(REQUIRED_COLUMNS)].copy()
    adj = df["exp_pir_adj"] if "exp_pir_adj" in df.columns else pd.Series(float("nan"), index=df.index)
    out["exp_pir_adj"] = adj.fillna(df["exp_pir"]).astype(float)
    out["turn"] = out["turn"].astype(int)
    out["in_prev_roster"] = out["in_prev_roster"].astype(int)
    return out.sort_values("player_id", kind="stable").reset_index(drop=True)


def build_model(df: pd.DataFrame, cash: float, max_trades: int | None, w_budget: float) -> pyo.ConcreteModel:
    """Build the single-round squad MILP. Pure: no I/O, no solving.

    Args:
        df: Validated and prepared candidate table (`_prepare` output); row position = model index.
        cash: Credits in the bank before trading.
        max_trades: Max buys (coach included), or None for no limit.
        w_budget: Weight of the expected price-growth term.

    Returns:
        The Pyomo model (maximisation).
    """
    m = pyo.ConcreteModel(name="squad_optimizer")
    rows = list(range(len(df)))
    pos = df["position"].tolist()
    price = df["price"].astype(float).tolist()
    prev = df["in_prev_roster"].tolist()
    exp = df["exp_pir"].astype(float).tolist()
    adj = df["exp_pir_adj"].tolist()
    team = df["team"].tolist()
    turn = df["turn"].tolist()

    # ---- Sets
    m.I = pyo.Set(initialize=rows, ordered=True)
    m.G = pyo.Set(initialize=[i for i in rows if pos[i] == "G"], ordered=True)
    m.F = pyo.Set(initialize=[i for i in rows if pos[i] == "F"], ordered=True)
    m.C = pyo.Set(initialize=[i for i in rows if pos[i] == "C"], ordered=True)
    m.HC = pyo.Set(initialize=[i for i in rows if pos[i] == COACH], ordered=True)
    m.P = pyo.Set(initialize=[i for i in rows if pos[i] != COACH], ordered=True)
    # Turn subsets of the players (T3+ is in neither). No constraint uses them since the doc's
    # "T1 starters >= 3" rule was dropped (spec deviation 6); kept for inspection.
    m.TURN1 = pyo.Set(initialize=[i for i in m.P if turn[i] == 1], ordered=True)
    m.TURN2 = pyo.Set(initialize=[i for i in m.P if turn[i] == 2], ordered=True)
    teams = sorted({team[i] for i in m.P})
    m.TEAMS = pyo.Set(initialize=teams, ordered=True)
    by_pos = {"G": m.G, "F": m.F, "C": m.C, COACH: m.HC}

    # Expected price change per player (community formula); the coach has no growth term.
    delta = {i: (exp[i] - PRICE_BREAKEVEN * price[i]) / PRICE_DIVISOR for i in m.P}

    # ---- Variables
    m.x = pyo.Var(m.I, domain=pyo.Binary)  # in the squad after trading
    m.s = pyo.Var(m.I, domain=pyo.Binary)  # sold
    m.b = pyo.Var(m.I, domain=pyo.Binary)  # bought
    m.y_start = pyo.Var(m.P, domain=pyo.Binary)
    m.y_6th = pyo.Var(m.P, domain=pyo.Binary)
    m.y_bench = pyo.Var(m.P, domain=pyo.Binary)
    m.c = pyo.Var(m.P, domain=pyo.Binary)  # captain

    # 1. Transition: the new squad is the old one minus sales plus purchases.
    m.transition = pyo.Constraint(m.I, rule=lambda m, i: m.x[i] == prev[i] - m.s[i] + m.b[i])
    # 2. Valid actions: sell only current members, buy only non-members. Stops a wasted
    #    "sell and re-buy" of the same player (s = b = 1) from eating a trade.
    m.sell_only_owned = pyo.Constraint(m.I, rule=lambda m, i: m.s[i] <= prev[i])
    m.buy_only_new = pyo.Constraint(m.I, rule=lambda m, i: m.b[i] <= 1 - prev[i])
    # 3. Trade limit: at most K buys per round, the coach included (rules.md "Trades").
    if max_trades is not None:
        m.trade_limit = pyo.Constraint(expr=sum(m.b[i] for i in m.I) <= max_trades)
    # 4. Budget: purchases are paid from the bank plus the proceeds of sales (at current price).
    m.budget = pyo.Constraint(expr=sum(price[i] * m.b[i] for i in m.I) <= cash + sum(price[i] * m.s[i] for i in m.I))
    # 5. Composition: exactly 4 G / 4 F / 2 C / 1 HC.
    m.composition = pyo.Constraint(list(SQUAD), rule=lambda m, p: sum(m.x[i] for i in by_pos[p]) == SQUAD[p])
    # 6. Club cap: at most 6 players (coach excluded) from one EuroLeague club.
    m.club_cap = pyo.Constraint(m.TEAMS, rule=lambda m, t: sum(m.x[i] for i in m.P if team[i] == t) <= MAX_PER_CLUB)
    # 7. Roles: every squad player gets exactly one of starter / 6th man / bench; non-members none.
    m.one_role = pyo.Constraint(m.P, rule=lambda m, i: m.x[i] == m.y_start[i] + m.y_6th[i] + m.y_bench[i])
    m.n_start = pyo.Constraint(expr=sum(m.y_start[i] for i in m.P) == N_STARTERS)
    m.n_6th = pyo.Constraint(expr=sum(m.y_6th[i] for i in m.P) == N_SIXTH)
    m.n_bench = pyo.Constraint(expr=sum(m.y_bench[i] for i in m.P) == N_BENCH)
    # 8. Formation: per-position starter bounds; with 5 starters these are exactly the legal formations.
    m.formation_min = pyo.Constraint(
        PLAYER_POSITIONS, rule=lambda m, p: sum(m.y_start[i] for i in by_pos[p]) >= FORMATION_BOUNDS[p][0]
    )
    m.formation_max = pyo.Constraint(
        PLAYER_POSITIONS, rule=lambda m, p: sum(m.y_start[i] for i in by_pos[p]) <= FORMATION_BOUNDS[p][1]
    )
    # 9. Captain: exactly one, and he must be in the starting five.
    m.one_captain = pyo.Constraint(expr=sum(m.c[i] for i in m.P) == 1)
    m.captain_starts = pyo.Constraint(m.P, rule=lambda m, i: m.c[i] <= m.y_start[i])

    # ---- Objective pieces (kept as expressions so the report can split the optimum).
    # Active points: starters and 6th man count 100% (option-adjusted score), the captain counts a
    # second time, bench players count 50% of their mean projection.
    m.active_points = pyo.Expression(
        expr=sum(adj[i] * (m.y_start[i] + m.y_6th[i] + m.c[i]) + BENCH_FACTOR * exp[i] * m.y_bench[i] for i in m.P)
    )
    # The coach always scores 100%.
    m.coach_points = pyo.Expression(expr=sum(exp[i] * m.x[i] for i in m.HC))
    # Expected capital growth of the whole squad (players only), in points via the w_budget rate.
    m.growth_term = pyo.Expression(expr=w_budget * sum(delta[i] * m.x[i] for i in m.P))
    m.objective = pyo.Objective(expr=m.active_points + m.coach_points + m.growth_term, sense=pyo.maximize)
    return m


def solve(m: pyo.ConcreteModel) -> None:
    """Solve `m` in place with HiGHS and load the optimal values into its variables.

    Args:
        m: Model from `build_model`.

    Raises:
        RuntimeError: If the solver does not prove optimality (e.g. infeasible budget/trade limit).
    """
    opt = pyo.SolverFactory(SOLVER)
    opt.highs_options = dict(SOLVER_OPTIONS)
    results = opt.solve(m, load_solutions=False)
    tc = results.solver.termination_condition
    if tc != pyo.TerminationCondition.optimal:
        hint = (
            " No legal squad fits cash + trade limit (e.g. a fresh team with max_trades < 11 or too little "
            "cash); check --cash / --max-trades / --unlimited-trades."
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
        if i in m.HC:
            role = "coach" if x else "none"
            captain = 0
        else:
            role = "starter" if on(m.y_start, i) else "sixth" if on(m.y_6th, i) else "bench" if x else "none"
            captain = int(on(m.c, i))
        action = "sell" if s else "buy" if b else "keep"
        rows.append({**df.loc[i, OUTPUT_COLUMNS[:7]].to_dict(), "action": action, "role": role, "is_captain": captain})

    table = pd.DataFrame(rows, columns=OUTPUT_COLUMNS)
    table["_role_rank"] = table["role"].map(ROLE_ORDER.index)
    table = table.sort_values(["_role_rank", "player_id"], kind="stable").drop(columns="_role_rank")
    table = table.reset_index(drop=True)

    bought = table.loc[table["action"] == "buy", "price"].sum()
    sold = table.loc[table["action"] == "sell", "price"].sum()
    return Solution(
        table=table,
        objective=float(pyo.value(m.objective)),
        active_points=float(pyo.value(m.active_points)),
        coach_points=float(pyo.value(m.coach_points)),
        growth_term=float(pyo.value(m.growth_term)),
        trades=int((table["action"] == "buy").sum()),
        cash_before=cash,
        cash_after=float(cash + sold - bought),
    )


def optimize_squad(df: pd.DataFrame, cash: float, max_trades: int | None, w_budget: float = 0.0) -> Solution:
    """Validate, build, solve and extract in one call.

    Args:
        df: Candidate table obeying the input contract.
        cash: Credits in the bank before trading (>= 0).
        max_trades: Max buys (int >= 0, coach included) or None for unlimited.
        w_budget: Capital-growth weight (>= 0).

    Returns:
        The optimal `Solution`.

    Raises:
        ValueError: If the table or a scalar violates the contract.
        RuntimeError: If the solver does not prove optimality.
    """
    validate_input(df)
    _validate_scalars(cash, max_trades, w_budget)
    prepared = _prepare(df)
    m = build_model(prepared, cash, max_trades, w_budget)
    solve(m)
    return extract_solution(prepared, m, cash)


def format_report(sol: Solution, max_trades: int | None) -> str:
    """Render the terse stdout report.

    Args:
        sol: Solved squad.
        max_trades: The trade limit used (None = unlimited).

    Returns:
        Multi-line report text.
    """
    t = sol.table

    def line(r: pd.Series) -> str:
        cap = " (C)" if r["is_captain"] else ""
        return f"  {r['position']:<2} {r['name']}{cap} [{r['team']}, T{r['turn']}] {r['price']:.1f}cr exp {r['exp_pir']:.1f}"

    limit = "unlimited" if max_trades is None else str(max_trades)
    out = [
        f"Objective {sol.objective:.2f} = active {sol.active_points:.2f} + coach {sol.coach_points:.2f}"
        f" + growth {sol.growth_term:.2f}",
        f"Trades {sol.trades} / {limit}; cash {sol.cash_before:.2f} -> {sol.cash_after:.2f}",
    ]
    for title, role in (("Starting five:", "starter"), ("6th man:", "sixth"), ("Bench:", "bench"), ("Coach:", "coach")):
        out.append(title)
        out.extend(line(r) for _, r in t[t["role"] == role].iterrows())
    moves = t[t["action"] != "keep"]
    if len(moves):
        out.append("Trades:")
        out.extend(f"  {r['action']:<4} {r['name']} ({r['position']}, {r['price']:.1f}cr)" for _, r in moves.iterrows())
    return "\n".join(out)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the CLI flags.

    Args:
        argv: Argument list; None reads `sys.argv`.

    Returns:
        Parsed namespace (`input`, `cash`, `max_trades`, `unlimited_trades`, `w_budget`, `out`).
    """
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="Input CSV (default: %(default)s)")
    parser.add_argument("--cash", type=float, default=100.0, help="Credits in the bank before trading")
    trades = parser.add_mutually_exclusive_group()
    trades.add_argument("--max-trades", type=int, default=4, help="Max buys this round, coach included (default 4)")
    trades.add_argument("--unlimited-trades", action="store_true", help="No trade limit (Round 1 / free windows)")
    parser.add_argument("--w-budget", type=float, default=0.0, help="Capital-growth weight (default 0.0)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Output CSV (default: %(default)s)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point: load the input CSV, solve, write the squad CSV and print the report.

    Args:
        argv: Argument list; None reads `sys.argv`.

    Raises:
        FileNotFoundError: If the input CSV is missing.
        ValueError: If the input or a scalar violates the contract.
        RuntimeError: If the solver does not prove optimality.
    """
    args = parse_args(argv)
    max_trades = None if args.unlimited_trades else args.max_trades
    df = load_input(args.input)
    started = time.perf_counter()
    sol = optimize_squad(df, cash=args.cash, max_trades=max_trades, w_budget=args.w_budget)
    elapsed = time.perf_counter() - started
    args.out.parent.mkdir(parents=True, exist_ok=True)
    sol.table.to_csv(args.out, index=False, encoding="utf-8", lineterminator="\n")
    print(format_report(sol, max_trades))
    print(f"Solved {len(df)} candidates in {elapsed:.2f}s; wrote {len(sol.table)} rows to {args.out}")


if __name__ == "__main__":
    main()
