"""Generic webhook connector.

CiviCRM and Salesforce are named destinations with a fixed field schema and a
native upsert operation this tool can call. A generic webhook has neither: the
operator points it at an arbitrary HTTP endpoint (a Zapier or Make automation,
an org's own intake API, a notification service) that this project has never
seen and cannot assume anything about beyond "it accepts a JSON POST." So this
connector, unlike the other two, does not map canonical fields onto a vendor
schema; it sends them under their own canonical names (see
``constituent_reconciler.models.CANONICAL_FIELDS``) in a documented envelope,
and idempotency on the receiving end is the operator's responsibility, keyed on
the ``external_id`` every payload carries. The exact wire shape, with a worked
example, is documented in ``docs/connectors/webhook.md``.

Two things this connector does NOT have to reason about, by construction, the
same way every other connector in this package does not:

* **Consent.** ``pipeline.export`` runs the consent gate (``consent.py``)
  before any connector is built or touched. A golden record without granted
  consent, under a consent-requiring policy pack, never reaches
  ``write_all``. This connector adds no consent logic of its own; it inherits
  the gate the rest of the compliance-focused pipeline already enforces.
* **Non-local egress.** ``is_local = False`` marks this as a network target
  the same way CiviCRM and Salesforce are. Under a policy pack that requires
  local-only write targets (the ``dv`` pack), ``pipeline.build_connector``
  refuses to construct this connector at all, fail-closed, before any client
  data is touched. See ``tests/test_no_egress.py``.

What this connector adds beyond the other two, because it is the first
destination this project has not vetted or built a relationship with: an
optional HMAC-SHA256 request signature (``[output] signing_secret_env``) so a
receiver can verify a payload actually came from this run and was not altered
in transit. Signing is opt-in (unset by default) so a quick local test against
an unauthenticated receiver still works, but it is the documented recommended
setting for any real deployment, the same way an API key is required, not
optional, for CiviCRM and Salesforce.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from constituent_reconciler.connectors.base import ConnectorError, WriteResult
from constituent_reconciler.models import GoldenRecord

# Bumped independently of schema.py's CONNECTOR_INTERFACE_VERSION: this is the
# shape of the JSON body this connector sends, not the Connector protocol
# itself. A receiver should read this field to detect a future breaking change
# to the envelope.
WEBHOOK_PAYLOAD_SCHEMA_VERSION = 1

# The event name every payload carries. A single constant today; a future
# connector version may add other event types (e.g. a withheld-record notice)
# without changing this one's meaning.
WEBHOOK_EVENT = "constituent.resolved"

# Header carrying the HMAC-SHA256 signature, hex-encoded, prefixed the way
# GitHub and Stripe webhooks do so a receiver's existing verification code is
# easy to adapt: "sha256=<hex digest>".
SIGNATURE_HEADER = "X-Reconciler-Signature"


class Transport(Protocol):
    def post(self, url: str, *, headers: dict[str, str], body: bytes) -> tuple[int, bytes]: ...


class UrllibTransport:
    """Default transport using urllib. Times out rather than hanging."""

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def post(self, url: str, *, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        # url is the operator's own recipe.toml `[output].endpoint`, not attacker
        # input; S310 flags any urlopen call regardless of scheme provenance.
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310
        try:
            # nosemgrep: dynamic-urllib-use-detected (operator-configured url, see noqa above)
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return int(response.status), response.read()
        except urllib.error.HTTPError as error:
            return int(error.code), error.read()
        except urllib.error.URLError as error:  # pragma: no cover - network failure
            raise ConnectorError(
                f"could not reach webhook endpoint {url}: {error.reason}"
            ) from error


@dataclass(frozen=True)
class WebhookConfig:
    endpoint: str
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    auth_token: str = ""
    signing_secret: str = ""
    external_id_field: str = "external_identifier"


class WebhookConnector:
    name = "webhook"
    # A write goes to an arbitrary network endpoint, so the DV pack refuses this
    # target the same way it refuses CiviCRM and Salesforce: client PII must not
    # leave the machine.
    is_local = False

    def __init__(self, config: WebhookConfig, transport: Transport | None = None) -> None:
        scheme = urllib.parse.urlsplit(config.endpoint).scheme
        if scheme not in ("http", "https"):
            raise ConnectorError(
                "webhook endpoint must be an http(s) URL "
                f"(configure [output].endpoint in the recipe); got {config.endpoint!r}"
            )
        self.config = config
        self.transport: Transport = transport or UrllibTransport()

    def _headers(self, body: bytes) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.config.auth_token:
            scheme = f"{self.config.auth_scheme} " if self.config.auth_scheme else ""
            headers[self.config.auth_header] = f"{scheme}{self.config.auth_token}"
        if self.config.signing_secret:
            digest = hmac.new(
                self.config.signing_secret.encode("utf-8"), body, hashlib.sha256
            ).hexdigest()
            headers[SIGNATURE_HEADER] = f"sha256={digest}"
        return headers

    def _payload(self, record: GoldenRecord, fields: tuple[str, ...]) -> dict[str, str]:
        # Only the recipe's active, mapped fields with a non-empty value, the
        # same "no field this run did not resolve" rule every other connector
        # in this package follows.
        return {f: record.fields[f] for f in fields if record.fields.get(f)}

    def _body(self, record: GoldenRecord, fields: tuple[str, ...]) -> tuple[bytes, dict[str, str]]:
        field_values = self._payload(record, fields)
        envelope = {
            "schema_version": WEBHOOK_PAYLOAD_SCHEMA_VERSION,
            "event": WEBHOOK_EVENT,
            "external_id_field": self.config.external_id_field,
            "external_id": record.cluster_id,
            "fields": field_values,
        }
        return json.dumps(envelope, sort_keys=True).encode("utf-8"), field_values

    def write_all(
        self,
        records: Sequence[GoldenRecord],
        fields: tuple[str, ...],
        *,
        dry_run: bool,
    ) -> list[WriteResult]:
        results: list[WriteResult] = []
        for record in records:
            body, field_values = self._body(record, fields)
            if dry_run:
                results.append(
                    WriteResult(
                        record.cluster_id, "would-write", record.cluster_id, payload=field_values
                    )
                )
                continue
            status, raw = self.transport.post(
                self.config.endpoint, headers=self._headers(body), body=body
            )
            if status >= 400:
                detail = raw.decode(errors="replace")[:200]
                raise ConnectorError(
                    f"webhook POST failed ({status}) for {record.cluster_id}: {detail}"
                )
            # A generic receiver's success body is not this project's to define:
            # some return 204 with nothing, some echo an ack. Only the status
            # code determines success; the external id stays the cluster id
            # because there is no upsert response to read one back from.
            results.append(
                WriteResult(record.cluster_id, "written", record.cluster_id, payload=field_values)
            )
        return results
