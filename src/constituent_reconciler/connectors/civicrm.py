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

This is also the pilot for ADR 0012's repair capabilities: ``inspect_repair``
reads the live destination version, and ``apply_repair`` executes a
``constituent-reconcile plan-split`` plan's ``field-restore`` and ``split-create``
operations, both idempotent by construction. Neither is reachable except
through ``repair.apply_repair_plan``, which holds the second-reviewer gate;
this module never checks reviewer counts itself.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from constituent_reconciler.connectors.base import ConnectorError, WriteResult
from constituent_reconciler.connectors.repair import (
    OP_FIELD_RESTORE,
    OP_SPLIT_CREATE,
    RepairDeclaration,
    RepairOperation,
    RepairOperationResult,
    declare_repair,
)
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

    def _contact_payload(
        self, values: Mapping[str, str], fields: tuple[str, ...]
    ) -> dict[str, str]:
        payload: dict[str, str] = {}
        for field_name in fields:
            target = _CONTACT_FIELD_MAP.get(field_name)
            value = values.get(field_name, "")
            if target and value:
                payload[target] = value
        return payload

    def _detail_values(self, values: Mapping[str, str], fields: tuple[str, ...]) -> dict[str, str]:
        """Canonical field -> value for the Email/Phone entities, empties dropped."""
        details: dict[str, str] = {}
        for field_name in fields:
            if field_name not in _DETAIL_ENTITIES:
                continue
            value = values.get(field_name, "")
            if value:
                details[field_name] = value
        return details

    def _find_contact_id(self, external_id: str) -> Any | None:
        """The CiviCRM contact id keyed to ``external_id``, or ``None``.

        Used before every create, in ``write_all`` and in ``apply_repair``'s
        ``split-create``: a second create against a live ``external_identifier``
        unique constraint fails as a DB error rather than a clean "exists"
        response (observed against CiviCRM 6.17.2), so this lookup is what
        keeps a rerun idempotent instead of raising or duplicating.
        """
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
        return matches[0]["id"] if matches else None

    def _existing_contact_id(self, raw_contact_id: str) -> int | None:
        """CiviCRM's own numeric id, if ``raw_contact_id`` still names a contact.

        Used only by ``apply_repair``'s ``field-restore``, which addresses
        the survivor directly by the id ``write_all`` recorded as its
        ``WriteResult.external_id`` -- CiviCRM's own numeric primary key,
        not our ``external_identifier`` upsert column. A lookup by
        ``external_identifier`` would query the wrong thing here: the
        plan's ``old_external_id`` already *is* the destination-assigned
        id, not a value keyed under our own column (confirmed against a
        live cluster on 2026-08-21: ``write_all``'s provenance entry for a
        written cluster carries CiviCRM's contact id as ``external_id``,
        e.g. ``"9"``, never the ``external_identifier`` string
        ``plan_split`` also has, e.g. ``"existing:E003"``). Blank or
        non-numeric input is refused as absent rather than sent to the API;
        the JSON round trip (int id -> ``str()`` for ``WriteResult`` and the
        plan file -> back to int here) is why this takes a string and
        returns the int every other contact-id call site in this class
        expects.
        """
        cleaned = raw_contact_id.strip()
        if not cleaned.isdigit():
            return None
        contact_id = int(cleaned)
        existing = self._call(
            "Contact", "get", {"where": [["id", "=", contact_id]], "select": ["id"], "limit": 1}
        )
        return contact_id if existing.get("values") else None

    def _current_contact_value(self, contact_id: Any, field_name: str) -> str:
        """The live value of one canonical field, for a field-restore receipt.

        ``write_all`` never needs this: it only ever writes forward. Repair's
        field-restore needs the value *before* it writes, both to decide
        whether a write is needed at all (idempotency) and to put a true
        before/after pair in the receipt.
        """
        detail = _DETAIL_ENTITIES.get(field_name)
        if detail is not None:
            entity, value_field = detail
            existing = self._call(
                entity,
                "get",
                {
                    "where": [["contact_id", "=", contact_id], ["is_primary", "=", 1]],
                    "select": [value_field],
                    "limit": 1,
                },
            )
            matches = existing.get("values", [])
            value = matches[0].get(value_field) if matches else None
            return "" if value is None else str(value)
        target = _CONTACT_FIELD_MAP.get(field_name)
        if target is None:
            return ""
        existing = self._call(
            "Contact", "get", {"where": [["id", "=", contact_id]], "select": [target], "limit": 1}
        )
        matches = existing.get("values", [])
        value = matches[0].get(target) if matches else None
        return "" if value is None else str(value)

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
            contact_payload = self._contact_payload(record.fields, fields)
            details = self._detail_values(record.fields, fields)
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

            contact_id = self._find_contact_id(external_id)
            if contact_id is not None:
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
                # ``values`` can come back an empty list (not just present-but-idless),
                # so index defensively rather than assuming an element exists.
                created_rows = created.get("values") or [{}]
                created_id = created_rows[0].get("id")
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

    def inspect_repair(self) -> dict[str, str]:
        """Read-only: the live destination version, for the repair capability gate.

        ``repair.apply_repair_plan`` calls this before it will honor a
        declaration, so "verified against 6.17.2" can never be taken on an
        operator's word: the version is read from the instance being written
        to, at apply time. Nothing here mutates anything; this is the
        ``inspect_repair`` capability ADR 0012 describes as safe to call even
        against a destination with no declaration at all.
        """
        response = self._call("Domain", "get", {"select": ["version"], "limit": 1})
        rows = response.get("values", [])
        version = str(rows[0].get("version", "")) if rows else ""
        return {"destination": "CiviCRM API v4", "destination_version": version}

    def apply_repair(
        self,
        plan: Mapping[str, Any],
        *,
        fields: tuple[str, ...],
        dry_run: bool,
        withhold_record_ids: frozenset[str] = frozenset(),
    ) -> list[RepairOperationResult]:
        """Execute one repair plan's ``field-restore`` and ``split-create`` ops.

        Both operations are independently idempotent, which is what makes a
        rerun safe: ``field-restore`` reads the survivor's current value
        first and writes nothing when it already equals the plan's
        ``restore_to``, and ``split-create`` looks a member up by its own
        record id (its proposed new ``external_identifier``) before ever
        calling create, because a second create against a live
        ``external_identifier`` unique constraint fails as a DB error rather
        than a clean "already exists" response.

        ``withhold_record_ids`` names ``split-create`` members whose current
        consent the caller (``repair.apply_repair_plan``) found inactive;
        those are reported ``withheld-consent`` and never reach a network
        call, the same fail-closed rule the main write path applies before a
        record reaches any connector.

        ``dry_run`` makes zero network calls. The preview comes entirely from
        the plan's own bytes, so it needs no credentials and cannot touch the
        destination, matching ``write_all``'s dry-run contract. Only a
        non-dry-run call opens a connection, and by the time it runs the
        second-reviewer gate in ``repair.apply_repair_plan`` has already
        refused to reach this method at all without two distinct recorded
        approvals.
        """
        survivor = str(plan.get("survivor", ""))
        old_external_id = str(plan.get("old_external_id", ""))
        survivor_contact_id: int | None = None
        if not dry_run and old_external_id:
            survivor_contact_id = self._existing_contact_id(old_external_id)

        results: list[RepairOperationResult] = []
        for entry in plan.get("restore_fields") or []:
            results.append(
                self._apply_field_restore(
                    entry,
                    survivor=survivor,
                    old_external_id=old_external_id,
                    survivor_contact_id=survivor_contact_id,
                    dry_run=dry_run,
                )
            )
        for entry in plan.get("split_records") or []:
            record_id = str(entry.get("record_id", ""))
            if record_id == survivor:
                # The survivor is old_external_id's existing contact; nothing
                # to create for it.
                continue
            results.append(
                self._apply_split_create(
                    entry,
                    fields=fields,
                    dry_run=dry_run,
                    withheld=record_id in withhold_record_ids,
                )
            )
        return results

    def _apply_field_restore(
        self,
        entry: Mapping[str, Any],
        *,
        survivor: str,
        old_external_id: str,
        survivor_contact_id: int | None,
        dry_run: bool,
    ) -> RepairOperationResult:
        """One ``restore_fields`` entry: read-check, then write only if changed."""

        def result(action: str, **kwargs: Any) -> RepairOperationResult:
            return RepairOperationResult(
                OP_FIELD_RESTORE, survivor, old_external_id, action, field=field_name, **kwargs
            )

        field_name = str(entry.get("field", ""))
        restore_to = str(entry.get("restore_to", ""))
        is_detail_field = field_name in _DETAIL_ENTITIES
        if not is_detail_field and field_name not in _CONTACT_FIELD_MAP:
            return result("error", detail=f"field {field_name!r} has no CiviCRM mapping")
        if dry_run:
            return result("would-restore", after=restore_to)
        if survivor_contact_id is None:
            return result(
                "error", detail=f"no CiviCRM contact found for external id {old_external_id!r}"
            )
        before = self._current_contact_value(survivor_contact_id, field_name)
        if before == restore_to:
            return result("unchanged", before=before, after=restore_to)
        if is_detail_field:
            entity, value_field = _DETAIL_ENTITIES[field_name]
            existing = self._call(
                entity,
                "get",
                {
                    "where": [["contact_id", "=", survivor_contact_id], ["is_primary", "=", 1]],
                    "select": ["id"],
                    "limit": 1,
                },
            )
            matches = existing.get("values", [])
            if matches:
                self._call(
                    entity,
                    "update",
                    {
                        "where": [["id", "=", matches[0]["id"]]],
                        "values": {value_field: restore_to or None},
                    },
                )
            elif restore_to:
                self._call(
                    entity,
                    "create",
                    {
                        "values": {
                            "contact_id": survivor_contact_id,
                            value_field: restore_to,
                            "is_primary": 1,
                        }
                    },
                )
        else:
            target = _CONTACT_FIELD_MAP[field_name]
            self._call(
                "Contact",
                "update",
                {
                    "where": [["id", "=", survivor_contact_id]],
                    "values": {target: restore_to or None},
                },
            )
        return result("restored", before=before, after=restore_to)

    def _apply_split_create(
        self,
        entry: Mapping[str, Any],
        *,
        fields: tuple[str, ...],
        dry_run: bool,
        withheld: bool,
    ) -> RepairOperationResult:
        """One ``split_records`` entry: idempotent create for a member split away."""

        record_id = str(entry.get("record_id", ""))
        if dry_run:
            return RepairOperationResult(OP_SPLIT_CREATE, record_id, record_id, "would-create")
        if withheld:
            return RepairOperationResult(
                OP_SPLIT_CREATE,
                record_id,
                record_id,
                "withheld-consent",
                detail="current consent is not active for this member; no CiviCRM call was made",
            )
        existing_id = self._find_contact_id(record_id)
        if existing_id is not None:
            return RepairOperationResult(
                OP_SPLIT_CREATE,
                record_id,
                str(existing_id),
                "already-exists",
                detail="a contact with this external id already exists; no call was made",
            )
        values = entry.get("fields") or {}
        contact_payload = self._contact_payload(values, fields)
        details = self._detail_values(values, fields)
        create_values = {**contact_payload, self.config.external_id_field: record_id}
        created = self._call("Contact", "create", {"values": create_values})
        created_rows = created.get("values") or [{}]
        created_id = created_rows[0].get("id")
        if created_id is None:
            return RepairOperationResult(
                OP_SPLIT_CREATE,
                record_id,
                record_id,
                "error",
                detail="CiviCRM Contact.create returned no id",
            )
        self._upsert_details(created_id, details)
        return RepairOperationResult(OP_SPLIT_CREATE, record_id, str(created_id), "created")


# The pilot declaration ADR 0012 requires before any repair operation may run
# against a live CiviCRM: an exact verified version, the vendor documentation
# consulted, and the disposable instance the behavior was exercised against.
# Verified 2026-08-21 against a disposable local `civicrm/civicrm-docker`
# Standalone instance (image civicrm/civicrm:6.17.2-php8.3 + mariadb:10.11,
# no client PII, torn down after the pilot) using CiviCRM API v4 with the
# authx bearer-key REST transport this connector already speaks. Observed
# directly, not assumed: birth_date clears on both `null` and `""`; a plain
# `Contact.get` does not filter out a soft-deleted (trashed) contact, so
# `_find_contact_id` would see a trashed contact as "still exists" -- this
# pilot's operations never delete, so that edge is inert here but is not
# inert for a future delete-capable operation; a second `Contact.create`
# against a live `external_identifier` fails as a DB uniqueness error
# (HTTP 500), not a clean "already exists" response, which is why every
# create in `apply_repair` looks the external id up first; and an invalid
# API key is rejected with HTTP 401. Re-verify before adding a second
# version to this tuple or a new operation to this declaration.
declare_repair(
    RepairDeclaration(
        connector="civicrm",
        destination="CiviCRM API v4 (Standalone UF)",
        verified_versions=("6.17.2",),
        operations=(
            RepairOperation(name=OP_FIELD_RESTORE, destructive=True),
            RepairOperation(name=OP_SPLIT_CREATE, destructive=True),
        ),
        vendor_documentation=(
            "https://docs.civicrm.org/dev/en/latest/api/v4/ "
            "(Contact.create/get/update, Email, Phone; entity reference for "
            "external_identifier uniqueness and the is_deleted/useTrash "
            "delete semantics), read 2026-08-21"
        ),
        checked_on="2026-08-21",
        live_instance=(
            "disposable local civicrm/civicrm-docker Standalone instance, "
            "civicrm/civicrm:6.17.2-php8.3 + mariadb:10.11, no client PII, "
            "torn down after verification"
        ),
    )
)
