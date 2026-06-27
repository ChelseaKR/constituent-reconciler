"""Connector interface.

A connector takes resolved golden records and writes them to a destination: a
CSV file, a CiviCRM instance, more later. Connectors are isolated behind this
one interface so a destination's API churn stays contained to its own module,
the way an adapter pattern keeps each source independent. Consent is enforced
before records reach a connector, so a connector never has to reason about it.
"""

from __future__ import annotations

from collections.abc import Sequence
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
