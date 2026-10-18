"""Tests for the single-round squad optimizer (Pyomo MILP, HiGHS; `eupy.optimize.optimize_squad`).

The central check is brute-force equivalence: on small random universes the MILP's optimum must
equal an independent enumeration of every legal squad x role assignment x captain, written here
from `docs/rules.md` (explicit list of legal formations, no bounds, no solver). Every solution the
model returns is also re-scored by that independent evaluator, so extraction bugs (wrong roles,
captain off the court) surface too. The solver is never mocked.

Universes use the workbook's column names (`crd_last` is the price); the module maps them itself.
"""

from __future__ import annotations

import itertools
import math
import random
import time
from dataclasses import fields, replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import eupy.optimize.optimize_squad as opt_mod
from eupy.config import load_config
from eupy.config.defaults import Optimization
from eupy.optimize.optimize_squad import (
    FORMATION_BOUNDS,
    MAX_PER_CLUB,
    OUTPUT_COLUMNS,
    ROLE_ORDER,
    Solution,
    _prepare,
    build_model,
    explain_credit_value,
    lineup_sum,
    main,
    model_frame,
    objective_components,
    optimize_squad,
    selection_sheet,
    solve,
    summary_sheet,
    validate_input,
)
from eupy.optimize.scenarios import Scenario, apply_scenario, load_scenarios, select_scenarios

SAMPLE = Path(__file__).parent / "data" / "optimizer_input_sample.csv"
CONFIG_SAMPLE = Path(__file__).parent / "data" / "optimizer_config_sample.toml"

# Written from rules.md, deliberately not derived from FORMATION_BOUNDS.
LEGAL_FORMATIONS = {(2, 2, 1), (1, 2, 2), (2, 1, 2), (1, 3, 1), (3, 1, 1)}
NEED = {"G": 4, "F": 4, "C": 2}
EPS = 1e-6


def cfg_of(
    cash: float = 100.0,
    max_trades: int | None = None,
    w: float = 0.0,
    **risk: float,
) -> Optimization:
    """Run parameters; `max_trades=None` = unlimited, `w` = credit_value, `risk` = lambda/beta/gamma weights."""
    return Optimization(
        cash=cash,
        max_trades=max_trades or 0,
        unlimited_trades=max_trades is None,
        credit_value=w,
        **risk,
    )


# --------------------------------------------------------------------------- independent reference


class BruteForce:
    """Exhaustive optimum over squads x roles x captain for one universe (no MILP involved)."""

    def __init__(self, u: pd.DataFrame, cfg: Optimization | None = None) -> None:
        cfg = cfg or cfg_of()
        self.u = u.reset_index(drop=True)
        frame = model_frame(self.u, cfg)
        # Score = target + the risk adjustment; the formulas are checked on their own in
        # test_components_follow_the_documented_formulas, so reusing them here keeps this class about the rules.
        self.score = objective_components(frame, cfg)["score"].tolist()
        self.pos = self.u["position"].tolist()
        self.price = self.u["crd_last"].tolist()
        self.exp = self.u["exp_pir"].tolist()
        self.team = self.u["team"].tolist()
        self.prev = frozenset(i for i, p in enumerate(self.u["in_prev_roster"]) if p == 1)
        self._roles: dict[tuple[int, ...], tuple[float, tuple]] = {}

    def lineup_value(self, starters, sixth, captain, squad_players) -> float:
        """Points of one role assignment, straight from the rules.md scoring table."""
        bench = [i for i in squad_players if i not in starters and i != sixth]
        pts = sum(self.score[i] for i in starters) + self.score[sixth] + self.score[captain]  # captain scores x2
        return pts + sum(0.5 * self.score[i] for i in bench)

    def best_roles(self, players: tuple[int, ...]) -> tuple[float, tuple]:
        """Enumerate every starting five (legal formation only), 6th man and captain."""
        if players not in self._roles:
            best = (-math.inf, ())
            for starters in itertools.combinations(players, 5):
                formation = tuple(sum(self.pos[i] == p for i in starters) for p in ("G", "F", "C"))
                if formation not in LEGAL_FORMATIONS:
                    continue
                for sixth in (i for i in players if i not in starters):
                    for captain in starters:
                        v = self.lineup_value(starters, sixth, captain, players)
                        if v > best[0]:
                            best = (v, (starters, sixth, captain))
            self._roles[players] = best
        return self._roles[players]

    def squad_growth_credits(self, squad: frozenset[int]) -> float:
        """Raw, unweighted expected price growth of the squad (independent of credit_value)."""
        return sum((self.exp[i] - 1.1 * self.price[i]) / 25 for i in squad)

    def squad_extra(self, squad: frozenset[int], w: float) -> float:
        """w * expected price change of the squad's players."""
        return w * self.squad_growth_credits(squad)

    def legal_squad(self, squad: frozenset[int], cash: float, max_trades: int | None) -> bool:
        buys, sells = squad - self.prev, self.prev - squad
        if max_trades is not None and len(buys) > max_trades:
            return False
        if sum(self.price[i] for i in buys) > cash + sum(self.price[i] for i in sells) + EPS:
            return False
        clubs = [self.team[i] for i in squad]
        return all(clubs.count(t) <= 6 for t in set(clubs))

    def solve(self, cash: float, max_trades: int | None, w: float) -> tuple[float, frozenset[int], tuple]:
        by_pos = {p: [i for i in range(len(self.pos)) if self.pos[i] == p] for p in NEED}
        best: tuple[float, frozenset[int], tuple] = (-math.inf, frozenset(), ())
        for combo in itertools.product(*(itertools.combinations(by_pos[p], n) for p, n in NEED.items())):
            squad = frozenset(itertools.chain(*combo))
            if not self.legal_squad(squad, cash, max_trades):
                continue
            players = tuple(sorted(squad))
            roles_value, roles = self.best_roles(players)
            v = roles_value + self.squad_extra(squad, w)
            if v > best[0]:
                best = (v, squad, roles)
        return best


def rescore(bf: BruteForce, sol: Solution, cash: float, max_trades: int | None, w: float) -> float:
    """Check a model output's squad/lineup/growth split are legal and return its value under the rules."""
    table = sol.table
    idx = {name: i for i, name in enumerate(bf.u["player"])}
    squad_rows = table[table["action"] != "sell"]
    squad = frozenset(idx[n] for n in squad_rows["player"])
    assert len(squad) == 10
    assert sorted(bf.pos[i] for i in squad) == sorted(p for p, n in NEED.items() for _ in range(n))
    assert bf.legal_squad(squad, cash, max_trades)
    # Actions are consistent with the previous roster (no player both sold and bought).
    for name, action in zip(table["player"], table["action"], strict=True):
        was_owned = idx[name] in bf.prev
        assert action in (("keep", "sell") if was_owned else ("buy",)), (name, action)
    starters = tuple(idx[n] for n in table.loc[table["role"] == "starter", "player"])
    (sixth,) = (idx[n] for n in table.loc[table["role"] == "sixth", "player"])
    (captain,) = (idx[n] for n in table.loc[table["is_captain"] == 1, "player"])
    assert len(starters) == 5 and captain in starters
    assert tuple(sum(bf.pos[i] == p for i in starters) for p in ("G", "F", "C")) in LEGAL_FORMATIONS
    assert bf.score[captain] == max(bf.score[i] for i in starters)
    assert (table.loc[table["action"] == "sell", "role"] == "none").all()
    # growth_credits/growth_term split: raw growth must match independently, and growth_term = w * it.
    growth_credits = bf.squad_growth_credits(squad)
    assert sol.growth_credits == pytest.approx(growth_credits, abs=EPS)
    assert sol.growth_term == pytest.approx(w * growth_credits, abs=EPS)
    players = tuple(sorted(squad))
    return bf.lineup_value(starters, sixth, captain, players) + bf.squad_extra(squad, w)


# --------------------------------------------------------------------------- universes


def random_universe(seed: int) -> pd.DataFrame:
    """6G/5F/3C, players split 7/4/3 over 3 clubs (so the cap can bind), a legal random previous roster."""
    rng = random.Random(seed)  # noqa: S311 -- seeded test data, not crypto
    # 7 non-AAA players always allow a previous roster with <= 6 AAA players, so the loop below ends.
    clubs = ["AAA"] * 7 + ["BBB"] * 4 + ["CCC"] * 3
    rng.shuffle(clubs)
    rows = []
    for pos, n in (("G", 6), ("F", 5), ("C", 3)):
        for k in range(n):
            price = round(rng.uniform(3, 20), 1)
            # Loosely price-correlated, so cheap over-performers exist and the growth term has a real trade-off.
            exp = round(0.5 * price + rng.uniform(0, 12), 1)
            rows.append({
                "player": f"{pos} player {k}",
                "team": clubs[len(rows)],
                "position": pos,
                "turn": rng.choice([1, 2, 3]),
                "crd_last": price,
                "exp_pir": exp,
                "pir_std": round(rng.uniform(0, 9), 1),
                "pir_p90": round(exp + rng.uniform(0, 15), 1),
                "pir_p10": round(exp - rng.uniform(0, 15), 1),
                "in_prev_roster": 0,
            })
    u = pd.DataFrame(rows)
    while True:
        prev = [i for p, n in NEED.items() for i in rng.sample(list(u.index[u["position"] == p]), n)]
        clubs = u.loc[prev]["team"].value_counts()
        if clubs.max() <= MAX_PER_CLUB:
            break
    u.loc[prev, "in_prev_roster"] = 1
    return u


def make_universe(spec: list[tuple[str, str, float, float, int]]) -> pd.DataFrame:
    """Rows from (position, team, price, exp_pir, in_prev_roster) tuples."""
    return pd.DataFrame([
        {
            "player": f"p{k}",
            "team": team,
            "position": pos,
            "turn": 1,
            "crd_last": price,
            "exp_pir": exp,
            "in_prev_roster": prev,
        }
        for k, (pos, team, price, exp, prev) in enumerate(spec)
    ])


# --------------------------------------------------------------------------- 1. brute-force equivalence

SEEDS = range(14)  # seed 6 is the one where credit_value changes the decision
CREDIT_SEED = 6
W = 3.0  # early-season capital-growth weight per the spec
LAMBDA = 1.5  # robustness weight used where the risk adjustment should bite


def _instances():
    """(seed, universe, cash) with a small bank so the budget can bind; tests use trade limit 2 and w=W."""
    for seed in SEEDS:
        rng = random.Random(1000 + seed)  # noqa: S311 -- seeded test data, not crypto
        yield seed, random_universe(seed), round(rng.uniform(0, 2), 1)


def test_model_matches_brute_force_and_each_feature_matters() -> None:
    """Model optimum == enumeration optimum in every config; each feature changes the answer somewhere."""
    matters = {"budget": False, "trade_limit": False, "credit_value": False, "robustness": False}
    for seed, u, cash in _instances():
        robust = cfg_of(cash, 2, W, robustness_lambda=LAMBDA)
        bf_plain, bf_robust = BruteForce(u), BruteForce(u, robust)
        configs = {
            "base": (bf_plain, cfg_of(cash, 2, W)),
            "no_budget": (bf_plain, cfg_of(1e6, 2, W)),
            "no_trade_limit": (bf_plain, cfg_of(cash, None, W)),
            "w0": (bf_plain, cfg_of(cash, 2, 0.0)),
            "robust": (bf_robust, robust),
        }
        results = {}
        for name, (bf, cfg) in configs.items():
            cap = None if cfg.unlimited_trades else cfg.max_trades
            ref_value, ref_squad, ref_roles = bf.solve(cfg.cash, cap, cfg.credit_value)
            assert ref_value > -math.inf, (seed, name, "instance infeasible")
            sol = optimize_squad(u, cfg)
            assert sol.objective == pytest.approx(ref_value, abs=EPS), (seed, name)
            assert rescore(bf, sol, cfg.cash, cap, cfg.credit_value) == pytest.approx(sol.objective, abs=EPS), (
                seed,
                name,
            )
            results[name] = (ref_value, ref_squad, ref_roles)

        base_value, base_squad, (starters, sixth, captain) = results["base"]
        players = tuple(sorted(base_squad))
        # Constraints matter if relaxing them strictly improves the optimum.
        matters["budget"] |= results["no_budget"][0] > base_value + EPS
        matters["trade_limit"] |= results["no_trade_limit"][0] > base_value + EPS
        # w / risk matter if the base decision is strictly suboptimal when judged without them.
        base_at_w0 = bf_plain.best_roles(players)[0] + bf_plain.squad_extra(base_squad, 0.0)
        matters["credit_value"] |= base_at_w0 < results["w0"][0] - EPS
        base_at_robust = bf_robust.lineup_value(starters, sixth, captain, players) + bf_robust.squad_extra(
            base_squad, W
        )
        matters["robustness"] |= base_at_robust < results["robust"][0] - EPS
    assert all(matters.values()), matters


@pytest.mark.parametrize(("var", "owned"), [("b", 1), ("s", 0)], ids=["rebuy_owned", "sell_unowned"])
def test_player_cannot_be_both_sold_and_bought(var: str, owned: int) -> None:
    """Forcing a sell+re-buy of one player must be infeasible.

    With binary x, s = b = 1 is cost- and score-neutral, so no optimum reveals it; only forcing it
    shows whether the valid-action constraints exist. Unlimited trades and ample cash, so nothing
    else can make the model infeasible.
    """
    _, u, _ = next(_instances())
    cfg = cfg_of(100.0, None, 0.0)
    df = _prepare(model_frame(u, cfg), cfg)
    m = build_model(df, cfg)
    i = int(df.index[df["in_prev_roster"] == owned][0])
    getattr(m, var)[i].fix(1)
    with pytest.raises(RuntimeError, match="termination condition infeasible"):
        solve(m, cfg)


# --------------------------------------------------------------------------- 2. rule scenarios


def test_formation_bounds_are_exactly_the_legal_formations() -> None:
    """Enumerate every (g, f, c) five a 4/4/2 squad could field: bounds admit exactly the legal five."""
    for g, f, c in itertools.product(range(5), range(5), range(3)):
        if g + f + c != 5:
            continue
        within = all(lo <= n <= hi for n, (lo, hi) in zip((g, f, c), FORMATION_BOUNDS.values(), strict=True))
        assert within == ((g, f, c) in LEGAL_FORMATIONS), (g, f, c)
    assert list(FORMATION_BOUNDS) == ["G", "F", "C"]


def test_club_cap_binds_when_one_club_dominates() -> None:
    """A full squad of stars from one club is available and affordable; only 6 may be picked."""
    stars = [("G", "AAA", 1.0, 30.0, 0)] * 4 + [("F", "AAA", 1.0, 30.0, 0)] * 4 + [("C", "AAA", 1.0, 30.0, 0)] * 2
    filler = [("G", "BBB", 1.0, 1.0, 0)] * 4 + [("F", "CCC", 1.0, 1.0, 0)] * 4 + [("C", "BBB", 1.0, 1.0, 0)] * 2
    u = make_universe([*stars, *filler])
    sol = optimize_squad(u, cfg_of(100.0, None))
    squad = sol.table[sol.table["action"] != "sell"]
    assert (squad["team"] == "AAA").sum() == 6  # rules.md; literal on purpose, not the module constant


def _upgrade_universe() -> pd.DataFrame:
    """Current squad of 10-point players; strictly better 25-point replacements at every position."""
    current = [("G", "AAA", 5.0, 10.0, 1)] * 4 + [("F", "BBB", 5.0, 10.0, 1)] * 4 + [("C", "CCC", 5.0, 10.0, 1)] * 2
    market = [("G", "DDD", 5.0, 25.0, 0)] * 4 + [("F", "EEE", 5.0, 25.0, 0)] * 4 + [("C", "FFF", 5.0, 25.0, 0)] * 2
    return make_universe(current + market)


def test_unlimited_trades_lifts_the_limit() -> None:
    """Ten strictly better same-price replacements exist: K=4 buys 4, None buys all 10."""
    u = _upgrade_universe()
    assert optimize_squad(u, replace(cfg_of(0.0, 4), max_trades=np.int64(4))).trades == 4  # numpy ints are valid limits
    assert optimize_squad(u, cfg_of(0.0, None)).trades == 10


def test_row_order_does_not_change_the_solution() -> None:
    """Many interchangeable candidates (ties everywhere): a shuffled input must give the same squad."""
    spec = [(pos, f"T{k % 4}", 5.0, 10.0, 0) for pos, n in (("G", 8), ("F", 8), ("C", 4)) for k in range(n)]
    u = make_universe(spec)
    a = optimize_squad(u, cfg_of(100.0, None)).table
    b = optimize_squad(u.sample(frac=1, random_state=0), cfg_of(100.0, None)).table
    pd.testing.assert_frame_equal(a, b)


def test_fresh_team_with_trade_limit_is_infeasible_and_says_so() -> None:
    """Round 1 needs 10 buys; a standard 4-trade limit must fail loudly, not return a partial squad."""
    with pytest.raises(RuntimeError, match="termination condition infeasible"):
        optimize_squad(_sample(), cfg_of(100.0, 4))


# --------------------------------------------------------------------------- 3. validation


def _sample() -> pd.DataFrame:
    """The Round-1 sample as a workbook table (`crd_last` = price)."""
    return pd.read_csv(SAMPLE).rename(columns={"name": "player", "price": "crd_last"}).drop(columns="exp_pir_adj")


def _set(df: pd.DataFrame, row: int, col: str, value) -> pd.DataFrame:
    df = df.copy()
    if isinstance(value, str | float):  # widen the column first; pandas refuses lossy in-place upcasts
        df[col] = df[col].astype(object if isinstance(value, str) else float)
    df.loc[row, col] = value
    return df


def _prev_roster(df: pd.DataFrame, n: int, composition: dict[str, int] | None = None) -> pd.DataFrame:
    df = df.copy()
    comp = composition or NEED
    picks = [i for p, k in comp.items() for i in df.index[df["position"] == p][:k]][:n]
    df.loc[picks, "in_prev_roster"] = 1
    return df


INVALID = {
    "missing column": (lambda d: d.drop(columns="team"), "lacks column"),
    "NaN required": (lambda d: _set(d, 0, "exp_pir", float("nan")), "Empty cells"),
    "non-numeric price": (lambda d: _set(d, 0, "crd_last", "cheap"), "non-numeric"),
    "empty player": (lambda d: _set(d, 0, "player", ""), "empty player"),
    "duplicate player": (lambda d: _set(d, 1, "player", d.loc[0, "player"]), "duplicate player"),
    "bad position": (lambda d: _set(d, 0, "position", "PG"), "unknown position"),
    "price zero": (lambda d: _set(d, 0, "crd_last", 0.0), "price <= 0"),
    "turn zero": (lambda d: _set(d, 0, "turn", 0), r"turn is not an integer >= 1"),
    "turn fractional": (lambda d: _set(d, 0, "turn", 1.5), r"turn is not an integer >= 1"),
    "prev flag 2": (lambda d: _set(d, 0, "in_prev_roster", 2), r"outside \{0, 1\}"),
    "inf price": (lambda d: _set(d, 0, "crd_last", math.inf), r"infinite values in column\(s\) \['price'\]"),
    "-inf target": (lambda d: _set(d, 0, "exp_pir", -math.inf), r"infinite values in column\(s\) \['target'\]"),
    "too few centers": (
        lambda d: d[~d["player"].isin(["Center Two", "Center Three", "Center Four"])],
        "too few candidates",
    ),
    "partial prev roster": (lambda d: _prev_roster(d, 5), "marks 5 rows"),
    "wrong prev composition": (
        lambda d: _prev_roster(d, 10, {"G": 5, "F": 3, "C": 2}),
        "previous roster composition",
    ),
}


@pytest.mark.parametrize("case", INVALID)
def test_invalid_input_raises_readable_error(case: str) -> None:
    mutate, match = INVALID[case]
    with pytest.raises(ValueError, match=match):
        optimize_squad(mutate(_sample()), cfg_of(100.0, None))


@pytest.mark.parametrize("df", [_sample(), _prev_roster(_sample(), 10)], ids=["r1", "prev"])
def test_valid_input_passes(df: pd.DataFrame) -> None:
    validate_input(model_frame(df, cfg_of()))


@pytest.mark.parametrize(
    "kwargs", [{"cash": -1.0}, {"max_trades": -1}, {"max_trades": 2.5}, {"credit_value": -0.1}], ids=str
)
def test_invalid_scalars_raise(kwargs: dict) -> None:
    base = {"cash": 100.0, "max_trades": 0, "unlimited_trades": False, "credit_value": 0.0, **kwargs}
    with pytest.raises(ValueError):
        optimize_squad(_sample(), Optimization(**base))


# --------------------------------------------------------------------------- 4-5. size, determinism, run


def synthetic_league(seed: int = 7) -> pd.DataFrame:
    """~680 candidates over 20 clubs (13 G / 13 F / 8 C each) with a legal previous roster."""
    rng = random.Random(seed)  # noqa: S311 -- seeded test data, not crypto
    rows = []
    for club in range(20):
        for pos, n in (("G", 13), ("F", 13), ("C", 8)):
            for _ in range(n):
                price = round(rng.uniform(1, 25), 1)
                exp = 1.1 * price + rng.gauss(0, 5)
                rows.append({
                    "player": f"{pos}-{club}-{len(rows)}",
                    "team": f"T{club:02d}",
                    "position": pos,
                    "turn": rng.choice([1, 2]),
                    "crd_last": price,
                    "exp_pir": round(exp, 1),
                    "in_prev_roster": 0,
                })
    u = pd.DataFrame(rows)
    prev = [i for p, n in NEED.items() for i in rng.sample(list(u.index[u["position"] == p]), n)]
    u.loc[prev, "in_prev_roster"] = 1
    return u


def test_realistic_size_solves_fast_and_is_identical_across_runs() -> None:
    u = synthetic_league()
    assert len(u) == 680
    cfg = cfg_of(1.5, 4, 3.0)
    tables = []
    for _ in range(2):
        started = time.perf_counter()
        tables.append(optimize_squad(u, cfg).table)
        assert time.perf_counter() - started < 30
    pd.testing.assert_frame_equal(tables[0], tables[1])


def _run_main(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, workbook: pd.DataFrame, cfg: Optimization) -> Path:
    """Run `main()` against a workbook in tmp_path with the given [optimization] section; returns the output path."""
    inp, out = tmp_path / "in.xlsx", tmp_path / "out" / "optimizer_output.xlsx"
    workbook.to_excel(inp, index=False)
    monkeypatch.setattr(opt_mod, "INPUT_PATH", inp)
    monkeypatch.setattr(opt_mod, "OUTPUT_PATH", out)
    monkeypatch.setattr(opt_mod, "get_config", lambda: SimpleNamespace(optimization=cfg))
    main()
    return out


def test_main_on_sample_writes_both_sheets_and_the_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = _run_main(monkeypatch, tmp_path, _sample(), cfg_of(100.0, None))
    sheets = pd.read_excel(out, sheet_name=None)
    assert list(sheets) == ["selection", "summary"]
    selection = sheets["selection"]
    assert len(selection) == len(_sample())
    assert selection["role"].value_counts().to_dict() == {
        "none": len(selection) - 10,
        "starter": 5,
        "bench": 4,
        "sixth": 1,
    }
    assert selection["is_captain"].sum() == 1
    report = capsys.readouterr().out
    for fragment in ("Objective", "Trades 10 / unlimited", "cash 100.00 ->", "Starting five:", "(C)", "6th man:"):
        assert fragment in report


def test_load_input_reads_the_first_sheet_untouched(tmp_path: Path) -> None:
    path = tmp_path / "in.xlsx"
    _sample().to_excel(path, index=False)
    pd.testing.assert_frame_equal(opt_mod.load_input(path), _sample())
    with pytest.raises(FileNotFoundError, match="optimizer_input"):
        opt_mod.load_input(tmp_path / "nope.xlsx")


# --------------------------------------------------------------------------- 6. credit_value


def test_growth_credits_is_independent_of_credit_value_when_squad_is_unchanged() -> None:
    """`good` dominates `bad` on both active points and growth at any credit_value >= 0, so the same
    squad is optimal at credit_value=0 and credit_value=5; growth_credits (raw) must match too.
    """
    good_g = [("G", "GG", 10.0, 20.0, 0)] * 4
    bad_g = [("G", "GG", 10.0, 1.0, 0)]
    good_f = [("F", "FF", 10.0, 20.0, 0)] * 4
    bad_f = [("F", "FF", 10.0, 1.0, 0)]
    good_c = [("C", "CC", 10.0, 20.0, 0)] * 2
    bad_c = [("C", "CC", 10.0, 1.0, 0)]
    u = make_universe([*good_g, *bad_g, *good_f, *bad_f, *good_c, *bad_c])
    low = optimize_squad(u, cfg_of(100.0, None, 0.0))
    high = optimize_squad(u, cfg_of(100.0, None, 5.0))
    assert set(low.table["player"]) == set(high.table["player"])  # same squad chosen
    assert low.growth_credits == pytest.approx(high.growth_credits)


def test_relaxing_cash_by_one_credit_cannot_lower_the_pure_pir_optimum() -> None:
    """+1 cash unlocks a strictly better G (barely unaffordable before): the PIR optimum cannot drop,
    and on this instance it strictly rises -- explain_credit_value's shadow price measures exactly this.
    """
    base_g = [("G", "GG", 10.0, 10.0, 0)] * 4
    premium_g = [("G", "GG", 11.0, 100.0, 0)]
    base_f = [("F", "FF", 10.0, 10.0, 0)] * 4
    base_c = [("C", "CC", 10.0, 10.0, 0)] * 2
    u = make_universe([*base_g, *premium_g, *base_f, *base_c])
    cfg = cfg_of(100.0, None, 0.0)
    report = explain_credit_value(u, cfg, optimize_squad(u, cfg))
    assert report.lambda_shadow >= 0  # invariant: relaxing cash cannot lower the PIR optimum
    assert report.lambda_shadow > EPS  # strictly greater here (the premium G becomes affordable)


def test_credit_value_can_strictly_cost_active_points() -> None:
    """Seed 6's instance (see SEEDS) is where credit_value=W changes the chosen squad: the
    credit_value=0 baseline strictly beats the actual credit_value=W squad on pure active_points.
    """
    _, u, cash = next(inst for inst in _instances() if inst[0] == CREDIT_SEED)
    cfg = cfg_of(cash, 2, W)
    actual = optimize_squad(u, cfg)
    report = explain_credit_value(u, cfg, actual)
    assert report.baseline_active >= actual.active_points  # invariant: credit_value=0 is the PIR max
    assert report.baseline_active > actual.active_points + EPS  # strictly costs PIR here


def test_explain_credit_value_skips_the_baseline_resolve_at_credit_value_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """credit_value == 0: `actual` already IS the pure-PIR baseline, so only the shadow (cash+1) solve
    runs -- one fewer than the credit_value > 0 case, which needs both a baseline and a shadow solve.
    """
    u = _sample()
    real_optimize_squad = opt_mod.optimize_squad
    calls: list[None] = []

    def counting(*args, **kwargs):
        calls.append(None)
        return real_optimize_squad(*args, **kwargs)

    monkeypatch.setattr(opt_mod, "optimize_squad", counting)

    cfg_zero = cfg_of(100.0, None, 0.0)
    actual_zero = real_optimize_squad(u, cfg_zero)
    calls.clear()
    explain_credit_value(u, cfg_zero, actual_zero)
    assert len(calls) == 1  # only the shadow (cash+1, credit_value=0) solve

    cfg_w = cfg_of(100.0, None, W)
    actual_nonzero = real_optimize_squad(u, cfg_w)
    calls.clear()
    explain_credit_value(u, cfg_w, actual_nonzero)
    assert len(calls) == 2  # baseline (credit_value=0) + shadow (cash+1, credit_value=0)


def test_main_prints_credit_value_report_both_when_unchanged_and_when_it_costs_pir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """format_credit_value_report's output appears in main()'s stdout: at credit_value=0 (squad
    unchanged, trivially) and at seed 6's credit_value=W (squad changes, see SEEDS)."""
    _, u, cash = next(inst for inst in _instances() if inst[0] == CREDIT_SEED)

    _run_main(monkeypatch, tmp_path, u, cfg_of(cash, 2, 0.0))
    report = capsys.readouterr().out
    assert "Credit value:" in report
    assert "did not change the squad" in report

    _run_main(monkeypatch, tmp_path, u, cfg_of(cash, 2, W))
    report = capsys.readouterr().out
    assert "Credit value:" in report
    assert "Growth trade-off:" in report


# --------------------------------------------------------------------------- 7. objective modes, turn minimums, output sheets

opt = opt_mod
CFG_S = cfg_of(100.0, 10)


def league(*, prev: bool = False) -> pd.DataFrame:
    """30 candidates, 10 per position, 8 clubs. Turn-1 players project far better than turn-2 players."""
    rows = []
    for n in range(30):
        pos = "GFC"[n % 3]
        turn = 1 if n % 5 else 2  # every fifth player is a turn-2 player
        rows.append({
            "player": f"P{n:02d}",
            "team": f"T{n % 8}",
            "position": pos,
            "turn": turn,
            "crd_last": 5.0,
            "exp_pir": 10.0 + (n % 7) + (8.0 if turn == 1 else 0.0),
            "pir_std": 1.0 + 3.0 * (n % 4),
            "pir_p90": 22.0 + 2.0 * (n % 5),
            "pir_p10": 4.0 + (n % 3),
            "in_prev_roster": 0,
        })
    df = pd.DataFrame(rows)
    if prev:  # 4 G / 4 F / 2 C, all turn 1
        picks = [
            *df[(df.position == "G") & (df.turn == 1)].index[:4],
            *df[(df.position == "F") & (df.turn == 1)].index[:4],
            *df[(df.position == "C") & (df.turn == 1)].index[:2],
        ]
        df.loc[picks, "in_prev_roster"] = 1
    return df


def summarize(raw: pd.DataFrame, sol, cfg: Optimization, baseline: float | None) -> pd.DataFrame:
    sheet = opt.selection_sheet(raw, sol, cfg)
    plain = opt.vanilla_config(cfg)
    vanilla = None if plain is None else opt.selection_sheet(raw, opt.optimize_squad(raw, plain), plain)
    return opt.summary_sheet(sol, sheet, cfg, len(raw), 0.1, baseline, opt.explain_credit_value(raw, cfg, sol), vanilla)


def squad_turns(sol) -> list[int]:
    return sol.table.loc[sol.table["role"] != "none", "turn"].tolist()


def test_model_frame_maps_workbook_columns_without_touching_the_input() -> None:
    df = league()
    frame = opt.model_frame(df, CFG_S)
    assert "crd_last" in df.columns and "price" not in df.columns
    assert list(frame.columns) == ["player", "team", "position", "turn", "price", "in_prev_roster", "target"]
    assert (frame["price"] == df["crd_last"]).all() and (frame["target"] == df["exp_pir"]).all()


def test_turn_minimum_binds_and_costs_objective() -> None:
    df = league()
    free = opt.optimize_squad(df, CFG_S)
    forced = opt.optimize_squad(df, replace(CFG_S, min_players_per_turn=[0, 4]))
    assert squad_turns(free).count(2) < 4  # the constraint is not vacuous
    assert squad_turns(forced).count(2) >= 4
    assert forced.objective < free.objective


def test_turn_minimum_for_both_turns() -> None:
    df = league()
    sol = opt.optimize_squad(df, replace(CFG_S, min_players_per_turn=[3, 3]))
    turns = squad_turns(sol)
    assert turns.count(1) >= 3 and turns.count(2) >= 3


def test_turn_minimum_fails_early_when_a_turn_has_too_few_candidates() -> None:
    df = league()
    df.loc[df.turn == 2, "turn"] = 1  # no turn-2 player left
    with pytest.raises(ValueError, match="min_players_per_turn needs more candidates"):
        opt.optimize_squad(df, replace(CFG_S, min_players_per_turn=[0, 1]))


@pytest.mark.parametrize("mins", [[6, 5], [-1], [True], [1.5]])
def test_bad_turn_minimums_are_rejected(mins: list) -> None:
    df = league()
    with pytest.raises(ValueError, match="min_players_per_turn"):
        opt.optimize_squad(df, replace(CFG_S, min_players_per_turn=mins))


def test_selection_sheet_keeps_every_input_row_and_column() -> None:
    raw = league(prev=True)
    sol = opt.optimize_squad(raw, replace(CFG_S, max_trades=2))
    sheet = opt.selection_sheet(raw, sol, CFG_S)
    assert list(sheet.columns) == [
        *raw.columns,
        "selected",
        "role",
        "action",
        "is_captain",
        *opt.COMPONENT_COLUMNS.values(),
    ]
    assert sorted(sheet["player"]) == sorted(raw["player"])
    assert sheet["selected"].sum() == 10
    assert sheet["is_captain"].sum() == 1
    assert (sheet["action"] == "buy").sum() == (sheet["action"] == "sell").sum() <= 2
    assert (sheet.head(10)["selected"] == 1).all()  # squad first
    assert (sheet.iloc[10:12]["action"] == "sell").sum() == (sheet["action"] == "sell").sum()  # sold right below it


def test_benefit_is_gain_over_keeping_the_current_squad() -> None:
    raw = league(prev=True)
    sol = opt.optimize_squad(raw, CFG_S)
    baseline = opt.current_squad_objective(raw, CFG_S)
    assert baseline is not None and sol.objective >= baseline
    summary = summarize(raw, sol, CFG_S, baseline)
    benefit = summary.loc[summary["metric"].str.startswith("benefit"), "value"].item()
    assert benefit == pytest.approx(sol.objective - baseline)


def test_no_baseline_for_a_fresh_team_or_an_infeasible_current_squad() -> None:
    fresh = league()
    assert opt.current_squad_objective(fresh, replace(CFG_S, unlimited_trades=True)) is None
    prev = league(prev=True)  # current squad is all turn 1
    assert opt.current_squad_objective(prev, replace(CFG_S, min_players_per_turn=[0, 2])) is None


def test_write_output_makes_two_sheets(tmp_path: Path) -> None:
    raw = league(prev=True)
    sol = opt.optimize_squad(raw, CFG_S)
    summary = summarize(raw, sol, CFG_S, None)
    out = tmp_path / "deep" / "optimizer_output.xlsx"
    opt.write_output(opt.selection_sheet(raw, sol, CFG_S), summary, out)
    sheets = pd.read_excel(out, sheet_name=None)
    assert list(sheets) == ["selection", "summary"]
    assert len(sheets["selection"]) == len(raw)
    assert sheets["summary"].loc[sheets["summary"]["metric"] == "objective value", "value"].item() == pytest.approx(
        sol.objective
    )


def test_optimization_config_section_overrides_defaults(tmp_path: Path) -> None:
    p = tmp_path / "c.toml"
    p.write_text(
        "[optimization]\nmin_players_per_turn = [3, 3]\nunlimited_trades = true\n[optimization.solver_options]\nthreads = 2\n"
    )
    cfg = load_config(p).optimization
    assert cfg.min_players_per_turn == [3, 3] and cfg.unlimited_trades is True
    assert cfg.solver_options == {"threads": 2}  # replaces the default dict, documented
    assert cfg.cash == Optimization().cash


def test_target_column_from_config_drives_the_selection() -> None:
    df = league()
    df["inverse"] = -df["exp_pir"]  # the best players by exp_pir are the worst by "inverse"
    by_pir = opt.optimize_squad(df, CFG_S)
    by_inverse = opt.optimize_squad(df, replace(CFG_S, target="inverse"))
    chosen = lambda sol: set(sol.table.loc[sol.table["role"] != "none", "player"])
    assert chosen(by_pir) != chosen(by_inverse)
    assert by_pir.objective > 0 > by_inverse.objective


def test_missing_mapped_column_names_the_fix() -> None:
    with pytest.raises(ValueError, match="no_such_column"):
        opt.optimize_squad(league(), replace(CFG_S, target="no_such_column"))
    with pytest.raises(ValueError, match="crd_last"):
        opt.optimize_squad(league().drop(columns="crd_last"), CFG_S)


@pytest.mark.parametrize("column", ["exp_pir", "crd_last", "team"])
def test_empty_cells_in_a_required_column_are_rejected(column: str) -> None:
    df = league()
    df.loc[3, column] = None
    with pytest.raises(ValueError, match="Empty cells"):
        opt.optimize_squad(df, CFG_S)


def test_zero_turn_minimums_are_the_same_as_none() -> None:
    df = league()
    assert opt.optimize_squad(df, replace(CFG_S, min_players_per_turn=[0, 0])).objective == pytest.approx(
        opt.optimize_squad(df, CFG_S).objective
    )


def test_components_follow_the_documented_formulas() -> None:
    frame = pd.DataFrame({
        "price": [10.0, 5.0],
        "target": [20.0, 6.0],
        "std": [4.0, 2.0],
        "p90": [30.0, 5.0],
        "p10": [8.0, 9.0],
    })
    base = replace(CFG_S, credit_value=3.0)
    growth = [(20.0 - 11.0) / 25, (6.0 - 5.5) / 25]
    off = {"robustness_lambda": 0.0, "upside_beta": 0.0, "downside_gamma": 0.0}
    cases = {
        "none": ({}, [0.0, 0.0]),
        "robustness": ({"robustness_lambda": 0.5}, [-2.0, -1.0]),  # -lambda * std
        "upside": ({"upside_beta": 0.25}, [0.25 * 10.0, 0.0]),  # positive part: p90 below target adds nothing
        "downside": ({"downside_gamma": 0.1}, [-0.1 * 12.0, 0.0]),  # positive part: target below p10 costs nothing
        "upside+downside": ({"upside_beta": 0.25, "downside_gamma": 0.1}, [2.5 - 1.2, 0.0]),  # terms add
        "all three": (
            {"robustness_lambda": 0.5, "upside_beta": 0.25, "downside_gamma": 0.1},
            [-2.0 + 2.5 - 1.2, -1.0],
        ),
    }
    for name, (weights, risk) in cases.items():
        comps = opt.objective_components(frame, replace(base, **{**off, **weights}))
        assert comps["risk_adjustment"].tolist() == pytest.approx(risk), name
        assert comps["score"].tolist() == pytest.approx([20.0 + risk[0], 6.0 + risk[1]]), name
        assert comps["credit_growth"].tolist() == pytest.approx(growth)
        assert comps["growth_value"].tolist() == pytest.approx([3.0 * g for g in growth])


def test_robustness_trades_expected_value_for_lower_volatility() -> None:
    df = league()
    vanilla = opt.optimize_squad(df, CFG_S)
    robust_cfg = replace(CFG_S, robustness_lambda=3.0)
    robust = opt.optimize_squad(df, robust_cfg)
    std = df.set_index("player")["pir_std"]

    def lineup_std(sol) -> float:
        return float(std[sol.table.loc[sol.table["role"] != "none", "player"]].sum())

    assert lineup_std(robust) < lineup_std(vanilla)
    assert robust.objective < vanilla.objective  # the penalty is paid


def test_only_terms_with_a_weight_need_their_risk_column() -> None:
    no_risk = league().drop(columns=["pir_std", "pir_p90", "pir_p10"])
    opt.optimize_squad(no_risk, CFG_S)  # all weights 0: fine
    with pytest.raises(ValueError, match="pir_std"):
        opt.optimize_squad(no_risk, replace(CFG_S, robustness_lambda=1.0))
    with pytest.raises(ValueError, match="pir_p10"):
        opt.optimize_squad(league().drop(columns="pir_p10"), replace(CFG_S, upside_beta=1.0, downside_gamma=2.0))
    # upside alone does not need p10
    opt.optimize_squad(league().drop(columns="pir_p10"), replace(CFG_S, upside_beta=1.0))


def test_empty_risk_cell_is_an_error_only_for_a_term_that_is_on() -> None:
    df = league()
    df.loc[2, "pir_std"] = None
    opt.optimize_squad(df, CFG_S)  # weight 0 ignores pir_std
    with pytest.raises(ValueError, match="Empty cells"):
        opt.optimize_squad(df, replace(CFG_S, robustness_lambda=1.0))


@pytest.mark.parametrize(
    "bad", [{"robustness_lambda": -1.0}, {"upside_beta": float("nan")}, {"downside_gamma": float("inf")}]
)
def test_bad_objective_parameters_are_rejected(bad: dict) -> None:
    with pytest.raises(ValueError, match="robustness_lambda|upside_beta|downside_gamma"):
        opt.optimize_squad(league(), replace(CFG_S, **bad))


def test_credit_value_adds_growth_and_selection_sheet_shows_components() -> None:
    df = league()
    cfg = replace(CFG_S, credit_value=3.0)
    sol = opt.optimize_squad(df, cfg)
    assert sol.growth_term == pytest.approx(3.0 * sol.growth_credits)
    assert sol.objective == pytest.approx(sol.active_points + sol.growth_term)
    sheet = opt.selection_sheet(df, sol, cfg)
    squad = sheet[sheet["selected"] == 1]
    assert squad["opt_growth_value"].sum() == pytest.approx(sol.growth_term)
    assert (sheet["opt_score"] == sheet["opt_target"] + sheet["opt_risk_adjustment"]).all()


def test_summary_compares_with_vanilla_only_when_the_run_is_not_vanilla() -> None:
    raw = league()
    assert opt.vanilla_config(CFG_S) is None
    cfg = replace(CFG_S, robustness_lambda=3.0)
    sol = opt.optimize_squad(raw, cfg)
    metrics = summarize(raw, sol, cfg, None)["metric"].tolist()
    assert "players different from the vanilla squad" in metrics
    assert (
        "players different from the vanilla squad"
        not in summarize(raw, opt.optimize_squad(raw, CFG_S), CFG_S, None)["metric"].tolist()
    )


# --------------------------------------------------------------------------- 8. the script twin


def _code_after_docstring(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return text[text.index('"""', text.index('"""') + 3) + 3 :]


def test_scripts_optimize_is_a_copy_of_the_package_module() -> None:
    """`scripts/optimize.py` and `eupy.optimize.optimize_squad` are maintained together: same code, bar the repo root."""
    root = Path(__file__).resolve().parents[1]
    script = _code_after_docstring(root / "scripts" / "optimize.py")
    package = _code_after_docstring(root / "src" / "eupy" / "optimize" / "optimize_squad.py")
    assert script.replace("parents[1]", "parents[3]") == package


def test_config_sample_lists_every_optimization_key_and_loads() -> None:
    """The documented sample stays in step with the typed section: same keys, and it loads."""
    import tomllib

    keys = set(tomllib.loads(CONFIG_SAMPLE.read_text(encoding="utf-8"))["optimization"])
    assert keys == set(Optimization.__dataclass_fields__)
    cfg = load_config(CONFIG_SAMPLE).optimization
    assert cfg.min_players_per_turn == [3, 3] and cfg.solver_options["random_seed"] == 7


# --------------------------------------------------------------------------- 8. scenarios

SCENARIOS_TOML = """
[alpha]
roster = ["{roster}"]
cash = 2.5
upside_beta = 0.5

[beta_2]
roster = []
unlimited_trades = true
"""


def _scenario_file(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "scenarios.toml"
    path.write_text(text)
    return path


def _prev_names(df: pd.DataFrame) -> list[str]:
    return df.loc[df["in_prev_roster"] == 1, "player"].tolist()


def test_load_scenarios_splits_the_roster_from_the_config_overrides(tmp_path: Path) -> None:
    path = _scenario_file(tmp_path, SCENARIOS_TOML.format(roster='", "'.join(_prev_names(league(prev=True)))))
    scenarios = load_scenarios(path)
    assert list(scenarios) == ["alpha", "beta_2"]
    assert scenarios["alpha"].overrides == {"cash": 2.5, "upside_beta": 0.5}
    assert len(scenarios["alpha"].roster) == 10
    assert scenarios["beta_2"].roster == ()  # [] = a fresh squad, not "no roster"
    assert load_scenarios(_scenario_file(tmp_path, "[x]\ncash = 1.0\n"))["x"].roster is None


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("", "no scenarios"),
        ("[team-1]\nroster = []\n", "letters, digits and underscores"),
        ("a = 3\n", "must be a table"),
        ('[a]\nroster = "Yam Madar"\n', "list of player names"),
        ('[a]\nroster = ["X", "X"]\n', "more than once"),
    ],
)
def test_load_scenarios_rejects_malformed_files(tmp_path: Path, text: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        load_scenarios(_scenario_file(tmp_path, text))
    with pytest.raises(FileNotFoundError, match="Scenarios file"):
        load_scenarios(tmp_path / "missing.toml")


def test_select_scenarios_by_list_or_all() -> None:
    scenarios = {k: Scenario(k, None) for k in ("a", "b", "c")}
    assert list(select_scenarios(scenarios, "all")) == ["a", "b", "c"]
    assert list(select_scenarios(scenarios, "c, a")) == ["c", "a"]
    with pytest.raises(ValueError, match=r"Unknown scenario.*'z'"):
        select_scenarios(scenarios, "a,z")


def test_apply_scenario_replaces_the_roster_and_overrides_without_touching_the_inputs() -> None:
    df = league(prev=True)
    before = df.copy()
    cfg = cfg_of(100.0, 4)
    new_roster = tuple(df.loc[df["in_prev_roster"] == 0, "player"].head(10))
    sdf, scfg = apply_scenario(df, cfg, Scenario("s", new_roster, {"cash": 7.0, "downside_gamma": 2.0}))
    assert sorted(_prev_names(sdf)) == sorted(new_roster)
    assert (scfg.cash, scfg.downside_gamma, scfg.max_trades) == (7.0, 2.0, 4)  # others stay as in the base config
    pd.testing.assert_frame_equal(df, before)
    assert cfg.cash == 100.0
    fresh, _ = apply_scenario(df, cfg, Scenario("s", ()))
    assert fresh["in_prev_roster"].sum() == 0
    kept, _ = apply_scenario(df, cfg, Scenario("s", None, {"cash": 1.0}))
    assert kept["in_prev_roster"].tolist() == df["in_prev_roster"].tolist()  # no roster key: the workbook decides


def test_apply_scenario_names_unknown_players_with_suggestions_and_unknown_keys() -> None:
    df = league(prev=True)
    with pytest.raises(ValueError, match=r"\[s\].*'P001'.*'P00'"):
        apply_scenario(df, cfg_of(), Scenario("s", ("P001",)))
    with pytest.raises(ValueError, match=r"\[s\].*cashh"):
        apply_scenario(df, cfg_of(), Scenario("s", None, {"cashh": 1.0}))


def _run_scenarios_main(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, scenario_text: str, argv: list[str]) -> Path:
    inp, out_dir = tmp_path / "in.xlsx", tmp_path / "out"
    league().to_excel(inp, index=False)
    monkeypatch.setattr(opt_mod, "INPUT_PATH", inp)
    monkeypatch.setattr(opt_mod, "OUTPUT_PATH", out_dir / "optimizer_output.xlsx")
    monkeypatch.setattr(opt_mod, "SCENARIOS_PATH", _scenario_file(tmp_path, scenario_text))
    monkeypatch.setattr(opt_mod, "get_config", lambda: SimpleNamespace(optimization=cfg_of(100.0, None)))
    main(argv)
    return out_dir


def test_scenarios_write_one_output_per_key_and_record_the_scenario(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    prev = '", "'.join(_prev_names(league(prev=True)))
    text = f'[team_1]\nroster = ["{prev}"]\ncash = 0.0\nmax_trades = 2\nunlimited_trades = false\n\n[team_2]\nroster = []\n'
    out = _run_scenarios_main(monkeypatch, tmp_path, text, ["--scenarios"])
    assert sorted(p.name for p in out.iterdir()) == ["optimizer_output_team_1.xlsx", "optimizer_output_team_2.xlsx"]
    one = pd.read_excel(out / "optimizer_output_team_1.xlsx", sheet_name=None)
    two = pd.read_excel(out / "optimizer_output_team_2.xlsx", sheet_name=None)
    summary = one["summary"].set_index("metric")["value"]
    assert summary["scenario"] == "team_1" and summary["roster source"] == "scenario"
    assert summary["parameters overridden"] == "['cash', 'max_trades', 'unlimited_trades']"
    assert (summary["cash"], summary["max_trades"]) == (0.0, 2)
    assert one["selection"]["in_prev_roster"].sum() == 10  # the roster used is visible in the output
    assert two["selection"]["in_prev_roster"].sum() == 0
    assert (two["selection"]["action"] == "buy").sum() == 10  # a fresh squad buys everybody


def test_scenarios_selector_runs_only_the_named_ones(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    out = _run_scenarios_main(monkeypatch, tmp_path, "[a]\nroster = []\n[b]\nroster = []\n", ["--scenarios", "b"])
    assert [p.name for p in out.iterdir()] == ["optimizer_output_b.xlsx"]


def test_a_bad_scenario_stops_everything_before_any_solve(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    text = '[good]\nroster = []\n[bad]\nroster = ["Nobody"]\n[worse]\ncashh = 1\n'
    with pytest.raises(ValueError, match="nothing was solved") as err:
        _run_scenarios_main(monkeypatch, tmp_path, text, ["--scenarios"])
    assert "[bad]" in str(err.value) and "[worse]" in str(err.value)  # every problem is listed at once
    assert not (tmp_path / "out").exists()  # not even the good one ran


def test_plain_run_is_unchanged_and_summary_lists_every_parameter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    out = _run_main(monkeypatch, tmp_path, league(), cfg_of(100.0, None))
    assert out.name == "optimizer_output.xlsx"
    summary = pd.read_excel(out, sheet_name="summary")
    assert summary.loc[summary["metric"] == "scenario", "value"].tolist() == ["none"]
    listed = summary.loc[summary["section"] == "parameters", "metric"].tolist()
    assert listed == [f.name for f in fields(Optimization)]  # defaults included, nothing silently left out


# --------------------------------------------------------------------------- must_have / avoid


def _squad(sol) -> set[str]:
    return set(sol.table.loc[sol.table["role"] != "none", "player"])


def test_must_have_forces_a_player_in_and_avoid_forces_an_owned_player_out() -> None:
    df = league(prev=True)
    free = optimize_squad(df, cfg_of())
    owned_kept = sorted(_squad(free) & set(_prev_names(df)))
    others = sorted(set(df["player"]) - _squad(free))
    worst = df[df["player"].isin(others)].sort_values("exp_pir").iloc[0]["player"]  # a player the optimum skips
    banned = owned_kept[0]
    sol = optimize_squad(df, cfg_of(must_have=[worst], avoid=[banned]))
    squad = _squad(sol)
    assert worst in squad and banned not in squad
    assert sol.table.set_index("player").loc[banned, "action"] == "sell"
    assert sol.objective < free.objective  # the rules bind: they cost something


def test_must_have_a_player_you_do_not_own_leaves_no_baseline_instead_of_crashing() -> None:
    df = league(prev=True)
    buy = sorted(set(df["player"]) - set(_prev_names(df)))[0]
    cfg = cfg_of(must_have=[buy])  # unlimited trades
    assert opt.current_squad_objective(df, cfg) is None  # keeping the squad cannot satisfy the must_have
    assert buy in _squad(optimize_squad(df, cfg))


def test_must_have_an_owned_player_means_no_trade_for_them_and_empty_lists_change_nothing() -> None:
    df = league(prev=True)
    owned = _prev_names(df)[0]
    sol = optimize_squad(df, cfg_of(must_have=[owned]))
    assert owned in _squad(sol) and sol.table.set_index("player").loc[owned, "action"] != "sell"
    assert optimize_squad(df, cfg_of()).objective == optimize_squad(df, cfg_of(must_have=[], avoid=[])).objective


def test_player_lists_report_every_problem_at_once() -> None:
    df = league(prev=True)
    guards = df.loc[df["position"] == "G", "player"].tolist()
    cfg = cfg_of(must_have=[*guards[:5], "P0O", "P01"], avoid=["P01", "Nobody"], max_trades=1)
    with pytest.raises(ValueError) as err:
        optimize_squad(df, cfg)
    msg = str(err.value)
    assert "'P0O' is not in the input, did you mean" in msg and "'Nobody' is not in the input" in msg
    assert "in both must_have and avoid: ['P01']" in msg
    assert "must_have has 5 G, a squad has 4" in msg


def test_player_lists_reject_repeats_a_bare_string_and_too_many_from_one_club() -> None:
    df = league(prev=True)
    with pytest.raises(ValueError, match="lists \\['P01'\\] more than once"):
        optimize_squad(df, cfg_of(avoid=["P01", "P01"]))
    with pytest.raises(ValueError, match="must be a list of player names"):
        optimize_squad(df, cfg_of(must_have="P01"))


def test_must_have_checks_the_club_cap_and_the_trade_limit() -> None:
    df = league()
    df.loc[:6, "team"] = "T0"  # seven players of one club
    with pytest.raises(ValueError, match="the cap is"):
        optimize_squad(df, cfg_of(must_have=df["player"].head(7).tolist()))
    fresh = league()
    with pytest.raises(ValueError, match="needs 2 purchases, max_trades is 1"):
        optimize_squad(fresh, cfg_of(must_have=fresh["player"].head(2).tolist(), max_trades=1))


def test_a_scenario_can_set_must_have_and_avoid_and_the_summary_lists_them() -> None:
    df = league(prev=True)
    cfg = replace(cfg_of(), unlimited_trades=True)
    scenario = Scenario("pin", None, {"must_have": ["P02"], "avoid": ["P01"]})
    _, applied = apply_scenario(df, cfg, scenario)
    assert applied.must_have == ["P02"] and applied.avoid == ["P01"]
    assert cfg.must_have == []  # base config untouched


def test_summary_lists_expected_mean_pir_and_pir_per_credit_when_the_input_has_them() -> None:
    df = league(prev=True).assign(pir_mean=lambda d: d["exp_pir"] + 1.0, pir_per_crd=lambda d: d["exp_pir"] / 5.0)
    cfg = cfg_of()
    sol = optimize_squad(df, cfg)
    sheet = selection_sheet(df, sol, cfg)
    summary = summary_sheet(sol, sheet, cfg, len(df), 0.0, None, explain_credit_value(df, cfg, sol), None)
    values = summary.set_index("metric")["value"]
    assert values["expected PIR of the line-up (weighted)"] == pytest.approx(lineup_sum(sheet, "exp_pir"))
    assert values["mean PIR of the line-up (weighted)"] == pytest.approx(
        lineup_sum(sheet, "exp_pir") + 9.0
    )  # +1 on each of the weights summing to 9
    squad = sheet[sheet["selected"] == 1]
    assert values["PIR per credit, average of the squad"] == pytest.approx((squad["exp_pir"] / 5.0).mean())
    bare = summary_sheet(
        sol, selection_sheet(league(prev=True), sol, cfg), cfg, 30, 0.0, None, explain_credit_value(df, cfg, sol), None
    )
    assert not any("PIR per credit" in m for m in bare["metric"])  # columns absent: rows absent
