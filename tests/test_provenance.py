from __future__ import annotations

import json
from pathlib import Path

from constituent_reconciler.provenance import ProvenanceLog, content_hash, verify_log


class _FixedClock:
    name = "fixed"

    def stamp(self, digest: str) -> str:
        return "2026-01-01T00:00:00+00:00"


def test_chain_appends_and_verifies(tmp_path: Path) -> None:
    path = tmp_path / "p.jsonl"
    log = ProvenanceLog(path, _FixedClock())
    log.append(action="created", record_id="E1", members=["E1"], consent=True, payload={"a": "1"})
    log.append(
        action="updated", record_id="E2", members=["E2", "N2"], consent=True, payload={"a": "2"}
    )
    ok, message = verify_log(path)
    assert ok, message


def test_tampering_with_a_past_entry_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "p.jsonl"
    log = ProvenanceLog(path, _FixedClock())
    log.append(action="created", record_id="E1", members=["E1"], consent=True, payload={"a": "1"})
    log.append(action="created", record_id="E2", members=["E2"], consent=True, payload={"a": "2"})

    lines = path.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[0])
    entry["consent"] = False  # flip a recorded fact without recomputing the hash
    lines[0] = json.dumps(entry, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, _ = verify_log(path)
    assert not ok


def test_chain_continues_across_separate_opens(tmp_path: Path) -> None:
    path = tmp_path / "p.jsonl"
    ProvenanceLog(path, _FixedClock()).append(
        action="created", record_id="E1", members=["E1"], consent=True, payload={}
    )
    ProvenanceLog(path, _FixedClock()).append(
        action="created", record_id="E2", members=["E2"], consent=True, payload={}
    )
    ok, message = verify_log(path)
    assert ok
    assert "2 entries" in message


def test_content_hash_is_field_order_independent() -> None:
    assert content_hash({"a": "1", "b": "2"}) == content_hash({"b": "2", "a": "1"})


def test_run_start_entry_chains_into_following_writes(tmp_path: Path) -> None:
    path = tmp_path / "p.jsonl"
    log = ProvenanceLog(path, _FixedClock())
    start = log.append_run_start("ab" * 32)
    write = log.append(
        action="created", record_id="E1", members=["E1"], consent=True, payload={"a": "1"}
    )
    assert start["action"] == "run-start"
    assert start["content_hash"] == "ab" * 32
    assert start["record_id"] == ""
    assert start["members"] == []
    assert start["consent"] is None
    assert write["prev_hash"] == start["entry_hash"]
    ok, message = verify_log(path)
    assert ok, message


def test_verify_reports_the_manifest_each_segment_belongs_to(tmp_path: Path) -> None:
    path = tmp_path / "p.jsonl"
    log = ProvenanceLog(path, _FixedClock())
    log.append_run_start("aa" * 32)
    log.append(action="created", record_id="E1", members=["E1"], consent=True, payload={"a": "1"})
    # A second run appends onto the same log with its own manifest.
    log = ProvenanceLog(path, _FixedClock())
    log.append_run_start("bb" * 32)
    log.append(action="updated", record_id="E1", members=["E1"], consent=True, payload={"a": "2"})
    ok, message = verify_log(path)
    assert ok, message
    assert f"entries from seq 0 under manifest {'aa' * 32}" in message
    assert f"entries from seq 2 under manifest {'bb' * 32}" in message


def test_verify_message_is_unchanged_without_run_start_entries(tmp_path: Path) -> None:
    path = tmp_path / "p.jsonl"
    ProvenanceLog(path, _FixedClock()).append(
        action="created", record_id="E1", members=["E1"], consent=True, payload={}
    )
    ok, message = verify_log(path)
    assert ok
    assert message == "intact: 1 entries"


def test_tampered_run_start_manifest_hash_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "p.jsonl"
    log = ProvenanceLog(path, _FixedClock())
    log.append_run_start("ab" * 32)
    log.append(action="created", record_id="E1", members=["E1"], consent=True, payload={"a": "1"})

    lines = path.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[0])
    entry["content_hash"] = "cd" * 32  # claim a different manifest produced these writes
    lines[0] = json.dumps(entry, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ok, _ = verify_log(path)
    assert not ok
