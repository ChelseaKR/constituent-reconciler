from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs

import pytest

from constituent_reconciler.connectors.base import ConnectorError
from constituent_reconciler.connectors.civicrm import CivicrmConfig, CivicrmConnector
from constituent_reconciler.models import Consent, GoldenRecord

FIELDS = ("first_name", "last_name", "dob", "email", "phone")


def _params(body: bytes) -> dict[str, Any]:
    decoded: dict[str, Any] = json.loads(parse_qs(body.decode("utf-8"))["params"][0])
    return decoded


class _FakeTransport:
    """Returns queued responses and records every request for inspection."""

    def __init__(self, responses: list[tuple[int, dict[str, object]]]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, dict[str, str], bytes]] = []

    def post(self, url: str, *, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        self.calls.append((url, headers, body))
        status, payload = self._responses.pop(0)
        return status, json.dumps(payload).encode("utf-8")

    def entity_calls(self) -> list[tuple[str, str, dict[str, Any]]]:
        """Every recorded request as (entity, action, params), in call order."""
        parsed: list[tuple[str, str, dict[str, Any]]] = []
        for url, _, body in self.calls:
            entity, action = url.rsplit("/", 2)[1:]
            parsed.append((entity, action, _params(body)))
        return parsed


def _golden(cluster_id: str, fields: dict[str, str], consent: bool = True) -> GoldenRecord:
    return GoldenRecord(
        cluster_id=cluster_id,
        members=(cluster_id,),
        fields=fields,
        field_sources={name: cluster_id for name, value in fields.items() if value},
        primary=cluster_id,
        consent=Consent(status="granted") if consent else Consent(),
    )


def _connector(transport: _FakeTransport) -> CivicrmConnector:
    return CivicrmConnector(
        CivicrmConfig(endpoint="https://x.example/api4", api_key="key"), transport=transport
    )


def test_creates_a_contact_when_none_exists() -> None:
    transport = _FakeTransport([(200, {"values": []}), (200, {"values": [{"id": 42}]})])
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
    # No email or phone on the record, so nothing beyond the Contact calls.
    assert len(transport.calls) == 2


def test_create_path_writes_email_and_phone_through_dedicated_entities() -> None:
    transport = _FakeTransport(
        [
            (200, {"values": []}),  # Contact.get: no match
            (200, {"values": [{"id": 42}]}),  # Contact.create
            (200, {"values": []}),  # Email.get: no primary yet
            (200, {"values": [{"id": 5}]}),  # Email.create
            (200, {"values": []}),  # Phone.get: no primary yet
            (200, {"values": [{"id": 6}]}),  # Phone.create
        ]
    )
    connector = _connector(transport)
    record = _golden(
        "E1",
        {
            "first_name": "jane",
            "last_name": "doe",
            "email": "jane@x.org",
            "phone": "555-0100",
        },
    )

    results = connector.write_all([record], FIELDS, dry_run=False)

    assert results[0].action == "created"
    calls = transport.entity_calls()
    assert [(entity, action) for entity, action, _ in calls] == [
        ("Contact", "get"),
        ("Contact", "create"),
        ("Email", "get"),
        ("Email", "create"),
        ("Phone", "get"),
        ("Phone", "create"),
    ]
    # The Contact payload carries no email or phone, join syntax or otherwise.
    contact_values = calls[1][2]["values"]
    assert not any("email" in key or "phone" in key for key in contact_values)
    # The entity writes are keyed to the freshly created contact id.
    assert calls[2][2]["where"] == [["contact_id", "=", 42], ["is_primary", "=", 1]]
    assert calls[3][2]["values"] == {"contact_id": 42, "email": "jane@x.org", "is_primary": 1}
    assert calls[5][2]["values"] == {"contact_id": 42, "phone": "555-0100", "is_primary": 1}
    # The reported payload still shows everything the write landed.
    assert results[0].payload == {
        "first_name": "jane",
        "last_name": "doe",
        "email": "jane@x.org",
        "phone": "555-0100",
    }


def test_updates_a_contact_when_it_already_exists() -> None:
    transport = _FakeTransport(
        [
            (200, {"values": [{"id": 7}]}),  # Contact.get: match
            (200, {"values": [{"id": 7}]}),  # Contact.update
            (200, {"values": [{"id": 3}]}),  # Email.get: primary exists
            (200, {"values": [{"id": 3}]}),  # Email.update
        ]
    )
    connector = _connector(transport)
    record = _golden("E1", {"first_name": "jane", "last_name": "doe", "email": "jane@x.org"})

    results = connector.write_all([record], FIELDS, dry_run=False)

    assert results[0].action == "updated"
    assert results[0].external_id == "7"
    calls = transport.entity_calls()
    assert [(entity, action) for entity, action, _ in calls] == [
        ("Contact", "get"),
        ("Contact", "update"),
        ("Email", "get"),
        ("Email", "update"),
    ]
    update_params = calls[1][2]
    assert update_params["where"] == [["id", "=", 7]]
    assert "email_primary.email" not in update_params["values"]
    assert calls[2][2]["where"] == [["contact_id", "=", 7], ["is_primary", "=", 1]]
    assert calls[3][2] == {"where": [["id", "=", 3]], "values": {"email": "jane@x.org"}}


def test_update_path_creates_email_when_no_primary_exists() -> None:
    transport = _FakeTransport(
        [
            (200, {"values": [{"id": 7}]}),  # Contact.get: match
            (200, {"values": [{"id": 7}]}),  # Contact.update
            (200, {"values": []}),  # Email.get: no primary row
            (200, {"values": [{"id": 9}]}),  # Email.create
        ]
    )
    connector = _connector(transport)
    record = _golden("E1", {"first_name": "jane", "email": "jane@x.org"})

    connector.write_all([record], FIELDS, dry_run=False)

    calls = transport.entity_calls()
    assert (calls[3][0], calls[3][1]) == ("Email", "create")
    assert calls[3][2]["values"] == {"contact_id": 7, "email": "jane@x.org", "is_primary": 1}


def test_no_email_or_phone_calls_when_the_fields_are_empty() -> None:
    transport = _FakeTransport(
        [
            (200, {"values": [{"id": 7}]}),  # Contact.get: match
            (200, {"values": [{"id": 7}]}),  # Contact.update
        ]
    )
    connector = _connector(transport)
    record = _golden("E1", {"first_name": "jane", "last_name": "doe", "email": "", "phone": ""})

    connector.write_all([record], FIELDS, dry_run=False)

    entities = [entity for entity, _, _ in transport.entity_calls()]
    assert entities == ["Contact", "Contact"]


def test_dry_run_makes_no_network_calls_and_reports_the_full_payload() -> None:
    transport = _FakeTransport([])
    connector = _connector(transport)
    record = _golden("E1", {"first_name": "jane", "last_name": "doe", "email": "jane@x.org"})

    results = connector.write_all([record], FIELDS, dry_run=True)

    assert results[0].action == "would-write"
    assert transport.calls == []
    assert results[0].payload == {
        "first_name": "jane",
        "last_name": "doe",
        "email": "jane@x.org",
    }


def test_create_without_returned_id_raises_before_entity_writes() -> None:
    transport = _FakeTransport(
        [
            (200, {"values": []}),  # Contact.get: no match
            (200, {"values": [{}]}),  # Contact.create: no id in the response
        ]
    )
    connector = _connector(transport)
    record = _golden("E1", {"first_name": "jane", "email": "jane@x.org"})

    with pytest.raises(ConnectorError):
        connector.write_all([record], FIELDS, dry_run=False)
    # Fail-closed: the missing id stopped the write before any Email call.
    assert len(transport.calls) == 2


def test_missing_api_key_raises_before_any_call() -> None:
    transport = _FakeTransport([(200, {"values": []})])
    connector = CivicrmConnector(
        CivicrmConfig(endpoint="https://x.example/api4", api_key=""), transport=transport
    )
    record = _golden("E1", {"first_name": "jane", "last_name": "doe"})
    with pytest.raises(ConnectorError):
        connector.write_all([record], FIELDS, dry_run=False)


def test_api_error_status_raises() -> None:
    transport = _FakeTransport([(500, {"error_message": "boom"})])
    connector = _connector(transport)
    record = _golden("E1", {"first_name": "jane", "last_name": "doe"})
    with pytest.raises(ConnectorError):
        connector.write_all([record], FIELDS, dry_run=False)


# -- apply_repair / inspect_repair (ADR 0012 pilot) ---------------------------


def test_inspect_repair_reads_the_live_version_read_only() -> None:
    transport = _FakeTransport([(200, {"values": [{"id": 1, "version": "6.17.2"}]})])
    connector = _connector(transport)

    result = connector.inspect_repair()

    assert result == {"destination": "CiviCRM API v4", "destination_version": "6.17.2"}
    assert transport.entity_calls() == [("Domain", "get", {"select": ["version"], "limit": 1})]


def test_apply_repair_field_restore_writes_when_value_differs() -> None:
    transport = _FakeTransport(
        [
            (200, {"values": [{"id": 7}]}),  # find old_external_id -> contact 7
            (200, {"values": [{"birth_date": "1990-04-12"}]}),  # current dob
            (200, {"values": [{"id": 7}]}),  # Contact.update
        ]
    )
    connector = _connector(transport)
    plan = {
        "survivor": "E1",
        "old_external_id": "E1",
        "restore_fields": [
            {"field": "dob", "written_value": "1990-04-12", "restore_to": "1990-01-01"}
        ],
        "split_records": [{"record_id": "E1", "source": "x", "fields": {}}],
    }

    (result,) = connector.apply_repair(plan, fields=FIELDS, dry_run=False)

    assert result.action == "restored"
    assert result.before == "1990-04-12"
    assert result.after == "1990-01-01"
    calls = transport.entity_calls()
    assert [(e, a) for e, a, _ in calls] == [
        ("Contact", "get"),
        ("Contact", "get"),
        ("Contact", "update"),
    ]
    assert calls[2][2] == {"where": [["id", "=", 7]], "values": {"birth_date": "1990-01-01"}}


def test_apply_repair_field_restore_is_idempotent_when_already_correct() -> None:
    """A rerun that finds the value already restored writes nothing (no third call)."""
    transport = _FakeTransport(
        [
            (200, {"values": [{"id": 7}]}),  # find old_external_id
            (200, {"values": [{"birth_date": "1990-01-01"}]}),  # already the restore_to value
        ]
    )
    connector = _connector(transport)
    plan = {
        "survivor": "E1",
        "old_external_id": "E1",
        "restore_fields": [{"field": "dob", "restore_to": "1990-01-01"}],
        "split_records": [{"record_id": "E1", "source": "x", "fields": {}}],
    }

    (result,) = connector.apply_repair(plan, fields=FIELDS, dry_run=False)

    assert result.action == "unchanged"
    assert len(transport.calls) == 2  # no update call issued


def test_apply_repair_split_create_creates_a_new_contact() -> None:
    transport = _FakeTransport(
        [
            (200, {"values": [{"id": 7}]}),  # find old_external_id (survivor lookup)
            (200, {"values": []}),  # find N002: no match
            (200, {"values": [{"id": 42}]}),  # Contact.create
        ]
    )
    connector = _connector(transport)
    plan = {
        "survivor": "E1",
        "old_external_id": "E1",
        "restore_fields": [],
        "split_records": [
            {"record_id": "E1", "source": "x", "fields": {}},
            {
                "record_id": "N002",
                "source": "y",
                "fields": {"first_name": "Jon", "last_name": "Reyes", "dob": "1990-04-12"},
            },
        ],
    }

    (result,) = connector.apply_repair(plan, fields=FIELDS, dry_run=False)

    assert result.action == "created"
    assert result.external_id == "42"
    calls = transport.entity_calls()
    assert [(e, a) for e, a, _ in calls] == [
        ("Contact", "get"),
        ("Contact", "get"),
        ("Contact", "create"),
    ]
    create_values = calls[2][2]["values"]
    assert create_values == {
        "first_name": "Jon",
        "last_name": "Reyes",
        "birth_date": "1990-04-12",
        "external_identifier": "N002",
    }


def test_apply_repair_split_create_is_idempotent_when_contact_already_exists() -> None:
    """A rerun finds the split-off contact already created and makes no create call."""
    transport = _FakeTransport(
        [
            (200, {"values": [{"id": 7}]}),  # find old_external_id
            (200, {"values": [{"id": 55}]}),  # find N002: already exists
        ]
    )
    connector = _connector(transport)
    plan = {
        "survivor": "E1",
        "old_external_id": "E1",
        "restore_fields": [],
        "split_records": [
            {"record_id": "E1", "source": "x", "fields": {}},
            {"record_id": "N002", "source": "y", "fields": {"first_name": "Jon"}},
        ],
    }

    (result,) = connector.apply_repair(plan, fields=FIELDS, dry_run=False)

    assert result.action == "already-exists"
    assert result.external_id == "55"
    assert len(transport.calls) == 2  # no create call issued


def test_apply_repair_withholds_consent_before_any_network_call() -> None:
    transport = _FakeTransport([(200, {"values": [{"id": 7}]})])  # find old_external_id only
    connector = _connector(transport)
    plan = {
        "survivor": "E1",
        "old_external_id": "E1",
        "restore_fields": [],
        "split_records": [
            {"record_id": "E1", "source": "x", "fields": {}},
            {"record_id": "N002", "source": "y", "fields": {"first_name": "Jon"}},
        ],
    }

    (result,) = connector.apply_repair(
        plan, fields=FIELDS, dry_run=False, withhold_record_ids=frozenset({"N002"})
    )

    assert result.action == "withheld-consent"
    assert len(transport.calls) == 1  # only the survivor lookup; N002 was never looked up


def test_apply_repair_dry_run_makes_zero_network_calls() -> None:
    transport = _FakeTransport([])
    connector = _connector(transport)
    plan = {
        "survivor": "E1",
        "old_external_id": "E1",
        "restore_fields": [{"field": "dob", "restore_to": "1990-01-01"}],
        "split_records": [
            {"record_id": "E1", "source": "x", "fields": {}},
            {"record_id": "N002", "source": "y", "fields": {"first_name": "Jon"}},
        ],
    }

    results = connector.apply_repair(plan, fields=FIELDS, dry_run=True)

    assert [r.action for r in results] == ["would-restore", "would-create"]
    assert transport.calls == []
