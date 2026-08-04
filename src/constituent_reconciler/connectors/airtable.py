"""Airtable connector using the Web API's native batched upsert.

The operator provisions a table with fields named after the reconciler's
canonical fields (``first_name``, ``last_name``, and so on) plus the configured
external-id field. ``endpoint`` is the table endpoint:

``https://api.airtable.com/v0/<base-id>/<table-name-or-id>``

Airtable's update-multiple-records endpoint accepts at most ten records and can
upsert them by a declared merge field. The connector therefore batches records
in tens, uses the reconciler cluster id as that merge key, and expands the batch
response back into one :class:`WriteResult` per input record.

Consent and policy enforcement stay outside the adapter. ``is_local`` is false,
so the DV pack refuses this destination before a record reaches ``write_all``.
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

AIRTABLE_BATCH_LIMIT = 10


class Transport(Protocol):
    def patch(self, url: str, *, headers: dict[str, str], body: bytes) -> tuple[int, bytes]: ...


class UrllibTransport:
    """Default transport using urllib. Times out rather than hanging."""

    def __init__(self, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def patch(self, url: str, *, headers: dict[str, str], body: bytes) -> tuple[int, bytes]:
        request = urllib.request.Request(url, data=body, headers=headers, method="PATCH")  # noqa: S310
        try:
            # The URL is operator-configured and scheme-validated by
            # AirtableConnector before this transport is called.
            # nosemgrep: dynamic-urllib-use-detected (operator-configured url, see noqa above)
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                return int(response.status), response.read()
        except urllib.error.HTTPError as error:
            return int(error.code), error.read()
        except urllib.error.URLError as error:  # pragma: no cover - network failure
            raise ConnectorError(f"could not reach Airtable at {url}: {error.reason}") from error


@dataclass(frozen=True)
class AirtableConfig:
    endpoint: str
    access_token: str = ""
    external_id_field: str = "external_identifier"


class AirtableConnector:
    name = "airtable"
    is_local = False

    def __init__(self, config: AirtableConfig, transport: Transport | None = None) -> None:
        parsed = urllib.parse.urlsplit(config.endpoint)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ConnectorError(
                "Airtable endpoint must be an http(s) table URL "
                f"(configure [output].endpoint); got {config.endpoint!r}"
            )
        if not config.external_id_field.strip():
            raise ConnectorError("Airtable external_id_field must not be empty")
        self.config = config
        self.transport: Transport = transport or UrllibTransport()

    def _headers(self) -> dict[str, str]:
        if not self.config.access_token:
            raise ConnectorError(
                "Airtable personal access token is not set; configure the auth env var to write"
            )
        return {
            "Authorization": f"Bearer {self.config.access_token}",
            "Content-Type": "application/json",
        }

    def _fields(self, record: GoldenRecord, active_fields: tuple[str, ...]) -> dict[str, str]:
        payload = {
            field_name: record.fields[field_name]
            for field_name in active_fields
            if record.fields.get(field_name)
        }
        payload[self.config.external_id_field] = record.cluster_id
        return payload

    def _write_batch(
        self,
        records: Sequence[GoldenRecord],
        active_fields: tuple[str, ...],
    ) -> list[WriteResult]:
        request_fields = [self._fields(record, active_fields) for record in records]
        body = json.dumps(
            {
                "performUpsert": {"fieldsToMergeOn": [self.config.external_id_field]},
                "records": [{"fields": fields} for fields in request_fields],
            },
            sort_keys=True,
        ).encode("utf-8")
        status, raw = self.transport.patch(
            self.config.endpoint,
            headers=self._headers(),
            body=body,
        )
        if status >= 400:
            detail = raw.decode(errors="replace")[:200]
            cooldown = " (wait 30 seconds before retrying)" if status == 429 else ""
            raise ConnectorError(f"Airtable upsert failed ({status}){cooldown}: {detail}")
        try:
            parsed = json.loads(raw)
            response_records = parsed["records"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise ConnectorError("Airtable upsert returned a malformed response") from error
        if not isinstance(response_records, list) or len(response_records) != len(records):
            raise ConnectorError(
                "Airtable upsert response count did not match the submitted batch; "
                "stopping rather than misattributing results"
            )

        created = frozenset(str(value) for value in parsed.get("createdRecords", []))
        updated = frozenset(str(value) for value in parsed.get("updatedRecords", []))
        results: list[WriteResult] = []
        for record, response, fields in zip(records, response_records, request_fields, strict=True):
            if not isinstance(response, dict) or not response.get("id"):
                raise ConnectorError(
                    f"Airtable upsert returned no record id for {record.cluster_id}"
                )
            airtable_id = str(response["id"])
            if airtable_id in created:
                action = "created"
            elif airtable_id in updated:
                action = "updated"
            else:
                # Older/alternate successful response shapes may omit the two
                # classification arrays. The write still happened, but its
                # create-versus-update branch is not safe to infer.
                action = "written"
            reported = {
                key: value for key, value in fields.items() if key != self.config.external_id_field
            }
            results.append(
                WriteResult(
                    record_id=record.cluster_id,
                    action=action,
                    external_id=record.cluster_id,
                    detail=f"Airtable record {airtable_id}",
                    payload=reported,
                )
            )
        return results

    def write_all(
        self,
        records: Sequence[GoldenRecord],
        fields: tuple[str, ...],
        *,
        dry_run: bool,
    ) -> list[WriteResult]:
        if dry_run:
            return [
                WriteResult(
                    record_id=record.cluster_id,
                    action="would-write",
                    external_id=record.cluster_id,
                    payload={
                        field_name: record.fields[field_name]
                        for field_name in fields
                        if record.fields.get(field_name)
                    },
                )
                for record in records
            ]

        results: list[WriteResult] = []
        for start in range(0, len(records), AIRTABLE_BATCH_LIMIT):
            results.extend(self._write_batch(records[start : start + AIRTABLE_BATCH_LIMIT], fields))
        return results
