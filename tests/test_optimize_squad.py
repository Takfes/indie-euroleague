"""Tests for the single-round squad optimizer (Pyomo MILP, HiGHS).

The central check is brute-force equivalence: on small random universes the MILP's optimum must
equal an independent enumeration of every legal squad x role assignment x captain, written here
from `docs/rules.md` (explicit list of legal formations, no bounds, no solver). Every solution the
model returns is also re-scored by that independent evaluator, so extraction bugs (wrong roles,
captain off the court) surface too. The solver is never mocked.
"""

from __future__ import annotations

import itertools
import math
import random
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from eupy.optimize.optimize_squad import (
    FORMATION_BOUNDS,
    MAX_PER_CLUB,
    OUTPUT_COLUMNS,
    ROLE_ORDER,
    _prepare,
    build_model,
    load_config,
    main,
    optimize_squad,
    solve,
    validate_input,
)

SAMPLE = Path(__file__).parent / "data" / "optimizer_input_sample.csv"
CONFIG_SAMPLE = Path(__file__).parent / "data" / "optimizer_config_sample.toml"

# Written from rules.md, deliberately not derived from FORMATION_BOUNDS.
LEGAL_FORMATIONS = {(2, 2, 1), (1, 2, 2), (2, 1, 2), (1, 3, 1), (3, 1, 1)}
NEED = {"G": 4, "F": 4, "C": 2}
EPS = 1e-6


# --------------------------------------------------------------------------- independent reference


class BruteForce:
    """Exhaustive optimum over squads x roles x captain for one universe (no MILP involved)."""

    def __init__(self, u: pd.DataFrame, use_adj: bool = True) -> None:
        self.u = u.reset_index(drop=True)
        self.pos = self.u["position"].tolist()
        self.price = self.u["price"].tolist()
        self.exp = self.u["exp_pir"].tolist()
        adj = self.u["exp_pir_adj"] if use_adj and "exp_pir_adj" in self.u else self.u["exp_pir"]
        self.adj = adj.fillna(self.u["exp_pir"]).tolist()
        self.team = self.u["team"].tolist()
        self.prev = frozenset(i for i, p in enumerate(self.u["in_prev_roster"]) if p == 1)
        self._roles: dict[tuple[int, ...], tuple[float, tuple]] = {}

    def lineup_value(self, starters, sixth, captain, squad_players) -> float:
        """Points of one role assignment, straight from the rules.md scoring table."""
        bench = [i for i in squad_players if i not in starters and i != sixth]
        pts = sum(self.adj[i] for i in starters) + self.adj[sixth] + self.adj[captain]  # captain scores x2
        return pts + sum(0.5 * self.exp[i] for i in bench)

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

    def squad_extra(self, squad: frozenset[int], w: float) -> float:
        """w * expected price change of the squad's players."""
        return w * sum((self.exp[i] - 1.1 * self.price[i]) / 25 for i in squad)

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


def rescore(bf: BruteForce, table: pd.DataFrame, cash: float, max_trades: int | None, w: float) -> float:
    """Check a model output table is a legal squad/lineup and return its value under the rules."""
    idx = {name: i for i, name in enumerate(bf.u["name"])}
    squad_rows = table[table["action"] != "sell"]
    squad = frozenset(idx[n] for n in squad_rows["name"])
    assert len(squad) == 10
    assert sorted(bf.pos[i] for i in squad) == sorted(p for p, n in NEED.items() for _ in range(n))
    assert bf.legal_squad(squad, cash, max_trades)
    # Actions are consistent with the previous roster (no player both sold and bought).
    for name, action in zip(table["name"], table["action"], strict=True):
        was_owned = idx[name] in bf.prev
        assert action in (("keep", "sell") if was_owned else ("buy",)), (name, action)
    starters = tuple(idx[n] for n in table.loc[table["role"] == "starter", "name"])
    (sixth,) = (idx[n] for n in table.loc[table["role"] == "sixth", "name"])
    (captain,) = (idx[n] for n in table.loc[table["is_captain"] == 1, "name"])
    assert len(starters) == 5 and captain in starters
    assert tuple(sum(bf.pos[i] == p for i in starters) for p in ("G", "F", "C")) in LEGAL_FORMATIONS
    assert bf.adj[captain] == max(bf.adj[i] for i in starters)
    assert (table.loc[table["action"] == "sell", "role"] == "none").all()
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
            adj = round(exp + rng.gauss(0, 5), 1) if rng.random() < 0.5 else float("nan")
            rows.append({
                "name": f"{pos} player {k}",
                "team": clubs[len(rows)],
                "position": pos,
                "turn": rng.choice([1, 2, 3]),
                "price": price,
                "exp_pir": exp,
                "exp_pir_adj": adj,
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


def model_table(u: pd.DataFrame, cash: float, max_trades: int | None, w: float, use_adj: bool = True):
    df = u if use_adj else u.drop(columns="exp_pir_adj")
    return optimize_squad(df, cash=cash, max_trades=max_trades, w_budget=w)


def make_universe(spec: list[tuple[str, str, float, float, int]]) -> pd.DataFrame:
    """Rows from (position, team, price, exp_pir, in_prev_roster) tuples."""
    return pd.DataFrame([
        {
            "name": f"p{k}",
            "team": team,
            "position": pos,
            "turn": 1,
            "price": price,
            "exp_pir": exp,
            "in_prev_roster": prev,
        }
        for k, (pos, team, price, exp, prev) in enumerate(spec)
    ])


# --------------------------------------------------------------------------- 1. brute-force equivalence

SEEDS = range(14)  # seed 13 is the one where w_budget changes the decision
W = 3.0  # early-season capital-growth weight per the spec


def _instances():
    """(seed, universe, cash) with a small bank so the budget can bind; tests use trade limit 2 and w=W."""
    for seed in SEEDS:
        rng = random.Random(1000 + seed)  # noqa: S311 -- seeded test data, not crypto
        yield seed, random_universe(seed), round(rng.uniform(0, 2), 1)


def test_model_matches_brute_force_and_each_feature_matters() -> None:
    """Model optimum == enumeration optimum in every config; each feature changes the answer somewhere."""
    matters = {"budget": False, "trade_limit": False, "w_budget": False, "exp_pir_adj": False}
    for seed, u, cash in _instances():
        bf_adj, bf_exp = BruteForce(u, use_adj=True), BruteForce(u, use_adj=False)
        configs = {
            "base": (bf_adj, True, cash, 2, W),
            "no_budget": (bf_adj, True, 1e6, 2, W),
            "no_trade_limit": (bf_adj, True, cash, None, W),
            "w0": (bf_adj, True, cash, 2, 0.0),
            "no_adj": (bf_exp, False, cash, 2, W),
        }
        results = {}
        for name, (bf, use_adj, c, k, w) in configs.items():
            ref_value, ref_squad, ref_roles = bf.solve(c, k, w)
            assert ref_value > -math.inf, (seed, name, "instance infeasible")
            sol = model_table(u, c, k, w, use_adj)
            assert sol.objective == pytest.approx(ref_value, abs=EPS), (seed, name)
            assert rescore(bf, sol.table, c, k, w) == pytest.approx(sol.objective, abs=EPS), (seed, name)
            results[name] = (ref_value, ref_squad, ref_roles)

        base_value, base_squad, (starters, sixth, captain) = results["base"]
        players = tuple(sorted(base_squad))
        # Constraints matter if relaxing them strictly improves the optimum.
        matters["budget"] |= results["no_budget"][0] > base_value + EPS
        matters["trade_limit"] |= results["no_trade_limit"][0] > base_value + EPS
        # w / adj matter if the base decision is strictly suboptimal when judged without them.
        base_at_w0 = bf_adj.best_roles(players)[0] + bf_adj.squad_extra(base_squad, 0.0)
        matters["w_budget"] |= base_at_w0 < results["w0"][0] - EPS
        base_at_exp = bf_exp.lineup_value(starters, sixth, captain, players) + bf_exp.squad_extra(base_squad, W)
        matters["exp_pir_adj"] |= base_at_exp < results["no_adj"][0] - EPS
    assert all(matters.values()), matters


@pytest.mark.parametrize(("var", "owned"), [("b", 1), ("s", 0)], ids=["rebuy_owned", "sell_unowned"])
def test_player_cannot_be_both_sold_and_bought(var: str, owned: int) -> None:
    """Forcing a sell+re-buy of one player must be infeasible.

    With binary x, s = b = 1 is cost- and score-neutral, so no optimum reveals it; only forcing it
    shows whether the valid-action constraints exist. Unlimited trades and ample cash, so nothing
    else can make the model infeasible.
    """
    _, u, _ = next(_instances())
    df = _prepare(u)
    m = build_model(df, cash=100.0, max_trades=None, w_budget=0.0)
    i = int(df.index[df["in_prev_roster"] == owned][0])
    getattr(m, var)[i].fix(1)
    with pytest.raises(RuntimeError, match="termination condition infeasible"):
        solve(m)


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
    sol = optimize_squad(u, cash=100.0, max_trades=None)
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
    assert optimize_squad(u, cash=0.0, max_trades=np.int64(4)).trades == 4  # numpy ints are valid limits
    assert optimize_squad(u, cash=0.0, max_trades=None).trades == 10


def test_row_order_does_not_change_the_solution() -> None:
    """Many interchangeable candidates (ties everywhere): a shuffled input must give the same squad."""
    spec = [(pos, f"T{k % 4}", 5.0, 10.0, 0) for pos, n in (("G", 8), ("F", 8), ("C", 4)) for k in range(n)]
    u = make_universe(spec)
    a = optimize_squad(u, cash=100.0, max_trades=None).table
    b = optimize_squad(u.sample(frac=1, random_state=0), cash=100.0, max_trades=None).table
    pd.testing.assert_frame_equal(a, b)


def test_fresh_team_with_trade_limit_is_infeasible_and_says_so() -> None:
    """Round 1 needs 10 buys; a standard 4-trade limit must fail loudly, not return a partial squad."""
    with pytest.raises(RuntimeError, match="termination condition infeasible"):
        optimize_squad(pd.read_csv(SAMPLE), cash=100.0, max_trades=4)


# --------------------------------------------------------------------------- 3. validation


def _sample() -> pd.DataFrame:
    return pd.read_csv(SAMPLE)


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
    "missing column": (lambda d: d.drop(columns="team"), "missing required column"),
    "NaN required": (lambda d: _set(d, 0, "exp_pir", float("nan")), "NaN"),
    "non-numeric price": (lambda d: _set(d, 0, "price", "cheap"), "non-numeric"),
    "empty name": (lambda d: _set(d, 0, "name", ""), "empty name"),
    "duplicate name": (lambda d: _set(d, 1, "name", d.loc[0, "name"]), "duplicate name"),
    "bad position": (lambda d: _set(d, 0, "position", "PG"), "unknown position"),
    "price zero": (lambda d: _set(d, 0, "price", 0.0), "price <= 0"),
    "turn zero": (lambda d: _set(d, 0, "turn", 0), r"turn is not an integer >= 1"),
    "turn fractional": (lambda d: _set(d, 0, "turn", 1.5), r"turn is not an integer >= 1"),
    "prev flag 2": (lambda d: _set(d, 0, "in_prev_roster", 2), r"outside \{0, 1\}"),
    "non-numeric adj": (lambda d: _set(d, 0, "exp_pir_adj", "n/a"), r"non-numeric column\(s\) \['exp_pir_adj'\]"),
    "inf price": (lambda d: _set(d, 0, "price", math.inf), r"infinite values in column\(s\) \['price'\]"),
    "-inf exp_pir": (lambda d: _set(d, 0, "exp_pir", -math.inf), r"infinite values in column\(s\) \['exp_pir'\]"),
    "inf adj": (lambda d: _set(d, 0, "exp_pir_adj", math.inf), r"infinite values in column\(s\) \['exp_pir_adj'\]"),
    "too few centers": (
        lambda d: d[~d["name"].isin(["Center Two", "Center Three", "Center Four"])],
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
        validate_input(mutate(_sample()))


@pytest.mark.parametrize(
    "df", [_sample(), _sample().drop(columns="exp_pir_adj"), _prev_roster(_sample(), 10)], ids=["r1", "no_adj", "prev"]
)
def test_valid_input_passes(df: pd.DataFrame) -> None:
    validate_input(df)


@pytest.mark.parametrize(
    "kwargs", [{"cash": -1.0}, {"max_trades": -1}, {"max_trades": 2.5}, {"w_budget": -0.1}], ids=str
)
def test_invalid_scalars_raise(kwargs: dict) -> None:
    args = {"cash": 100.0, "max_trades": None, "w_budget": 0.0, **kwargs}
    with pytest.raises(ValueError):
        optimize_squad(_sample(), **args)


# --------------------------------------------------------------------------- 4-5. size, determinism, CLI


def synthetic_league(seed: int = 7) -> pd.DataFrame:
    """~540 candidates over 20 clubs (13 G / 13 F / 8 C each) with a legal previous roster."""
    rng = random.Random(seed)  # noqa: S311 -- seeded test data, not crypto
    rows = []
    for club in range(20):
        for pos, n in (("G", 13), ("F", 13), ("C", 8)):
            for _ in range(n):
                price = round(rng.uniform(1, 25), 1)
                exp = 1.1 * price + rng.gauss(0, 5)
                rows.append({
                    "name": f"{pos}-{club}-{len(rows)}",
                    "team": f"T{club:02d}",
                    "position": pos,
                    "turn": rng.choice([1, 2]),
                    "price": price,
                    "exp_pir": round(exp, 1),
                    "exp_pir_adj": round(exp + rng.gauss(0, 3), 1) if rng.random() < 0.3 else None,
                    "in_prev_roster": 0,
                })
    u = pd.DataFrame(rows)
    prev = [i for p, n in NEED.items() for i in rng.sample(list(u.index[u["position"] == p]), n)]
    u.loc[prev, "in_prev_roster"] = 1
    return u


def test_realistic_size_solves_fast_and_is_byte_identical(tmp_path: Path) -> None:
    u = synthetic_league()
    assert len(u) == 680
    inp = tmp_path / "league.csv"
    u.to_csv(inp, index=False)
    outs = []
    for k in range(2):
        out = tmp_path / f"out{k}.csv"
        started = time.perf_counter()
        main(["--input", str(inp), "--cash", "1.5", "--max-trades", "4", "--w-budget", "3.0", "--out", str(out)])
        assert time.perf_counter() - started < 30
        outs.append(out.read_bytes())
    assert outs[0] == outs[1]


def test_cli_on_sample_writes_sorted_csv_and_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "squad.csv"
    main(["--input", str(SAMPLE), "--unlimited-trades", "--out", str(out)])
    table = pd.read_csv(out)
    assert list(table.columns) == OUTPUT_COLUMNS
    ranks = table["role"].map(ROLE_ORDER.index)
    assert list(zip(ranks, table["name"], strict=True)) == sorted(zip(ranks, table["name"], strict=True))
    assert table["role"].value_counts().to_dict() == {"starter": 5, "bench": 4, "sixth": 1}
    report = capsys.readouterr().out
    for fragment in ("Objective", "Trades 10 / unlimited", "cash 100.00 ->", "Starting five:", "(C)", "6th man:"):
        assert fragment in report


# --------------------------------------------------------------------------- 6. config file


def test_config_values_used_unless_a_cli_flag_overrides(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A config's `cash` is used when `--cash` is not passed; `--cash` overrides it when it is."""
    config = tmp_path / "config.toml"
    config.write_text(f'input = "{SAMPLE}"\ncash = 90.0\nunlimited_trades = true\n', encoding="utf-8")
    out = tmp_path / "out.csv"

    main(["--config", str(config), "--out", str(out)])
    assert "cash 90.00 ->" in capsys.readouterr().out

    main(["--config", str(config), "--cash", "65.0", "--out", str(out)])
    assert "cash 65.00 ->" in capsys.readouterr().out


def test_unrecognized_config_key_raises(tmp_path: Path) -> None:
    """A typo'd config key (`max_trade` instead of `max_trades`) must not be silently ignored."""
    config = tmp_path / "config.toml"
    config.write_text("cash = 100.0\nmax_trade = 4\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Unrecognized config key"):
        load_config(config)


def test_config_sample_round_trips_through_cli(tmp_path: Path) -> None:
    """The documented sample config loads cleanly; --input/--unlimited-trades override its Round-1 mismatch."""
    out = tmp_path / "out.csv"
    main(["--config", str(CONFIG_SAMPLE), "--input", str(SAMPLE), "--unlimited-trades", "--out", str(out)])
    assert out.is_file()


# --------------------------------------------------------------------------- 7. Excel input


def test_excel_input_matches_csv(tmp_path: Path) -> None:
    """The same content as an .xlsx file gives a byte-identical output to the .csv sample."""
    xlsx = tmp_path / "sample.xlsx"
    pd.read_csv(SAMPLE).to_excel(xlsx, index=False)
    out_csv = tmp_path / "out_csv.csv"
    out_xlsx = tmp_path / "out_xlsx.csv"
    main(["--input", str(SAMPLE), "--unlimited-trades", "--out", str(out_csv)])
    main(["--input", str(xlsx), "--unlimited-trades", "--out", str(out_xlsx)])
    assert out_csv.read_bytes() == out_xlsx.read_bytes()
