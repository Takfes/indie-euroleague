"""Tests for the Kaggle-base + live-delta box-score append."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

from eupy.transform.append_live_boxscores import append_rows, main
from eupy.transform.live_append import load_base, load_deltas

HEADER = ["game_player_id", "game_id", "season_code", "is_starter", "points"]


def write_csv(path: Path, rows: list[list[str]], header: list[str] = HEADER) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([header, *rows])


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


def test_base_season_only_and_deltas_in_timestamp_order(
    raw: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kaggle, live = raw
    # Written out of order on purpose: filename order, not creation order, decides.
    write_csv(live / "E2026_delta_20260929T100000Z.csv", [["E2026_2_A", "E2026_2", "E2026", "1", "7"]])
    write_csv(live / "E2026_delta_20260927T100000Z.csv", [["E2026_1_A", "E2026_1", "E2026", "0", "3"]])
    out = tmp_path / "out.csv"
    monkeypatch.setattr(sys, "argv", ["prog", "--kaggle", str(kaggle), "--live-dir", str(live), "--out", str(out)])
    main()
    with out.open(newline="", encoding="utf-8") as f:
        ids = [r["game_player_id"] for r in csv.DictReader(f)]
    assert ids == ["E2025_1_A", "E2025_1_B", "E2026_1_A", "E2026_2_A"]  # E2024 excluded


def test_values_copied_as_text(raw: tuple[Path, Path]) -> None:
    kaggle, live = raw
    write_csv(live / "E2026_delta_1.csv", [["E2026_1_A", "E2026_1", "E2026", "1", "3"]])
    header, base = load_base(kaggle, "E2025")
    rows, _ = append_rows(base, load_deltas(live, header))
    assert [r["is_starter"] for r in rows] == ["1.0", "0.0", "1"]  # no float/int normalization


def test_schema_mismatch_raises(raw: tuple[Path, Path]) -> None:
    kaggle, live = raw
    write_csv(live / "E2026_delta_1.csv", [["x", "y", "E2026", "1"]], header=HEADER[:-1])
    header, _ = load_base(kaggle, "E2025")
    with pytest.raises(ValueError, match=r"Missing: \['points'\]"):
        load_deltas(live, header)


def test_reingested_game_keeps_last_and_reports(raw: tuple[Path, Path]) -> None:
    kaggle, live = raw
    write_csv(live / "E2026_delta_1.csv", [["E2026_1_A", "E2026_1", "E2026", "1", "3"]])
    write_csv(live / "E2026_delta_2.csv", [["E2026_1_A", "E2026_1", "E2026", "1", "5"]])
    header, base = load_base(kaggle, "E2025")
    rows, dropped = append_rows(base, load_deltas(live, header))
    assert dropped == 1
    assert [r["points"] for r in rows if r["game_player_id"] == "E2026_1_A"] == ["5"]


def test_unknown_base_season_raises(raw: tuple[Path, Path]) -> None:
    with pytest.raises(ValueError, match="E1999"):
        load_base(raw[0], "E1999")


def test_no_deltas_yields_base_only(raw: tuple[Path, Path]) -> None:
    kaggle, live = raw
    live.mkdir()
    header, base = load_base(kaggle, "E2025")
    rows, dropped = append_rows(base, load_deltas(live, header))
    assert len(rows) == 2 and dropped == 0
