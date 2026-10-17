"""Tests for the live-delta header merge (Kaggle history optional)."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

from eupy.transform.merge_headers import main

HEADER = ["game_id", "season_code", "score_a"]


def write_csv(path: Path, rows: list[list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows([HEADER, *rows])


def test_kaggle_games_of_a_season_covered_by_a_delta_are_replaced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kaggle = tmp_path / "kaggle.csv"
    live = tmp_path / "live"
    out = tmp_path / "out.csv"
    write_csv(kaggle, [["E2025_1", "E2025", "80"], ["E2026_1", "E2026", "60"]])
    write_csv(live / "E2026_delta_1.csv", [["E2026_1", "E2026", "61"], ["E2026_2", "E2026", "70"]])
    monkeypatch.setattr(
        sys, "argv", ["prog", "--include-kaggle", "--kaggle", str(kaggle), "--live-dir", str(live), "--out", str(out)]
    )
    main()
    with out.open(newline="", encoding="utf-8") as f:
        rows = {r["game_id"]: r["score_a"] for r in csv.DictReader(f)}
    assert rows == {"E2025_1": "80", "E2026_1": "61", "E2026_2": "70"}  # live wins, no duplicate-key failure


def test_kaggle_games_are_ignored_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    kaggle, live, out = tmp_path / "kaggle.csv", tmp_path / "live", tmp_path / "out.csv"
    write_csv(kaggle, [["E2025_1", "E2025", "80"]])
    write_csv(live / "E2026_delta_1.csv", [["E2026_1", "E2026", "61"]])
    monkeypatch.setattr(sys, "argv", ["prog", "--kaggle", str(kaggle), "--live-dir", str(live), "--out", str(out)])
    main()
    assert out.read_text().splitlines()[1:] == ["E2026_1,E2026,61"]


def test_no_deltas_and_no_kaggle_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    live = tmp_path / "live"
    live.mkdir()
    monkeypatch.setattr(sys, "argv", ["prog", "--live-dir", str(live), "--out", str(tmp_path / "out.csv")])
    with pytest.raises(ValueError, match="include-kaggle"):
        main()
