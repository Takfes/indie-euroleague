"""Crosswalks are a pure function of raw data + tracked verdict batches.

Two groups:
- Real-data rebuilds: each crosswalk is rebuilt from the (untracked) raw snapshot + the versioned
  `data/curated/*_verdicts/` batches; its md5 must equal the md5 recorded for that out in the
  committed `dvc.lock` (data/ itself is structure-only). Skipped when the raw files are absent.
- Synthetic purity checks: a resolver never reads its previous output (deleted or poisoned output
  gives the same result), and rerunning gives identical bytes.
"""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from eupy.devtools import extract_initial_verdicts
from eupy.entity import player_sources, resolve_fantasy_stats_player_names, resolve_player_names, resolve_team_names
from eupy.entity.verdict_batches import write_batch

REPO_ROOT = Path(__file__).resolve().parents[1]
CURATED = REPO_ROOT / "data" / "curated"
DVC_LOCK = REPO_ROOT / "dvc.lock"

PRICES = player_sources.MASTER_PATH
BOXSCORE = player_sources.BOXSCORE_PATH
HEADER = resolve_team_names.HEADER_PATH
PLAYERS = resolve_fantasy_stats_player_names.PLAYERS_PATH


def _missing(*paths: Path) -> bool:
    return not all(p.exists() for p in paths)


def locked_md5(lock_path: Path, out_path: str) -> str:
    """md5 recorded in a dvc.lock for the stage out `out_path` (repo-relative, posix)."""
    stages = yaml.safe_load(lock_path.read_text())["stages"]
    hits = [o["md5"] for st in stages.values() for o in st.get("outs", []) if o["path"] == out_path]
    assert len(hits) == 1, f"{out_path}: expected exactly one lock entry, found {len(hits)}"
    return hits[0]


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()  # DVC fingerprint, not security


def test_locked_md5_reads_synthetic_lock(tmp_path: Path) -> None:
    lock = tmp_path / "dvc.lock"
    lock.write_text(
        "schema: '2.0'\n"
        "stages:\n"
        "  a:\n    outs:\n    - path: data/stage_01/x.csv\n      md5: abc\n"
        "  b:\n    deps:\n    - path: data/stage_01/x.csv\n      md5: zzz\n"
        "    outs:\n    - path: data/stage_01/y.csv\n      md5: def\n"
    )
    assert locked_md5(lock, "data/stage_01/x.csv") == "abc"  # dep entry (zzz) is ignored
    assert locked_md5(lock, "data/stage_01/y.csv") == "def"
    with pytest.raises(AssertionError):
        locked_md5(lock, "data/stage_01/missing.csv")


# --- real-data rebuilds (md5 equal to the one recorded in the committed dvc.lock) ---------


@pytest.mark.skipif(_missing(PRICES, BOXSCORE), reason="raw prices / box-score data not staged")
def test_player_name_crosswalk_rebuilds_to_locked_md5(tmp_path: Path) -> None:
    out = tmp_path / "player_name_crosswalk.csv"
    resolve_player_names.main(["--out", str(out)])

    assert _md5(out) == locked_md5(DVC_LOCK, "data/stage_01/player_name_crosswalk.csv")


@pytest.mark.skipif(_missing(PRICES, HEADER), reason="raw prices / header data not staged")
def test_team_name_crosswalk_rebuilds_to_locked_md5(tmp_path: Path) -> None:
    out = tmp_path / "team_name_crosswalk.csv"
    resolve_team_names.main(["--out", str(out)])

    assert _md5(out) == locked_md5(DVC_LOCK, "data/stage_01/team_name_crosswalk.csv")


@pytest.mark.skipif(_missing(PLAYERS, PRICES, BOXSCORE), reason="raw fantasy stats / prices / box-score not staged")
def test_fantasy_stats_player_name_crosswalk_rebuilds_to_locked_md5(tmp_path: Path) -> None:
    out = tmp_path / "fantasy_stats_player_name_crosswalk.csv"
    resolve_fantasy_stats_player_names.main(["--out", str(out)])

    assert _md5(out) == locked_md5(DVC_LOCK, "data/stage_01/fantasy_stats_player_name_crosswalk.csv")


def test_extraction_keeps_only_agent_resolved_rows_sorted_and_is_reproducible(tmp_path: Path) -> None:
    """Synthetic crosswalks, so the test does not depend on (or break with) later committed batches."""
    stage, out_a, out_b = tmp_path / "stage", tmp_path / "a", tmp_path / "b"
    stage.mkdir()
    for csv_name, _dir, key_field, target_field in extract_initial_verdicts.CROSSWALKS:
        fields = [key_field, "match_status", target_field, "match_score", "matched_by", "notes"]
        rows = [
            dict(zip(fields, ["z", "confirmed", "Z Full", "95.0", "agent", "ok"], strict=True)),
            dict(zip(fields, ["m", "exact", "M Full", "100.0", "exact", ""], strict=True)),
            dict(zip(fields, ["a", "no_match", "", "", "agent", "none"], strict=True)),
            dict(zip(fields, ["q", "needs_review", "", "70.0", "fuzzy", ""], strict=True)),
        ]
        with (stage / csv_name).open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)

    extract_initial_verdicts.main(["--stage-dir", str(stage), "--curated-dir", str(out_a)])
    extract_initial_verdicts.main(["--stage-dir", str(stage), "--curated-dir", str(out_b)])

    for _csv, verdict_dir, key_field, _target in extract_initial_verdicts.CROSSWALKS:
        rel = Path(verdict_dir) / extract_initial_verdicts.BATCH_NAME
        assert (out_a / rel).read_bytes() == (out_b / rel).read_bytes()
        keys = [v[key_field] for v in json.loads((out_a / rel).read_text())["verdicts"]]
        assert keys == ["a", "z"]  # exact / needs_review rows are not verdicts; sorted by key


# --- synthetic purity checks -------------------------------------------------------------


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return path


@pytest.fixture
def raw(tmp_path: Path) -> dict[str, Path]:
    """Tiny raw inputs + one verdict batch per crosswalk."""
    prices = _write_csv(
        tmp_path / "prices.csv",
        ["name", "club", "role"],
        [
            {"name": "John Smith", "club": "Baskonia", "role": "player"},
            {"name": "Nobody Known", "club": "Zzz Club", "role": "player"},
        ],
    )
    boxscore = _write_csv(
        tmp_path / "box.csv",
        ["player", "player_id", "dorsal"],
        [{"player": "SMITH, John", "player_id": "P1", "dorsal": "5"}],
    )
    header = _write_csv(
        tmp_path / "header.csv",
        ["team_a", "team_id_a", "team_b", "team_id_b"],
        [{"team_a": "BASKONIA", "team_id_a": "BAS", "team_b": "OLYMPIACOS", "team_id_b": "OLY"}],
    )
    players = _write_csv(
        tmp_path / "players.csv",
        ["player_id", "name", "team"],
        [{"player_id": "1", "name": "J. Smith", "team": "KBA"}, {"player_id": "2", "name": "Q. Zed", "team": "KBA"}],
    )
    verdict_dirs = {k: tmp_path / k for k in ("player", "team", "fantasy")}
    for d in verdict_dirs.values():
        d.mkdir()
    write_batch(
        verdict_dirs["player"] / "0001.json",
        [{"name": "Nobody Known", "match_status": "no_match", "notes": "not in box scores"}],
        ingested_at="d",
        source="s",
    )
    write_batch(
        verdict_dirs["team"] / "0001.json",
        [{"name": "Zzz Club", "match_status": "no_match", "notes": "new club"}],
        ingested_at="d",
        source="s",
    )
    write_batch(
        verdict_dirs["fantasy"] / "0001.json",
        [{"player_id": "2", "match_status": "no_match", "notes": "rookie"}],
        ingested_at="d",
        source="s",
    )
    return {"prices": prices, "boxscore": boxscore, "header": header, "players": players} | {
        f"{k}_verdicts": v for k, v in verdict_dirs.items()
    }


def _poison(out: Path, header: str, row: str) -> None:
    out.write_text(f"{header}\n{row}\n", encoding="utf-8")


def _argv(raw: dict[str, Path], kind: str, out: Path) -> list[str]:
    common = ["--out", str(out), "--verdicts-dir", str(raw[f"{kind}_verdicts"])]
    if kind == "player":
        return [*common, "--master", str(raw["prices"]), "--boxscore", str(raw["boxscore"])]
    if kind == "team":
        return [*common, "--master", str(raw["prices"]), "--header", str(raw["header"])]
    return [
        *common,
        "--players",
        str(raw["players"]),
        "--prices",
        str(raw["prices"]),
        "--boxscore",
        str(raw["boxscore"]),
    ]


MAINS = {
    "player": resolve_player_names.main,
    "team": resolve_team_names.main,
    "fantasy": resolve_fantasy_stats_player_names.main,
}
# A pre-existing "agent-resolved" row the old merge-in-place code would have carried over.
POISON = {
    "player": (
        "name,boxscore_name,match_status,match_score,matched_by,notes",
        "John Smith,X,confirmed,1.0,agent,stale",
    ),
    "team": (
        "name,boxscore_team_name,match_status,match_score,matched_by,notes",
        "Baskonia,X,confirmed,1.0,agent,stale",
    ),
    "fantasy": (
        "player_id,name,team,resolved_name,match_status,match_score,matched_by,notes",
        "1,J. Smith,KBA,X,confirmed,1.0,agent,stale",
    ),
}


@pytest.mark.parametrize("kind", ["player", "team", "fantasy"])
def test_resolver_ignores_previous_output_and_is_reproducible(raw: dict[str, Path], tmp_path: Path, kind: str) -> None:
    out = tmp_path / f"{kind}.csv"
    MAINS[kind](_argv(raw, kind, out))
    first = out.read_bytes()

    MAINS[kind](_argv(raw, kind, out))  # rerun over its own output
    assert out.read_bytes() == first

    out.unlink()
    MAINS[kind](_argv(raw, kind, out))  # rebuild from nothing
    assert out.read_bytes() == first

    _poison(out, *POISON[kind])
    MAINS[kind](_argv(raw, kind, out))  # poisoned output is ignored
    assert out.read_bytes() == first
    assert b"stale" not in first
    assert b"no_match" in first  # the verdict batch was applied
