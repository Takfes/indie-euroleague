"""Tests for the I/O-block parser and module discovery (eupy.registry.headers)."""

from __future__ import annotations

from pathlib import Path

import pytest

from eupy.registry.headers import Header, HeaderError, discover_headers, parse_block, read_header

FULL_BLOCK = """\
Inputs:
  - fantasy_stats/players: data/raw_data/fantasy_stats/players.csv
  - crosswalk: data/stage_01/crosswalk.csv
Sources:
  - JSON verdicts file (--verdicts PATH)
Outputs:
  - normalized: data/stage_02/normalized.csv
Final: true
Impure: false
Refresh: full overwrite per run
Notes: first line
  continued here.
"""


def test_parse_multi_entry_block_with_continuation() -> None:
    header = parse_block(FULL_BLOCK)
    assert header == Header(
        inputs=(
            ("fantasy_stats/players", "data/raw_data/fantasy_stats/players.csv"),
            ("crosswalk", "data/stage_01/crosswalk.csv"),
        ),
        sources=("JSON verdicts file (--verdicts PATH)",),
        outputs=(("normalized", "data/stage_02/normalized.csv"),),
        final=True,
        impure=False,
        refresh="full overwrite per run",
        notes="first line continued here.",
    )


def test_parse_none_lists_trailing_slash_dir_and_optional_keys_absent() -> None:
    header = parse_block(
        "Inputs: none\nSources: none\nOutputs:\n  - src/deltas: data/raw_data/src/deltas/\nFinal: false\nImpure: true\n"
    )
    assert header.inputs == ()
    assert header.sources == ()
    assert header.outputs == (("src/deltas", "data/raw_data/src/deltas/"),)
    assert (header.refresh, header.notes) == (None, None)


MINIMAL = "Inputs: none\nSources: none\nOutputs: none\nFinal: false\nImpure: false"


@pytest.mark.parametrize(
    ("block", "match"),
    [
        (MINIMAL.replace("Impure: false", ""), "missing required key"),
        ("Sources: none\nInputs: none\nOutputs: none\nFinal: false\nImpure: false", "order"),
        (MINIMAL + "\nNotes: a\nRefresh: b", "order"),
        (MINIMAL + "\nImpure: true", "order"),  # duplicate key
        (MINIMAL + "\nPipeline: x", "expected one of"),
        (MINIMAL.replace("Final: false", "Final: yes"), "true' or 'false"),
        (MINIMAL.replace("Inputs: none", "Inputs:"), "'none' or a list"),
        (MINIMAL.replace("Inputs: none", "Inputs: none\n  - a: data/a.csv"), "'none' or a list"),
        (MINIMAL.replace("Inputs: none", "Inputs:\n  a: data/a.csv"), "list lines"),
        (MINIMAL.replace("Outputs: none", "Outputs:\n  - data/a.csv"), "dataset-name"),
        (MINIMAL.replace("Outputs: none", "Outputs:\n  - s: data/raw/schedule_{season}.csv"), "no templates"),
        (MINIMAL.replace("Outputs: none", "Outputs:\n  - s: /abs/s.csv"), "repo-relative"),
        (MINIMAL.replace("Outputs: none", "Outputs:\n  - a: data/a.csv\n  - a: data/b.csv"), "duplicate"),
        (MINIMAL.replace("Sources: none", "\nSources: none"), "blank line"),
        (MINIMAL + "\nNotes:", "empty value"),
    ],
)
def test_malformed_blocks_rejected(block: str, match: str) -> None:
    with pytest.raises(HeaderError, match=match):
        parse_block(block)


def _write_module(path: Path, docstring: str, body: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'"""{docstring}"""\n{body}', encoding="utf-8")
    return path


def test_read_header_uses_ast_and_never_imports(tmp_path: Path) -> None:
    # Importing this module would fail; reading its header must not.
    mod = _write_module(tmp_path / "s.py", f"Prose.\n\n{MINIMAL}\n", "import module_that_does_not_exist\n")
    assert read_header(mod) == Header((), (), (), final=False, impure=False)


def test_read_header_without_block_is_none_and_indented_inputs_is_not_a_block(tmp_path: Path) -> None:
    mod = _write_module(tmp_path / "s.py", "Library module.\n\n    Inputs: described in prose, indented.\n")
    assert read_header(mod) is None


def test_discovery_rule(tmp_path: Path) -> None:
    pkg = tmp_path / "src" / "eupy"
    _write_module(pkg / "transform" / "good.py", MINIMAL)
    _write_module(pkg / "transform" / "__init__.py", "Package.")
    _write_module(pkg / "entity" / "matching.py", "Library module, no block.")
    _write_module(pkg / "devtools" / "tidy.py", "Dev tool, no block.")
    _write_module(pkg / "registry" / "model.py", "Registry, no block.")
    _write_module(pkg / "optimize" / "forgot.py", "Pipeline script that forgot its block.")
    _write_module(pkg / "fetchers" / "broken.py", MINIMAL.replace("Final: false", "Final: maybe"))

    headers, problems = discover_headers(pkg)

    assert [p.relative_to(pkg).as_posix() for p in headers] == ["transform/good.py"]
    assert len(problems) == 2
    assert problems[0].startswith("fetchers/broken.py:")
    assert problems[1].startswith("optimize/forgot.py: pipeline script has no I/O block")
