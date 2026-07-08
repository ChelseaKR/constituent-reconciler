from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs

import pytest

from constituent_reconciler.connectors.base import ConnectorError
from constituent_reconciler.connectors.civicrm import CivicrmConfig, CivicrmConnector
from constituent_reconciler.models import GoldenRecord
from tests.conftest import FakeCivicrmTransport

FIELDS = ("first_name", "last_name", "dob", "email", "phone")


def _golden(cluster_id: str, fields: dict[str, str], consent: bool = True) -> GoldenRecord:
    return GoldenRecord(
        cluster_id=cluster_id,
        members=(cluster_id,),
        fields=fields,
        primary=cluster_id,
        consent=consent,
    )


def _params(body: bytes) -> dict[str, Any]:
    decoded: dict[str, Any] = json.loads(parse_qs(body.decode("utf-8"))["params"][0])
    return decoded


def _connector(transport: FakeCivicrmTransport) -> CivicrmConnector:
    return CivicrmConnector(
        CivicrmConfig(endpoint="https://x.example/api4", api_key="key"), transport=transport
    )


def test_creates_a_contact_when_none_exists() -> None:
    transport = FakeCivicrmTransport([(200, {"values": []}), (200, {"values": [{"id": 42}]})])
    connector = _connector(transport)
    record = _golden("E1", {"first_name": "jane", "last_name": "doe", "dob": "1990-01-01"})

    results = connector.write_all([record], FIELDS, dry_run=False)

    assert results[0].action == "created"
    assert results[0].external_id == "42"
    create_url, _, create_body = transport.calls[1]
    assert create_url.endswith("/Contact/create")
    values = _params(create_body)["values"]
    assert values["external_identifier"] == "E1"
    assert values["first_name"] == "jane"
    assert values["birth_date"] == "1990-01-01"


def test_updates_a_contact_when_it_already_exists() -> None:
    transport = FakeCivicrmTransport(
        [(200, {"values": [{"id": 7}]}), (200, {"values": [{"id": 7}]})]
    )
    connector = _connector(transport)
    record = _golden("E1", {"first_name": "jane", "last_name": "doe", "email": "jane@x.org"})

    results = connector.write_all([record], FIELDS, dry_run=False)

    assert results[0].action == "updated"
    assert results[0].external_id == "7"
    update_url, _, update_body = transport.calls[1]
    assert update_url.endswith("/Contact/update")
    params = _params(update_body)
    assert params["where"] == [["id", "=", 7]]
    assert params["values"]["email_primary.email"] == "jane@x.org"


def test_dry_run_makes_no_network_calls() -> None:
    transport = FakeCivicrmTransport([])
    connector = _connector(transport)
    record = _golden("E1", {"first_name": "jane", "last_name": "doe"})

    results = connector.write_all([record], FIELDS, dry_run=True)

    assert results[0].action == "would-write"
    assert transport.calls == []


def test_missing_api_key_raises_before_any_call() -> None:
    transport = FakeCivicrmTransport([(200, {"values": []})])
    connector = CivicrmConnector(
        CivicrmConfig(endpoint="https://x.example/api4", api_key=""), transport=transport
    )
    record = _golden("E1", {"first_name": "jane", "last_name": "doe"})
    with pytest.raises(ConnectorError):
        connector.write_all([record], FIELDS, dry_run=False)


def test_api_error_status_raises() -> None:
    transport = FakeCivicrmTransport([(500, {"error_message": "boom"})])
    connector = _connector(transport)
    record = _golden("E1", {"first_name": "jane", "last_name": "doe"})
    with pytest.raises(ConnectorError):
        connector.write_all([record], FIELDS, dry_run=False)
