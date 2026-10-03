"""Tests for the HTML data map (eupy.registry.render_html) and the `html` CLI subcommand."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from eupy.registry import __main__ as cli
from eupy.registry.__main__ import main
from eupy.registry.dvc_gen import PIPELINES_FILE, Pipeline, checked_pipelines, load_wrappers
from eupy.registry.model import REPO_ROOT, Registry
from eupy.registry.render_graph import build_model
from eupy.registry.render_html import parse_dvc_status, render_html

PAYLOAD = re.compile(r'<script id="payload" type="application/json">(.*?)</script>', re.S)


def _payload(text: str) -> dict:
    return json.loads(PAYLOAD.search(text).group(1))


@pytest.fixture(scope="module")
def repo() -> tuple[Registry, dict[str, Pipeline], dict[str, tuple[str, ...]]]:
    reg = Registry.from_repo(REPO_ROOT)
    return reg, checked_pipelines(reg, REPO_ROOT / PIPELINES_FILE), load_wrappers(REPO_ROOT / PIPELINES_FILE)


def test_payload_nodes_and_edges_equal_the_markdown_graph_model(repo) -> None:
    reg, pipelines, wrappers = repo
    model = build_model(reg)
    data = _payload(render_html(reg, pipelines, wrappers, REPO_ROOT))
    assert set(data["nodes"]) == set(model.ids.values())
    assert {tuple(e) for e in data["edges"]} == {(model.ids[a], model.ids[b]) for a, b in model.edges}
    for node, (_, cls) in model.decl.items():
        assert data["nodes"][model.ids[node]]["cls"] == cls
    assert set(data["classes"]) == set(model.classes)
    assert data["pipelines"].keys() == pipelines.keys() and data["wrappers"] == {
        k: list(v) for k, v in wrappers.items()
    }


def test_panel_details_and_static_content(repo) -> None:
    reg, pipelines, wrappers = repo
    text = render_html(reg, pipelines, wrappers, REPO_ROOT)
    data = _payload(text)
    script = data["nodes"]["s_build_schedule_turns"]["detail"]
    assert script["path"].endswith("build_schedule_turns.py") and script["pipelines"] == ["schedule"]
    assert script["doc"] and "\n" not in script["doc"]
    ds = data["nodes"]["d_schedule"]["detail"]
    assert ds["producer"] == "s_build_schedule_turns" and ds["final"] is True
    assert str(REPO_ROOT) not in text  # no absolute paths -> identical across machines
    assert "status" not in script and data["status"] is False


def test_output_is_deterministic(repo) -> None:
    reg, pipelines, wrappers = repo
    assert render_html(reg, pipelines, wrappers, REPO_ROOT) == render_html(reg, pipelines, wrappers, REPO_ROOT)


def test_header_text_cannot_break_out_of_the_script_element(repo, tmp_path: Path) -> None:
    reg, pipelines, wrappers = repo
    evil = tmp_path / "src.py"
    evil.write_text('"""</script><img src=x onerror=alert(1)> first paragraph."""\n', encoding="utf-8")
    name, script = next(iter(reg.scripts.items()))
    # Point one script of the real registry at the crafted file, then restore it.
    reg.scripts[name] = replace(script, path="src.py")
    try:
        text = render_html(reg, pipelines, wrappers, tmp_path)
    finally:
        reg.scripts[name] = script
    body = PAYLOAD.search(text).group(1)
    assert "</script" not in body and "<img" not in body
    assert "<img src=x" in _payload(text)["nodes"][f"s_{name}"]["detail"]["doc"]  # data intact, just escaped


def test_parse_dvc_status() -> None:
    steps = ["a", "b", "c"]
    assert parse_dvc_status("{}", steps) == {"a": "fresh", "b": "fresh", "c": "fresh"}
    raw = json.dumps({"b": [{"changed deps": {"x": "modified"}}], "dvc.yaml:c": ["always changed"]})
    assert parse_dvc_status(raw, steps) == {"a": "fresh", "b": "stale", "c": "stale"}
    for bad in ("not json", "[]"):
        with pytest.raises(ValueError):
            parse_dvc_status(bad, steps)


def test_status_overlay_marks_scripts(repo) -> None:
    reg, pipelines, wrappers = repo
    text = render_html(reg, pipelines, wrappers, REPO_ROOT, {"build_schedule_turns": "stale"})
    data = _payload(text)
    assert data["status"] is True and data["nodes"]["s_build_schedule_turns"]["detail"]["status"] == "stale"


def test_cli_writes_page_and_status_degrades_gracefully(monkeypatch, capsys, tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "pipelines.toml").write_text((REPO_ROOT / "pipelines.toml").read_text(), encoding="utf-8")
    real = Registry.from_repo(REPO_ROOT)
    monkeypatch.setattr(cli.Registry, "from_repo", lambda root: real)

    def boom(*a, **k):
        raise FileNotFoundError("no uv")

    monkeypatch.setattr(subprocess, "run", boom)
    assert main(["html", "--status", "--root", str(tmp_path)]) == 0
    assert (tmp_path / "docs" / "data-map.html").is_file()
    assert not (tmp_path / "docs" / "data-map.status.html").exists()
    assert "no DVC status overlay" in capsys.readouterr().err


def test_status_with_stdout_does_not_run_dvc_status(monkeypatch, capsys, tmp_path: Path) -> None:
    (tmp_path / "pipelines.toml").write_text((REPO_ROOT / "pipelines.toml").read_text(), encoding="utf-8")
    real = Registry.from_repo(REPO_ROOT)
    monkeypatch.setattr(cli.Registry, "from_repo", lambda root: real)
    calls: list[object] = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: calls.append(a))

    assert main(["html", "--status", "--stdout", "--root", str(tmp_path)]) == 0

    assert calls == []
    assert capsys.readouterr().out.startswith("<!doctype html>")


def test_committed_page_is_current_and_status_variant_is_ignored(repo) -> None:
    reg, pipelines, wrappers = repo
    committed = (REPO_ROOT / "docs" / "data-map.html").read_text(encoding="utf-8")
    assert committed == render_html(reg, pipelines, wrappers, REPO_ROOT)
    assert not _payload(committed)["status"]
    cmd = ["git", "check-ignore", "-q", "docs/data-map.status.html"]
    ignored = subprocess.run(cmd, cwd=REPO_ROOT, check=False).returncode  # noqa: S603
    assert ignored == 0
