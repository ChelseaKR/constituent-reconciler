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
    "CrmCsvConnector",
]


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

    def _columns(self, fields: tuple[str, ...]) -> list[str]:
        # Active, mapped fields in canonical order, then the external-id column.
        return [self.field_map[f] for f in fields if f in self.field_map] + [
            self.external_id_column
        ]

    def _row(self, record: GoldenRecord, fields: tuple[str, ...]) -> dict[str, str]:
        row: dict[str, str] = {}
        for field_name in fields:
            column = self.field_map.get(field_name)
            value = record.fields.get(field_name, "")
            if column and value:
                row[column] = value
        row[self.external_id_column] = record.cluster_id
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
