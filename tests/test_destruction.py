"""Tests for the retention-policy destruction executor.

The invariants: a dry run deletes and logs nothing; a real run deletes only
the listed PII artifacts older than the cutoff, never the provenance log or
newer files; each deletion appends a ``destroyed`` certificate binding the
pre-deletion SHA-256 into the chain; the chain still verifies afterward; and
no planted field value survives anywhere under the out directory. The cache
walk holds to the exact stage-cache entry shape, and an operator-supplied
``--cache-dir`` without that shape is refused whole before anything is
deleted.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import timedelta
from pathlib import Path

import pytest

from constituent_reconciler.cli import main
from constituent_reconciler.destruction import (
    PII_ARTIFACTS,
    PROVENANCE_FILENAME,
    destroy,
    inventory,
    parse_retention,
)
from constituent_reconciler.provenance import ProvenanceLog, content_hash, verify_log

SENTINEL = "PLANTED-PII-alice.walker@example.org"
DAY_SECONDS = 86_400.0


def _age(path: Path, seconds: float) -> None:
    stamp = time.time() - seconds
    os.utime(path, (stamp, stamp))


def _make_out_dir(tmp_path: Path) -> Path:
    """An out dir with two day-old PII artifacts and a seeded provenance log."""

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    (out_dir / "resolved.csv").write_text(f"cluster_id,email\nC1,{SENTINEL}\n", encoding="utf-8")
    (out_dir / "review_queue.csv").write_text(
        f"left,right,email_left\nA,B,{SENTINEL}\n", encoding="utf-8"
    )
    for name in ("resolved.csv", "review_queue.csv"):
        _age(out_dir / name, 2 * DAY_SECONDS)
    log = ProvenanceLog(out_dir / PROVENANCE_FILENAME)
    log.append(action="created", record_id="C1", members=["C1"], consent=True, payload={"a": "1"})
    _age(out_dir / PROVENANCE_FILENAME, 2 * DAY_SECONDS)
    return out_dir


def test_parse_retention_accepts_days_hours_and_zero() -> None:
    assert parse_retention("30d") == timedelta(days=30)
    assert parse_retention("12h") == timedelta(hours=12)
    assert parse_retention("0d") == timedelta(0)


@pytest.mark.parametrize("bad", ["", "30", "1w", "-1d", "d", "30 days", "1.5d"])
def test_parse_retention_rejects_unknown_forms(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_retention(bad)


def test_inventory_lists_only_old_listed_artifacts(tmp_path: Path) -> None:
    out_dir = _make_out_dir(tmp_path)
    # A fresh PII artifact and an old non-PII artifact must both be excluded.
    (out_dir / "withheld.csv").write_text("cluster_id,members,reason\n", encoding="utf-8")
    (out_dir / "aggregate_summary.json").write_text("{}", encoding="utf-8")
    _age(out_dir / "aggregate_summary.json", 2 * DAY_SECONDS)

    names = [path.name for path in inventory(out_dir, timedelta(days=1))]
    assert names == ["resolved.csv", "review_queue.csv"]


def test_provenance_log_is_not_a_listed_artifact() -> None:
    assert PROVENANCE_FILENAME not in PII_ARTIFACTS


def test_dry_run_deletes_nothing_and_logs_nothing(tmp_path: Path) -> None:
    out_dir = _make_out_dir(tmp_path)
    before = (out_dir / PROVENANCE_FILENAME).read_text(encoding="utf-8")

    log = ProvenanceLog(out_dir / PROVENANCE_FILENAME)
    summary = destroy(out_dir, timedelta(days=1), policy="1d", log=log, dry_run=True)

    assert summary.dry_run
    assert summary.candidates == ("resolved.csv", "review_queue.csv")
    assert summary.destroyed == ()
    assert (out_dir / "resolved.csv").exists()
    assert (out_dir / "review_queue.csv").exists()
    assert (out_dir / PROVENANCE_FILENAME).read_text(encoding="utf-8") == before


def test_destroy_deletes_hashes_and_certifies(tmp_path: Path) -> None:
    out_dir = _make_out_dir(tmp_path)
    (out_dir / "withheld.csv").write_text("cluster_id,members,reason\n", encoding="utf-8")
    expected = {
        name: hashlib.sha256((out_dir / name).read_bytes()).hexdigest()
        for name in ("resolved.csv", "review_queue.csv")
    }
    sizes = {name: (out_dir / name).stat().st_size for name in ("resolved.csv", "review_queue.csv")}

    log = ProvenanceLog(out_dir / PROVENANCE_FILENAME)
    summary = destroy(out_dir, timedelta(days=1), policy="1d", log=log, dry_run=False)

    assert not (out_dir / "resolved.csv").exists()
    assert not (out_dir / "review_queue.csv").exists()
    # Newer PII artifacts and the provenance log survive.
    assert (out_dir / "withheld.csv").exists()
    assert (out_dir / PROVENANCE_FILENAME).exists()
    assert {a.name: a.sha256 for a in summary.destroyed} == expected

    entries = [
        json.loads(line)
        for line in (out_dir / PROVENANCE_FILENAME).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    certificates = [e for e in entries if e["action"] == "destroyed"]
    assert [e["record_id"] for e in certificates] == ["resolved.csv", "review_queue.csv"]
    for entry in certificates:
        name = str(entry["record_id"])
        # The pre-deletion hash is readable in the entry and bound into the chain.
        assert entry["external_id"] == f"sha256:{expected[name]}"
        assert entry["members"] == []
        assert entry["content_hash"] == content_hash(
            {
                "artifact": name,
                "sha256": expected[name],
                "size": str(sizes[name]),
                "policy": "1d",
            }
        )

    ok, message = verify_log(out_dir / PROVENANCE_FILENAME)
    assert ok, message


def test_repair_plan_is_a_listed_artifact_and_is_destroyed(tmp_path: Path) -> None:
    """The split-repair plan holds raw field values, so destroy must cover it.

    ADR 0012 names the destruction-inventory entry a prerequisite for storing
    plans at all: without it, `constituent-reconcile destroy` would leave the one artifact
    that concentrates a bad merge's raw values behind.
    """

    assert "repair_plan.json" in PII_ARTIFACTS
    out_dir = _make_out_dir(tmp_path)
    plan_path = out_dir / "repair_plan.json"
    plan_path.write_text(
        json.dumps({"split_records": [{"fields": {"email": SENTINEL}}]}), encoding="utf-8"
    )
    _age(plan_path, 2 * DAY_SECONDS)

    log = ProvenanceLog(out_dir / PROVENANCE_FILENAME)
    summary = destroy(out_dir, timedelta(days=1), policy="1d", log=log, dry_run=False)

    assert not plan_path.exists()
    assert "repair_plan.json" in {artifact.name for artifact in summary.destroyed}
    entries = [
        json.loads(line)
        for line in (out_dir / PROVENANCE_FILENAME).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    certified = [
        e for e in entries if e["action"] == "destroyed" and e["record_id"] == plan_path.name
    ]
    assert len(certified) == 1
    assert SENTINEL not in (out_dir / PROVENANCE_FILENAME).read_text(encoding="utf-8")
    ok, message = verify_log(out_dir / PROVENANCE_FILENAME)
    assert ok, message


def test_repair_receipts_is_a_listed_artifact_and_is_destroyed(tmp_path: Path) -> None:
    """The applied-repair receipt holds before/after raw values, so destroy must cover it.

    ADR 0012 places the same duty on ``apply_repair``'s receipt file as on
    the plan file it is written beside: without this entry, `constituent-reconcile
    destroy` would leave behind the one artifact that concentrates the raw
    values a real repair actually changed.
    """

    assert "repair_receipts.json" in PII_ARTIFACTS
    out_dir = _make_out_dir(tmp_path)
    receipts_path = out_dir / "repair_receipts.json"
    receipts_path.write_text(
        json.dumps({"operations": [{"before": SENTINEL, "after": "corrected"}]}),
        encoding="utf-8",
    )
    _age(receipts_path, 2 * DAY_SECONDS)

    log = ProvenanceLog(out_dir / PROVENANCE_FILENAME)
    summary = destroy(out_dir, timedelta(days=1), policy="1d", log=log, dry_run=False)

    assert not receipts_path.exists()
    assert "repair_receipts.json" in {artifact.name for artifact in summary.destroyed}
    assert SENTINEL not in (out_dir / PROVENANCE_FILENAME).read_text(encoding="utf-8")
    ok, message = verify_log(out_dir / PROVENANCE_FILENAME)
    assert ok, message


def test_no_planted_value_survives_under_out_dir(tmp_path: Path) -> None:
    out_dir = _make_out_dir(tmp_path)
    log = ProvenanceLog(out_dir / PROVENANCE_FILENAME)
    destroy(out_dir, timedelta(0), policy="0d", log=log, dry_run=False)

    remaining = [p for p in out_dir.rglob("*") if p.is_file()]
    assert remaining, "the provenance log must survive"
    for path in remaining:
        assert SENTINEL.encode("utf-8") not in path.read_bytes(), path


ENTRY_NAME = "ab" * 16 + ".json"


def _make_cache_root(root: Path, *, age_seconds: float = 2 * DAY_SECONDS) -> Path:
    """A well-formed stage cache: one aged entry per stage directory."""

    for stage in ("extract", "normalize"):
        stage_dir = root / stage
        stage_dir.mkdir(parents=True, exist_ok=True)
        entry = stage_dir / ENTRY_NAME
        entry.write_text('{"payload": {}}', encoding="utf-8")
        _age(entry, age_seconds)
    return root


def test_destroy_refuses_cache_dir_pointed_at_the_out_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The reviewer's misuse scenario: --out OUT --cache-dir OUT. The out dir
    # holds artifacts destruction deliberately excludes; treating it as a
    # cache root must be refused whole, with nothing deleted and no
    # certificate minted.
    out_dir = _make_out_dir(tmp_path)
    for name in ("run_manifest.json", "run_summary.json", "decisions.json"):
        (out_dir / name).write_text("{}", encoding="utf-8")
        _age(out_dir / name, 2 * DAY_SECONDS)
    _make_cache_root(out_dir / "stage_cache")
    before = sorted(p for p in out_dir.rglob("*") if p.is_file())
    log_before = (out_dir / PROVENANCE_FILENAME).read_text(encoding="utf-8")

    for extra in ([], ["--dry-run"]):
        code = main(
            ["destroy", "--out", str(out_dir), "--cache-dir", str(out_dir), "--older-than", "0d"]
            + extra
        )
        assert code == 2
        assert "refusing --cache-dir" in capsys.readouterr().err
        assert sorted(p for p in out_dir.rglob("*") if p.is_file()) == before
        assert (out_dir / PROVENANCE_FILENAME).read_text(encoding="utf-8") == log_before

    # The API-level call refuses the same way, before any deletion.
    log = ProvenanceLog(out_dir / PROVENANCE_FILENAME)
    with pytest.raises(ValueError, match="stage-cache"):
        destroy(out_dir, timedelta(0), policy="0d", log=log, cache_dir=out_dir)
    assert sorted(p for p in out_dir.rglob("*") if p.is_file()) == before


def test_destroy_refuses_a_cache_dir_with_foreign_content(tmp_path: Path) -> None:
    out_dir = _make_out_dir(tmp_path)
    boundary = _make_cache_root(tmp_path / "boundary")
    stray = boundary / "notes.txt"
    stray.write_text("not a cache entry", encoding="utf-8")
    _age(stray, 2 * DAY_SECONDS)

    log = ProvenanceLog(out_dir / PROVENANCE_FILENAME)
    with pytest.raises(ValueError, match="notes.txt"):
        destroy(out_dir, timedelta(0), policy="0d", log=log, cache_dir=boundary)
    # The refusal is pre-flight: the valid entries beside the stray file
    # survive, and so does everything in the out dir.
    assert (boundary / "extract" / ENTRY_NAME).exists()
    assert stray.exists()
    assert (out_dir / "resolved.csv").exists()


def test_cache_walk_never_reaches_files_outside_the_entry_shape(tmp_path: Path) -> None:
    out_dir = _make_out_dir(tmp_path)
    cache_root = _make_cache_root(out_dir / "stage_cache")
    strays = [
        cache_root / "README.md",  # a file directly at the cache root
        cache_root / "normalize" / "notes.txt",  # wrong filename shape
        cache_root / "normalize" / ("cd" * 16 + ".txt"),  # hex stem, wrong suffix
        cache_root / "scores" / ("ef" * 16 + ".json"),  # unknown stage directory
        cache_root / "normalize" / "deep" / ("ab" * 16 + ".json"),  # nested too far
    ]
    for stray in strays:
        stray.parent.mkdir(parents=True, exist_ok=True)
        stray.write_text("stray", encoding="utf-8")
        _age(stray, 2 * DAY_SECONDS)

    log = ProvenanceLog(out_dir / PROVENANCE_FILENAME)
    summary = destroy(out_dir, timedelta(0), policy="0d", log=log)

    for stray in strays:
        assert stray.exists(), stray
    destroyed = {artifact.name for artifact in summary.destroyed}
    assert f"stage_cache/extract/{ENTRY_NAME}" in destroyed
    assert f"stage_cache/normalize/{ENTRY_NAME}" in destroyed
    ok, message = verify_log(out_dir / PROVENANCE_FILENAME)
    assert ok, message


def test_cli_dry_run_and_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out_dir = _make_out_dir(tmp_path)

    code = main(["destroy", "--out", str(out_dir), "--older-than", "0d", "--dry-run"])
    assert code == 0
    out = capsys.readouterr().out
    assert "would destroy" in out
    assert "nothing deleted, nothing logged" in out
    assert (out_dir / "resolved.csv").exists()

    assert main(["destroy", "--out", str(out_dir), "--older-than", "nonsense"]) == 2
    assert main(["destroy", "--out", str(tmp_path / "missing"), "--older-than", "0d"]) == 2
    capsys.readouterr()

    code = main(["destroy", "--out", str(out_dir), "--older-than", "0d"])
    assert code == 0
    out = capsys.readouterr().out
    assert "destroyed 2 artifact(s)" in out
    assert not (out_dir / "resolved.csv").exists()
    ok, message = verify_log(out_dir / PROVENANCE_FILENAME)
    assert ok, message
