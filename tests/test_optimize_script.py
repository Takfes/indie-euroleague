"""Tests for `scripts/optimize.py`: workbook input, turn-distribution constraint, two-sheet output.

The solver is never mocked. The universe is small and built so that the turn minimum visibly binds.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from eupy.config import load_config
from eupy.config.defaults import Optimization

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "optimize.py"
spec = importlib.util.spec_from_file_location("optimize_script", SCRIPT)
opt = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = opt  # dataclasses resolve string annotations through sys.modules
spec.loader.exec_module(opt)

CFG = Optimization(cash=100.0, max_trades=10, unlimited_trades=False, credit_value=0.0)


def universe(*, prev: bool = False) -> pd.DataFrame:
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


def test_load_input_keeps_workbook_columns_and_model_frame_maps_them(tmp_path: Path) -> None:
    path = tmp_path / "in.xlsx"
    universe().to_excel(path, index=False)
    df = opt.load_input(path)
    assert "crd_last" in df.columns and "price" not in df.columns  # input is not renamed
    frame = opt.model_frame(df, CFG)
    assert list(frame.columns) == ["player", "team", "position", "turn", "price", "in_prev_roster", "target"]
    assert (frame["price"] == df["crd_last"]).all() and (frame["target"] == df["exp_pir"]).all()


def test_load_input_missing_file_says_what_to_do(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="optimizer_input"):
        opt.load_input(tmp_path / "nope.xlsx")


def test_turn_minimum_binds_and_costs_objective() -> None:
    df = universe()
    free = opt.optimize_squad(df, CFG)
    forced = opt.optimize_squad(df, replace(CFG, min_players_per_turn=[0, 4]))
    assert squad_turns(free).count(2) < 4  # the constraint is not vacuous
    assert squad_turns(forced).count(2) >= 4
    assert forced.objective < free.objective


def test_turn_minimum_for_both_turns() -> None:
    df = universe()
    sol = opt.optimize_squad(df, replace(CFG, min_players_per_turn=[3, 3]))
    turns = squad_turns(sol)
    assert turns.count(1) >= 3 and turns.count(2) >= 3


def test_turn_minimum_fails_early_when_a_turn_has_too_few_candidates() -> None:
    df = universe()
    df.loc[df.turn == 2, "turn"] = 1  # no turn-2 player left
    with pytest.raises(ValueError, match="min_players_per_turn needs more candidates"):
        opt.optimize_squad(df, replace(CFG, min_players_per_turn=[0, 1]))


@pytest.mark.parametrize("mins", [[6, 5], [-1], [True], [1.5]])
def test_bad_turn_minimums_are_rejected(mins: list) -> None:
    df = universe()
    with pytest.raises(ValueError, match="min_players_per_turn"):
        opt.optimize_squad(df, replace(CFG, min_players_per_turn=mins))


def test_selection_sheet_keeps_every_input_row_and_column() -> None:
    raw = universe(prev=True)
    sol = opt.optimize_squad(raw, replace(CFG, max_trades=2))
    sheet = opt.selection_sheet(raw, sol, CFG)
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
    raw = universe(prev=True)
    sol = opt.optimize_squad(raw, CFG)
    baseline = opt.current_squad_objective(raw, CFG)
    assert baseline is not None and sol.objective >= baseline
    summary = summarize(raw, sol, CFG, baseline)
    benefit = summary.loc[summary["metric"].str.startswith("benefit"), "value"].item()
    assert benefit == pytest.approx(sol.objective - baseline)


def test_no_baseline_for_a_fresh_team_or_an_infeasible_current_squad() -> None:
    fresh = universe()
    assert opt.current_squad_objective(fresh, replace(CFG, unlimited_trades=True)) is None
    prev = universe(prev=True)  # current squad is all turn 1
    assert opt.current_squad_objective(prev, replace(CFG, min_players_per_turn=[0, 2])) is None


def test_write_output_makes_two_sheets(tmp_path: Path) -> None:
    raw = universe(prev=True)
    sol = opt.optimize_squad(raw, CFG)
    summary = summarize(raw, sol, CFG, None)
    out = tmp_path / "deep" / "optimizer_output.xlsx"
    opt.write_output(opt.selection_sheet(raw, sol, CFG), summary, out)
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
    df = universe()
    df["inverse"] = -df["exp_pir"]  # the best players by exp_pir are the worst by "inverse"
    by_pir = opt.optimize_squad(df, CFG)
    by_inverse = opt.optimize_squad(df, replace(CFG, target="inverse"))
    chosen = lambda sol: set(sol.table.loc[sol.table["role"] != "none", "player"])
    assert chosen(by_pir) != chosen(by_inverse)
    assert by_pir.objective > 0 > by_inverse.objective


def test_missing_mapped_column_names_the_fix() -> None:
    with pytest.raises(ValueError, match="no_such_column"):
        opt.optimize_squad(universe(), replace(CFG, target="no_such_column"))
    with pytest.raises(ValueError, match="crd_last"):
        opt.optimize_squad(universe().drop(columns="crd_last"), CFG)


@pytest.mark.parametrize("column", ["exp_pir", "crd_last", "team"])
def test_empty_cells_in_a_required_column_are_rejected(column: str) -> None:
    df = universe()
    df.loc[3, column] = None
    with pytest.raises(ValueError, match="Empty cells"):
        opt.optimize_squad(df, CFG)


def test_zero_turn_minimums_are_the_same_as_none() -> None:
    df = universe()
    assert opt.optimize_squad(df, replace(CFG, min_players_per_turn=[0, 0])).objective == pytest.approx(
        opt.optimize_squad(df, CFG).objective
    )


def test_components_follow_the_documented_formulas() -> None:
    frame = pd.DataFrame({
        "price": [10.0, 5.0],
        "target": [20.0, 6.0],
        "std": [4.0, 2.0],
        "p90": [30.0, 5.0],
        "p10": [8.0, 9.0],
    })
    base = replace(CFG, credit_value=3.0, robustness_lambda=0.5, upside_beta=0.25, downside_gamma=0.1)
    growth = [(20.0 - 11.0) / 25, (6.0 - 5.5) / 25]
    cases = {
        "vanilla": [0.0, 0.0],
        "robustness": [-2.0, -1.0],  # -lambda * std
        "upside": [0.25 * 10.0, 0.0],  # positive part: p90 below target adds nothing
        "downside": [-0.1 * 12.0, 0.0],  # positive part: target below p10 costs nothing
    }
    for mode, risk in cases.items():
        comps = opt.objective_components(frame, replace(base, objective_mode=mode))
        assert comps["risk_adjustment"].tolist() == pytest.approx(risk), mode
        assert comps["score"].tolist() == pytest.approx([20.0 + risk[0], 6.0 + risk[1]]), mode
        assert comps["credit_growth"].tolist() == pytest.approx(growth)
        assert comps["growth_value"].tolist() == pytest.approx([3.0 * g for g in growth])


def test_robustness_trades_expected_value_for_lower_volatility() -> None:
    df = universe()
    vanilla = opt.optimize_squad(df, CFG)
    robust_cfg = replace(CFG, objective_mode="robustness", robustness_lambda=3.0)
    robust = opt.optimize_squad(df, robust_cfg)
    std = df.set_index("player")["pir_std"]

    def lineup_std(sol) -> float:
        return float(std[sol.table.loc[sol.table["role"] != "none", "player"]].sum())

    assert lineup_std(robust) < lineup_std(vanilla)
    assert robust.objective < vanilla.objective  # the penalty is paid


def test_mode_only_needs_its_own_risk_column() -> None:
    no_risk = universe().drop(columns=["pir_std", "pir_p90", "pir_p10"])
    opt.optimize_squad(no_risk, CFG)  # vanilla: fine
    with pytest.raises(ValueError, match="pir_std"):
        opt.optimize_squad(no_risk, replace(CFG, objective_mode="robustness"))
    with pytest.raises(ValueError, match="pir_p10"):
        opt.optimize_squad(universe().drop(columns="pir_p10"), replace(CFG, objective_mode="downside"))


def test_empty_risk_cell_is_an_error_only_in_the_mode_that_uses_it() -> None:
    df = universe()
    df.loc[2, "pir_std"] = None
    opt.optimize_squad(df, CFG)  # vanilla ignores pir_std
    with pytest.raises(ValueError, match="Empty cells"):
        opt.optimize_squad(df, replace(CFG, objective_mode="robustness", robustness_lambda=1.0))


@pytest.mark.parametrize(
    "bad", [{"objective_mode": "bold"}, {"robustness_lambda": -1.0}, {"upside_beta": float("nan")}]
)
def test_bad_objective_parameters_are_rejected(bad: dict) -> None:
    with pytest.raises(ValueError, match="objective_mode|robustness_lambda|upside_beta"):
        opt.optimize_squad(universe(), replace(CFG, **bad))


def test_credit_value_adds_growth_and_selection_sheet_shows_components() -> None:
    df = universe()
    cfg = replace(CFG, credit_value=3.0)
    sol = opt.optimize_squad(df, cfg)
    assert sol.growth_term == pytest.approx(3.0 * sol.growth_credits)
    assert sol.objective == pytest.approx(sol.active_points + sol.growth_term)
    sheet = opt.selection_sheet(df, sol, cfg)
    squad = sheet[sheet["selected"] == 1]
    assert squad["opt_growth_value"].sum() == pytest.approx(sol.growth_term)
    assert (sheet["opt_score"] == sheet["opt_target"] + sheet["opt_risk_adjustment"]).all()


def test_summary_compares_with_vanilla_only_when_the_run_is_not_vanilla() -> None:
    raw = universe()
    assert opt.vanilla_config(CFG) is None
    cfg = replace(CFG, objective_mode="robustness", robustness_lambda=3.0)
    sol = opt.optimize_squad(raw, cfg)
    metrics = summarize(raw, sol, cfg, None)["metric"].tolist()
    assert "players different from the vanilla squad" in metrics
    assert (
        "players different from the vanilla squad"
        not in summarize(raw, opt.optimize_squad(raw, CFG), CFG, None)["metric"].tolist()
    )
