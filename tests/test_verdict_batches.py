"""Tests for verdict batch loading, validation and the later-batch-wins merge rule."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eupy.entity.verdict_batches import (
    apply_verdicts,
    ingest_batch,
    load_batches,
    merge_verdicts,
    run_ingest_cli,
    write_batch,
)


def _verdict(name: str, status: str = "no_match", notes: str = "n", **extra: str) -> dict[str, str]:
    return {"name": name, "match_status": status, "notes": notes, **extra}


def _write_raw(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj), encoding="utf-8")


def test_load_batches_orders_records_by_file_name_not_creation_order(tmp_path: Path) -> None:
    write_batch(tmp_path / "0002_second.json", [_verdict("B")], ingested_at="2026-10-01", source="s")
    write_batch(tmp_path / "0001_first.json", [_verdict("A")], ingested_at="2026-09-30", source="s")
    (tmp_path / ".gitkeep").write_text("", encoding="utf-8")  # non-JSON files are ignored

    assert [r["name"] for r in load_batches(tmp_path)] == ["A", "B"]


def test_later_batch_wins_on_the_same_key(tmp_path: Path) -> None:
    write_batch(tmp_path / "0001_a.json", [_verdict("A", "no_match", "old")], ingested_at="d", source="s")
    write_batch(
        tmp_path / "0002_b.json",
        [_verdict("A", "confirmed", "new", boxscore_name="A X")],
        ingested_at="d",
        source="s",
    )

    merged = merge_verdicts(load_batches(tmp_path), "name", "boxscore_name")

    assert merged["A"]["match_status"] == "confirmed"
    assert merged["A"]["notes"] == "new"


def test_write_batch_is_deterministic_sorted_json_with_trailing_newline(tmp_path: Path) -> None:
    path = tmp_path / "0001.json"
    write_batch(path, [_verdict("A")], ingested_at="2026-09-30", source="s")
    text = path.read_text(encoding="utf-8")

    assert text.endswith("}\n")
    assert json.loads(text) == {"ingested_at": "2026-09-30", "source": "s", "verdicts": [_verdict("A")]}
    assert text == json.dumps(json.loads(text), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def test_load_batches_raises_for_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_batches(tmp_path / "nope")


@pytest.mark.parametrize(
    ("obj", "message"),
    [
        ([_verdict("A")], "JSON object"),  # bare list (old --verdicts format), not a batch object
        ({"source": "s", "verdicts": []}, "ingested_at"),
        ({"ingested_at": "d", "verdicts": []}, "source"),
        ({"ingested_at": "d", "source": "s", "verdicts": {}}, "verdicts"),
        ({"ingested_at": "d", "source": "s", "verdicts": ["x"]}, "verdicts"),
    ],
)
def test_malformed_batch_is_rejected_naming_the_file(tmp_path: Path, obj: object, message: str) -> None:
    _write_raw(tmp_path / "0001_bad.json", obj)

    with pytest.raises(ValueError, match=message) as excinfo:
        load_batches(tmp_path)
    assert "0001_bad.json" in str(excinfo.value)


@pytest.mark.parametrize(
    "verdict",
    [
        {"match_status": "no_match", "notes": "n"},  # no key
        _verdict("A", "needs_review"),  # non-terminal status
        _verdict("A", "confirmed"),  # confirmed without a target name
        _verdict("A", "no_match", boxscore_name="A X"),  # target name on non-confirmed
        _verdict("A", notes=""),  # no rationale
    ],
)
def test_merge_rejects_malformed_verdicts(verdict: dict) -> None:
    with pytest.raises(ValueError):
        merge_verdicts([verdict], "name", "boxscore_name")


def test_merge_stringifies_integer_keys() -> None:
    merged = merge_verdicts([{"player_id": 7, "match_status": "no_match", "notes": "n"}], "player_id", "resolved_name")

    assert list(merged) == ["7"]


def test_apply_verdicts_overwrites_resolution_fields_and_skips_unknown_keys() -> None:
    rows = {
        "A": {
            "name": "A",
            "boxscore_name": "",
            "match_status": "no_candidate",
            "match_score": "",
            "matched_by": "fuzzy",
            "notes": "",
        }
    }
    verdicts = merge_verdicts(
        [_verdict("A", "confirmed", "why", boxscore_name="A X", match_score=91.25), _verdict("Gone")],
        "name",
        "boxscore_name",
    )

    apply_verdicts(rows, verdicts, "boxscore_name")

    assert rows["A"] == {
        "name": "A",
        "boxscore_name": "A X",
        "match_status": "confirmed",
        "match_score": "91.2",
        "matched_by": "agent",
        "notes": "why",
    }
    assert "Gone" not in rows


# --- ingest_batch ---------------------------------------------------------------------------

EXISTING = {"A": "0001_a.json", "B": "0003_b.json"}


def _seed_dir(directory: Path) -> dict[str, bytes]:
    """Write two batches (gap in numbering) and return their bytes for before/after comparison."""
    write_batch(directory / EXISTING["A"], [_verdict("A")], ingested_at="2026-09-30", source="s")
    write_batch(directory / EXISTING["B"], [_verdict("B")], ingested_at="2026-09-30", source="s")
    return {p.name: p.read_bytes() for p in directory.glob("*.json")}


def _input(tmp_path: Path, payload: object, name: str = "My Verdicts.json") -> Path:
    path = tmp_path / name
    _write_raw(path, payload)
    return path


def test_ingest_batch_writes_envelope_at_next_sequence_number(tmp_path: Path) -> None:
    batches = tmp_path / "batches"
    before = _seed_dir(batches)
    records = [_verdict("C", "confirmed", boxscore_name="X")]

    out = ingest_batch(_input(tmp_path, records), batches, "name", "boxscore_name", today="2026-10-03")

    assert out.name == "0004_my-verdicts.json"  # max existing (3) + 1; stem sanitized to a slug
    assert out.read_text(encoding="utf-8").endswith("}\n")
    assert json.loads(out.read_text(encoding="utf-8")) == {
        "ingested_at": "2026-10-03",
        "source": "My Verdicts.json",
        "verdicts": records,
    }
    assert {n: (batches / n).read_bytes() for n in before} == before  # existing batches byte-unchanged


def test_ingest_batch_second_ingest_increments_and_label_overrides_slug(tmp_path: Path) -> None:
    batches = tmp_path / "batches"
    src = _input(tmp_path, [_verdict("A")])
    first = ingest_batch(src, batches, "name", "boxscore_name", today="d")
    second = ingest_batch(src, batches, "name", "boxscore_name", label="Round 2!", today="d")

    assert (first.name, second.name) == ("0001_my-verdicts.json", "0002_round-2.json")


def test_ingest_batch_ingested_files_are_loadable_by_load_batches(tmp_path: Path) -> None:
    batches = tmp_path / "batches"
    ingest_batch(_input(tmp_path, [_verdict("A")]), batches, "name", "boxscore_name")

    assert [r["name"] for r in load_batches(batches)] == ["A"]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ([], "empty"),
        ({"name": "A"}, "JSON list"),
        (["not-an-object"], "JSON object"),
        ([{"match_status": "no_match", "notes": "n"}], "no 'name'"),
        ([_verdict("A", "needs_review")], "match_status"),
        ([_verdict("A", "confirmed")], "required for"),
        ([_verdict("A", "rejected", boxscore_name="X")], "only allowed on"),
        ([{"name": "A", "match_status": "no_match"}], "no notes"),
        ([_verdict("A"), _verdict("B", "needs_review")], "match_status"),  # one bad record poisons the file
        ([_verdict("A"), _verdict("B"), _verdict("A")], r"duplicate 'name' within one batch: \['A'\]"),
    ],
)
def test_ingest_batch_rejects_invalid_input_and_writes_nothing(tmp_path: Path, payload: object, message: str) -> None:
    batches = tmp_path / "batches"
    before = _seed_dir(batches)

    with pytest.raises(ValueError, match=message):
        ingest_batch(_input(tmp_path, payload), batches, "name", "boxscore_name")

    assert {p.name: p.read_bytes() for p in batches.iterdir()} == before


def test_ingest_batch_rejects_unreadable_and_non_json_files_without_creating_the_directory(tmp_path: Path) -> None:
    batches = tmp_path / "batches"
    garbage = tmp_path / "garbage.json"
    garbage.write_text("{nope", encoding="utf-8")

    with pytest.raises(ValueError, match="not valid JSON"):
        ingest_batch(garbage, batches, "name", "boxscore_name")
    with pytest.raises(ValueError, match="Cannot read"):
        ingest_batch(tmp_path / "missing.json", batches, "name", "boxscore_name")
    assert not batches.exists()


def test_ingest_batch_refuses_to_overwrite_a_colliding_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    batches = tmp_path / "batches"
    before = _seed_dir(batches)
    # Simulate a race: the computed next path points at an already existing batch.
    monkeypatch.setattr("eupy.entity.verdict_batches._next_batch_path", lambda d, s: d / EXISTING["A"])

    with pytest.raises(FileExistsError, match="Refusing to overwrite"):
        ingest_batch(_input(tmp_path, [_verdict("Z")]), batches, "name", "boxscore_name")

    assert {p.name: p.read_bytes() for p in batches.iterdir()} == before


def test_ingest_batch_does_not_check_keys_against_data_and_supports_player_id_key(tmp_path: Path) -> None:
    records = [{"player_id": 99999, "match_status": "confirmed", "resolved_name": "X", "notes": "n"}]

    out = ingest_batch(_input(tmp_path, records), tmp_path / "b", "player_id", "resolved_name", today="d")

    assert json.loads(out.read_text(encoding="utf-8"))["verdicts"] == records


def test_cli_overlong_label_exits_1_with_error_line_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    batches = tmp_path / "batches"
    argv = ["prog", "--verdicts", str(_input(tmp_path, [_verdict("A")])), "--batches-dir", str(batches)]
    monkeypatch.setattr("sys.argv", [*argv, "--label", "x" * 300])

    assert run_ingest_cli("d", batches, "name", "boxscore_name") == 1

    err = capsys.readouterr().err
    assert err.startswith("error:") and "nothing written" in err
    assert list(batches.glob("*.json")) == []
