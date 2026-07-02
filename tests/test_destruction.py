"""Tests for the retention-policy destruction executor.

The invariants: a dry run deletes and logs nothing; a real run deletes only
the listed PII artifacts older than the cutoff, never the provenance log or
newer files; each deletion appends a ``destroyed`` certificate binding the
pre-deletion SHA-256 into the chain; the chain still verifies afterward; and
no planted field value survives anywhere under the out directory.
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
    (out_dir / "resolved.csv").write_text(
        f"cluster_id,email\nC1,{SENTINEL}\n", encoding="utf-8"
    )
    (out_dir / "review_queue.csv").write_text(
        f"left,right,email_left\nA,B,{SENTINEL}\n", encoding="utf-8"
    )
    for name in ("resolved.csv", "review_queue.csv"):
        _age(out_dir / name, 2 * DAY_SECONDS)
    log = ProvenanceLog(out_dir / PROVENANCE_FILENAME)
    log.append(
        action="created", record_id="C1", members=["C1"], consent=True, payload={"a": "1"}
    )
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
    sizes = {
        name: (out_dir / name).stat().st_size for name in ("resolved.csv", "review_queue.csv")
    }

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


def test_no_planted_value_survives_under_out_dir(tmp_path: Path) -> None:
    out_dir = _make_out_dir(tmp_path)
    log = ProvenanceLog(out_dir / PROVENANCE_FILENAME)
    destroy(out_dir, timedelta(0), policy="0d", log=log, dry_run=False)

    remaining = [p for p in out_dir.rglob("*") if p.is_file()]
    assert remaining, "the provenance log must survive"
    for path in remaining:
        assert SENTINEL.encode("utf-8") not in path.read_bytes(), path


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
