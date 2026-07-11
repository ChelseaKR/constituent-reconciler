from __future__ import annotations

import json

import pytest

from constituent_reconciler.connectors.base import ConnectorError
from constituent_reconciler.connectors.salesforce import (
    SalesforceConfig,
    SalesforceConnector,
)
from constituent_reconciler.models import Consent, GoldenRecord
from tests.conftest import FakeSalesforceTransport

FIELDS = ("first_name", "last_name", "dob", "email", "phone")


def _golden(cluster_id: str, fields: dict[str, str], consent: bool = True) -> GoldenRecord:
    return GoldenRecord(
        cluster_id=cluster_id,
        members=(cluster_id,),
        fields=fields,
        field_sources={name: cluster_id for name, value in fields.items() if value},
        primary=cluster_id,
        consent=Consent(status="granted") if consent else Consent(),
    )


def _connector(transport: FakeSalesforceTransport) -> SalesforceConnector:
    return SalesforceConnector(
        SalesforceConfig(instance_url="https://x.my.salesforce.com", access_token="tok"),
        transport=transport,
    )


def test_upsert_creates_when_record_is_new() -> None:
    transport = FakeSalesforceTransport([(201, {"id": "003ABC", "success": True, "created": True})])
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
    transport = FakeSalesforceTransport(
        [(200, {"id": "003XYZ", "success": True, "created": False})]
    )
    connector = _connector(transport)
    record = _golden("E1", {"first_name": "jane", "last_name": "doe"})

    results = connector.write_all([record], FIELDS, dry_run=False)

    assert results[0].action == "updated"
    assert results[0].external_id == "003XYZ"


def test_upsert_update_with_204_no_content_falls_back_to_external_id() -> None:
    transport = FakeSalesforceTransport([(204, None)])
    connector = _connector(transport)
    record = _golden("E1", {"first_name": "jane", "last_name": "doe"})

    results = connector.write_all([record], FIELDS, dry_run=False)

    assert results[0].action == "updated"
    # 204 carries no body, so the stable external id is reported.
    assert results[0].external_id == "E1"


def test_dry_run_makes_no_network_calls() -> None:
    transport = FakeSalesforceTransport([])
    connector = _connector(transport)
    record = _golden("E1", {"first_name": "jane", "last_name": "doe"})

    results = connector.write_all([record], FIELDS, dry_run=True)

    assert results[0].action == "would-write"
    assert transport.calls == []


def test_missing_access_token_raises_before_any_call() -> None:
    transport = FakeSalesforceTransport([(201, {"id": "x", "created": True})])
    connector = SalesforceConnector(
        SalesforceConfig(instance_url="https://x.my.salesforce.com", access_token=""),
        transport=transport,
    )
    record = _golden("E1", {"first_name": "jane", "last_name": "doe"})
    with pytest.raises(ConnectorError):
        connector.write_all([record], FIELDS, dry_run=False)
    assert transport.calls == []


def test_api_error_status_raises() -> None:
    transport = FakeSalesforceTransport([(400, {"message": "bad"})])
    connector = _connector(transport)
    record = _golden("E1", {"first_name": "jane", "last_name": "doe"})
    with pytest.raises(ConnectorError):
        connector.write_all([record], FIELDS, dry_run=False)


def test_salesforce_target_is_not_local() -> None:
    # The DV pack relies on this: a network target must report is_local False.
    assert SalesforceConnector.is_local is False
