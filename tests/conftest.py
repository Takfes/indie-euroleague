"""Shared fixtures: keep the test suite from writing to the real `logs/log.log`."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_log_file(tmp_path, monkeypatch):
    monkeypatch.setattr("eupy.logging_setup.REPO_ROOT", tmp_path)
