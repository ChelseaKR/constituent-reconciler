"""Connector interfaces: writing resolved records out, and reading a side in.

A connector takes resolved golden records and writes them to a destination: a
CSV file, a CiviCRM instance, more later. Connectors are isolated behind this
one interface so a destination's API churn stays contained to its own module,
the way an adapter pattern keeps each source independent. Consent is enforced
before records reach a connector, so a connector never has to reason about it.

``SourceConnector`` is the read-only mirror: it pulls the records a system
already holds, so a run can match against what the CRM holds now rather than
against an export somebody took last month.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from constituent_reconciler.models import GoldenRecord

# Actions that represent a real write, and therefore get a provenance entry.
WRITE_ACTIONS: frozenset[str] = frozenset({"written", "created", "updated"})


class ConnectorError(RuntimeError):
    """A connector could not complete a write (bad config, transport, or API)."""


@dataclass(frozen=True)
class WriteResult:
    record_id: str
    action: str  # written | created | updated | would-write | skipped | error
    external_id: str | None = None
    detail: str = ""
    payload: dict[str, str] | None = None

    @property
    def is_write(self) -> bool:
        return self.action in WRITE_ACTIONS


#: The consent token a pull writes when nothing maps the vendor's own consent
#: fields onto this project's consent lifecycle. It is deliberately not a
#: recognized status: ``Consent.reason`` withholds on an unrecognized token, so
#: an unmapped consent can never read as granted. Deciding that a particular
#: CiviCRM privacy flag means consent for a particular scope is a judgement
#: with legal weight that differs per organization, so no default ships.
UNMAPPED_CONSENT = "unmapped"

#: The two non-field columns a pulled snapshot carries beside the canonical
#: fields. Vendor field names never appear in a snapshot or in a record.
SNAPSHOT_ID_COLUMN = "id"
SNAPSHOT_CONSENT_COLUMN = "consent"


@runtime_checkable
class SourceConnector(Protocol):
    """A read-only pull of one side of a run from a live system.

    Rows come back keyed by canonical field name, never by the vendor's, so
    nothing vendor-specific reaches a record. Every row carries the same keys,
    including ``SNAPSHOT_ID_COLUMN`` and ``SNAPSHOT_CONSENT_COLUMN``, so the
    snapshot written from them has a stable header.

    ``is_local`` is false when the read crosses the network. A policy pack that
    requires local targets refuses such a pull before a byte moves, which is
    the rule that already refuses a network write: reading a constituent file
    out of a hosted CRM is an egress in both directions.

    ``api_version`` names the vendor interface the rows came through. The run
    manifest records it beside the snapshot's digest, so a replay states what
    it replayed rather than implying the vendor's API has stood still.

    ``read_all`` raises ``ConnectorError`` on any transport or API failure,
    including one part-way through pagination. A caller writing a snapshot must
    treat that as "no snapshot", never as a short one.
    """

    name: str
    is_local: bool
    api_version: str

    def read_all(self) -> Iterator[dict[str, str]]: ...


@runtime_checkable
class Connector(Protocol):
    name: str
    # True when the destination stays on the machine running the tool (a local
    # file), false when a write leaves the machine (a network API). The DV pack
    # refuses a non-local target, so client PII never egresses.
    is_local: bool

    def write_all(
        self,
        records: Sequence[GoldenRecord],
        fields: tuple[str, ...],
        *,
        dry_run: bool,
    ) -> list[WriteResult]: ...
