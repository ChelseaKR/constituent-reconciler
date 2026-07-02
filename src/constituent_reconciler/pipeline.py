"""The run orchestrator.

Reads the source CSVs, normalizes, scores candidate pairs with the matcher, bands
them, builds clusters from confident merges, reduces each cluster to a golden
record, and applies the consent gate. The result is returned as a value; writing
files is a separate, explicit step so a dry run can produce the same result
without touching disk.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from constituent_reconciler import consent, decisions, matching, suppression
from constituent_reconciler.config import Recipe
from constituent_reconciler.connectors import get_factory
from constituent_reconciler.connectors.base import Connector, WriteResult
from constituent_reconciler.connectors.civicrm import Transport
from constituent_reconciler.connectors.salesforce import Transport as SalesforceTransport
from constituent_reconciler.models import GoldenRecord, Pair, Record, RunResult, SourceSpan
from constituent_reconciler.normalize import normalize_record
from constituent_reconciler.policy import PolicyViolation
from constituent_reconciler.provenance import ProvenanceLog, TimestampAuthority
from constituent_reconciler.suppression import AggregateSummary


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


def read_pdf_records(
    path: Path,
    source: str,
    *,
    recipe: Recipe,
    id_prefix: str,
    _start_index: int = 1,
) -> list[Record]:
    """Extract records from a PDF, routing low-confidence pages through the seam.

    Each page that yields at least a first_name or last_name becomes one Record.
    Pages that produce nothing useful are skipped. Low-confidence pages are
    offered to the cloud seam when the policy pack allows it; under DV and HIPAA
    packs the seam is always a NoOp regardless of the recipe's backend setting.
    """
    from constituent_reconciler.extract.pdf import PdfplumberExtractor
    from constituent_reconciler.extract.seam import make_seam

    extractor = PdfplumberExtractor()
    seam = make_seam(recipe.policy_pack, recipe.extract.backend)
    extraction = extractor.extract(path)

    records: list[Record] = []
    for page in extraction.pages:
        page_fields = list(page.fields)

        if page.confidence < recipe.extract.confidence_threshold and seam.is_enabled():
            refined = seam.refine(path, page.page_num)
            if refined:
                page_fields = refined

        raw: dict[str, str] = {}
        spans: dict[str, SourceSpan] = {}
        for ef in page_fields:
            if ef.field_name in recipe.mapping and ef.value:
                raw[ef.field_name] = ef.value
                if ef.span is not None:
                    spans[ef.field_name] = ef.span

        if not raw.get("first_name") and not raw.get("last_name"):
            continue

        unique_id = f"{id_prefix}{_start_index + len(records):04d}"
        records.append(
            Record(
                unique_id=unique_id,
                source=source,
                raw=raw,
                spans=spans,
            )
        )

    return records


def _ingest_source(
    path: Path,
    source: str,
    *,
    recipe: Recipe,
    id_prefix: str,
    _start_index: int = 1,
) -> list[Record]:
    """Route a source path to the right reader based on file type.

    A directory is walked; each .csv is read as a structured source and each
    .pdf is run through the extractor. A single file is routed by extension.
    Files with other extensions are silently skipped inside a directory;
    passed as a direct argument they fall through to the CSV reader.
    """
    if path.is_dir():
        records: list[Record] = []
        for child in sorted(path.iterdir()):
            suffix = child.suffix.lower()
            if suffix == ".csv":
                chunk = read_records(
                    child,
                    source,
                    mapping=recipe.mapping,
                    id_column=recipe.id_column,
                    consent_column=recipe.consent_column,
                    id_prefix=id_prefix,
                )
                records += chunk
            elif suffix == ".pdf" and recipe.extract.backend != "none":
                chunk = read_pdf_records(
                    child,
                    source,
                    recipe=recipe,
                    id_prefix=id_prefix,
                    _start_index=_start_index + len(records),
                )
                records += chunk
        return records
    elif path.suffix.lower() == ".pdf" and recipe.extract.backend != "none":
        return read_pdf_records(
            path, source, recipe=recipe, id_prefix=id_prefix, _start_index=_start_index
        )
    else:
        return read_records(
            path,
            source,
            mapping=recipe.mapping,
            id_column=recipe.id_column,
            consent_column=recipe.consent_column,
            id_prefix=id_prefix,
        )


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
        raw_records += _ingest_source(
            recipe.existing,
            "existing",
            recipe=recipe,
            id_prefix="E",
        )
    raw_records += _ingest_source(
        recipe.incoming,
        "incoming",
        recipe=recipe,
        id_prefix="N",
        _start_index=len(raw_records) + 1,
    )

    records = {
        r.unique_id: normalize_record(
            r, recipe.fields, address_backend=recipe.normalize.address_backend
        )
        for r in raw_records
    }

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


def _write_review_queue(result: RunResult, recipe: Recipe, out_dir: Path) -> Path:
    review_path = out_dir / "review_queue.csv"
    out_dir.mkdir(parents=True, exist_ok=True)
    has_spans = any(record.spans for record in result.records.values())
    with review_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        header = ["left", "right", "probability", "left_source", "right_source"]
        for f in recipe.fields:
            header += [f"{f}_left", f"{f}_right"]
        if has_spans:
            for f in recipe.fields:
                header += [f"{f}_left_span", f"{f}_right_span"]
        writer.writerow(header)
        for pair in sorted(
            result.review_pairs, key=lambda p: (-p.probability, p.left, p.right)
        ):
            left = result.records[pair.left]
            right = result.records[pair.right]
            row = [pair.left, pair.right, f"{pair.probability:.4f}", left.source, right.source]
            for f in recipe.fields:
                row += [left.raw.get(f, ""), right.raw.get(f, "")]
            if has_spans:
                for f in recipe.fields:
                    left_span = left.spans.get(f)
                    right_span = right.spans.get(f)
                    row += [
                        str(left_span) if left_span else "",
                        str(right_span) if right_span else "",
                    ]
            writer.writerow(row)
    return review_path


def _write_aggregate_summary(summary: AggregateSummary, out_dir: Path) -> Path:
    """Write the non-identifying, suppressed aggregate summary as JSON.

    This is the only artifact the DV pack considers shareable: counts with small
    cells suppressed and no field values, ids, or member lists.
    """

    import json

    from constituent_reconciler.schema import REPORT_SCHEMA_VERSION

    summary_path = out_dir / "aggregate_summary.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "total_resolved": summary.total,
        "breakdowns": {b.name: b.cells for b in summary.breakdowns},
        "note": (
            "Non-identifying aggregate. Small cells suppressed (counts 1-10), "
            "modeled on the U.S. CMS Cell Size Suppression Policy; true zeros "
            "preserved. Not a substitute for review against your own obligations."
        ),
    }
    summary_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary_path


def _write_withheld(withheld: Sequence[GoldenRecord], out_dir: Path) -> Path:
    withheld_path = out_dir / "withheld.csv"
    out_dir.mkdir(parents=True, exist_ok=True)
    with withheld_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["cluster_id", "members", "reason"])
        for record in sorted(withheld, key=lambda r: r.cluster_id):
            writer.writerow([record.cluster_id, "|".join(record.members), "no-consent"])
    return withheld_path


def build_connector(
    recipe: Recipe,
    out_dir: Path,
    *,
    transport: Transport | None = None,
    sf_transport: SalesforceTransport | None = None,
) -> Connector:
    """Construct the connector named by the recipe. Secrets come from the env.

    Construction is a registry lookup (``connectors.get_factory``), so adding a
    destination means one new module plus a registry entry, not an edit here.
    Under a policy pack that requires local targets (the DV pack), a non-local
    connector is refused before any write, fail-closed: client PII must not
    egress, so the network write target is rejected rather than used.
    """

    transports: dict[str, object] = {}
    if transport is not None:
        transports["civicrm"] = transport
    if sf_transport is not None:
        transports["salesforce"] = sf_transport
    factory = get_factory(recipe.output.connector)
    connector = factory(recipe.output, out_dir, transports)

    if recipe.require_local_targets and not connector.is_local:
        raise PolicyViolation(
            f"policy pack {recipe.policy_pack!r} forbids the non-local write target "
            f"{connector.name!r}; client information must stay on this machine. "
            f"Use the csv connector or a local target."
        )
    return connector


@dataclass(frozen=True)
class ExportSummary:
    write_results: tuple[WriteResult, ...]
    withheld: tuple[GoldenRecord, ...]
    review_path: Path
    withheld_path: Path | None
    provenance_path: Path | None
    logged: int
    aggregate: AggregateSummary | None = None
    aggregate_path: Path | None = None

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for result in self.write_results:
            out[result.action] = out.get(result.action, 0) + 1
        return out

    def describe(self) -> str:
        counts = self.counts()
        if not counts:
            return "no records to write"
        return ", ".join(f"{action}: {n}" for action, n in sorted(counts.items()))


def export(
    result: RunResult,
    recipe: Recipe,
    *,
    out_dir: Path,
    dry_run: bool = False,
    authority: TimestampAuthority | None = None,
    transport: Transport | None = None,
    sf_transport: SalesforceTransport | None = None,
) -> ExportSummary:
    """Write resolved records through the configured connector.

    Consent is enforced before the connector is touched: records without granted
    consent (under a consent-required policy) are withheld and never handed to a
    connector. Each real write is recorded in the append-only provenance log. A
    dry run performs no writes and logs nothing.
    """

    out_dir.mkdir(parents=True, exist_ok=True)
    exportable, withheld = consent.partition_by_consent(
        result.golden, require_consent=recipe.require_consent
    )
    by_id = {record.cluster_id: record for record in exportable}

    connector = build_connector(
        recipe, out_dir, transport=transport, sf_transport=sf_transport
    )
    write_results = connector.write_all(exportable, recipe.fields, dry_run=dry_run)

    provenance_path = out_dir / "provenance.jsonl"
    logged = 0
    if not dry_run:
        log = ProvenanceLog(provenance_path, authority)
        for write_result in write_results:
            if not write_result.is_write:
                continue
            record = by_id[write_result.record_id]
            log.append(
                action=write_result.action,
                record_id=write_result.record_id,
                members=record.members,
                consent=record.consent,
                payload=write_result.payload or {},
                external_id=write_result.external_id,
            )
            logged += 1

    review_path = _write_review_queue(result, recipe, out_dir)
    withheld_path = _write_withheld(withheld, out_dir) if withheld else None

    # Under a pack that requires aggregate sharing (the DV pack), build a
    # non-identifying, suppressed summary over the exportable records. It is the
    # only artifact that pack treats as shareable beyond the org's own machine.
    aggregate: AggregateSummary | None = None
    aggregate_path: Path | None = None
    if recipe.aggregate_export:
        aggregate = suppression.aggregate_summary(
            exportable, threshold=recipe.suppression_threshold
        )
        if not dry_run:
            aggregate_path = _write_aggregate_summary(aggregate, out_dir)

    return ExportSummary(
        write_results=tuple(write_results),
        withheld=tuple(withheld),
        review_path=review_path,
        withheld_path=withheld_path,
        provenance_path=provenance_path if (not dry_run and logged) else None,
        logged=logged,
        aggregate=aggregate,
        aggregate_path=aggregate_path,
    )
