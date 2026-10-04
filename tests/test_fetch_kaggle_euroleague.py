"""Tests for the Kaggle fetcher: keep-two-files, validate-before-move, untouched-on-failure."""

from __future__ import annotations

from pathlib import Path

import pytest

from eupy.fetchers import fetch_kaggle_euroleague as fk
from eupy.quality import DataQualityError


def _fake_download(files: dict[str, str]):
    def download(_dataset: str, dest: Path) -> None:
        for name, text in files.items():
            (dest / name).write_text(text, encoding="utf-8")

    return download


GOOD = {
    "euroleague_box_score.csv": "game_player_id,points\nE_1_A,3\nE_1_B,5\n",
    "euroleague_header.csv": "game_id,season_code\nE_1,E2025\n",
    "euroleague_points.csv": "x\n1\n",  # unused file: must be discarded
}


def test_keeps_only_the_two_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fk, "download", _fake_download(GOOD))
    fk.run(tmp_path / "out", "o/n")
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == sorted(fk.KEEP)


def test_duplicate_key_fails_and_leaves_previous_files_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "euroleague_box_score.csv").write_text("previous\n", encoding="utf-8")
    bad = {**GOOD, "euroleague_box_score.csv": "game_player_id,points\nE_1_A,3\nE_1_A,5\n"}
    monkeypatch.setattr(fk, "download", _fake_download(bad))
    with pytest.raises(DataQualityError, match="duplicate"):
        fk.run(out, "o/n")
    assert (out / "euroleague_box_score.csv").read_text(encoding="utf-8") == "previous\n"
    assert not (out / "euroleague_header.csv").exists()


def test_missing_expected_file_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fk, "download", _fake_download({"euroleague_points.csv": "x\n1\n"}))
    with pytest.raises(FileNotFoundError, match="euroleague_box_score.csv"):
        fk.run(tmp_path / "out", "o/n")
