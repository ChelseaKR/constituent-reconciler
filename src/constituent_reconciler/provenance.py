"""Append-only, tamper-evident provenance log.

Every write to a downstream system is recorded as one line in a JSONL log. Each
entry carries a BLAKE2b hash of the field values that were written and the hash
of the previous entry, so the log forms a chain: changing or removing any past
entry breaks every entry after it, and `verify_log` detects it. The log answers
"what was written, when, and under which consent" with evidence rather than
assertion.

Time is supplied by a TimestampAuthority. The default is the local clock, which
is honest but only as trustworthy as the machine. The interface exists so a
production deployment can plug in an RFC 3161 trusted-timestamp authority for
third-party non-repudiation; that integration is a later hardening step, not
something this module pretends to do already.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

GENESIS_HASH = "0" * 64


def content_hash(payload: dict[str, str]) -> str:
    """BLAKE2b-256 over a canonical JSON encoding of the written fields."""

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.blake2b(canonical, digest_size=32).hexdigest()


def _entry_hash(entry: dict[str, object]) -> str:
    body = {key: value for key, value in entry.items() if key != "entry_hash"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.blake2b(canonical, digest_size=32).hexdigest()


class TimestampAuthority(Protocol):
    name: str

    def stamp(self, digest: str) -> str: ...


class LocalClockAuthority:
    """Stamps with the machine's UTC clock. The default authority."""

    name = "local-clock"

    def stamp(self, digest: str) -> str:
        return datetime.now(UTC).isoformat()


class ProvenanceLog:
    """Appends entries to a JSONL file, chaining each to the last.

    The chain survives across runs: opening an existing log reads the last
    entry's hash and continues from it, so a second run's entries link onto the
    first run's.
    """

    def __init__(self, path: Path, authority: TimestampAuthority | None = None) -> None:
        self.path = path
        self.authority: TimestampAuthority = authority or LocalClockAuthority()
        self._prev_hash, self._seq = self._read_tail()

    def _read_tail(self) -> tuple[str, int]:
        if not self.path.exists():
            return GENESIS_HASH, 0
        last_hash = GENESIS_HASH
        count = 0
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                last_hash = str(entry["entry_hash"])
                count += 1
        return last_hash, count

    def append(
        self,
        *,
        action: str,
        record_id: str,
        members: Sequence[str],
        consent: bool,
        payload: dict[str, str],
        external_id: str | None = None,
    ) -> dict[str, object]:
        digest = content_hash(payload)
        entry: dict[str, object] = {
            "seq": self._seq,
            "time": self.authority.stamp(digest),
            "authority": self.authority.name,
            "action": action,
            "record_id": record_id,
            "members": list(members),
            "consent": consent,
            "external_id": external_id,
            "content_hash": digest,
            "prev_hash": self._prev_hash,
        }
        entry["entry_hash"] = _entry_hash(entry)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")
        self._prev_hash = str(entry["entry_hash"])
        self._seq += 1
        return entry


def verify_log(path: Path) -> tuple[bool, str]:
    """Recompute the chain and report whether it is intact.

    Returns ``(ok, message)``. A log is intact when every entry's recomputed
    hash matches what is stored and every entry's ``prev_hash`` equals the prior
    entry's ``entry_hash``.
    """

    if not path.exists():
        return False, "log does not exist"
    prev = GENESIS_HASH
    seq = 0
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if entry.get("prev_hash") != prev:
                return False, f"broken chain at line {line_number}: prev_hash mismatch"
            recomputed = _entry_hash(entry)
            if recomputed != entry.get("entry_hash"):
                return False, f"tampered entry at line {line_number}: hash mismatch"
            if entry.get("seq") != seq:
                return False, f"out-of-order entry at line {line_number}"
            prev = str(entry["entry_hash"])
            seq += 1
    return True, f"intact: {seq} entries"
