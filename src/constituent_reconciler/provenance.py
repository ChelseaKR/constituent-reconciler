"""Append-only, tamper-evident provenance log.

Every write to a downstream system is recorded as one line in a JSONL log. Each
entry carries a BLAKE2b hash of the field values that were written and the hash
of the previous entry, so the log forms a chain: changing or removing any past
entry breaks every entry after it, and `verify_log` detects it. The log answers
"what was written, when, and under which consent" with evidence rather than
assertion.

A run may open with a ``run-start`` entry carrying the hash of that run's
reproducibility manifest (see ``manifest.py``). The entry chains like any
other, so the writes that follow it are bound to the recipe and inputs that
produced them; ``verify_log`` reports the manifest hashes it finds.

Time is supplied by a TimestampAuthority. The default is the local clock, which
is honest but only as trustworthy as the machine. For third-party
non-repudiation, ``Rfc3161Authority`` obtains each timestamp from an RFC 3161
trusted timestamp authority instead: it sends the entry's content hash to the
configured TSA, verifies the signed response covers that hash, records the
returned genTime as the entry's time, and keeps the TSA's token on the entry so
the proof can be re-checked later. A failed or rejected TSA call raises; the
log never silently falls back to the local clock.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

GENESIS_HASH = "0" * 64

# Action recorded when a run announces its reproducibility manifest. Entries
# with this action carry the manifest hash as their content hash and no
# record id, members, or consent.
RUN_START_ACTION = "run-start"

# Action recorded when a repair plan is written for a previously written
# cluster (``reconcile plan-split``). The entry's content hash is the plan
# file's own digest; the plan's raw field values never enter the log.
REPAIR_PLAN_ACTION = "repair-plan"


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


class TimestampError(RuntimeError):
    """A trusted-timestamp request failed. The entry is not written."""


# The subset of DER this module needs for RFC 3161: single-byte tags only,
# definite lengths only. Hand-rolled so the package takes no ASN.1 dependency.
_BOOLEAN = 0x01
_INTEGER = 0x02
_OCTET_STRING = 0x04
_NULL = 0x05
_OID = 0x06
_GENERALIZED_TIME = 0x18
_SEQUENCE = 0x30
_SET = 0x31
_CONTEXT_0 = 0xA0

_SHA256_OID = "2.16.840.1.101.3.4.2.1"
_ID_SIGNED_DATA_OID = "1.2.840.113549.1.7.2"
_ID_CT_TSTINFO_OID = "1.2.840.113549.1.9.16.1.4"


def _der(tag: int, content: bytes) -> bytes:
    length = len(content)
    if length < 0x80:
        return bytes((tag, length)) + content
    encoded = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes((tag, 0x80 | len(encoded))) + encoded + content


def _der_sequence(*parts: bytes) -> bytes:
    return _der(_SEQUENCE, b"".join(parts))


def _der_integer(value: int) -> bytes:
    if value < 0:
        raise ValueError("only non-negative integers are encoded here")
    body = value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")
    if body[0] & 0x80:
        body = b"\x00" + body
    return _der(_INTEGER, body)


def _der_boolean(value: bool) -> bytes:
    return _der(_BOOLEAN, b"\xff" if value else b"\x00")


def _oid_body(dotted: str) -> bytes:
    arcs = [int(part) for part in dotted.split(".")]
    body = bytearray([arcs[0] * 40 + arcs[1]])
    for arc in arcs[2:]:
        chunk = bytearray([arc & 0x7F])
        arc >>= 7
        while arc:
            chunk.insert(0, 0x80 | (arc & 0x7F))
            arc >>= 7
        body.extend(chunk)
    return bytes(body)


def _der_oid(dotted: str) -> bytes:
    return _der(_OID, _oid_body(dotted))


def _read_tlv(data: bytes, offset: int) -> tuple[int, bytes, int]:
    """Read one DER element. Returns (tag, content, offset past the element)."""

    if offset + 2 > len(data):
        raise TimestampError("truncated DER element in TSA response")
    tag = data[offset]
    if tag & 0x1F == 0x1F:
        raise TimestampError("unsupported multi-byte DER tag in TSA response")
    first = data[offset + 1]
    offset += 2
    if first < 0x80:
        length = first
    else:
        n_octets = first & 0x7F
        if n_octets == 0 or offset + n_octets > len(data):
            raise TimestampError("unsupported or truncated DER length in TSA response")
        length = int.from_bytes(data[offset : offset + n_octets], "big")
        offset += n_octets
    end = offset + length
    if end > len(data):
        raise TimestampError("truncated DER element in TSA response")
    return tag, data[offset:end], end


def _expect(data: bytes, offset: int, tag: int, what: str) -> tuple[bytes, int]:
    found, content, end = _read_tlv(data, offset)
    if found != tag:
        raise TimestampError(f"malformed TSA response: expected {what}")
    return content, end


def _message_imprint(digest: str) -> bytes:
    """MessageImprint over the entry's content hash: SHA-256, then the digest octets."""

    hashed = hashlib.sha256(digest.encode("utf-8")).digest()
    algorithm = _der_sequence(_der_oid(_SHA256_OID), _der(_NULL, b""))
    return _der_sequence(algorithm, _der(_OCTET_STRING, hashed))


def _timestamp_request(imprint: bytes, nonce: int) -> bytes:
    """DER TimeStampReq (RFC 3161 section 2.4.1): version 1, fresh nonce, certReq."""

    return _der_sequence(
        _der_integer(1),
        imprint,
        _der_integer(nonce),
        _der_boolean(True),
    )


def _tstinfo_octets(token: bytes) -> bytes:
    """Extract the DER TSTInfo from a timeStampToken (a CMS SignedData ContentInfo)."""

    content_info, _ = _expect(token, 0, _SEQUENCE, "ContentInfo")
    content_type, off = _expect(content_info, 0, _OID, "content type OID")
    if content_type != _oid_body(_ID_SIGNED_DATA_OID):
        raise TimestampError("timestamp token is not a CMS SignedData")
    wrapped, _ = _expect(content_info, off, _CONTEXT_0, "SignedData wrapper")
    signed_data, _ = _expect(wrapped, 0, _SEQUENCE, "SignedData")
    _, off = _expect(signed_data, 0, _INTEGER, "SignedData version")
    _, off = _expect(signed_data, off, _SET, "digest algorithms")
    encap, _ = _expect(signed_data, off, _SEQUENCE, "encapsulated content")
    e_content_type, off = _expect(encap, 0, _OID, "eContentType")
    if e_content_type != _oid_body(_ID_CT_TSTINFO_OID):
        raise TimestampError("timestamp token does not carry a TSTInfo")
    e_content, _ = _expect(encap, off, _CONTEXT_0, "eContent")
    tst_octets, _ = _expect(e_content, 0, _OCTET_STRING, "TSTInfo octets")
    return tst_octets


def _parse_gen_time(octets: bytes) -> str:
    """Convert an RFC 3161 genTime (GeneralizedTime, always UTC) to ISO-8601."""

    try:
        text = octets.decode("ascii")
    except UnicodeDecodeError as error:
        raise TimestampError("malformed genTime in TSA response") from error
    if not text.endswith("Z"):
        raise TimestampError(f"genTime must be expressed in UTC: {text!r}")
    body, _, fraction = text[:-1].partition(".")
    if len(body) != 14 or not body.isdigit() or (fraction and not fraction.isdigit()):
        raise TimestampError(f"malformed genTime in TSA response: {text!r}")
    try:
        moment = datetime.strptime(body, "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError as error:
        raise TimestampError(f"malformed genTime in TSA response: {text!r}") from error
    if fraction:
        moment = moment.replace(microsecond=int(fraction[:6].ljust(6, "0")))
    return moment.isoformat()


def _parse_timestamp_response(raw: bytes, *, imprint: bytes, nonce: int) -> tuple[str, bytes]:
    """Verify a DER TimeStampResp; return the ISO-8601 genTime and the raw token.

    Fail-closed on every check: the PKIStatus must be granted(0) or
    grantedWithMods(1), the returned messageImprint must byte-equal the one
    sent, and the nonce must round-trip. Anything else raises TimestampError.
    """

    response, _ = _expect(raw, 0, _SEQUENCE, "TimeStampResp")
    status_info, off = _expect(response, 0, _SEQUENCE, "PKIStatusInfo")
    status_octets, _ = _expect(status_info, 0, _INTEGER, "PKIStatus")
    status = int.from_bytes(status_octets, "big")
    if status not in (0, 1):
        raise TimestampError(f"TSA did not grant the timestamp (PKIStatus {status})")
    if off >= len(response):
        raise TimestampError("TSA granted the timestamp but returned no token")
    _, _, token_end = _read_tlv(response, off)
    token = response[off:token_end]

    info, _ = _expect(_tstinfo_octets(token), 0, _SEQUENCE, "TSTInfo")
    _, off = _expect(info, 0, _INTEGER, "TSTInfo version")
    _, off = _expect(info, off, _OID, "TSA policy id")
    imprint_tag, _, imprint_end = _read_tlv(info, off)
    if imprint_tag != _SEQUENCE:
        raise TimestampError("malformed TSA response: expected messageImprint")
    if info[off:imprint_end] != imprint:
        raise TimestampError("TSA response messageImprint does not match the digest sent")
    _, off = _expect(info, imprint_end, _INTEGER, "serial number")
    gen_time_octets, off = _expect(info, off, _GENERALIZED_TIME, "genTime")

    # After genTime come accuracy (SEQUENCE), ordering (BOOLEAN), nonce
    # (INTEGER), tsa ([0]), extensions ([1]); the nonce is the only INTEGER.
    returned_nonce: int | None = None
    while off < len(info):
        tag, content, off = _read_tlv(info, off)
        if tag == _INTEGER:
            returned_nonce = int.from_bytes(content, "big")
            break
    if returned_nonce != nonce:
        raise TimestampError("TSA response nonce does not match the request")

    return _parse_gen_time(gen_time_octets), token


class TsaTransport(Protocol):
    def post(self, url: str, *, headers: dict[str, str], body: bytes) -> tuple[int, bytes]: ...


class UrllibTsaTransport:
    """Default TSA transport on the standard library. Times out rather than hanging."""

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def post(self, url: str, *, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https":
            raise TimestampError(f"TSA URL must use https, got {parsed.scheme or 'no scheme'}")
        request = urllib.request.Request(  # noqa: S310 - scheme validated above.
            url, data=body, headers=headers, method="POST"
        )
        try:
            # nosemgrep: dynamic-urllib-use-detected (TSA URL scheme is restricted to HTTPS above)
            with urllib.request.urlopen(  # noqa: S310 - request URL scheme validated above.
                request, timeout=self.timeout
            ) as response:
                return int(response.status), response.read()
        except urllib.error.HTTPError as error:
            return int(error.code), error.read()
        except urllib.error.URLError as error:  # pragma: no cover - network failure
            raise TimestampError(f"could not reach the TSA at {url}: {error.reason}") from error


class Rfc3161Authority:
    """Stamps entries with a trusted timestamp from an RFC 3161 TSA.

    Each call builds a DER TimeStampReq over the entry's content hash, posts it
    to the TSA with an injectable transport, and verifies the response before
    trusting it. The verified genTime becomes the entry's time, and the TSA's
    signed token is kept (base64) in ``last_token`` so ProvenanceLog can record
    it beside the entry. Every failure raises TimestampError; there is no
    local-clock fallback.
    """

    name: str

    def __init__(self, url: str, transport: TsaTransport | None = None) -> None:
        self.url = url
        self.name = f"rfc3161:{url}"
        self.transport: TsaTransport = transport or UrllibTsaTransport()
        self.last_token: str | None = None

    def stamp(self, digest: str) -> str:
        self.last_token = None
        imprint = _message_imprint(digest)
        nonce = secrets.randbits(64)
        status, raw = self.transport.post(
            self.url,
            headers={"Content-Type": "application/timestamp-query"},
            body=_timestamp_request(imprint, nonce),
        )
        if status >= 400:
            raise TimestampError(f"TSA at {self.url} answered HTTP {status}")
        gen_time, token = _parse_timestamp_response(raw, imprint=imprint, nonce=nonce)
        self.last_token = base64.b64encode(token).decode("ascii")
        return gen_time


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
        field_sources: Mapping[str, str] | None = None,
        fill_policy: str = "",
    ) -> dict[str, object]:
        return self._append(
            action=action,
            record_id=record_id,
            members=members,
            consent=consent,
            digest=content_hash(payload),
            external_id=external_id,
            field_sources=field_sources,
            fill_policy=fill_policy,
        )

    def append_run_start(self, manifest_hash: str) -> dict[str, object]:
        """Record the manifest of the run whose write entries follow.

        The entry carries the manifest hash as its content hash; it concerns no
        record, so record_id is empty, members is empty, and consent is null.
        Every write appended after it chains to it, binding those writes to the
        recipe and inputs the manifest describes.
        """

        return self._append(
            action=RUN_START_ACTION,
            record_id="",
            members=(),
            consent=None,
            digest=manifest_hash,
            external_id=None,
            field_sources=None,
            fill_policy="",
        )

    def append_repair_plan(
        self,
        *,
        cluster_id: str,
        members: Sequence[str],
        plan_digest: str,
        external_id: str,
    ) -> dict[str, object]:
        """Record that a repair plan was written for one written cluster.

        The entry carries the plan file's digest as its content hash, the
        cluster and member ids, and the destination's external id, never the
        plan's field values (docs/adr/0012-connector-repair-capabilities.md).
        Consent is null: the entry records planning, not a disclosure, and
        planning discloses nothing.
        """

        return self._append(
            action=REPAIR_PLAN_ACTION,
            record_id=cluster_id,
            members=members,
            consent=None,
            digest=plan_digest,
            external_id=external_id,
            field_sources=None,
            fill_policy="",
        )

    def _append(
        self,
        *,
        action: str,
        record_id: str,
        members: Sequence[str],
        consent: bool | None,
        digest: str,
        external_id: str | None,
        field_sources: Mapping[str, str] | None,
        fill_policy: str,
    ) -> dict[str, object]:
        entry: dict[str, object] = {
            "seq": self._seq,
            "time": self.authority.stamp(digest),
            "authority": self.authority.name,
            "action": action,
            "record_id": record_id,
            "members": list(members),
            "consent": consent,
            "external_id": external_id,
            # Field-level lineage: canonical field name -> the member record id
            # that supplied the written value. Ids only, never field values.
            "field_sources": dict(field_sources or {}),
            "fill_policy": fill_policy,
            "content_hash": digest,
            "prev_hash": self._prev_hash,
        }
        # An authority may retain the TSA's signed token for the stamp it just
        # issued; record it so the proof travels with the entry. The entry hash
        # covers every key present, so verify_log needs no change.
        token = getattr(self.authority, "last_token", None)
        if isinstance(token, str) and token:
            entry["tsa_token"] = token
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
    entry's ``entry_hash``. When the log contains ``run-start`` entries, the
    message also states which manifest hash each chain segment belongs to.
    """

    if not path.exists():
        return False, "log does not exist"
    prev = GENESIS_HASH
    seq = 0
    run_manifests: list[str] = []
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
            if entry.get("action") == RUN_START_ACTION:
                run_manifests.append(
                    f"entries from seq {seq} under manifest {entry['content_hash']}"
                )
            prev = str(entry["entry_hash"])
            seq += 1
    message = f"intact: {seq} entries"
    if run_manifests:
        message += "; " + "; ".join(run_manifests)
    return True, message
