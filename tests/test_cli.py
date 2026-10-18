"""Tests for the `eupy` CLI (eupy.cli): link-final."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from eupy.cli import main
from eupy.registry.model import REPO_ROOT


def _repo(tmp_path: Path) -> Path:
    """Real repo layout (headers, registry data, pipelines.toml) copied into tmp_path; no data files."""
    shutil.copytree(REPO_ROOT / "src", tmp_path / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(REPO_ROOT / "pipelines.toml", tmp_path / "pipelines.toml")
    return tmp_path


def test_link_final_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _repo(tmp_path)
    (root / "data/stage_01").mkdir(parents=True)
    (root / "data/stage_01/squad_solution.csv").write_text("x")
    assert main(["link-final"], root=root) == 0
    assert "linked   data/stage_99/squad_solution.csv" in capsys.readouterr().out
    assert main(["link-final"], root=root) == 0
    assert "linked" not in capsys.readouterr().out  # second run: only skip notes, no changes
