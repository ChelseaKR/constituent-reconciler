from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from constituent_reconciler.connectors.base import ConnectorError
from constituent_reconciler.connectors.webhook import (
    SIGNATURE_HEADER,
    WEBHOOK_EVENT,
    WEBHOOK_PAYLOAD_SCHEMA_VERSION,
    WebhookConfig,
    WebhookConnector,
)
from constituent_reconciler.models import Consent, GoldenRecord
from tests.conftest import FakeWebhookTransport

FIELDS = ("first_name", "last_name", "dob", "email", "phone", "address")


def _golden(cluster_id: str, fields: dict[str, str], *, granted: bool = True) -> GoldenRecord:
    consent = Consent(status="granted" if granted else "revoked")
    return GoldenRecord(
        cluster_id=cluster_id,
        members=(cluster_id,),
        fields=fields,
        primary=cluster_id,
        consent=consent,
    )


def _connector(transport: FakeWebhookTransport, **config_overrides: object) -> WebhookConnector:
    config = WebhookConfig(endpoint="https://example.org/hooks/reconciler", **config_overrides)  # type: ignore[arg-type]
    return WebhookConnector(config, transport=transport)


def test_posts_one_request_per_record_with_the_documented_envelope() -> None:
    transport = FakeWebhookTransport([(200, b""), (200, b"")])
    connector = _connector(transport)
    records = [
        _golden("C0001", {"first_name": "Jane", "last_name": "Doe", "dob": "1990-01-01"}),
        _golden("C0002", {"first_name": "Bo", "last_name": "Nguyen"}),
    ]

    results = connector.write_all(records, FIELDS, dry_run=False)

    assert [r.action for r in results] == ["written", "written"]
    assert [r.external_id for r in results] == ["C0001", "C0002"]
    assert len(transport.calls) == 2

    url, headers, body = transport.calls[0]
    assert url == "https://example.org/hooks/reconciler"
    assert headers["Content-Type"] == "application/json"
    payload = json.loads(body)
    assert payload["schema_version"] == WEBHOOK_PAYLOAD_SCHEMA_VERSION
    assert payload["event"] == WEBHOOK_EVENT
    assert payload["external_id"] == "C0001"
    assert payload["external_id_field"] == "external_identifier"
    assert payload["fields"] == {
        "first_name": "Jane",
        "last_name": "Doe",
        "dob": "1990-01-01",
    }


def test_only_active_fields_with_a_value_are_sent() -> None:
    # A field the recipe did not map, or that resolved empty, must not appear:
    # a receiver should not infer anything from an absent key versus an empty one.
    transport = FakeWebhookTransport([(200, b"")])
    connector = _connector(transport)
    record = _golden("C0001", {"first_name": "Jane", "last_name": "", "email": "jane@example.org"})

    connector.write_all([record], FIELDS, dry_run=False)

    body = transport.calls[0][2]
    fields = json.loads(body)["fields"]
    assert fields == {"first_name": "Jane", "email": "jane@example.org"}
    assert "last_name" not in fields
    assert "dob" not in fields


def test_dry_run_makes_no_network_calls() -> None:
    transport = FakeWebhookTransport([])
    connector = _connector(transport)
    record = _golden("C0001", {"first_name": "Jane", "last_name": "Doe"})

    results = connector.write_all([record], FIELDS, dry_run=True)

    assert results[0].action == "would-write"
    assert results[0].payload == {"first_name": "Jane", "last_name": "Doe"}
    assert transport.calls == []


def test_bearer_token_is_sent_when_configured() -> None:
    transport = FakeWebhookTransport([(200, b"")])
    connector = _connector(transport, auth_token="secret-token")
    record = _golden("C0001", {"first_name": "Jane", "last_name": "Doe"})

    connector.write_all([record], FIELDS, dry_run=False)

    headers = transport.calls[0][1]
    assert headers["Authorization"] == "Bearer secret-token"


def test_no_authorization_header_when_no_token_configured() -> None:
    transport = FakeWebhookTransport([(200, b"")])
    connector = _connector(transport)
    record = _golden("C0001", {"first_name": "Jane", "last_name": "Doe"})

    connector.write_all([record], FIELDS, dry_run=False)

    assert "Authorization" not in transport.calls[0][1]


def test_signing_secret_adds_a_verifiable_hmac_signature() -> None:
    transport = FakeWebhookTransport([(200, b"")])
    connector = _connector(transport, signing_secret="shh")
    record = _golden("C0001", {"first_name": "Jane", "last_name": "Doe"})

    connector.write_all([record], FIELDS, dry_run=False)

    _, headers, body = transport.calls[0]
    algo, _, digest = headers[SIGNATURE_HEADER].partition("=")
    assert algo == "sha256"
    expected = hmac.new(b"shh", body, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(expected, digest)


def test_no_signature_header_when_no_signing_secret_configured() -> None:
    transport = FakeWebhookTransport([(200, b"")])
    connector = _connector(transport)
    record = _golden("C0001", {"first_name": "Jane", "last_name": "Doe"})

    connector.write_all([record], FIELDS, dry_run=False)

    assert SIGNATURE_HEADER not in transport.calls[0][1]


def test_error_status_raises_and_stops_further_writes() -> None:
    transport = FakeWebhookTransport([(500, b"internal error")])
    connector = _connector(transport)
    records = [
        _golden("C0001", {"first_name": "Jane", "last_name": "Doe"}),
        _golden("C0002", {"first_name": "Bo", "last_name": "Nguyen"}),
    ]

    with pytest.raises(ConnectorError, match="500"):
        connector.write_all(records, FIELDS, dry_run=False)

    # The failing call happened; the second record was never attempted.
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "bad_endpoint", ["", "not-a-url", "ftp://example.org/hook", "file:///etc/passwd"]
)
def test_malformed_endpoint_is_rejected_before_any_network_call(bad_endpoint: str) -> None:
    with pytest.raises(ConnectorError, match="http"):
        WebhookConnector(WebhookConfig(endpoint=bad_endpoint), transport=FakeWebhookTransport([]))


def test_webhook_target_is_not_local() -> None:
    # The DV pack relies on this: a network target must report is_local False.
    assert WebhookConnector.is_local is False
