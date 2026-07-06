"""CiviCRM connector (API v4).

Writes resolved records into CiviCRM as Contacts. The write is an upsert keyed on
an external identifier (the cluster id): the connector first looks the contact up
by that key, then updates it if present or creates it if not, so re-running a
batch updates rows already in CiviCRM rather than minting duplicates. That
idempotency is the whole point of writing back through a stable key.

HTTP goes through an injected Transport, so the request construction, the upsert
logic, and consent behavior are all testable without a live CiviCRM. The default
transport uses the standard library; no third-party HTTP dependency.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from constituent_reconciler.connectors.base import ConnectorError, WriteResult
from constituent_reconciler.models import GoldenRecord

# Canonical field -> CiviCRM API v4 writable field. Email and phone use the
# join syntax API v4 accepts on Contact.create/update.
_FIELD_MAP = {
    "first_name": "first_name",
    "last_name": "last_name",
    "dob": "birth_date",
    "email": "email_primary.email",
    "phone": "phone_primary.phone",
}

# Canonical field -> CiviCRM "Import Contacts" CSV column. The import tool maps
# header names to its own fields, so the join syntax the live API uses is not a
# valid column header here: email, phone, and street address are plain columns.
# This is the schema the offline export file (connectors/crm_csv.py) maps to.
IMPORT_FIELD_MAP = {
    "first_name": "first_name",
    "last_name": "last_name",
    "dob": "birth_date",
    "email": "email",
    "phone": "phone",
    "address": "street_address",
}


class Transport(Protocol):
    def post(self, url: str, *, headers: dict[str, str], body: bytes) -> tuple[int, bytes]: ...


class UrllibTransport:
    """Default transport using urllib. Times out rather than hanging."""

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def post(self, url: str, *, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        # url is the operator's own recipe.toml `[output].url`, not attacker input;
        # S310 flags any urlopen call regardless of scheme provenance.
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")  # noqa: S310
        try:
            # nosemgrep: dynamic-urllib-use-detected (operator-configured url, see noqa above)
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return int(response.status), response.read()
        except urllib.error.HTTPError as error:
            return int(error.code), error.read()
        except urllib.error.URLError as error:  # pragma: no cover - network failure
            raise ConnectorError(f"could not reach CiviCRM at {url}: {error.reason}") from error


@dataclass(frozen=True)
class CivicrmConfig:
    endpoint: str
    api_key: str = ""
    auth_header: str = "Authorization"
    auth_scheme: str = "Bearer"
    external_id_field: str = "external_identifier"


class CivicrmConnector:
    name = "civicrm"
    # A write goes over the network to a CiviCRM server, so the DV pack refuses
    # this target: client PII must not leave the machine.
    is_local = False

    def __init__(self, config: CivicrmConfig, transport: Transport | None = None) -> None:
        self.config = config
        self.transport: Transport = transport or UrllibTransport()

    def _headers(self) -> dict[str, str]:
        if not self.config.api_key:
            raise ConnectorError("CiviCRM API key is not set; configure the auth env var to write")
        scheme = f"{self.config.auth_scheme} " if self.config.auth_scheme else ""
        return {
            self.config.auth_header: f"{scheme}{self.config.api_key}",
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Requested-With": "XMLHttpRequest",
        }

    def _call(self, entity: str, action: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.config.endpoint.rstrip('/')}/{entity}/{action}"
        body = urllib.parse.urlencode({"params": json.dumps(params)}).encode("utf-8")
        status, raw = self.transport.post(url, headers=self._headers(), body=body)
        if status >= 400:
            detail = raw.decode(errors="replace")[:200]
            raise ConnectorError(f"CiviCRM {entity}.{action} failed ({status}): {detail}")
        parsed: dict[str, Any] = json.loads(raw)
        return parsed

    def _payload(self, record: GoldenRecord, fields: tuple[str, ...]) -> dict[str, str]:
        payload: dict[str, str] = {}
        for field_name in fields:
            target = _FIELD_MAP.get(field_name)
            value = record.fields.get(field_name, "")
            if target and value:
                payload[target] = value
        return payload

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
            external_id = record.cluster_id
            if dry_run:
                results.append(
                    WriteResult(record.cluster_id, "would-write", external_id, payload=payload)
                )
                continue

            existing = self._call(
                "Contact",
                "get",
                {
                    "where": [[self.config.external_id_field, "=", external_id]],
                    "select": ["id"],
                    "limit": 1,
                },
            )
            matches = existing.get("values", [])
            if matches:
                contact_id = matches[0]["id"]
                self._call(
                    "Contact",
                    "update",
                    {"where": [["id", "=", contact_id]], "values": payload},
                )
                results.append(
                    WriteResult(record.cluster_id, "updated", str(contact_id), payload=payload)
                )
            else:
                values = {**payload, self.config.external_id_field: external_id}
                created = self._call("Contact", "create", {"values": values})
                created_id = created.get("values", [{}])[0].get("id")
                results.append(
                    WriteResult(
                        record.cluster_id,
                        "created",
                        str(created_id) if created_id is not None else None,
                        payload=payload,
                    )
                )
        return results
