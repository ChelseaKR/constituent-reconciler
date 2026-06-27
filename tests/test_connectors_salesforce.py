from __future__ import annotations

import json

import pytest

from constituent_reconciler.connectors.base import ConnectorError
from constituent_reconciler.connectors.salesforce import (
    SalesforceConfig,
    SalesforceConnector,
)
from constituent_reconciler.models import GoldenRecord

FIELDS = ("first_name", "last_name", "dob", "email", "phone")


class _FakeTransport:
    """Returns queued responses and records every request for inspection."""

    def __init__(self, responses: list[tuple[int, dict[str, object] | None]]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str, dict[str, str], bytes | None]] = []

    def send(
        self, method: str, url: str, *, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        self.calls.append((method, url, headers, body))
        status, payload = self._responses.pop(0)
        raw = b"" if payload is None else json.dumps(payload).encode("utf-8")
        return status, raw


def _golden(cluster_id: str, fields: dict[str, str], consent: bool = True) -> GoldenRecord:
    return GoldenRecord(
        cluster_id=cluster_id,
        members=(cluster_id,),
        fields=fields,
        primary=cluster_id,
        consent=consent,
    )


def _connector(transport: _FakeTransport) -> SalesforceConnector:
    return SalesforceConnector(
        SalesforceConfig(instance_url="https://x.my.salesforce.com", access_token="tok"),
        transport=transport,
    )


def test_upsert_creates_when_record_is_new() -> None:
    transport = _FakeTransport([(201, {"id": "003ABC", "success": True, "created": True})])
    connector = _connector(transport)
    record = _golden("E1", {"first_name": "jane", "last_name": "doe", "dob": "1990-01-01"})

    results = connector.write_all([record], FIELDS, dry_run=False)

    assert results[0].action == "created"
    assert results[0].external_id == "003ABC"
    method, url, _, body = transport.calls[0]
    assert method == "PATCH"
    # Upsert by external id is encoded in the URL, not the body.
    assert url.endswith("/sobjects/Contact/External_Id__c/E1")
    assert body is not None
    payload = json.loads(body)
    assert payload["FirstName"] == "jane"
    assert payload["Birthdate"] == "1990-01-01"
    assert "External_Id__c" not in payload


def test_upsert_updates_when_record_exists_with_200_body() -> None:
    transport = _FakeTransport([(200, {"id": "003XYZ", "success": True, "created": False})])
    connector = _connector(transport)
    record = _golden("E1", {"first_name": "jane", "last_name": "doe"})

    results = connector.write_all([record], FIELDS, dry_run=False)

    assert results[0].action == "updated"
    assert results[0].external_id == "003XYZ"


def test_upsert_update_with_204_no_content_falls_back_to_external_id() -> None:
    transport = _FakeTransport([(204, None)])
    connector = _connector(transport)
    record = _golden("E1", {"first_name": "jane", "last_name": "doe"})

    results = connector.write_all([record], FIELDS, dry_run=False)

    assert results[0].action == "updated"
    # 204 carries no body, so the stable external id is reported.
    assert results[0].external_id == "E1"


def test_dry_run_makes_no_network_calls() -> None:
    transport = _FakeTransport([])
    connector = _connector(transport)
    record = _golden("E1", {"first_name": "jane", "last_name": "doe"})

    results = connector.write_all([record], FIELDS, dry_run=True)

    assert results[0].action == "would-write"
    assert transport.calls == []


def test_missing_access_token_raises_before_any_call() -> None:
    transport = _FakeTransport([(201, {"id": "x", "created": True})])
    connector = SalesforceConnector(
        SalesforceConfig(instance_url="https://x.my.salesforce.com", access_token=""),
        transport=transport,
    )
    record = _golden("E1", {"first_name": "jane", "last_name": "doe"})
    with pytest.raises(ConnectorError):
        connector.write_all([record], FIELDS, dry_run=False)
    assert transport.calls == []


def test_api_error_status_raises() -> None:
    transport = _FakeTransport([(400, {"message": "bad"})])
    connector = _connector(transport)
    record = _golden("E1", {"first_name": "jane", "last_name": "doe"})
    with pytest.raises(ConnectorError):
        connector.write_all([record], FIELDS, dry_run=False)


def test_salesforce_target_is_not_local() -> None:
    # The DV pack relies on this: a network target must report is_local False.
    assert SalesforceConnector.is_local is False
