"""The run orchestrator.

Reads the source CSVs, normalizes, scores candidate pairs with the matcher, bands
them, builds clusters from confident merges, reduces each cluster to a golden
record, and applies the consent gate. The result is returned as a value; writing
files is a separate, explicit step so a dry run can produce the same result
without touching disk.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path

from constituent_reconciler import consent, decisions, matching
from constituent_reconciler.config import Recipe
from constituent_reconciler.models import GoldenRecord, Pair, Record, RunResult
from constituent_reconciler.normalize import normalize_record


def read_records(
    path: Path,
    source: str,
    *,
    mapping: dict[str, str],
    id_column: str | None,
    consent_column: str | None,
    id_prefix: str,
) -> list[Record]:
    """Read one CSV into Records, applying the column mapping at read time."""

    records: list[Record] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for index, row in enumerate(reader, start=1):
            raw = {
                canonical: (row.get(column) or "").strip()
                for canonical, column in mapping.items()
            }
            if id_column and (row.get(id_column) or "").strip():
                unique_id = row[id_column].strip()
            else:
                unique_id = f"{id_prefix}{index:04d}"
            consent_status = ""
            if consent_column:
                consent_status = (row.get(consent_column) or "").strip()
            records.append(
                Record(
                    unique_id=unique_id,
                    source=source,
                    raw=raw,
                    consent_status=consent_status,
                )
            )
    return records


def _apply_overrides(
    pairs: list[Pair],
    force_auto: frozenset[frozenset[str]],
    force_drop: frozenset[frozenset[str]],
) -> list[Pair]:
    from constituent_reconciler.models import Band

    if not force_auto and not force_drop:
        return pairs
    adjusted: list[Pair] = []
    for pair in pairs:
        band = pair.band
        if pair.key() in force_auto:
            band = Band.AUTO
        elif pair.key() in force_drop:
            band = Band.DROP
        adjusted.append(Pair(pair.left, pair.right, pair.probability, band))
    return adjusted


def run(
    recipe: Recipe,
    *,
    force_auto: Iterable[frozenset[str]] = (),
    force_drop: Iterable[frozenset[str]] = (),
) -> RunResult:
    """Execute the pipeline and return the result.

    ``force_auto`` and ``force_drop`` carry human review decisions back in: an
    approved review pair becomes a confident merge, a rejected one is dropped.
    Scoring is deterministic, so re-running with decisions reproduces the rest of
    the result exactly.
    """

    raw_records: list[Record] = []
    if recipe.existing is not None:
        raw_records += read_records(
            recipe.existing,
            "existing",
            mapping=recipe.mapping,
            id_column=recipe.id_column,
            consent_column=recipe.consent_column,
            id_prefix="E",
        )
    raw_records += read_records(
        recipe.incoming,
        "incoming",
        mapping=recipe.mapping,
        id_column=recipe.id_column,
        consent_column=recipe.consent_column,
        id_prefix="N",
    )

    records = {r.unique_id: normalize_record(r, recipe.fields) for r in raw_records}

    scored = matching.score_pairs(
        records.values(), recipe.fields, prior=recipe.prior
    )
    pairs = decisions.band_pairs(
        scored,
        auto_threshold=recipe.auto_threshold,
        review_threshold=recipe.review_threshold,
    )
    pairs = _apply_overrides(pairs, frozenset(force_auto), frozenset(force_drop))

    clusters = decisions.build_clusters(records.keys(), pairs)
    golden = decisions.golden_records(clusters, records, recipe.fields)

    return RunResult(
        records=records,
        pairs=tuple(pairs),
        clusters=tuple(clusters),
        golden=tuple(golden),
    )


def write_outputs(result: RunResult, recipe: Recipe, out_dir: Path) -> dict[str, Path]:
    """Write resolved records, the review queue, and any withheld records.

    The consent gate runs here: when the policy requires consent, records without
    it are written to ``withheld.csv`` with no field values, only ids and a
    reason, so the withheld file itself leaks nothing.
    """

    out_dir.mkdir(parents=True, exist_ok=True)
    exportable, withheld = consent.partition_by_consent(
        result.golden, require_consent=recipe.require_consent
    )
    paths: dict[str, Path] = {}

    resolved_path = out_dir / "resolved.csv"
    with resolved_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["cluster_id", "primary", "members", "consent", *recipe.fields])
        for record in _sorted_golden(exportable):
            writer.writerow(
                [
                    record.cluster_id,
                    record.primary,
                    "|".join(record.members),
                    "granted" if record.consent else "none",
                    *(record.fields.get(f, "") for f in recipe.fields),
                ]
            )
    paths["resolved"] = resolved_path

    review_path = out_dir / "review_queue.csv"
    with review_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        header = ["left", "right", "probability", "left_source", "right_source"]
        for f in recipe.fields:
            header += [f"{f}_left", f"{f}_right"]
        writer.writerow(header)
        for pair in sorted(
            result.review_pairs, key=lambda p: (-p.probability, p.left, p.right)
        ):
            left = result.records[pair.left]
            right = result.records[pair.right]
            row = [
                pair.left,
                pair.right,
                f"{pair.probability:.4f}",
                left.source,
                right.source,
            ]
            for f in recipe.fields:
                row += [left.raw.get(f, ""), right.raw.get(f, "")]
            writer.writerow(row)
    paths["review_queue"] = review_path

    if withheld:
        withheld_path = out_dir / "withheld.csv"
        with withheld_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["cluster_id", "members", "reason"])
            for record in _sorted_golden(withheld):
                writer.writerow(
                    [record.cluster_id, "|".join(record.members), "no-consent"]
                )
        paths["withheld"] = withheld_path

    return paths


def _sorted_golden(golden: Iterable[GoldenRecord]) -> list[GoldenRecord]:
    return sorted(golden, key=lambda record: record.cluster_id)
