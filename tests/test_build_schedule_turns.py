"""Tests for the schedule turn assignment and per-team round/turn table."""

from __future__ import annotations

import pandas as pd
import pytest

from eupy.schedule.build_schedule_turns import assign_turns, main, team_round_turns


def _games(rows: list[tuple[int, str, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["gameday", "date", "homecode", "awaycode"])


def test_two_dates_split_and_three_dates_put_early_game_in_t1():
    df = _games([
        (1, "Sep 24, 2026", "A", "B"),
        (1, "Sep 25, 2026", "C", "D"),
        (2, "Oct 07, 2026", "A", "C"),  # lone Wednesday
        (2, "Oct 08, 2026", "B", "D"),
        (2, "Oct 09, 2026", "E", "F"),
    ])
    assert assign_turns(df)["turn"].tolist() == ["T1", "T2", "T1", "T1", "T2"]


def test_dates_sort_chronologically_across_month_and_year_boundaries():
    # Lexical order would put "Dec 31, 2026" after "Jan 01, 2027"; dates must be parsed.
    df = _games([(1, "Dec 31, 2026", "A", "B"), (1, "Jan 01, 2027", "C", "D")])
    assert assign_turns(df)["turn"].tolist() == ["T1", "T2"]


def test_single_date_round_is_all_t1_and_input_is_unchanged():
    df = _games([(1, "Sep 24, 2026", "A", "B")])
    out = assign_turns(df)
    assert out["turn"].tolist() == ["T1"]
    assert "turn" not in df.columns


def test_bad_date_and_missing_column_raise():
    with pytest.raises(ValueError, match="Sep 24, 2026"):
        assign_turns(_games([(1, "2026-09-24", "A", "B")]))
    with pytest.raises(ValueError, match="missing required columns"):
        assign_turns(_games([(1, "Sep 24, 2026", "A", "B")]).drop(columns="awaycode"))


def test_team_round_turns_shape_order_and_duplicate_guard():
    df = assign_turns(
        _games([(1, "Sep 24, 2026", "B", "A"), (1, "Sep 25, 2026", "C", "D"), (2, "Oct 01, 2026", "A", "C")])
    )
    out = team_round_turns(df)
    assert list(out.columns) == ["team", "round", "turn"]
    assert out.values.tolist() == [
        ["A", 1, "T1"],
        ["B", 1, "T1"],
        ["C", 1, "T2"],
        ["D", 1, "T2"],
        ["A", 2, "T1"],
        ["C", 2, "T1"],
    ]
    with pytest.raises(ValueError, match="more than once"):
        team_round_turns(assign_turns(_games([(1, "Sep 24, 2026", "A", "B"), (1, "Sep 25, 2026", "A", "C")])))


def test_main_writes_both_files_with_raw_values_verbatim(tmp_path):
    raw = tmp_path / "schedule.csv"
    raw.write_text(
        'gameday,round,date,homecode,awaycode,played\n1,RS,"Sep 24, 2026",A,B,true\n1,RS,"Sep 25, 2026",C,D,false\n'
    )
    s_out, t_out = tmp_path / "s.csv", tmp_path / "t.csv"
    main(["--input", str(raw), "--out-schedule", str(s_out), "--out-team-turns", str(t_out)])
    s = pd.read_csv(s_out, dtype=str)
    assert list(s.columns) == ["gameday", "round", "date", "homecode", "awaycode", "played", "turn"]
    assert s["played"].tolist() == ["true", "false"]  # not coerced to bool
    assert s["date"].tolist() == ["Sep 24, 2026", "Sep 25, 2026"]
    assert len(pd.read_csv(t_out)) == 4
