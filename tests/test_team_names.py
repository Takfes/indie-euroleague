"""Tests for the team name map builder (`build_team_name_map`) and the `TeamNames` lookup."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eupy.entity.build_team_name_map import build_team_entries, load_schedule_rows, main, write_map
from eupy.entity.matching import UnknownNameError
from eupy.entity.team_names import TeamNames

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
        build_team_entries(rows)


def test_empty_schedule_fails() -> None:
    with pytest.raises(ValueError, match="no rows"):
        build_team_entries([])


def test_missing_schedule_dir_fails(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="fetch_game_schedule"):
        load_schedule_rows(tmp_path)


def test_main_writes_stable_json_that_loads(tmp_path: Path) -> None:
    (tmp_path / "schedule_E2026.csv").write_text(
        "hometeam,homecode,hometv\nPanathinaikos,PAN,PAO\nReal Madrid,MAD,RMB\n", encoding="utf-8"
    )
    out = tmp_path / "map.json"
    main(["--schedule-dir", str(tmp_path), "--out", str(out)])
    first = out.read_bytes()
    main(["--schedule-dir", str(tmp_path), "--out", str(out)])
    assert out.read_bytes() == first
    assert json.loads(first)["teams"][0]["name"] == "Panathinaikos"
    assert TeamNames.load(out).normalize("MAD") == "Real Madrid"


def test_load_missing_map_points_at_the_builder(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="build_team_name_map"):
        TeamNames.load(tmp_path / "nope.json")


def test_variant_used_by_two_teams_is_rejected_on_load() -> None:
    entries = [{"name": "A", "variants": ["X"]}, {"name": "B", "variants": ["X"]}]
    with pytest.raises(ValueError, match="belongs to both"):
        TeamNames(entries)


def test_write_map_is_deterministic(tmp_path: Path) -> None:
    write_map(build_team_entries(ROWS), tmp_path / "a.json")
    write_map(build_team_entries(list(reversed(ROWS))), tmp_path / "b.json")
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()
