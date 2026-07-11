"""CiviCRM connector (API v4).

Writes resolved records into CiviCRM as Contacts. The write is an upsert keyed on
an external identifier (the cluster id): the connector first looks the contact up
by that key, then updates it if present or creates it if not, so re-running a
batch updates rows already in CiviCRM rather than minting duplicates. That
idempotency is the whole point of writing back through a stable key.

Email and phone are not Contact fields in CiviCRM's data model: they live on
dedicated Email and Phone entities keyed by ``contact_id``. Once the contact id
is known, the connector upserts the contact's primary Email and Phone rows
through those entities, updating the existing primary row when one exists and
creating one when none does. A record with no email or phone value makes no
Email or Phone call at all, so an empty value can never blank a row in CiviCRM.

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

# Canonical field -> CiviCRM API v4 Contact writable field. Only fields that
# live on the Contact entity itself belong here.
_CONTACT_FIELD_MAP = {
    "first_name": "first_name",
    "last_name": "last_name",
    "dob": "birth_date",
}

# Canonical field -> (API v4 entity, value field on that entity). These are
# written through the dedicated Email and Phone entities, not the Contact
# join-field shorthand, after the contact id is resolved.
_DETAIL_ENTITIES: dict[str, tuple[str, str]] = {
    "email": ("Email", "email"),
    "phone": ("Phone", "phone"),
}

# Canonical field -> CiviCRM "Import Contacts" CSV column. The import tool maps
# header names to its own fields, so email, phone, and street address are plain
# columns here even though the live API writes them through dedicated entities.
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

    def _contact_payload(self, record: GoldenRecord, fields: tuple[str, ...]) -> dict[str, str]:
        payload: dict[str, str] = {}
        for field_name in fields:
            target = _CONTACT_FIELD_MAP.get(field_name)
            value = record.fields.get(field_name, "")
            if target and value:
                payload[target] = value
        return payload

    def _detail_values(self, record: GoldenRecord, fields: tuple[str, ...]) -> dict[str, str]:
        """Canonical field -> value for the Email/Phone entities, empties dropped."""
        values: dict[str, str] = {}
        for field_name in fields:
            if field_name not in _DETAIL_ENTITIES:
                continue
            value = record.fields.get(field_name, "")
            if value:
                values[field_name] = value
        return values

    def _upsert_details(self, contact_id: Any, details: dict[str, str]) -> None:
        """Upsert the contact's primary Email/Phone rows through their entities.

        Fields absent from ``details`` were empty on the record and are skipped
        entirely: no call is made, so an empty value never blanks a stored row.
        """
        for field_name, value in details.items():
            entity, value_field = _DETAIL_ENTITIES[field_name]
            existing = self._call(
                entity,
                "get",
                {
                    "where": [["contact_id", "=", contact_id], ["is_primary", "=", 1]],
                    "select": ["id"],
                    "limit": 1,
                },
            )
            matches = existing.get("values", [])
            if matches:
                self._call(
                    entity,
                    "update",
                    {"where": [["id", "=", matches[0]["id"]]], "values": {value_field: value}},
                )
            else:
                self._call(
                    entity,
                    "create",
                    {"values": {"contact_id": contact_id, value_field: value, "is_primary": 1}},
                )

    def write_all(
        self,
        records: Sequence[GoldenRecord],
        fields: tuple[str, ...],
        *,
        dry_run: bool,
    ) -> list[WriteResult]:
        results: list[WriteResult] = []
        for record in records:
            contact_payload = self._contact_payload(record, fields)
            details = self._detail_values(record, fields)
            # The reported payload covers everything the write would land,
            # Contact fields and Email/Phone entity values alike, so dry-run
            # output and the provenance hash reflect the full write.
            reported = {**contact_payload, **details}
            external_id = record.cluster_id
            if dry_run:
                results.append(
                    WriteResult(record.cluster_id, "would-write", external_id, payload=reported)
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
                    {"where": [["id", "=", contact_id]], "values": contact_payload},
                )
                self._upsert_details(contact_id, details)
                results.append(
                    WriteResult(record.cluster_id, "updated", str(contact_id), payload=reported)
                )
            else:
                values = {**contact_payload, self.config.external_id_field: external_id}
                created = self._call("Contact", "create", {"values": values})
                created_id = created.get("values", [{}])[0].get("id")
                if created_id is None:
                    raise ConnectorError(
                        f"CiviCRM Contact.create returned no id for {record.cluster_id}; "
                        "stopping before any Email or Phone write"
                    )
                self._upsert_details(created_id, details)
                results.append(
                    WriteResult(record.cluster_id, "created", str(created_id), payload=reported)
                )
        return results
