"""Import-ready CRM export files.

The live CiviCRM and Salesforce connectors push records over the network. This
module is the offline-first counterpart: it writes a CSV whose columns are the
target CRM's import field names, so an organization loads it with the CRM's own
import tool (Salesforce Data Loader or the Import Wizard, CiviCRM "Import
Contacts") without this tool ever contacting the server. That makes the export
file the default path and the live API push the explicit opt-in, which is the
posture the project commits to: nothing leaves the machine unless a recipe asks
for a network connector by name.

The column schema comes from the same field maps the live connectors use
(``salesforce.FIELD_MAP`` and ``civicrm.IMPORT_FIELD_MAP``), so the file an org
imports and the payload the API would push describe the same mapping. Each record
also carries the external-id column, keyed on the cluster id, so a CRM upsert on
that column is idempotent the same way the live connectors' upsert is.

The destination is a local file, so ``is_local`` is True and the DV policy pack
permits it: an org under VAWA/FVPSA can produce a CRM-shaped import file on its
own machine, while the network push connectors stay refused.

Optionally, a confirmed household grouping (household.py; see
``CrmCsvConnector.set_household_column``) adds one more column: a shared id
for the records a human reviewer confirmed belong to the same household. The
column is present only when the caller supplies a household map, and a record
absent from that map (its household was never suggested, or was suggested but
not confirmed) gets an empty value in that column rather than being left out
of the map's own household -- no suggestion is ever treated as confirmed by
default.

  * CiviCRM's "Import Contacts" wizard maps a column to the built-in
    "Household Name" relationship field to create or match a Household contact
    and a "Household Member of" relationship for each row that shares a value;
    the household column here is written so an operator can point that mapping
    at it directly (CiviCRM User and Administrator Guide, Contacts > Importing
    Contacts > Relationships).
  * Salesforce/NPSP has no fixed header name for this: the household column is
    written so an operator maps it, during the import tool's own field
    mapping, to the org's chosen external-id field on the Household Account
    (NPSP's standard household-account model), so contacts sharing a value
    resolve to the same Account.

Neither claim is a promise that the target org's schema matches the default
NPSP or CiviCRM install; both CRMs are configurable, and this module does not
call either API, so the CSV is what the operator inspects and maps by hand.
"""

from __future__ import annotations

import csv
from collections.abc import Mapping, Sequence
from pathlib import Path

from constituent_reconciler.connectors.base import WriteResult
from constituent_reconciler.connectors.civicrm import IMPORT_FIELD_MAP as CIVICRM_IMPORT_MAP
from constituent_reconciler.connectors.salesforce import FIELD_MAP as SALESFORCE_IMPORT_MAP
from constituent_reconciler.models import GoldenRecord

__all__ = [
    "CIVICRM_IMPORT_MAP",
    "SALESFORCE_IMPORT_MAP",
    "DEFAULT_HOUSEHOLD_COLUMN",
    "CrmCsvConnector",
]

# Default header for the optional household column. A recipe does not choose
# this name today; it exists so the two shipped CRM exports use one column
# name a reader learns once.
DEFAULT_HOUSEHOLD_COLUMN = "household_external_id"


class CrmCsvConnector:
    """Write resolved records to a CSV mapped to a CRM's import schema.

    ``field_map`` maps a canonical field name to the CRM's import column header.
    ``external_id_column`` is the header under which the cluster id is written, so
    a later CRM-side upsert keys on it. Only the recipe's active fields that the
    map covers are emitted, so a run that does not map ``address`` produces no
    address column.
    """

    # A local file on the machine running the tool; permitted under the DV pack.
    is_local = True

    def __init__(
        self,
        name: str,
        path: Path,
        field_map: Mapping[str, str],
        *,
        external_id_column: str,
    ) -> None:
        self.name = name
        self.path = path
        self.field_map = field_map
        self.external_id_column = external_id_column
        self._household_map: Mapping[str, str] | None = None
        self._household_column = DEFAULT_HOUSEHOLD_COLUMN

    def set_household_column(
        self,
        household_map: Mapping[str, str],
        *,
        column: str = DEFAULT_HOUSEHOLD_COLUMN,
    ) -> None:
        """Add a household-id column, populated only for confirmed groupings.

        ``household_map`` is a cluster-id -> household-id mapping built from
        confirmed suggestions only (``household.confirmed_member_map``); a
        cluster id absent from it (no suggestion, or a suggestion a reviewer
        has not yet confirmed) gets an empty value in the column, never a
        guess. Calling this is optional and additive: a connector that never
        has this called writes exactly the file it wrote before this feature
        existed.
        """

        self._household_map = household_map
        self._household_column = column

    def _columns(self, fields: tuple[str, ...]) -> list[str]:
        # Active, mapped fields in canonical order, then the external-id
        # column, then the optional household column (only when set).
        columns = [self.field_map[f] for f in fields if f in self.field_map] + [
            self.external_id_column
        ]
        if self._household_map is not None:
            columns.append(self._household_column)
        return columns

    def _row(self, record: GoldenRecord, fields: tuple[str, ...]) -> dict[str, str]:
        row: dict[str, str] = {}
        for field_name in fields:
            column = self.field_map.get(field_name)
            value = record.fields.get(field_name, "")
            if column and value:
                row[column] = value
        row[self.external_id_column] = record.cluster_id
        if self._household_map is not None:
            row[self._household_column] = self._household_map.get(record.cluster_id, "")
        return row

    def write_all(
        self,
        records: Sequence[GoldenRecord],
        fields: tuple[str, ...],
        *,
        dry_run: bool,
    ) -> list[WriteResult]:
        columns = self._columns(fields)
        results: list[WriteResult] = []
        if not dry_run:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=columns)
                writer.writeheader()
                for record in records:
                    writer.writerow(self._row(record, fields))
        for record in records:
            payload = self._row(record, fields)
            results.append(
                WriteResult(
                    record_id=record.cluster_id,
                    action="would-write" if dry_run else "written",
                    external_id=record.cluster_id,
                    payload=payload,
                )
            )
        return results
