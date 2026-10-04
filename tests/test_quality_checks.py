"""Tests for quality.validate and the shared log file."""

from __future__ import annotations

import pytest

from eupy.logging_setup import get_logger
from eupy.quality import DataQualityError, validate


@pytest.fixture
def log_path(tmp_path):
    path = tmp_path / "logs" / "log.log"
    return path, get_logger(f"t-{tmp_path.name}", path)


def test_clean_data_passes_and_logs_info(log_path):
    path, log = log_path
    validate([{"id": "1", "v": "a"}, {"id": "2", "v": ""}], "id", "ds", log)
    assert "INFO" in path.read_text() and "ds: 2 rows ok" in path.read_text()


def test_duplicates_and_empty_rows_reported_together(log_path):
    path, log = log_path
    rows = [{"id": "1", "v": "a"}, {"id": "1", "v": "b"}, {"id": " ", "v": ""}, {"id": "2", "v": "c"}]
    with pytest.raises(DataQualityError, match="ds failed validation"):
        validate(rows, "id", "ds", log)
    text = path.read_text()
    assert "1 empty row(s)" in text and "1 duplicate key(s)" in text  # both logged, not just the first
    assert "ERROR" in text


def test_composite_key(log_path):
    _, log = log_path
    ok = [{"r": "1", "p": "a"}, {"r": "1", "p": "b"}, {"r": "2", "p": "a"}]
    validate(ok, ["r", "p"], "ds", log)
    with pytest.raises(DataQualityError, match="duplicate"):
        validate([*ok, {"r": "2", "p": "a"}], ["r", "p"], "ds", log)


def test_missing_key_column_fails(log_path):
    _, log = log_path
    with pytest.raises(DataQualityError, match="not in the data"):
        validate([{"a": "1"}], "id", "ds", log)


def test_non_string_values_and_none(log_path):
    _, log = log_path
    with pytest.raises(DataQualityError, match="empty"):
        validate([{"id": 1, "v": None}, {"id": None, "v": ""}], "id", "ds", log)


def test_logger_does_not_stack_handlers(tmp_path):
    path = tmp_path / "l.log"
    get_logger("dup", path)
    n = len(get_logger("dup", path).handlers)
    assert n == 2
