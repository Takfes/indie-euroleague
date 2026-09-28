"""Tests for the live-header fetcher's row mapping / run loop and the delta-append step.

The header fixture is a trimmed real response for gamecode=1, seasoncode=E2022
(PAN vs MAD, 06/10/2022). No test hits the network.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from eupy.fetchers import fetch_euroleague_live_headers as fh
from eupy.fetchers.append_live_headers import append_headers
from eupy.fetchers.append_live_headers import run as append_run

HEADER: dict[str, Any] = {
    "Live": False,
    "Round": "1",
    "Date": "06/10/2022",
    "Hour": "21:00 ",
    "Stadium": "OAKA Altion",
    "Capacity": "0",
    "TeamA": "PANATHINAIKOS ATHENS",
    "TeamB": "REAL MADRID",
    "CodeTeamA": "PAN",
    "CodeTeamB": "MAD",
    "ScoreA": "68",
    "ScoreB": "71",
    "CoachA": "RADONJIC, DEJAN",
    "CoachB": "MATEO DIEZ, JESUS",
    "GameTime": "40:00",
    "RemainingPartialTime": "00:00",
    "wid": "80",
    "FoultsA": "16",
    "FoultsB": "22",
    "TimeoutsA": "5",
    "TimeoutsB": "3",
    "ScoreQuarter1A": 22,
    "ScoreQuarter2A": 37,
    "ScoreQuarter3A": 51,
    "ScoreQuarter4A": 68,
    "ScoreExtraTimeA": 0,
    "ScoreQuarter1B": 23,
    "ScoreQuarter2B": 38,
    "ScoreQuarter3B": 54,
    "ScoreQuarter4B": 71,
    "ScoreExtraTimeB": 0,
    "Phase": "REGULAR SEASON",
    "pcom": "E2022     ",
    "Referee1": "BOLTAUZER, MATEJ",
    "Referee2": "PATERNICO, CARMELO",
    "Referee3": "MOGULKOC, EMIN",
}


def test_build_header_row_matches_kaggle_shape() -> None:
    row = fh.build_header_row(HEADER, "E2022", 1)
    assert list(row) == fh.CSV_FIELDNAMES
    assert row["game_id"] == "E2022_001"  # zero-padded like Kaggle
    assert row["game"] == "PAN-MAD"
    assert row["date"] == "2022-10-06"
    assert row["time"] == "21:00:00"
    assert row["score_quarter_4_a"] == 68
    assert row["w_id"] == "80"
    assert row["score_extra_time_1_a"] == ""  # no overtime -> blank, not 0


def test_overtime_final_score_lands_in_first_extra_period_only() -> None:
    row = fh.build_header_row({**HEADER, "ScoreExtraTimeA": 107, "ScoreExtraTimeB": 112}, "E2022", 86)
    assert (row["score_extra_time_1_a"], row["score_extra_time_1_b"]) == (107, 112)
    assert row["score_extra_time_2_a"] == "" and row["score_extra_time_4_b"] == ""


def test_season_mismatch_raises() -> None:
    with pytest.raises(ValueError, match="E2022"):
        fh.build_header_row(HEADER, "E2023", 1)


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, False),
        ({**HEADER, "Live": True}, False),
        ({**HEADER, "ScoreA": "0", "ScoreB": "0"}, False),
        (HEADER, True),
    ],
)
def test_is_header_complete(header: dict[str, Any] | None, expected: bool) -> None:
    assert fh.is_header_complete(header) is expected


def _run_with(responses: dict[int, dict[str, Any] | None], out_dir: Path, max_gap: int = 2) -> None:
    with (
        patch.object(fh, "fetch_header", side_effect=lambda _s, gc: responses.get(gc)),
        patch.object(fh.time, "sleep"),
    ):
        fh.run("E2022", out_dir, max_gap)


def test_run_bridges_gaps_and_stops_at_live_game(tmp_path: Path) -> None:
    # 1 ok, 2-3 empty (gap == max_gap-1 is fine when followed by data), 4 ok, 5 live -> stop.
    responses = {1: HEADER, 4: HEADER, 5: {**HEADER, "Live": True}, 6: HEADER}
    _run_with(responses, tmp_path, max_gap=3)
    (delta,) = (tmp_path / "headers").glob("E2022_delta_*.csv")
    with delta.open(newline="") as f:
        assert [r["game_id"] for r in csv.DictReader(f)] == ["E2022_001", "E2022_004"]
    assert '"last_ingested_gamecode": 4' in (tmp_path / "headers" / "_state.json").read_text()


def test_run_resumes_from_state_and_writes_nothing_when_no_news(tmp_path: Path) -> None:
    _run_with({1: HEADER}, tmp_path)
    _run_with({1: HEADER}, tmp_path)  # second run starts at gamecode 2 -> nothing new
    assert len(list((tmp_path / "headers").glob("E2022_delta_*.csv"))) == 1


def _hdr_row(game_id: str, score: str = "1") -> dict[str, str]:
    return {"game_id": game_id, "season_code": game_id.split("_")[0], "score_a": score}


def test_append_base_wins_dedupes_deltas_and_sorts_numerically() -> None:
    base = [_hdr_row("E2025_010"), _hdr_row("E2025_002"), _hdr_row("E2026_001", "base-should-be-cut")]
    deltas = [
        _hdr_row("E2025_002", "live"),  # overlaps base -> dropped
        _hdr_row("E2026_002", "old"),
        _hdr_row("E2026_002", "new"),  # re-fetched -> latest wins
        _hdr_row("E2026_001", "live"),
    ]
    out = append_headers(base, deltas, "E2025")
    assert [(r["game_id"], r["score_a"]) for r in out] == [
        ("E2025_002", "1"),
        ("E2025_010", "1"),
        ("E2026_001", "live"),
        ("E2026_002", "new"),
    ]


def test_append_run_rejects_schema_drift(tmp_path: Path) -> None:
    base, deltas = tmp_path / "base.csv", tmp_path / "headers"
    deltas.mkdir()
    base.write_text("game_id,season_code,score_a\nE2025_001,E2025,1\n")
    (deltas / "E2026_delta_1.csv").write_text("game_id,season_code\nE2026_001,E2026\n")
    with pytest.raises(ValueError, match="columns differ"):
        append_run(base, deltas, tmp_path / "out.csv", "E2025")
