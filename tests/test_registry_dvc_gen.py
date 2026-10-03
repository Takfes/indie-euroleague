"""Tests for dvc.yaml generation (eupy.registry.dvc_gen), the pipelines.toml lint and the `dvc` / `check` CLI."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from eupy.registry.__main__ import main
from eupy.registry.dvc_gen import (
    Pipeline,
    dvc_steps,
    import_closure,
    lint_pipelines,
    lint_wrappers,
    load_pipelines,
    load_wrappers,
    render_dvc,
)
from eupy.registry.headers import Header
from eupy.registry.model import REPO_ROOT, Registry, RegistryError

RAW_SOURCES = {"manual/raw": {"origin": "Somewhere (manual)", "refresh": "manual"}}


def _registry() -> Registry:
    """Impure fetcher -> raw dir; pure make_a (raw dir + manual raw csv) -> a, b; pure make_c (a) -> c."""
    headers = {
        "src/eupy/fetchers/fetch_x.py": Header(
            (), ("api.example.com/x (live)",), (("x/raw", "data/raw_data/x/"),), False, True, "incremental", None
        ),
        "src/eupy/transform/make_a.py": Header(
            (("x/raw", "data/raw_data/x/"), ("manual/raw", "data/raw_data/manual/raw.csv")),
            (),
            (("b", "data/stage_01/b.csv"), ("a", "data/stage_01/a.csv")),
            True,
            False,
        ),
        "src/eupy/transform/make_c.py": Header(
            (("a", "data/stage_01/a.csv"),), (), (("c", "data/stage_02/c.csv"),), False, False
        ),
    }
    return Registry.from_headers(headers, RAW_SOURCES)


def _pipelines(c_dvc: bool = False) -> dict[str, Pipeline]:
    return {
        "main": Pipeline("main", True, ("fetch_x", "make_a")),
        "later": Pipeline("later", c_dvc, ("make_c",)),
    }


def test_step_has_script_and_input_deps_and_uncached_outs() -> None:
    assert render_dvc(_registry(), _pipelines()).splitlines()[1:] == [
        "stages:",
        "  make_a:",
        "    cmd: uv run python src/eupy/transform/make_a.py",
        "    deps:",
        "    - src/eupy/transform/make_a.py",
        "    - data/raw_data/manual/raw.csv",
        "    - data/raw_data/x",  # directory input: trailing slash dropped, hashed as a dir by DVC
        "    outs:",
        "    - data/stage_01/a.csv:",
        "        cache: false",
        "    - data/stage_01/b.csv:",
        "        cache: false",
    ]


def test_impure_script_and_dvc_false_pipeline_are_not_steps() -> None:
    assert [s.name for s in dvc_steps(_registry(), _pipelines())] == ["make_a"]  # fetch_x impure, make_c dvc=false
    assert [s.name for s in dvc_steps(_registry(), _pipelines(c_dvc=True))] == ["make_a", "make_c"]


def test_render_is_idempotent_and_bannered() -> None:
    text = render_dvc(_registry(), _pipelines())
    assert text == render_dvc(_registry(), _pipelines())
    assert text.startswith("# GENERATED — do not edit.")


@pytest.mark.parametrize(
    ("pipelines", "expected"),
    [
        ({"main": Pipeline("main", True, ("fetch_x", "make_a"))}, "script make_c is in no pipeline"),
        (
            {
                "main": Pipeline("main", True, ("fetch_x", "make_a", "make_c")),
                "later": Pipeline("later", False, ("make_c",)),
            },
            "script make_c is in 2 pipelines (main, later)",
        ),
        ({**_pipelines(), "extra": Pipeline("extra", False, ("ghost",))}, "[extra] lists unknown script ghost"),
    ],
)
def test_lint_rejects_bad_membership(pipelines: dict[str, Pipeline], expected: str) -> None:
    problems = lint_pipelines(_registry(), pipelines)
    assert any(expected in p for p in problems), problems
    with pytest.raises(RegistryError):
        dvc_steps(_registry(), pipelines)


def test_load_pipelines_rejects_malformed_table(tmp_path: Path) -> None:
    path = tmp_path / "pipelines.toml"
    path.write_text('[ok]\ndvc = true\nscripts = ["a"]\n[bad]\ndvc = "yes"\nscripts = ["b"]\n', encoding="utf-8")
    with pytest.raises(RegistryError, match=r"\[bad\] needs 'dvc'"):
        load_pipelines(path)


def test_cli_dvc_writes_and_check_lints_membership(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shutil.copytree(REPO_ROOT / "src" / "eupy", tmp_path / "src" / "eupy")
    toml = (REPO_ROOT / "pipelines.toml").read_text(encoding="utf-8")
    (tmp_path / "pipelines.toml").write_text(toml, encoding="utf-8")
    assert main(["dvc", "--root", str(tmp_path)]) == 0
    written = (tmp_path / "dvc.yaml").read_text(encoding="utf-8")
    assert main(["dvc", "--stdout", "--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out == written

    # Drop a script from its pipeline: both `check` and `dvc` fail, and dvc.yaml is left untouched.
    (tmp_path / "pipelines.toml").write_text(toml.replace('"optimize_squad"', ""), encoding="utf-8")
    assert main(["check", "--root", str(tmp_path)]) == 1
    assert main(["dvc", "--root", str(tmp_path)]) == 1
    assert "script optimize_squad is in no pipeline" in capsys.readouterr().err
    assert (tmp_path / "dvc.yaml").read_text(encoding="utf-8") == written


def test_committed_dvc_yaml_is_current() -> None:
    # Guards against a header or pipelines.toml change landing without regenerating dvc.yaml.
    registry = Registry.from_repo()
    expected = render_dvc(registry, load_pipelines(REPO_ROOT / "pipelines.toml"), REPO_ROOT)
    assert (REPO_ROOT / "dvc.yaml").read_text(encoding="utf-8") == expected


def test_path_that_cannot_be_written_unquoted_is_rejected() -> None:
    headers = {
        "src/eupy/transform/make_a.py": Header((), (), (("a", "data/stage_01/a b.csv"),), False, False),
    }
    reg = Registry.from_headers(headers, {})
    with pytest.raises(RegistryError, match="not safe to write unquoted"):
        dvc_steps(reg, {"main": Pipeline("main", True, ("make_a",))})


def test_wrappers_table_is_not_a_pipeline_and_is_linted(tmp_path: Path) -> None:
    toml = tmp_path / "pipelines.toml"
    toml.write_text('[a]\ndvc = true\nscripts = ["x"]\n\n[wrappers]\nall = ["a"]\nbad = ["a", "nope"]\na = ["a"]\n')
    pipes = load_pipelines(toml)
    assert list(pipes) == ["a"]
    wrappers = load_wrappers(toml)
    assert wrappers["all"] == ("a",)
    problems = lint_wrappers(pipes, wrappers)
    assert any("unknown pipeline nope" in p for p in problems) and any("same name as a pipeline" in p for p in problems)
    toml.write_text('[a]\ndvc = true\nscripts = []\n[wrappers]\nall = "a"\n')
    with pytest.raises(RegistryError, match="non-empty list"):
        load_wrappers(toml)


def _pkg(tmp_path: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        f = tmp_path / "src" / "eupy" / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text, encoding="utf-8")


def test_import_closure_resolves_direct_transitive_from_package_and_relative(tmp_path: Path) -> None:
    _pkg(
        tmp_path,
        {
            "__init__.py": "",
            "lib/__init__.py": "",  # empty package markers are skipped
            "lib/a.py": "import json\nimport pandas\nfrom eupy.lib import b\n",
            "lib/b.py": "from . import c\n",
            "lib/c.py": "from .d import thing\nimport eupy.other.e\n",
            "lib/d.py": "thing = 1\n",
            "other/__init__.py": "X = 1\n",  # non-empty package init is a dep
            "other/e.py": "",
            "other/unused.py": "",
            "run/script.py": "import eupy.lib.a\nfrom eupy.nope import missing\nimport eupy.nope2\n",
        },
    )
    assert import_closure("src/eupy/run/script.py", tmp_path) == (
        "src/eupy/lib/a.py",
        "src/eupy/lib/b.py",
        "src/eupy/lib/c.py",
        "src/eupy/lib/d.py",
        "src/eupy/other/__init__.py",
        "src/eupy/other/e.py",
    )


def test_import_closure_is_cycle_safe_and_excludes_the_script(tmp_path: Path) -> None:
    _pkg(
        tmp_path,
        {
            "x/a.py": "from eupy.x import b\n",
            "x/b.py": "import eupy.x.a\nimport eupy.x.b\n",
            "x/s.py": "import eupy.x.a\n",
        },
    )
    assert import_closure("src/eupy/x/s.py", tmp_path) == ("src/eupy/x/a.py", "src/eupy/x/b.py")
    assert import_closure("src/eupy/x/a.py", tmp_path) == ("src/eupy/x/b.py",)  # a <-> b cycle; a itself dropped


def test_import_closure_of_missing_or_unparsable_script_is_empty(tmp_path: Path) -> None:
    _pkg(tmp_path, {"x/bad.py": "def (:\n"})
    assert import_closure("src/eupy/x/gone.py", tmp_path) == ()
    assert import_closure("src/eupy/x/bad.py", tmp_path) == ()


def test_step_deps_include_import_closure_between_script_and_inputs(tmp_path: Path) -> None:
    _pkg(tmp_path, {"transform/make_a.py": "from eupy.lib import helper\n", "lib/helper.py": ""})
    (step,) = dvc_steps(_registry(), _pipelines(), tmp_path)
    assert step.deps == (
        "src/eupy/transform/make_a.py",
        "src/eupy/lib/helper.py",
        "data/raw_data/manual/raw.csv",
        "data/raw_data/x",
    )
