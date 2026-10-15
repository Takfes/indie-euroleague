"""Tests for the Kaggle + live-delta box-score merge."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

from eupy.quality import DataQualityError
from eupy.transform.merge_boxscore import main
from eupy.transform.merge_common import load_base, load_deltas

HEADER = ["game_player_id", "game_id", "season_code", "is_starter", "points"]


def write_csv(path: Path, rows: list[list[str]], header: list[str] = HEADER) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([header, *rows])


def read_ids(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8") as f:
        return [r["game_player_id"] for r in csv.DictReader(f)]


@pytest.fixture
def raw(tmp_path: Path) -> tuple[Path, Path]:
    kaggle = tmp_path / "kaggle.csv"
    write_csv(
        kaggle,
        [
            ["E2024_1_A", "E2024_1", "E2024", "1.0", "9"],
            ["E2025_1_A", "E2025_1", "E2025", "1.0", "10"],
            ["E2025_1_B", "E2025_1", "E2025", "0.0", "4"],
        ],
    )
    return kaggle, tmp_path / "live"


def run_main(monkeypatch: pytest.MonkeyPatch, kaggle: Path, live: Path, out: Path, *extra: str) -> None:
    argv = ["prog", "--kaggle", str(kaggle), "--live-dir", str(live), "--out", str(out), *extra]
    monkeypatch.setattr(sys, "argv", argv)
    main()


def test_full_history_by_default_and_deltas_in_timestamp_order(
    raw: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kaggle, live = raw
    # Written out of order on purpose: filename order, not creation order, decides.
    write_csv(live / "E2026_delta_20260929T100000Z.csv", [["E2026_2_A", "E2026_2", "E2026", "1", "7"]])
    write_csv(live / "E2026_delta_20260927T100000Z.csv", [["E2026_1_A", "E2026_1", "E2026", "0", "3"]])
    out = tmp_path / "out.csv"
    run_main(monkeypatch, kaggle, live, out)
    assert read_ids(out) == ["E2024_1_A", "E2025_1_A", "E2025_1_B", "E2026_1_A", "E2026_2_A"]


def test_base_season_restricts_kaggle_rows(raw: tuple[Path, Path], tmp_path: Path, monkeypatch) -> None:
    kaggle, live = raw
    out = tmp_path / "out.csv"
    live.mkdir()
    run_main(monkeypatch, kaggle, live, out, "--base-season", "E2025")
    assert read_ids(out) == ["E2025_1_A", "E2025_1_B"]


def test_values_copied_as_text(raw: tuple[Path, Path]) -> None:
    kaggle, live = raw
    write_csv(live / "E2026_delta_1.csv", [["E2026_1_A", "E2026_1", "E2026", "1", "3"]])
    header, base = load_base(kaggle, "E2025")
    rows = [*base, *load_deltas(live, header)]
    assert [r["is_starter"] for r in rows] == ["1.0", "0.0", "1"]  # no float/int normalization


def test_schema_mismatch_raises(raw: tuple[Path, Path]) -> None:
    kaggle, live = raw
    write_csv(live / "E2026_delta_1.csv", [["x", "y", "E2026", "1"]], header=HEADER[:-1])
    header, _ = load_base(kaggle, "E2025")
    with pytest.raises(ValueError, match=r"Missing: \['points'\]"):
        load_deltas(live, header)


def test_duplicate_key_across_deltas_fails_and_writes_nothing(
    raw: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kaggle, live = raw
    write_csv(live / "E2026_delta_1.csv", [["E2026_1_A", "E2026_1", "E2026", "1", "3"]])
    write_csv(live / "E2026_delta_2.csv", [["E2026_1_A", "E2026_1", "E2026", "1", "5"]])
    out = tmp_path / "out.csv"
    with pytest.raises(DataQualityError, match="duplicate"):
        run_main(monkeypatch, kaggle, live, out)
    assert not out.exists()


def test_unknown_base_season_raises(raw: tuple[Path, Path]) -> None:
    with pytest.raises(ValueError, match="E1999"):
        load_base(raw[0], "E1999")


def test_no_deltas_yields_kaggle_only(raw: tuple[Path, Path], tmp_path: Path, monkeypatch) -> None:
    kaggle, live = raw
    live.mkdir()
    out = tmp_path / "out.csv"
    run_main(monkeypatch, kaggle, live, out)
    assert len(read_ids(out)) == 3


def test_kaggle_rows_of_a_season_covered_by_a_delta_are_replaced(
    raw: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kaggle, live = raw
    # Kaggle already holds E2026_1 (as its dump started covering the live season); the delta holds E2026_1 and E2026_2.
    write_csv(
        kaggle,
        [
            ["E2025_1_A", "E2025_1", "E2025", "1.0", "10"],
            ["E2026_1_A", "E2026_1", "E2026", "1.0", "6"],
        ],
    )
    write_csv(
        live / "E2026_delta_1.csv",
        [["E2026_1_A", "E2026_1", "E2026", "1", "7"], ["E2026_2_A", "E2026_2", "E2026", "1", "3"]],
    )
    out = tmp_path / "out.csv"
    run_main(monkeypatch, kaggle, live, out)
    assert read_ids(out) == ["E2025_1_A", "E2026_1_A", "E2026_2_A"]
    with out.open(newline="", encoding="utf-8") as f:
        points = {r["game_player_id"]: r["points"] for r in csv.DictReader(f)}
    assert points["E2026_1_A"] == "7"  # the live row wins over the Kaggle one
