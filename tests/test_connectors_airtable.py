from __future__ import annotations

import json

import pytest

from constituent_reconciler.connectors.airtable import (
    AIRTABLE_BATCH_LIMIT,
    AirtableConfig,
    AirtableConnector,
)
from constituent_reconciler.connectors.base import ConnectorError
from constituent_reconciler.models import Consent, GoldenRecord
from tests.conftest import FakeAirtableTransport

FIELDS = ("first_name", "last_name", "dob", "email", "phone", "address")


def _record(number: int) -> GoldenRecord:
    cluster_id = f"C{number:04d}"
    return GoldenRecord(
        cluster_id=cluster_id,
        members=(cluster_id,),
        fields={
            "first_name": f"person-{number}",
            "last_name": "doe",
            "email": "" if number % 2 else f"p{number}@example.org",
        },
        primary=cluster_id,
        consent=Consent(status="granted"),
    )


def _response(records: list[GoldenRecord], *, created: bool = True) -> bytes:
    ids = [f"rec{i:04d}" for i, _ in enumerate(records, start=1)]
    payload: dict[str, object] = {
        "records": [{"id": record_id, "fields": {}} for record_id in ids],
        "createdRecords" if created else "updatedRecords": ids,
    }
    return json.dumps(payload).encode()


def _connector(transport: FakeAirtableTransport) -> AirtableConnector:
    return AirtableConnector(
        AirtableConfig(
            endpoint="https://api.airtable.com/v0/app123/Constituents",
            access_token="pat-test",
        ),
        transport=transport,
    )


def test_native_upsert_uses_external_id_and_canonical_fields() -> None:
    records = [_record(1), _record(2)]
    transport = FakeAirtableTransport([(200, _response(records))])

    results = _connector(transport).write_all(records, FIELDS, dry_run=False)

    assert [result.action for result in results] == ["created", "created"]
    assert [result.external_id for result in results] == ["C0001", "C0002"]
    url, headers, body = transport.calls[0]
    assert url == "https://api.airtable.com/v0/app123/Constituents"
    assert headers["Authorization"] == "Bearer pat-test"
    payload = json.loads(body)
    assert payload["performUpsert"] == {"fieldsToMergeOn": ["external_identifier"]}
    assert payload["records"] == [
        {
            "fields": {
                "external_identifier": "C0001",
                "first_name": "person-1",
                "last_name": "doe",
            }
        },
        {
            "fields": {
                "email": "p2@example.org",
                "external_identifier": "C0002",
                "first_name": "person-2",
                "last_name": "doe",
            }
        },
    ]


def test_batches_at_airtable_limit() -> None:
    records = [_record(i) for i in range(AIRTABLE_BATCH_LIMIT + 1)]
    transport = FakeAirtableTransport(
        [
            (200, _response(records[:AIRTABLE_BATCH_LIMIT])),
            (200, _response(records[AIRTABLE_BATCH_LIMIT:])),
        ]
    )

    results = _connector(transport).write_all(records, FIELDS, dry_run=False)

    assert len(results) == len(records)
    assert [len(json.loads(call[2])["records"]) for call in transport.calls] == [10, 1]


def test_dry_run_does_not_require_token_or_touch_transport() -> None:
    transport = FakeAirtableTransport([])
    connector = AirtableConnector(
        AirtableConfig(endpoint="https://api.airtable.com/v0/app123/Constituents"),
        transport=transport,
    )

    results = connector.write_all([_record(1)], FIELDS, dry_run=True)

    assert results[0].action == "would-write"
    assert transport.calls == []


def test_missing_token_fails_before_transport() -> None:
    transport = FakeAirtableTransport([])
    connector = AirtableConnector(
        AirtableConfig(endpoint="https://api.airtable.com/v0/app123/Constituents"),
        transport=transport,
    )
    with pytest.raises(ConnectorError, match="token"):
        connector.write_all([_record(1)], FIELDS, dry_run=False)
    assert transport.calls == []


def test_rate_limit_error_names_required_cooldown() -> None:
    transport = FakeAirtableTransport([(429, b'{"error":"RATE_LIMIT_REACHED"}')])
    with pytest.raises(ConnectorError, match="wait 30 seconds"):
        _connector(transport).write_all([_record(1)], FIELDS, dry_run=False)


def test_response_count_mismatch_fails_closed() -> None:
    transport = FakeAirtableTransport([(200, b'{"records":[]}')])
    with pytest.raises(ConnectorError, match="count"):
        _connector(transport).write_all([_record(1)], FIELDS, dry_run=False)


@pytest.mark.parametrize("endpoint", ["", "not-a-url", "file:///tmp/table"])
def test_bad_endpoint_is_rejected(endpoint: str) -> None:
    with pytest.raises(ConnectorError, match="http"):
        AirtableConnector(AirtableConfig(endpoint=endpoint), FakeAirtableTransport([]))


def test_airtable_is_non_local() -> None:
    assert AirtableConnector.is_local is False
