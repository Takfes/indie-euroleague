"""Tests for the `TeamNames` lookup, derived from the game schedule."""

from __future__ import annotations

from pathlib import Path

import pytest

from eupy.entity.matching import UnknownNameError
from eupy.entity.team_names import TeamNames, build_team_entries, load_schedule_rows

ROWS = [
    {"hometeam": "Panathinaikos", "homecode": "PAN", "hometv": "PAO"},
    {"hometeam": "Real Madrid", "homecode": "MAD", "hometv": "RMB"},
    {"hometeam": "Panathinaikos", "homecode": "PAN", "hometv": "PAO"},  # second home game
]


def _teams() -> TeamNames:
    return TeamNames(build_team_entries(ROWS))


def test_builder_makes_one_sorted_entry_per_team() -> None:
    assert build_team_entries(ROWS) == [
        {"name": "Panathinaikos", "variants": ["PAN", "PAO", "Panathinaikos"]},
        {"name": "Real Madrid", "variants": ["MAD", "RMB", "Real Madrid"]},
    ]


def test_every_variant_normalizes_to_the_team_name() -> None:
    teams = _teams()
    assert {teams.normalize(v) for v in ("PAN", "pao", "Panathinaikos")} == {"Panathinaikos"}
    assert teams.normalize("RMB") == teams.normalize("MAD") == "Real Madrid"


def test_unknown_team_raises() -> None:
    with pytest.raises(UnknownNameError, match="XYZ"):
        _teams().normalize("XYZ")


def test_team_with_two_code_pairs_fails() -> None:
    rows = [*ROWS, {"hometeam": "Real Madrid", "homecode": "RMA", "hometv": "RMB"}]
    with pytest.raises(ValueError, match="several code pairs"):
        build_team_entries(rows)


def test_code_shared_by_two_teams_fails() -> None:
    rows = [*ROWS, {"hometeam": "Baskonia", "homecode": "PAN", "hometv": "KBA"}]
    with pytest.raises(ValueError, match="belongs to both"):
        TeamNames(build_team_entries(rows))


def test_empty_schedule_fails() -> None:
    with pytest.raises(ValueError, match="no rows"):
        build_team_entries([])


def test_missing_schedule_dir_fails(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="fetch_game_schedule"):
        load_schedule_rows(tmp_path)


def test_load_reads_every_schedule_file_in_the_directory(tmp_path: Path) -> None:
    header = "hometeam,homecode,hometv\n"
    (tmp_path / "schedule_E2025.csv").write_text(header + "Panathinaikos,PAN,PAO\n", encoding="utf-8")
    (tmp_path / "schedule_E2026.csv").write_text(header + "Real Madrid,MAD,RMB\n", encoding="utf-8")
    teams = TeamNames.load(tmp_path)
    assert (teams.normalize("PAO"), teams.normalize("MAD")) == ("Panathinaikos", "Real Madrid")


def test_variant_used_by_two_teams_is_rejected_on_load() -> None:
    entries = [{"name": "A", "variants": ["X"]}, {"name": "B", "variants": ["X"]}]
    with pytest.raises(ValueError, match="belongs to both"):
        TeamNames(entries)
