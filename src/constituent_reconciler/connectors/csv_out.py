"""CSV connector.

Writes resolved golden records to a single CSV file. This is the default
destination and the one used by the demo and the tests, because it needs no
running server. The column layout is stable so downstream tools can rely on it.
"""

from __future__ import annotations

import csv
from collections.abc import Sequence
from pathlib import Path

from constituent_reconciler.connectors.base import WriteResult
from constituent_reconciler.models import GoldenRecord


class CsvConnector:
    name = "csv"
    # A local file on the machine running the tool; permitted under the DV pack.
    is_local = True

    def __init__(self, path: Path) -> None:
        self.path = path

    def write_all(
        self,
        records: Sequence[GoldenRecord],
        fields: tuple[str, ...],
        *,
        dry_run: bool,
    ) -> list[WriteResult]:
        results: list[WriteResult] = []
        if not dry_run:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["cluster_id", "primary", "members", "consent", *fields])
                for record in records:
                    writer.writerow(
                        [
                            record.cluster_id,
                            record.primary,
                            "|".join(record.members),
                            "granted" if record.consent else "none",
                            *(record.fields.get(f, "") for f in fields),
                        ]
                    )
        for record in records:
            payload = {f: record.fields.get(f, "") for f in fields}
            results.append(
                WriteResult(
                    record_id=record.cluster_id,
                    action="would-write" if dry_run else "written",
                    external_id=record.cluster_id,
                    payload=payload,
                )
            )
        return results
