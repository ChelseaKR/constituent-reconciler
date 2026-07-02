from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from constituent_reconciler import provenance
from constituent_reconciler.provenance import (
    ProvenanceLog,
    Rfc3161Authority,
    TimestampError,
    content_hash,
    verify_log,
)


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


# --- RFC 3161 authority -------------------------------------------------------
#
# The fake TSA below answers timestamp queries offline, building its responses
# with the same DER encoder the authority uses for requests, so these tests
# exercise the real request construction and the real response verification
# without any network egress.


def _request_parts(body: bytes) -> tuple[bytes, int]:
    """Pull the messageImprint DER and the nonce out of a TimeStampReq."""

    _, request, _ = provenance._read_tlv(body, 0)
    _, _, off = provenance._read_tlv(request, 0)  # version
    _, _, imprint_end = provenance._read_tlv(request, off)
    imprint = request[off:imprint_end]
    _, nonce_octets, _ = provenance._read_tlv(request, imprint_end)
    return imprint, int.from_bytes(nonce_octets, "big")


def _timestamp_response(
    imprint: bytes,
    nonce: int,
    *,
    status: int = 0,
    gen_time: str = "20260101120000Z",
) -> bytes:
    """Build a DER TimeStampResp the way a granting TSA would."""

    der = provenance._der
    seq = provenance._der_sequence
    integer = provenance._der_integer
    oid = provenance._der_oid
    status_info = seq(integer(status))
    if status not in (0, 1):
        return seq(status_info)
    tst_info = seq(
        integer(1),
        oid("1.3.6.1.4.1.601.10.3.1"),  # an arbitrary TSA policy id
        imprint,
        integer(42),  # serialNumber
        der(0x18, gen_time.encode("ascii")),  # genTime
        integer(nonce),
    )
    encap = seq(
        oid("1.2.840.113549.1.9.16.1.4"),  # id-ct-TSTInfo
        der(0xA0, der(0x04, tst_info)),
    )
    signed_data = seq(integer(3), der(0x31, b""), encap)
    token = seq(oid("1.2.840.113549.1.7.2"), der(0xA0, signed_data))  # id-signedData
    return seq(status_info, token)


class _FakeTsa:
    """Offline TSA: echoes the request's imprint and nonce back, unless told to lie."""

    def __init__(
        self, *, status: int = 0, wrong_imprint: bool = False, wrong_nonce: bool = False
    ) -> None:
        self.status = status
        self.wrong_imprint = wrong_imprint
        self.wrong_nonce = wrong_nonce
        self.requests: list[bytes] = []

    def post(self, url: str, *, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        assert headers["Content-Type"] == "application/timestamp-query"
        self.requests.append(body)
        imprint, nonce = _request_parts(body)
        if self.wrong_imprint:
            imprint = provenance._message_imprint("some other digest")
        if self.wrong_nonce:
            nonce += 1
        return 200, _timestamp_response(imprint, nonce, status=self.status)


def test_rfc3161_entry_records_verified_tsa_time_and_token(tmp_path: Path) -> None:
    path = tmp_path / "p.jsonl"
    authority = Rfc3161Authority("https://tsa.example/tsr", transport=_FakeTsa())
    log = ProvenanceLog(path, authority)
    entry = log.append(
        action="created", record_id="E1", members=["E1"], consent=True, payload={"a": "1"}
    )
    assert str(entry["authority"]).startswith("rfc3161:")
    stamped = datetime.fromisoformat(str(entry["time"]))
    assert stamped == datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    # The TSA's signed token is kept with the entry so the proof can be re-checked.
    token = base64.b64decode(str(entry["tsa_token"]))
    assert token[0] == 0x30  # a DER SEQUENCE (the CMS ContentInfo)
    ok, message = verify_log(path)
    assert ok, message


def test_rfc3161_request_carries_the_entry_content_hash(tmp_path: Path) -> None:
    transport = _FakeTsa()
    authority = Rfc3161Authority("https://tsa.example/tsr", transport=transport)
    log = ProvenanceLog(tmp_path / "p.jsonl", authority)
    payload = {"a": "1"}
    log.append(action="created", record_id="E1", members=["E1"], consent=True, payload=payload)
    imprint, _ = _request_parts(transport.requests[0])
    assert imprint == provenance._message_imprint(content_hash(payload))


def test_rfc3161_non_granted_status_raises_and_writes_nothing(tmp_path: Path) -> None:
    path = tmp_path / "p.jsonl"
    authority = Rfc3161Authority("https://tsa.example/tsr", transport=_FakeTsa(status=2))
    log = ProvenanceLog(path, authority)
    with pytest.raises(TimestampError, match="did not grant"):
        log.append(
            action="created", record_id="E1", members=["E1"], consent=True, payload={"a": "1"}
        )
    assert not path.exists()


def test_rfc3161_imprint_mismatch_raises(tmp_path: Path) -> None:
    authority = Rfc3161Authority(
        "https://tsa.example/tsr", transport=_FakeTsa(wrong_imprint=True)
    )
    log = ProvenanceLog(tmp_path / "p.jsonl", authority)
    with pytest.raises(TimestampError, match="messageImprint"):
        log.append(
            action="created", record_id="E1", members=["E1"], consent=True, payload={"a": "1"}
        )


def test_rfc3161_nonce_mismatch_raises(tmp_path: Path) -> None:
    authority = Rfc3161Authority(
        "https://tsa.example/tsr", transport=_FakeTsa(wrong_nonce=True)
    )
    log = ProvenanceLog(tmp_path / "p.jsonl", authority)
    with pytest.raises(TimestampError, match="nonce"):
        log.append(
            action="created", record_id="E1", members=["E1"], consent=True, payload={"a": "1"}
        )


def test_rfc3161_http_error_raises_rather_than_falling_back(tmp_path: Path) -> None:
    class _DownTsa:
        def post(self, url: str, *, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
            return 503, b"service unavailable"

    path = tmp_path / "p.jsonl"
    authority = Rfc3161Authority("https://tsa.example/tsr", transport=_DownTsa())
    log = ProvenanceLog(path, authority)
    with pytest.raises(TimestampError, match="HTTP 503"):
        log.append(
            action="created", record_id="E1", members=["E1"], consent=True, payload={"a": "1"}
        )
    assert not path.exists()


def test_rfc3161_garbage_response_raises(tmp_path: Path) -> None:
    class _GarbageTsa:
        def post(self, url: str, *, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
            return 200, b"this is not DER"

    authority = Rfc3161Authority("https://tsa.example/tsr", transport=_GarbageTsa())
    log = ProvenanceLog(tmp_path / "p.jsonl", authority)
    with pytest.raises(TimestampError):
        log.append(
            action="created", record_id="E1", members=["E1"], consent=True, payload={"a": "1"}
        )
