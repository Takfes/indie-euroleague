"""Tests for the registry model (stages, producers, cycles, lint) and its CLI (eupy.registry)."""

from __future__ import annotations

from graphlib import CycleError
from pathlib import Path

import pytest

from eupy.registry.__main__ import main, render
from eupy.registry.headers import Header
from eupy.registry.model import Registry, RegistryError


def _h(inputs: dict[str, str], outputs: dict[str, str], sources: tuple[str, ...] = ()) -> Header:
    return Header(tuple(inputs.items()), sources, tuple(outputs.items()), final=True, impure=False)


RAW = {"src/raw": "data/raw_data/src/raw.csv"}
RAW_SOURCES = {"src/raw": {"origin": "manual", "refresh": "manual"}}


def _build(scripts: dict[str, Header], **kwargs: object) -> Registry:
    headers = {f"src/eupy/transform/{name}.py": h for name, h in scripts.items()}
    return Registry.from_headers(headers, RAW_SOURCES, **kwargs)  # type: ignore[arg-type]


def test_stage_depth_on_chain() -> None:
    reg = _build({
        "c": _h({"b": "data/stage_02/b.csv"}, {"c": "data/stage_03/c.csv"}),
        "a": _h(RAW, {"a": "data/stage_01/a.csv"}),
        "b": _h({"a": "data/stage_01/a.csv"}, {"b": "data/stage_02/b.csv"}),
    })
    assert [reg.stage(d) for d in ("src/raw", "a", "b", "c")] == [0, 1, 2, 3]
    assert reg.topo_order() == ("a", "b", "c")
    assert reg.upstream("c") == ("a", "b")
    assert reg.producer("b") == "b"
    assert reg.producer("src/raw") is None


def test_stage_is_max_of_inputs_on_diamond() -> None:
    # raw -> a(1) -> b(2); d reads raw (0), a (1) and b (2) -> 3, not 1 or 2.
    reg = _build({
        "a": _h(RAW, {"a": "data/stage_01/a.csv"}),
        "b": _h({"a": "data/stage_01/a.csv"}, {"b": "data/stage_02/b.csv"}),
        "d": _h({**RAW, "a": "data/stage_01/a.csv", "b": "data/stage_02/b.csv"}, {"d": "data/stage_03/d.csv"}),
    })
    assert reg.stage("d") == 3
    assert reg.consumers("a") == ("b", "d")


def test_raw_output_of_a_fetcher_is_stage_zero_and_needs_no_raw_sources_entry() -> None:
    reg = _build({
        "fetch": _h({}, {"src/live": "data/raw_data/src/live/"}, sources=("live api",)),
        "t": _h({**RAW, "src/live": "data/raw_data/src/live/"}, {"t": "data/stage_01/t.csv"}),
    })
    assert reg.stage("src/live") == 0
    assert reg.producer("src/live") == "fetch"
    assert reg.stage("t") == 1
    assert reg.sources["live api"].scripts == ("fetch",)


def test_letter_suffix_accepted_when_numeric_part_matches() -> None:
    reg = _build({"a": _h(RAW, {"a": "data/stage_01a/a.csv"})})
    assert reg.stage("a") == 1


@pytest.mark.parametrize("path", ["data/stage_02/a.csv", "data/stage_02a/a.csv", "data/other/a.csv"])
def test_path_vs_stage_mismatch_rejected(path: str) -> None:
    with pytest.raises(RegistryError, match="computed stage 1"):
        _build({"a": _h(RAW, {"a": path})})


def test_double_producer_rejected() -> None:
    with pytest.raises(RegistryError, match=r"produced by more than one script \(a, b\)"):
        _build({"a": _h(RAW, {"x": "data/stage_01/x.csv"}), "b": _h(RAW, {"x": "data/stage_01/x.csv"})})


def test_cycle_between_scripts_rejected() -> None:
    with pytest.raises(CycleError):
        _build({
            "a": _h({**RAW, "y": "data/stage_01/y.csv"}, {"x": "data/stage_01/x.csv"}),
            "b": _h({"x": "data/stage_01/x.csv"}, {"y": "data/stage_01/y.csv"}),
        })


def test_self_loop_rejected_unless_allowlisted() -> None:
    scripts = {
        "resolve": _h(RAW, {"xw": "data/stage_01/xw.csv"}),
        "apply": _h({"xw": "data/stage_01/xw.csv"}, {"xw": "data/stage_01/xw.csv"}),
    }
    with pytest.raises(CycleError):
        _build({"apply": scripts["apply"], "resolve": _h(RAW, {"y": "data/stage_01/y.csv"})}, allowlist=frozenset())
    # Not allowlisted, the applier is also just a second producer.
    with pytest.raises(RegistryError, match="more than one script"):
        _build(scripts, allowlist=frozenset())

    # Allowlisted: the applier is an in-place updater, not a second producer, and adds no stage.
    reg = _build(scripts, allowlist=frozenset({"apply"}))
    assert reg.producer("xw") == "resolve"
    assert reg.datasets["xw"].updaters == ("apply",)
    assert reg.scripts["apply"].in_place == ("xw",)
    assert reg.stage("xw") == 1
    assert reg.topo_order() == ("resolve", "apply")


def test_allowlisted_sole_in_place_writer_is_producer_and_self_input_ignored_for_stage() -> None:
    reg = _build(
        {"resolve": _h({**RAW, "xw": "data/stage_01/xw.csv"}, {"xw": "data/stage_01/xw.csv"})},
        allowlist=frozenset({"resolve"}),
    )
    assert reg.producer("xw") == "resolve"
    assert reg.stage("xw") == 1


def test_unproduced_non_raw_and_undeclared_raw_rejected() -> None:
    with pytest.raises(RegistryError) as exc:
        Registry.from_headers(
            {
                "src/eupy/transform/a.py": _h(
                    {"ghost": "data/stage_01/ghost.csv", "src/other": "data/raw_data/o.csv"}, {}
                )
            },
            RAW_SOURCES,
        )
    text = str(exc.value)
    assert "ghost: no script produces it" in text
    assert "src/other: raw and unproduced, but not declared" in text
    assert "raw_sources.toml: src/raw is not an unproduced raw dataset" in text


def test_conflicting_paths_for_one_dataset_rejected() -> None:
    with pytest.raises(RegistryError, match="declared with different paths"):
        _build({
            "a": _h(RAW, {"a": "data/stage_01/a.csv"}),
            "b": _h({"a": "data/stage_01/a_old.csv"}, {"b": "data/stage_02/b.csv"}),
        })


# --- CLI + real repo -----------------------------------------------------------------


def test_cli_check_reports_problems_with_nonzero_exit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    mod = tmp_path / "src" / "eupy" / "transform" / "bad.py"
    mod.parent.mkdir(parents=True)
    mod.write_text('"""No block here."""\n', encoding="utf-8")
    assert main(["check", "--root", str(tmp_path)]) == 1
    assert "transform/bad.py: pipeline script has no I/O block" in capsys.readouterr().err


def test_real_repo_check_passes_and_show_is_deterministic() -> None:
    # Guards against header drift in any future change; reads docstrings only, no data or network.
    assert main(["check"]) == 0
    assert render(Registry.from_repo()) == render(Registry.from_repo())


def test_curated_dataset_is_stage_zero_even_when_an_ingest_script_writes_it() -> None:
    reg = _build({
        "ingest": _h({}, {"v": "data/curated/v/"}),
        "resolve": _h({**RAW, "v": "data/curated/v/"}, {"xw": "data/stage_01/xw.csv"}),
    })
    assert reg.stage("v") == 0 and reg.datasets["v"].raw and reg.producer("v") == "ingest"
    assert reg.stage("xw") == 1
    assert reg.topo_order() == ("ingest", "resolve")


def test_unproduced_curated_needs_a_raw_sources_entry_but_produced_one_does_not() -> None:
    headers = {"src/eupy/transform/a.py": _h({"v": "data/curated/v/"}, {})}
    with pytest.raises(RegistryError, match="not declared in raw_sources.toml"):
        Registry.from_headers(headers, RAW_SOURCES)
    ok = {"v": {"origin": "hand-curated", "refresh": "manual"}}
    assert Registry.from_headers(headers, ok).stage("v") == 0


def test_real_repo_has_no_in_place_scripts_and_no_self_loops() -> None:
    from eupy.registry import IN_PLACE_ALLOWLIST

    reg = Registry.from_repo()
    assert frozenset() == IN_PLACE_ALLOWLIST
    assert all(not s.in_place for s in reg.scripts.values())
    assert all(a != b for a, b in reg.edges())
    assert all(not (set(s.inputs) & set(s.outputs)) for s in reg.scripts.values())
