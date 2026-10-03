"""Tests for the `eupy` CLI (eupy.cli): target resolution, run planning/order with a fake runner, link-final."""

from __future__ import annotations

import shutil
from collections.abc import Sequence
from pathlib import Path

import pytest

from eupy.cli import CliError, is_fetcher, main, plan_run, resolve_target
from eupy.registry.dvc_gen import Pipeline
from eupy.registry.headers import Header
from eupy.registry.model import REPO_ROOT, Registry, is_stage0_path

RAW_SOURCES: dict[str, dict[str, str]] = {}


def _registry() -> Registry:
    """fetch_x (impure) -> x/raw -> make_a -> a -> make_b -> b (final) -> make_z; fetch_y (impure) -> y/raw."""
    headers = {
        "src/eupy/fetchers/fetch_x.py": Header(
            (), ("api.example.com/x (live)",), (("x/raw", "data/raw_data/x/"),), False, True, "full overwrite", None
        ),
        "src/eupy/fetchers/fetch_y.py": Header(
            (), ("api.example.com/y (live)",), (("y/raw", "data/raw_data/y/"),), False, True, "full overwrite", None
        ),
        "src/eupy/transform/make_a.py": Header(
            (("x/raw", "data/raw_data/x/"),), (), (("a", "data/stage_01/a.csv"),), False, False
        ),
        "src/eupy/transform/make_b.py": Header(
            (("a", "data/stage_01/a.csv"),), (), (("b", "data/stage_02/b.csv"),), True, False
        ),
        "src/eupy/transform/make_z.py": Header(
            (("b", "data/stage_02/b.csv"),), (), (("z", "data/stage_03/z.csv"),), False, False
        ),
    }
    return Registry.from_headers(headers, RAW_SOURCES)


PIPES = {
    "first": Pipeline("first", True, ("fetch_x", "make_a")),
    "second": Pipeline("second", True, ("make_z", "make_b")),  # listed out of order on purpose
    "grab": Pipeline("grab", False, ("fetch_y",)),
    "later": Pipeline("later", False, ("make_z",)),
}
WRAPPERS = {"both": ("first", "second")}


def test_resolve_target_pipeline_wrapper_and_unknown() -> None:
    assert resolve_target("first", PIPES, WRAPPERS) == ["first"]
    assert resolve_target("both", PIPES, WRAPPERS) == ["first", "second"]
    with pytest.raises(CliError, match=r"unknown target 'nope'.*first.*later.*both"):
        resolve_target("nope", PIPES, WRAPPERS)


def test_plan_offline_is_dvc_repro_only_in_topo_order() -> None:
    plan = plan_run(_registry(), PIPES, ["first", "second"], fetch=False)
    assert plan == [["uv", "run", "dvc", "repro", "make_a", "make_b", "make_z"]]


def test_plan_fetch_runs_fetcher_before_repro() -> None:
    plan = plan_run(_registry(), PIPES, ["first"], fetch=True)
    assert plan == [
        ["uv", "run", "python", "src/eupy/fetchers/fetch_x.py"],
        ["uv", "run", "dvc", "repro", "make_a"],
    ]


def test_plan_non_dvc_pipeline_runs_fetchers_only_or_errors() -> None:
    assert plan_run(_registry(), PIPES, ["grab"], fetch=False) == [
        ["uv", "run", "python", "src/eupy/fetchers/fetch_y.py"]
    ]
    with pytest.raises(CliError, match="not runnable under DVC yet"):
        plan_run(_registry(), PIPES, ["later"], fetch=False)


def _repo(tmp_path: Path) -> Path:
    """Real repo layout (headers, registry data, pipelines.toml) copied into tmp_path; no data files."""
    shutil.copytree(REPO_ROOT / "src", tmp_path / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(REPO_ROOT / "pipelines.toml", tmp_path / "pipelines.toml")
    return tmp_path


class _Fake:
    """Recording runner: never executes anything."""

    def __init__(self, code: int = 0) -> None:
        self.calls: list[list[str]] = []
        self.code = code

    def __call__(self, cmd: Sequence[str]) -> int:
        self.calls.append(list(cmd))
        return self.code


def test_run_schedule_fetch_orders_fetcher_then_repro_then_links(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root, fake = _repo(tmp_path), _Fake()
    (root / "data/stage_01").mkdir(parents=True)
    (root / "data/stage_01/schedule.csv").write_text("x")
    assert main(["run", "schedule", "--fetch"], runner=fake, root=root) == 0
    assert fake.calls == [
        ["uv", "run", "python", "src/eupy/fetchers/fetch_euroleague_schedule.py"],
        ["uv", "run", "dvc", "repro", "build_schedule_turns"],
    ]
    assert (root / "data/stage_99/schedule.csv").is_symlink()
    assert "linked   data/stage_99/schedule.csv" in capsys.readouterr().out


def _stage_raw(root: Path) -> list[str]:
    """Create every stage-0 (raw / curated) dataset path of the real registry under `root`; returns the paths."""
    registry = Registry.from_repo(root)
    paths = sorted(registry.path(ds) for ds in registry.datasets if is_stage0_path(registry.path(ds)))
    for path in paths:
        target = root / path
        if path.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("x")
    return paths


def test_run_all_wrapper_is_offline_by_default(tmp_path: Path) -> None:
    fake, root = _Fake(), _repo(tmp_path)
    _stage_raw(root)
    assert main(["run", "all"], runner=fake, root=root) == 0
    assert len(fake.calls) == 1 and fake.calls[0][:4] == ["uv", "run", "dvc", "repro"]


def test_dry_run_prints_and_never_executes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, fake = _repo(tmp_path), _Fake()
    assert main(["run", "schedule", "--fetch", "--dry-run"], runner=fake, root=root) == 0
    out = capsys.readouterr().out
    assert fake.calls == []
    assert out.index("fetch_euroleague_schedule.py") < out.index("dvc repro")
    assert not (root / "data/stage_99").exists()


def test_failure_stops_and_skips_link_final(tmp_path: Path) -> None:
    root, fake = _repo(tmp_path), _Fake(code=3)
    assert main(["run", "schedule", "--fetch"], runner=fake, root=root) == 3
    assert len(fake.calls) == 1  # fetcher failed; repro never ran
    assert not (root / "data/stage_99").exists()


def test_unknown_and_non_dvc_targets_exit_nonzero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, fake = _repo(tmp_path), _Fake()
    assert main(["run", "bogus"], runner=fake, root=root) == 1
    assert main(["run", "optimize"], runner=fake, root=root) == 1
    err = capsys.readouterr().err
    assert "unknown target 'bogus'" in err and "not runnable under DVC yet" in err
    assert fake.calls == []


def test_acquire_runs_fetchers_only(tmp_path: Path) -> None:
    fake = _Fake()
    assert main(["run", "acquire"], runner=fake, root=_repo(tmp_path)) == 0
    assert [c[3] for c in fake.calls] == [
        "src/eupy/fetchers/fetch_basketballsphere_prices.py",
        "src/eupy/fetchers/fetch_euroleague_fantasy_stats.py",
    ]


def test_link_final_command(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = _repo(tmp_path)
    (root / "data/stage_01").mkdir(parents=True)
    (root / "data/stage_01/schedule.csv").write_text("x")
    assert main(["link-final"], root=root) == 0
    assert "linked   data/stage_99/schedule.csv" in capsys.readouterr().out
    assert main(["link-final"], root=root) == 0
    assert "linked" not in capsys.readouterr().out  # second run: only skip notes, no changes


def test_ingest_scripts_are_never_fetchers() -> None:
    headers = {
        "src/eupy/e/ingest.py": Header(
            (), ("JSON verdicts file (--verdicts PATH)",), (("v", "data/curated/v/"),), False, True
        ),
        "src/eupy/e/resolve.py": Header(
            (("v", "data/curated/v/"),), (), (("xw", "data/stage_01/xw.csv"),), False, False
        ),
    }
    reg = Registry.from_headers(headers, RAW_SOURCES)
    pipes = {"ent": Pipeline("ent", True, ("ingest", "resolve"))}
    assert plan_run(reg, pipes, ["ent"], fetch=True) == [["uv", "run", "dvc", "repro", "resolve"]]
    assert not is_fetcher(reg, "ingest")


def test_missing_raw_input_blocks_before_anything_runs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, fake = _repo(tmp_path), _Fake()
    assert main(["run", "all"], runner=fake, root=root) == 1
    err = capsys.readouterr().err
    assert fake.calls == [] and not (root / "data/stage_99").exists()
    assert "kaggle_data/euroleague_box_score: data/raw_data/kaggle_data/euroleague_box_score.csv" in err
    assert "eupy run acquire" in err and "Kaggle" in err
    # Only absent paths are listed: stage them all but one.
    assert _stage_raw(root)
    (root / "data/raw_data/kaggle_data/euroleague_box_score.csv").unlink()
    assert main(["run", "all"], runner=fake, root=root) == 1
    err = capsys.readouterr().err
    assert err.count("data/raw_data/") == 1 and "euroleague_box_score.csv" in err and fake.calls == []


def test_fetch_covers_fetched_raw_inputs_but_not_the_rest(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    # schedule's only raw input is produced by its fetcher: fine with --fetch, blocked without.
    assert main(["run", "schedule", "--fetch"], runner=_Fake(), root=root) == 0
    fake = _Fake()
    assert main(["run", "schedule"], runner=fake, root=root) == 1
    assert fake.calls == []


def test_dry_run_skips_the_precheck(tmp_path: Path) -> None:
    fake = _Fake()
    assert main(["run", "all", "--dry-run"], runner=fake, root=_repo(tmp_path)) == 0
    assert fake.calls == []


def test_is_fetcher_is_path_based() -> None:
    headers = {
        "src/eupy/fetchers/fetch_q.py": Header((), ("plain label",), (("q", "data/raw_data/q/"),), False, True),
        "src/eupy/entity/live_ingest.py": Header(
            (), ("api.example.com (live)",), (("v", "data/curated/v/"),), False, True
        ),
        "src/eupy/fetchers/pure_one.py": Header((), (), (("p", "data/stage_01/p.csv"),), False, False),
    }
    reg = Registry.from_headers(headers, RAW_SOURCES)
    assert is_fetcher(reg, "fetch_q")  # under fetchers/, impure, no "(live" marker needed
    assert not is_fetcher(reg, "live_ingest")  # "(live" source but outside fetchers/
    assert not is_fetcher(reg, "pure_one")  # under fetchers/ but pure
