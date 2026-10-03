"""Tests for the catalogue renderer (eupy.registry.render_catalogue) and its CLI subcommand."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from eupy.registry.__main__ import main
from eupy.registry.headers import Header
from eupy.registry.model import REPO_ROOT, Registry
from eupy.registry.render_catalogue import render_catalogue

RAW_SOURCES = {"manual/raw": {"origin": "Somewhere", "refresh": "manual"}}


def _registry(fetch_notes: str = "Files are named x_{season}.csv.") -> Registry:
    headers = {
        "src/eupy/fetchers/fetch_x.py": Header(
            (),
            ("api.example.com (live)",),
            (("x/raw", "data/raw_data/x/"),),
            False,
            True,
            "full overwrite",
            fetch_notes,
        ),
        "src/eupy/transform/make_a.py": Header(
            (("x/raw", "data/raw_data/x/"), ("manual/raw", "data/raw_data/manual/raw.csv")),
            (),
            (("a", "data/stage_01/a.csv"),),
            True,
            False,
            None,
            "Overwritten each run.",
        ),
        "src/eupy/transform/make_b.py": Header(
            (("a", "data/stage_01/a.csv"),), (), (("b", "data/stage_02/b.csv"),), False, False
        ),
    }
    return Registry.from_headers(headers, RAW_SOURCES)


GOLDEN = """\
| Dataset | Path | Produced by | Consumed by | Refresh |
| --- | --- | --- | --- | --- |
| `manual/raw` | `data/raw_data/manual/raw.csv` | external: Somewhere | `make_a.py` | manual |
| `x/raw` | `data/raw_data/x/` | `fetch_x.py` | `make_a.py` | full overwrite |

Notes:

- `fetch_x.py`: Files are named x_{season}.csv.

## Produced datasets (`data/stage_XX/`)

| Dataset | Stage | Path | Produced by | Inputs | Final | `stage_99` symlink |
| --- | --- | --- | --- | --- | --- | --- |
| `a` | 01 | `data/stage_01/a.csv` | `make_a.py` | `x/raw`, `manual/raw` | true | `data/stage_99/a.csv` |
| `b` | 02 | `data/stage_02/b.csv` | `make_b.py` | `a` | false | — |

Notes:

- `make_a.py`: Overwritten each run.

## Scripts

| Script | Inputs | Outputs | Final | Impure |
| --- | --- | --- | --- | --- |
| `src/eupy/fetchers/fetch_x.py` | api.example.com (live) | `x/raw` | false | true |
| `src/eupy/transform/make_a.py` | `x/raw`, `manual/raw` | `a` | true | false |
| `src/eupy/transform/make_b.py` | `a` | `b` | false | false |
"""


def test_golden_output() -> None:
    text = render_catalogue(_registry())
    assert text.startswith("<!-- GENERATED")
    assert GOLDEN in text
    assert text.endswith("\n") and not text.endswith("\n\n")


def test_deterministic_and_each_entity_once() -> None:
    reg = _registry()
    text = render_catalogue(reg)
    assert text == render_catalogue(_registry())
    for name in reg.datasets:
        assert text.count(f"\n| `{name}` |") == 1
    for script in reg.scripts.values():
        assert text.count(f"\n| `{script.path}` |") == 1


def test_header_change_flows_into_output() -> None:
    before = render_catalogue(_registry())
    after = render_catalogue(_registry(fetch_notes="Changed note."))
    assert "Changed note." in after and "Changed note." not in before


def test_cli_writes_file_and_stdout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shutil.copytree(REPO_ROOT / "src" / "eupy", tmp_path / "src" / "eupy")
    (tmp_path / "docs").mkdir()
    assert main(["catalogue", "--root", str(tmp_path)]) == 0
    written = (tmp_path / "docs" / "data-catalogue.md").read_text()
    assert written == render_catalogue(Registry.from_repo())
    capsys.readouterr()
    assert main(["catalogue", "--stdout", "--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out == written


def test_committed_data_catalogue_is_fresh() -> None:
    """docs/data-catalogue.md equals a fresh render of the real registry (regenerate: `python -m eupy.registry docs`)."""
    fresh = render_catalogue(Registry.from_repo())
    assert (REPO_ROOT / "docs" / "data-catalogue.md").read_text(encoding="utf-8") == fresh
