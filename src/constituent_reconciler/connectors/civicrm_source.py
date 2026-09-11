"""Read-only CiviCRM source: the existing side pulled from the live CRM (API v4).

The write connector's mirror. ``connectors/civicrm.py`` pushes resolved records
into CiviCRM; this pulls the contacts CiviCRM holds now, so a returning-client
batch is matched against the CRM's current state rather than against an export
somebody took last month, which is the most common way a run merges against
contacts that have since changed or been deleted.

It reuses that module's ``Transport``, ``CivicrmConfig`` and error type, so
request construction and pagination are testable without a live CiviCRM, and a
deployment configures one vendor in one shape.

Three properties are worth stating because a snapshot is meant to be replayed:

* **Deterministic order.** Every page asks for ``orderBy`` contact id ascending.
  Without an explicit order a server may page in whatever order it likes, and
  two pulls of an unchanged database could differ -- which would make the
  snapshot digest the run manifest records meaningless.
* **Trashed contacts are excluded**, with ``is_deleted = false`` in the query
  rather than a filter afterwards, so a contact the CRM considers deleted never
  enters a match.
* **Bounded paging.** A server that answers every page with a full page would
  otherwise be paged forever; after ``max_pages`` the pull refuses and says so.

Canonical rows only: CiviCRM's own field names (``birth_date``,
``email_primary.email``) are mapped here and never leave this module. Consent
is not mapped at all -- every row carries ``UNMAPPED_CONSENT``, which the
consent lifecycle withholds on, because deciding that a vendor privacy flag
means consent for a scope is a per-organization judgement with legal weight.
"""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Callable, Iterator
from typing import Any

from constituent_reconciler.connectors.base import (
    SNAPSHOT_CONSENT_COLUMN,
    SNAPSHOT_ID_COLUMN,
    UNMAPPED_CONSENT,
    ConnectorError,
)
from constituent_reconciler.connectors.civicrm import CivicrmConfig, Transport, UrllibTransport

#: CiviCRM API v4 field -> canonical field. The email and phone entries use
#: API v4's implicit join, so the primary email and phone arrive on the contact
#: row rather than costing a call each.
FIELD_MAP: dict[str, str] = {
    "first_name": "first_name",
    "last_name": "last_name",
    "birth_date": "dob",
    "email_primary.email": "email",
    "phone_primary.phone": "phone",
}

#: The interface these rows came through, recorded in the run manifest beside
#: the snapshot digest. It names the API contract, not the server's version:
#: reading that would cost another call and another failure mode.
API_VERSION = "civicrm-api4"

DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_PAGES = 1000
DEFAULT_RETRY_WAIT_S = 2.0
DEFAULT_MAX_RATE_LIMIT_RETRIES = 4
RATE_LIMITED = 429


class CivicrmSource:
    """Pull contacts from CiviCRM as canonical rows. See the module docstring."""

    name = "civicrm"
    # A read crosses the network, so a pack that requires local targets refuses
    # it, exactly as it refuses a network write.
    is_local = False
    api_version = API_VERSION

    def __init__(
        self,
        config: CivicrmConfig,
        transport: Transport | None = None,
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_rate_limit_retries: int = DEFAULT_MAX_RATE_LIMIT_RETRIES,
        retry_wait_s: float = DEFAULT_RETRY_WAIT_S,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        if page_size < 1:
            raise ValueError(f"page_size must be at least 1, not {page_size}")
        self.config = config
        self.transport: Transport = transport or UrllibTransport()
        self.page_size = page_size
        self.max_pages = max_pages
        self.max_rate_limit_retries = max_rate_limit_retries
        self.retry_wait_s = retry_wait_s
        if sleep is None:
            import time

            sleep = time.sleep
        self._sleep = sleep

    def _headers(self) -> dict[str, str]:
        if not self.config.api_key:
            raise ConnectorError(
                "CiviCRM API key is not set; configure the auth env var to read the existing side"
            )
        scheme = f"{self.config.auth_scheme} " if self.config.auth_scheme else ""
        return {
            self.config.auth_header: f"{scheme}{self.config.api_key}",
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
        }

    def _post(self, url: str, body: bytes) -> tuple[int, bytes]:
        """One request, retried on HTTP 429 with a fixed backoff.

        The transport returns a status and a body and no headers, so
        ``Retry-After`` cannot be read; the wait grows linearly instead, and
        after the last attempt the pull fails rather than hammering the server.
        """
        headers = self._headers()
        for attempt in range(self.max_rate_limit_retries + 1):
            status, raw = self.transport.post(url, headers=headers, body=body)
            if status != RATE_LIMITED:
                return status, raw
            if attempt == self.max_rate_limit_retries:
                break
            self._sleep(self.retry_wait_s * (attempt + 1))
        raise ConnectorError(
            f"CiviCRM rate-limited {self.max_rate_limit_retries + 1} attempts (HTTP "
            f"{RATE_LIMITED}); no Retry-After is visible through this transport, so the "
            "wait was a fixed backoff. Nothing was written."
        )

    def _page(self, offset: int) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "select": ["id", self.config.external_id_field, *FIELD_MAP],
            "where": [["is_deleted", "=", False]],
            "orderBy": {"id": "ASC"},
            "limit": self.page_size,
            "offset": offset,
        }
        url = f"{self.config.endpoint.rstrip('/')}/Contact/get"
        body = urllib.parse.urlencode({"params": json.dumps(params)}).encode("utf-8")
        status, raw = self._post(url, body)
        if status >= 400:
            detail = raw.decode(errors="replace")[:200]
            raise ConnectorError(
                f"CiviCRM Contact.get failed at offset {offset} ({status}): {detail}"
            )
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as error:
            raise ConnectorError(
                f"CiviCRM Contact.get returned no readable JSON at offset {offset}: {error}"
            ) from error
        values = parsed.get("values") if isinstance(parsed, dict) else None
        if not isinstance(values, list):
            raise ConnectorError(f"CiviCRM Contact.get returned no values list at offset {offset}")
        return [row for row in values if isinstance(row, dict)]

    def _row(self, contact: dict[str, Any]) -> dict[str, str]:
        """One canonical row. Missing values are empty strings, never absent keys."""
        external = str(contact.get(self.config.external_id_field) or "").strip()
        contact_id = str(contact.get("id") or "").strip()
        row = {
            # The external identifier is the key the write connector upserts on,
            # so a pulled record and a written one name the same person. A
            # contact that has never been written through this tool has none,
            # and its CiviCRM id is used, namespaced so the two cannot collide.
            SNAPSHOT_ID_COLUMN: external or f"civicrm:{contact_id}",
            SNAPSHOT_CONSENT_COLUMN: UNMAPPED_CONSENT,
        }
        for vendor_field, canonical in FIELD_MAP.items():
            row[canonical] = str(contact.get(vendor_field) or "").strip()
        return row

    def read_all(self) -> Iterator[dict[str, str]]:
        """Every non-deleted contact, in id order, as canonical rows.

        Raises ``ConnectorError`` on any API or transport failure, including
        one part-way through: a caller writing a snapshot must treat that as no
        snapshot at all rather than as a short one.
        """
        offset = 0
        for _ in range(self.max_pages):
            page = self._page(offset)
            for contact in page:
                yield self._row(contact)
            if len(page) < self.page_size:
                return
            offset += self.page_size
        raise ConnectorError(
            f"CiviCRM returned {self.max_pages} full pages of {self.page_size} and never a "
            "short one; refusing to page further rather than reading forever."
        )
