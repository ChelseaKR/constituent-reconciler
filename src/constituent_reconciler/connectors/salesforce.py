"""Salesforce connector (REST API, NPSP Contact).

Writes resolved records into Salesforce as Contacts using the REST upsert-by-
external-id endpoint, which is a native idempotent operation: a PATCH to
``/sobjects/Contact/<ExternalIdField>/<value>`` creates the contact if no record
carries that external id and updates it if one does. Keying on the cluster id as
the external id is what makes a re-run safe, the same property the CiviCRM
connector relies on.

This is the second connector, and it exists to prove the connector interface is
real: it implements the same ``Connector`` protocol, is isolated in its own
module, and goes through an injected ``Transport`` so request construction and
the create-versus-update branch are tested without a live Salesforce org or any
third-party HTTP dependency. The default transport uses the standard library.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from constituent_reconciler.connectors.base import ConnectorError, WriteResult
from constituent_reconciler.models import GoldenRecord

# Canonical field -> Salesforce NPSP Contact field. Address maps to the mailing
# street; a full structured address mapping is a later refinement. Public so the
# import-ready CSV exporter (connectors/crm_csv.py) maps to the same schema, which
# keeps the live API push and the offline export file in agreement.
FIELD_MAP = {
    "first_name": "FirstName",
    "last_name": "LastName",
    "dob": "Birthdate",
    "email": "Email",
    "phone": "Phone",
    "address": "MailingStreet",
}


class Transport(Protocol):
    def send(
        self, method: str, url: str, *, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes]: ...


class UrllibTransport:
    """Default transport using urllib. Times out rather than hanging."""

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def send(
        self, method: str, url: str, *, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        # url is the operator's own recipe.toml `[output].instance_url`, not attacker
        # input; S310 flags any urlopen call regardless of scheme provenance.
        request = urllib.request.Request(url, data=body, headers=headers, method=method)  # noqa: S310
        try:
            # nosemgrep: dynamic-urllib-use-detected (operator-configured url, see noqa above)
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return int(response.status), response.read()
        except urllib.error.HTTPError as error:
            return int(error.code), error.read()
        except urllib.error.URLError as error:  # pragma: no cover - network failure
            raise ConnectorError(f"could not reach Salesforce at {url}: {error.reason}") from error


@dataclass(frozen=True)
class SalesforceConfig:
    instance_url: str
    access_token: str = ""
    api_version: str = "v60.0"
    external_id_field: str = "External_Id__c"
    object_name: str = "Contact"


class SalesforceConnector:
    name = "salesforce"
    # A write goes over the network to a Salesforce org, so the DV pack refuses
    # this target: client PII must not leave the machine.
    is_local = False

    def __init__(self, config: SalesforceConfig, transport: Transport | None = None) -> None:
        self.config = config
        self.transport: Transport = transport or UrllibTransport()

    def _headers(self) -> dict[str, str]:
        if not self.config.access_token:
            raise ConnectorError(
                "Salesforce access token is not set; configure the auth env var to write"
            )
        return {
            "Authorization": f"Bearer {self.config.access_token}",
            "Content-Type": "application/json",
        }

    def _upsert_url(self, external_id: str) -> str:
        base = self.config.instance_url.rstrip("/")
        ext_field = self.config.external_id_field
        quoted = urllib.parse.quote(external_id, safe="")
        return (
            f"{base}/services/data/{self.config.api_version}"
            f"/sobjects/{self.config.object_name}/{ext_field}/{quoted}"
        )

    def _payload(self, record: GoldenRecord, fields: tuple[str, ...]) -> dict[str, str]:
        payload: dict[str, str] = {}
        for field_name in fields:
            target = FIELD_MAP.get(field_name)
            value = record.fields.get(field_name, "")
            if target and value:
                payload[target] = value
        return payload

    def _upsert(self, record: GoldenRecord, payload: dict[str, str]) -> WriteResult:
        body = json.dumps(payload).encode("utf-8")
        status, raw = self.transport.send(
            "PATCH", self._upsert_url(record.cluster_id), headers=self._headers(), body=body
        )
        if status >= 400:
            detail = raw.decode(errors="replace")[:200]
            raise ConnectorError(
                f"Salesforce upsert failed ({status}) for {record.cluster_id}: {detail}"
            )
        # 204 No Content is an update with no body. 200/201 carry a JSON body with
        # an id and a "created" flag (true on insert, false on update).
        if status == 204 or not raw.strip():
            return WriteResult(record.cluster_id, "updated", record.cluster_id, payload=payload)
        parsed = json.loads(raw)
        action = "created" if parsed.get("created") else "updated"
        external_id = str(parsed.get("id") or record.cluster_id)
        return WriteResult(record.cluster_id, action, external_id, payload=payload)

    def write_all(
        self,
        records: Sequence[GoldenRecord],
        fields: tuple[str, ...],
        *,
        dry_run: bool,
    ) -> list[WriteResult]:
        results: list[WriteResult] = []
        for record in records:
            payload = self._payload(record, fields)
            if dry_run:
                results.append(
                    WriteResult(
                        record.cluster_id, "would-write", record.cluster_id, payload=payload
                    )
                )
                continue
            results.append(self._upsert(record, payload))
        return results
