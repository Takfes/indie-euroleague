"""Tests for the stage_99 link logic (eupy.registry.link_final) on a synthetic registry in tmp_path."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from eupy.registry.headers import Header
from eupy.registry.link_final import final_targets, link_final
from eupy.registry.model import Registry, RegistryError


def _registry(final: bool = True) -> Registry:
    headers = {
        "src/eupy/transform/make_a.py": Header((), (), (("a", "data/stage_01/a.csv"),), final, False),
        "src/eupy/transform/make_b.py": Header((), (), (("b", "data/stage_01/b.csv"),), final, False),
        "src/eupy/transform/make_c.py": Header((), (), (("c", "data/stage_01/c.csv"),), False, False),
    }
    return Registry.from_headers(headers, {})


def _touch(root: Path, *names: str) -> None:
    for n in names:
        (root / n).parent.mkdir(parents=True, exist_ok=True)
        (root / n).write_text("x")


def _links(root: Path) -> dict[str, str]:
    d = root / "data/stage_99"
    return {p.name: os.readlink(p) for p in sorted(d.iterdir()) if p.is_symlink()}


def test_final_targets_are_final_produced_datasets_only() -> None:
    assert final_targets(_registry()) == {"a.csv": "data/stage_01/a.csv", "b.csv": "data/stage_01/b.csv"}
    assert final_targets(_registry(final=False)) == {}


def test_creates_relative_links_and_is_idempotent(tmp_path: Path) -> None:
    _touch(tmp_path, "data/stage_01/a.csv", "data/stage_01/b.csv")
    first = link_final(_registry(), tmp_path)
    assert first.created == ["a.csv", "b.csv"]
    assert _links(tmp_path) == {"a.csv": "../stage_01/a.csv", "b.csv": "../stage_01/b.csv"}
    assert (tmp_path / "data/stage_99/a.csv").read_text() == "x"
    second = link_final(_registry(), tmp_path)
    assert second.lines() == []
    assert _links(tmp_path) == {"a.csv": "../stage_01/a.csv", "b.csv": "../stage_01/b.csv"}


def test_stale_link_removed_but_regular_files_and_dirs_untouched(tmp_path: Path) -> None:
    _touch(tmp_path, "data/stage_01/a.csv", "data/stage_01/b.csv", "data/stage_99/untracked.csv")
    (tmp_path / "data/stage_99/subdir").mkdir()
    os.symlink("../stage_01/gone.csv", tmp_path / "data/stage_99/old.csv")  # dangling and stale
    report = link_final(_registry(), tmp_path)
    assert report.removed == ["old.csv"]
    assert not (tmp_path / "data/stage_99/old.csv").is_symlink()
    assert (tmp_path / "data/stage_99/untracked.csv").read_text() == "x"
    assert (tmp_path / "data/stage_99/subdir").is_dir()


def test_missing_target_is_skipped_not_dangling(tmp_path: Path) -> None:
    _touch(tmp_path, "data/stage_01/a.csv")
    report = link_final(_registry(), tmp_path)
    assert report.created == ["a.csv"]
    assert len(report.skipped) == 1 and report.skipped[0].startswith("b.csv")
    assert set(_links(tmp_path)) == {"a.csv"}


def test_regular_file_with_final_name_is_left_alone(tmp_path: Path) -> None:
    _touch(tmp_path, "data/stage_01/a.csv", "data/stage_01/b.csv", "data/stage_99/a.csv")
    (tmp_path / "data/stage_99/a.csv").write_text("mine")
    report = link_final(_registry(), tmp_path)
    assert (tmp_path / "data/stage_99/a.csv").read_text() == "mine"
    assert not (tmp_path / "data/stage_99/a.csv").is_symlink()
    assert any(s.startswith("a.csv") for s in report.skipped)


def test_wrong_link_target_is_refreshed(tmp_path: Path) -> None:
    _touch(tmp_path, "data/stage_01/a.csv", "data/stage_01/b.csv", "data/stage_02/other.csv")
    (tmp_path / "data/stage_99").mkdir()
    os.symlink("../stage_02/other.csv", tmp_path / "data/stage_99/a.csv")
    report = link_final(_registry(), tmp_path)
    assert report.updated == ["a.csv"]
    assert _links(tmp_path)["a.csv"] == "../stage_01/a.csv"


def test_symlink_not_pointing_into_a_stage_dir_is_left_alone(tmp_path: Path) -> None:
    _touch(tmp_path, "data/stage_01/a.csv", "data/stage_01/b.csv", "elsewhere.csv")
    (tmp_path / "data/stage_99").mkdir()
    os.symlink("../../elsewhere.csv", tmp_path / "data/stage_99/mine.csv")
    assert link_final(_registry(), tmp_path).removed == []
    assert (tmp_path / "data/stage_99/mine.csv").is_symlink()


def test_final_datasets_sharing_a_link_name_are_rejected() -> None:
    headers = {
        "src/eupy/transform/make_a.py": Header((), (), (("a", "data/stage_01/x.csv"),), True, False),
        "src/eupy/transform/make_b.py": Header(
            (("a", "data/stage_01/x.csv"),), (), (("b", "data/stage_02/x.csv"),), True, False
        ),
    }
    with pytest.raises(RegistryError, match="share the stage_99 link name"):
        final_targets(Registry.from_headers(headers, {}))
