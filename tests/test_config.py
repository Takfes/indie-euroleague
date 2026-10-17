"""Tests for the config loader: defaults, key-by-key override, strict unknown keys."""

from __future__ import annotations

import pytest

from eupy.config import Config, load_config


def test_missing_file_gives_defaults(tmp_path):
    assert load_config(tmp_path / "nope.toml") == Config()


def test_override_is_key_by_key(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[acquisition]\nseason = "E2027"\n[transformation]\ninclude_kaggle = true\nbase_season = "E2025"\n')
    cfg = load_config(p)
    assert cfg.acquisition.season == "E2027"
    assert cfg.acquisition.max_gap == Config().acquisition.max_gap  # untouched keys keep defaults
    assert cfg.transformation.include_kaggle is True
    assert cfg.transformation.base_season == "E2025"
    assert Config().transformation.include_kaggle is False


@pytest.mark.parametrize("body", ["[acquisition]\nseasno = 1\n", "[nope]\nx = 1\n", "acquisition = 3\n"])
def test_unknown_or_malformed_raises(tmp_path, body):
    p = tmp_path / "c.toml"
    p.write_text(body)
    with pytest.raises(ValueError):
        load_config(p)


def test_repo_config_file_is_valid():
    from eupy.config.loader import CONFIG_PATH

    load_config(CONFIG_PATH)
