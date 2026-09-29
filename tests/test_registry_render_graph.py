"""Tests for the graph renderer (eupy.registry.render_graph) and the `graph` / `docs` CLI subcommands."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from eupy.registry import render_graph as rg
from eupy.registry.__main__ import main
from eupy.registry.headers import Header
from eupy.registry.model import REPO_ROOT, Registry
from eupy.registry.render_catalogue import render_catalogue
from eupy.registry.render_graph import GraphError, render_graph

RAW_SOURCES = {"manual/raw": {"origin": "Some where (manual)", "refresh": "manual"}}
NODE = re.compile(r"^    ((?:x|s|d)_\w+)(\(\[|\[\(|\[)(\".*\")(\]\)|\)\]|\])$", re.M)
EDGE = re.compile(r"^    ((?:x|s|d)_\w+) --> ((?:x|s|d)_\w+)$", re.M)
CLASS = re.compile(r"^    class ([\w,]+) (\w+)$", re.M)


def _registry() -> Registry:
    headers = {
        "src/eupy/fetchers/fetch_x.py": Header(
            (), ("api.example.com/x (live)",), (("x/raw", "data/raw_data/x/"),), False, True, "full overwrite", None
        ),
        "src/eupy/transform/make_a.py": Header(
            (("x/raw", "data/raw_data/x/"), ("manual/raw", "data/raw_data/manual/raw.csv")),
            (),
            (("a", "data/stage_01/a.csv"),),
            True,
            False,
        ),
        "src/eupy/transform/make_b.py": Header(
            (("a", "data/stage_01/a.csv"),), (), (("b", "data/stage_02/b.csv"),), False, False
        ),
        "src/eupy/transform/fix_a.py": Header(
            (("a", "data/stage_01/a.csv"),), (), (("a", "data/stage_01/a.csv"),), False, False
        ),
    }
    return Registry.from_headers(headers, RAW_SOURCES, allowlist=frozenset({"fix_a"}))


def _mermaid(text: str) -> str:
    return text.split("```mermaid")[1].split("```")[0]


def _edges(text: str) -> set[tuple[str, str]]:
    return set(EDGE.findall(_mermaid(text)))


def test_golden_shapes_ids_colors() -> None:
    text = render_graph(_registry())
    assert text.startswith("<!-- GENERATED") and "data-catalogue.md" in text
    assert text.endswith("```\n")
    for line in [
        '    x_api_example_com_x__live_(["api.example.com/x (live)"])',
        '    x_Some_where__manual_(["Some where (manual)"])',
        '    s_make_a["make_a"]',
        '    d_x_raw[("x/raw<br/>(stage 0)")]',
        '    d_a[("a ★<br/>(stage 1)")]',
        '    d_b[("b<br/>(stage 2)")]',
        "    d_manual_raw --> s_make_a",
        "    x_Some_where__manual_ --> d_manual_raw",
        "    classDef stage1_final fill:#dcecdc,stroke:#5a9c5a,color:#1e3a1e,stroke-width:4px",
        "    classDef stage2 fill:#dbe9f6,stroke:#5b84a3,color:#1a2b3c",
        "    classDef script_stage2 fill:#f7f7f7,stroke:#5b84a3,color:#222222",
        "    classDef source fill:#e8e8e8,stroke:#888888,color:#333333",
        "    class d_a stage1_final",
        "    class s_make_b script_stage2",
    ]:
        assert line in text.splitlines(), line
    assert "flowchart LR" in text


def test_in_place_script_drawn_as_self_loop() -> None:
    edges = _edges(render_graph(_registry()))
    assert ("d_a", "s_fix_a") in edges and ("s_fix_a", "d_a") in edges


def test_deterministic() -> None:
    assert render_graph(_registry()) == render_graph(_registry())


def _check_edges_equal_registry(reg: Registry) -> None:
    def mid(node: str) -> str:
        kind, _, name = node.partition(":")
        return rg._ident({"source": "x", "script": "s", "dataset": "d"}[kind], name)

    expected = {(mid(a), mid(b)) for a, b in reg.edges()}
    for s in reg.scripts.values():
        for ds in s.in_place:
            expected |= {
                (mid(f"dataset:{ds}"), mid(f"script:{s.name}")),
                (mid(f"script:{s.name}"), mid(f"dataset:{ds}")),
            }
    assert _edges(render_graph(reg)) == expected


def test_edge_set_equals_registry_synthetic_and_real() -> None:
    _check_edges_equal_registry(_registry())
    _check_edges_equal_registry(Registry.from_repo())


@pytest.mark.parametrize("reg", [_registry(), Registry.from_repo()], ids=["synthetic", "repo"])
def test_coverage_every_node_exactly_one_class(reg: Registry) -> None:
    body = _mermaid(render_graph(reg))
    declared = [m[0] for m in NODE.findall(body)]
    assert len(declared) == len(set(declared)) == len(reg.sources) + len(reg.scripts) + len(reg.datasets)
    classed = [i for ids, _ in CLASS.findall(body) for i in ids.split(",")]
    assert sorted(classed) == sorted(declared)
    defined = set(re.findall(r"classDef (\w+) ", body))
    assert {c for _, c in CLASS.findall(body)} <= defined


def test_coverage_check_rejects_violations() -> None:
    with pytest.raises(GraphError, match="no class"):
        rg._check_coverage({"a", "b"}, {"c1": ["a"]}, {"c1"})
    with pytest.raises(GraphError, match="not a declared node"):
        rg._check_coverage({"a"}, {"c1": ["a", "typo"]}, {"c1"})
    with pytest.raises(GraphError, match="2 classes"):
        rg._check_coverage({"a"}, {"c1": ["a"], "c2": ["a"]}, {"c1", "c2"})
    with pytest.raises(GraphError, match="no classDef"):
        rg._check_coverage({"a"}, {"c1": ["a"]}, set())


def test_id_collision_raises() -> None:
    """Datasets `x.y` and `x_y` sanitize to the same id."""
    headers = {
        "src/eupy/t/p.py": Header(
            (), (), (("x.y", "data/stage_01/xy1.csv"), ("x_y", "data/stage_01/xy2.csv")), False, False
        )
    }
    with pytest.raises(GraphError, match="collision"):
        render_graph(Registry.from_headers(headers, {}))


def test_palette_exhausted_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rg, "PALETTE", rg.PALETTE[:2])
    with pytest.raises(GraphError, match="stage 2"):
        render_graph(_registry())


def test_new_stage_gets_new_hue() -> None:
    headers = {
        "src/eupy/t/p.py": Header((), (), (("a", "data/stage_01/a.csv"),), False, False),
        "src/eupy/t/q.py": Header((("a", "data/stage_01/a.csv"),), (), (("b", "data/stage_02/b.csv"),), False, False),
        "src/eupy/t/r.py": Header((("b", "data/stage_02/b.csv"),), (), (("c", "data/stage_03/c.csv"),), False, False),
    }
    text = render_graph(Registry.from_headers(headers, {}))
    fills = re.findall(r"classDef stage\d fill:(#\w+)", text)
    assert len(fills) == len(set(fills)) == 3


def test_graph_and_catalogue_agree_on_datasets_and_stages() -> None:
    reg = Registry.from_repo()
    graph = render_graph(reg)
    catalogue = render_catalogue(reg)
    from_graph = {
        name: int(stage) for name, stage in re.findall(r'\[\("(.+?)(?: ★)?<br/>\(stage (\d+)\)"\)\]', _mermaid(graph))
    }
    from_catalogue = {name: 0 for name in re.findall(r"^\| `([^`]+)` \| `data/raw_data/", catalogue, re.M)}
    from_catalogue |= {
        name: int(stage) for name, stage in re.findall(r"^\| `([^`]+)` \| (\d\d) \| `data/stage_", catalogue, re.M)
    }
    assert from_graph == from_catalogue == {n: d.stage for n, d in reg.datasets.items()}


def test_cli_graph_and_docs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    shutil.copytree(REPO_ROOT / "src" / "eupy", tmp_path / "src" / "eupy")
    (tmp_path / "docs").mkdir()
    reg = Registry.from_repo()
    assert main(["graph", "--root", str(tmp_path)]) == 0
    graph = (tmp_path / "docs" / "data-graph.md").read_text()
    assert graph == render_graph(reg) and not (tmp_path / "docs" / "data-catalogue.md").exists()
    capsys.readouterr()
    assert main(["graph", "--stdout", "--root", str(tmp_path)]) == 0
    assert capsys.readouterr().out == graph
    (tmp_path / "docs" / "data-graph.md").unlink()
    assert main(["docs", "--root", str(tmp_path)]) == 0
    assert (tmp_path / "docs" / "data-graph.md").read_text() == graph
    assert (tmp_path / "docs" / "data-catalogue.md").read_text() == render_catalogue(reg)


def test_label_escaping_keeps_mermaid_special_characters_literal() -> None:
    assert rg._q('a#b "c" <d> & e') == '"a#35;b #quot;c#quot; #lt;d#gt; #amp; e"'


def test_docs_writes_nothing_when_a_renderer_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A graph failure must not leave a freshly written catalogue next to a stale graph."""
    import eupy.registry.__main__ as cli

    def boom(_reg: Registry) -> str:
        raise GraphError("boom")

    shutil.copytree(REPO_ROOT / "src" / "eupy", tmp_path / "src" / "eupy")
    (tmp_path / "docs").mkdir()
    monkeypatch.setitem(cli.DOCS, "graph", (cli.DOCS["graph"][0], boom))
    assert main(["docs", "--root", str(tmp_path)]) == 1
    assert list((tmp_path / "docs").iterdir()) == []
