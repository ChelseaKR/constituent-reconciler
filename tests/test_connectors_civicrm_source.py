"""The read-only CiviCRM source: canonical rows, bounded paging, fail-closed errors.

Every test drives the real adapter through a queued fake transport, so the
request it builds is inspected rather than assumed: what it selects, that it
excludes trashed contacts, that it asks for a deterministic order, and what it
does when a page fails half way through a pull.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs

import pytest

from constituent_reconciler.connectors.base import (
    SNAPSHOT_CONSENT_COLUMN,
    SNAPSHOT_ID_COLUMN,
    UNMAPPED_CONSENT,
    ConnectorError,
    SourceConnector,
)
from constituent_reconciler.connectors.civicrm import CivicrmConfig
from constituent_reconciler.connectors.civicrm_source import API_VERSION, CivicrmSource


def _params(body: bytes) -> dict[str, Any]:
    decoded = parse_qs(body.decode("utf-8"))
    parsed: dict[str, Any] = json.loads(decoded["params"][0])
    return parsed


class _Transport:
    """Returns queued responses and records every request for inspection."""

    def __init__(self, responses: list[tuple[int, dict[str, object]]]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, str], bytes]] = []

    def post(self, url: str, *, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        self.calls.append((url, headers, body))
        if not self._responses:
            raise AssertionError("the source asked for more pages than the test queued")
        status, payload = self._responses.pop(0)
        return status, json.dumps(payload).encode("utf-8")


def _config() -> CivicrmConfig:
    return CivicrmConfig(endpoint="https://crm.example.org/civicrm/ajax/api4", api_key="k")


def _contact(contact_id: int, first: str, last: str, **extra: str) -> dict[str, object]:
    row: dict[str, object] = {
        "id": contact_id,
        "external_identifier": extra.pop("external_identifier", f"E{contact_id:03d}"),
        "first_name": first,
        "last_name": last,
        "birth_date": extra.pop("birth_date", ""),
        "email_primary.email": extra.pop("email", ""),
        "phone_primary.phone": extra.pop("phone", ""),
    }
    row.update(extra)
    return row


def _page(*contacts: dict[str, object]) -> tuple[int, dict[str, object]]:
    return 200, {"values": list(contacts)}


def test_a_source_satisfies_the_protocol_and_is_not_local() -> None:
    source = CivicrmSource(_config(), _Transport([]))
    assert isinstance(source, SourceConnector)
    # A read crosses the network, so a local-target pack refuses it.
    assert source.is_local is False
    assert source.api_version == API_VERSION


def test_a_pull_yields_canonical_rows_and_no_vendor_field() -> None:
    transport = _Transport(
        [
            _page(
                _contact(
                    1,
                    "Alice",
                    "Walker",
                    birth_date="1970-05-12",
                    email="alice@example.org",
                    phone="555-123-4567",
                )
            )
        ]
    )
    rows = list(CivicrmSource(_config(), transport, page_size=10).read_all())

    assert rows == [
        {
            SNAPSHOT_ID_COLUMN: "E001",
            SNAPSHOT_CONSENT_COLUMN: UNMAPPED_CONSENT,
            "first_name": "Alice",
            "last_name": "Walker",
            "dob": "1970-05-12",
            "email": "alice@example.org",
            "phone": "555-123-4567",
        }
    ]
    # The vendor's own names stay inside the adapter.
    assert "birth_date" not in rows[0]
    assert not [key for key in rows[0] if "." in key]


def test_every_row_carries_the_same_keys_even_when_the_crm_omits_values() -> None:
    """A stable header is what makes the snapshot a CSV anything can read."""
    transport = _Transport([_page(_contact(1, "Alice", "Walker"), _contact(2, "Wei", "Chen"))])
    rows = list(CivicrmSource(_config(), transport, page_size=10).read_all())
    assert [set(row) for row in rows] == [set(rows[0])] * 2
    assert rows[1]["email"] == ""


def test_consent_is_never_inferred_from_the_crm() -> None:
    """No vendor consent mapping ships, so every pulled row reads as withheld."""
    from datetime import date

    from constituent_reconciler.models import Consent

    transport = _Transport([_page(_contact(1, "Alice", "Walker"))])
    (row,) = list(CivicrmSource(_config(), transport, page_size=10).read_all())
    assert row[SNAPSHOT_CONSENT_COLUMN] == UNMAPPED_CONSENT
    reason = Consent(status=row[SNAPSHOT_CONSENT_COLUMN]).reason(as_of=date(2026, 9, 11))
    assert reason is not None, "an unmapped consent must never read as granted"


def test_a_contact_with_no_external_identifier_keeps_a_namespaced_id() -> None:
    transport = _Transport([_page(_contact(7, "Dana", "Okafor", external_identifier=""))])
    (row,) = list(CivicrmSource(_config(), transport, page_size=10).read_all())
    assert row[SNAPSHOT_ID_COLUMN] == "civicrm:7"


def test_the_query_excludes_trashed_contacts_and_fixes_the_order() -> None:
    transport = _Transport([_page(_contact(1, "Alice", "Walker"))])
    list(CivicrmSource(_config(), transport, page_size=10).read_all())

    params = _params(transport.calls[0][2])
    assert ["is_deleted", "=", False] in params["where"]
    # Without an explicit order, two pulls of an unchanged database may differ,
    # and the snapshot digest the manifest records would mean nothing.
    assert params["orderBy"] == {"id": "ASC"}
    assert "external_identifier" in params["select"]
    assert "email_primary.email" in params["select"]


def test_paging_walks_offsets_until_a_short_page() -> None:
    transport = _Transport(
        [
            _page(_contact(1, "A", "One"), _contact(2, "B", "Two")),
            _page(_contact(3, "C", "Three"), _contact(4, "D", "Four")),
            _page(_contact(5, "E", "Five")),
        ]
    )
    rows = list(CivicrmSource(_config(), transport, page_size=2).read_all())

    assert [row["first_name"] for row in rows] == ["A", "B", "C", "D", "E"]
    assert [_params(call[2])["offset"] for call in transport.calls] == [0, 2, 4]
    assert [_params(call[2])["limit"] for call in transport.calls] == [2, 2, 2]


def test_a_server_that_never_returns_a_short_page_is_refused() -> None:
    """Otherwise a pull against a misbehaving server never ends."""
    transport = _Transport([_page(_contact(1, "A", "One"))] * 4)
    source = CivicrmSource(_config(), transport, page_size=1, max_pages=3)
    with pytest.raises(ConnectorError, match="refusing to page further"):
        list(source.read_all())
    assert len(transport.calls) == 3


def test_an_http_error_part_way_through_aborts_the_pull() -> None:
    transport = _Transport(
        [_page(_contact(1, "A", "One"), _contact(2, "B", "Two")), (500, {"error": "boom"})]
    )
    source = CivicrmSource(_config(), transport, page_size=2)
    with pytest.raises(ConnectorError, match="failed at offset 2"):
        list(source.read_all())


def test_a_body_that_is_not_a_values_list_is_refused_rather_than_read_as_empty() -> None:
    transport = _Transport([(200, {"unexpected": "shape"})])
    with pytest.raises(ConnectorError, match="no values list"):
        list(CivicrmSource(_config(), transport).read_all())


def test_rate_limiting_is_retried_with_a_backoff_then_refused() -> None:
    waits: list[float] = []
    transport = _Transport([(429, {}), (429, {}), _page(_contact(1, "A", "One"))])
    source = CivicrmSource(_config(), transport, page_size=10, retry_wait_s=0.5, sleep=waits.append)
    rows = list(source.read_all())
    assert [row["first_name"] for row in rows] == ["A"]
    assert waits == [0.5, 1.0]

    waits.clear()
    always = _Transport([(429, {})] * 5)
    refusing = CivicrmSource(
        _config(),
        always,
        page_size=10,
        max_rate_limit_retries=2,
        retry_wait_s=0.5,
        sleep=waits.append,
    )
    with pytest.raises(ConnectorError, match="rate-limited 3 attempts"):
        list(refusing.read_all())
    assert len(always.calls) == 3
    assert waits == [0.5, 1.0]


def test_a_missing_api_key_is_refused_before_any_request() -> None:
    transport = _Transport([])
    source = CivicrmSource(CivicrmConfig(endpoint="https://crm.example.org", api_key=""), transport)
    with pytest.raises(ConnectorError, match="API key is not set"):
        list(source.read_all())
    assert transport.calls == []


def test_a_page_size_below_one_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="page_size must be at least 1"):
        CivicrmSource(_config(), _Transport([]), page_size=0)
