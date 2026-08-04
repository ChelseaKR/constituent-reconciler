"""The run orchestrator.

Reads the source CSVs, normalizes, scores candidate pairs with the matcher, bands
them, builds clusters from confident merges, reduces each cluster to a golden
record, and applies the consent gate. The result is returned as a value; writing
files is a separate, explicit step so a dry run can produce the same result
without touching disk.
"""

from __future__ import annotations

import csv
import hashlib
import json
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from constituent_reconciler import consent, decisions, household, matching, stage_cache, suppression
from constituent_reconciler.config import Recipe
from constituent_reconciler.connectors import get_factory
from constituent_reconciler.connectors.airtable import Transport as AirtableTransport
from constituent_reconciler.connectors.base import Connector, WriteResult
from constituent_reconciler.connectors.civicrm import Transport
from constituent_reconciler.connectors.crm_csv import CrmCsvConnector
from constituent_reconciler.connectors.salesforce import Transport as SalesforceTransport
from constituent_reconciler.connectors.webhook import Transport as WebhookTransport
from constituent_reconciler.extract.base import ExtractedField
from constituent_reconciler.manifest import build_manifest, manifest_hash, write_manifest
from constituent_reconciler.models import (
    Consent,
    Correction,
    GoldenRecord,
    IngestReport,
    Pair,
    Record,
    RunResult,
    SkippedFile,
    SourceSpan,
    TextSpan,
)
from constituent_reconciler.policy import PolicyViolation
from constituent_reconciler.provenance import ProvenanceLog, Rfc3161Authority, TimestampAuthority
from constituent_reconciler.suppression import AggregateSummary, ComparableReport


class DuplicateIdError(ValueError):
    """Two records in one run carry the same unique id.

    Raised instead of silently keeping one record and dropping the other: a
    dropped record would vanish from matching, review, and export with no
    trace, which violates the fail-closed rule.
    """


def _content_id(source: str, raw: dict[str, str], id_prefix: str, seen: dict[str, int]) -> str:
    """Mint a stable record id from the record's own content.

    The digest covers the source name and the mapped raw values, so an id
    survives row insertion and reordering: editing a row changes that row's id
    and no other, and a decisions file recorded against one run still points at
    the same people in the next. Exact-duplicate rows share a digest and are
    disambiguated with a deterministic ``-2``, ``-3``, ... suffix in read order.
    """

    digest = hashlib.blake2b(
        f"{source}|{json.dumps(raw, sort_keys=True)}".encode(),
        digest_size=6,
    ).hexdigest()
    count = seen.get(digest, 0) + 1
    seen[digest] = count
    if count == 1:
        return f"{id_prefix}{digest}"
    return f"{id_prefix}{digest}-{count}"


def read_records(
    path: Path,
    source: str,
    *,
    mapping: dict[str, str],
    id_column: str | None,
    consent_column: str | None,
    id_prefix: str,
    consent_date_column: str | None = None,
    consent_expires_column: str | None = None,
    consent_scope_column: str | None = None,
    _seen: dict[str, int] | None = None,
) -> list[Record]:
    """Read one CSV into Records, applying the column mapping at read time.

    Ids are collision-safe by construction: a user-supplied id is namespaced by
    its source (``existing:E003``), so the same id in two source files stays two
    records; a generated id is derived from the row's content, not its position.
    ``_seen`` carries the duplicate-row counter across the files of one source.
    """

    seen = _seen if _seen is not None else {}
    records: list[Record] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            raw = {
                canonical: (row.get(column) or "").strip() for canonical, column in mapping.items()
            }
            if id_column and (row.get(id_column) or "").strip():
                unique_id = f"{source}:{row[id_column].strip()}"
            else:
                unique_id = _content_id(source, raw, id_prefix, seen)
            records.append(
                Record(
                    unique_id=unique_id,
                    source=source,
                    raw=raw,
                    consent=_read_consent(
                        row,
                        status_column=consent_column,
                        granted_on_column=consent_date_column,
                        expires_on_column=consent_expires_column,
                        scope_column=consent_scope_column,
                    ),
                )
            )
    return records


def _collect_mapped_fields(
    page_fields: Iterable[ExtractedField],
    mapping: dict[str, str],
) -> tuple[dict[str, str], dict[str, SourceSpan | TextSpan]]:
    """Keep the extracted fields the recipe maps, along with their spans."""
    raw: dict[str, str] = {}
    spans: dict[str, SourceSpan | TextSpan] = {}
    for ef in page_fields:
        if ef.field_name in mapping and ef.value:
            raw[ef.field_name] = ef.value
            if ef.span is not None:
                spans[ef.field_name] = ef.span
    return raw, spans


@dataclass
class IngestAccumulator:
    """Mutable ingest accounting, frozen into an ``IngestReport`` after a run.

    The readers stay simple: they take one optional accumulator and note what
    they saw. ``run`` owns the accumulator's lifetime and freezes it, so the
    accounting is a value on ``RunResult`` rather than shared mutable state.
    """

    files_read: list[str] = field(default_factory=list)
    files_skipped: list[SkippedFile] = field(default_factory=list)
    pages_extracted: int = 0
    pages_dropped: int = 0
    normalization_failures: dict[str, dict[str, int]] = field(default_factory=dict)

    def note_read(self, path: Path) -> None:
        self.files_read.append(str(path))

    def note_skipped(self, path: Path, reason: str) -> None:
        self.files_skipped.append(SkippedFile(path=str(path), reason=reason))

    def freeze(self) -> IngestReport:
        return IngestReport(
            files_read=tuple(self.files_read),
            files_skipped=tuple(self.files_skipped),
            pages_extracted=self.pages_extracted,
            pages_dropped=self.pages_dropped,
            normalization_failures={
                name: dict(counts) for name, counts in self.normalization_failures.items()
            },
        )


def _read_date(row: dict[str, str], column: str | None) -> date | None:
    if not column:
        return None
    value = (row.get(column) or "").strip()
    return date.fromisoformat(value) if value else None


def _read_scope(row: dict[str, str], column: str | None) -> frozenset[str]:
    if not column:
        return frozenset()
    value = (row.get(column) or "").strip()
    if not value:
        return frozenset()
    return frozenset(part.strip() for part in value.split(",") if part.strip())


def _read_consent(
    row: dict[str, str],
    *,
    status_column: str | None,
    granted_on_column: str | None,
    expires_on_column: str | None,
    scope_column: str | None,
) -> Consent:
    status = (row.get(status_column) or "").strip() if status_column else ""
    return Consent(
        status=status,
        granted_on=_read_date(row, granted_on_column),
        expires_on=_read_date(row, expires_on_column),
        scope=_read_scope(row, scope_column),
    )


def _extract_pdf_rows(path: Path, recipe: Recipe) -> stage_cache.ExtractedRows:
    """Parse one PDF into kept rows plus page accounting, without minting ids.

    Low-confidence pages are offered to the cloud seam when the policy pack
    allows it; under DV and HIPAA packs the seam is always a NoOp regardless
    of the recipe's backend setting. ``backend = "pdfplumber+ocr"`` selects
    the OCR-fallback extractor, which OCRs any page with no embedded text
    layer instead of yielding an empty page; every other backend value uses
    the plain pdfplumber text-layer extractor.

    Unless the recipe sets ``[extract] sandbox = false``, the parse runs in a
    resource-limited child process (``extract/sandbox.py``): a hostile or
    malformed PDF fails closed to a zero-confidence page that is dropped and
    accounted for here, instead of crashing the run. A parse the sandbox
    ended early (``extraction.note`` is set) is marked not cacheable, because
    a resource-limit kill reflects the machine's load rather than the file
    bytes; the stage cache must not freeze it as this document's permanent
    result.
    """
    from constituent_reconciler.extract.base import Extractor
    from constituent_reconciler.extract.pdf import PdfplumberExtractor
    from constituent_reconciler.extract.sandbox import SandboxedExtractor
    from constituent_reconciler.extract.seam import make_seam

    wants_ocr = recipe.extract.backend == "pdfplumber+ocr"
    extractor: Extractor
    if recipe.extract.sandbox:
        extractor = SandboxedExtractor(ocr=wants_ocr)
    elif wants_ocr:
        from constituent_reconciler.extract.ocr import PdfplumberOcrExtractor

        extractor = PdfplumberOcrExtractor()
    else:
        extractor = PdfplumberExtractor()
    seam = make_seam(
        recipe.policy_pack,
        recipe.extract.backend,
        local_model_override=recipe.extract.local_model_override,
        local_model_id=recipe.extract.local_model_id,
    )
    extraction = extractor.extract(path)

    rows: list[stage_cache.Row] = []
    pages_extracted = 0
    pages_dropped = 0
    for page in extraction.pages:
        page_fields = list(page.fields)

        if page.confidence < recipe.extract.confidence_threshold and seam.is_enabled():
            refined = seam.refine(path, page.page_num)
            if refined:
                page_fields = refined

        raw, spans = _collect_mapped_fields(page_fields, recipe.mapping)

        if not raw.get("first_name") and not raw.get("last_name"):
            pages_dropped += 1
            continue
        pages_extracted += 1
        rows.append((raw, spans))

    return stage_cache.ExtractedRows(
        rows=rows,
        pages_extracted=pages_extracted,
        pages_dropped=pages_dropped,
        cacheable=extraction.note is None,
    )


def _extract_text_rows(path: Path, recipe: Recipe) -> stage_cache.ExtractedRows:
    """Parse one .txt or .eml file into kept rows plus page accounting.

    Parsing is stdlib-only and fully offline; the cloud seam is never consulted
    for text sources. A body that produces nothing useful is dropped and
    counted, same as an empty PDF page.
    """
    from constituent_reconciler.extract.text import extract_eml, extract_text_file

    if path.suffix.lower() == ".eml":
        extraction = extract_eml(path)
    else:
        extraction = extract_text_file(path)

    rows: list[stage_cache.Row] = []
    pages_extracted = 0
    pages_dropped = 0
    for page in extraction.pages:
        raw, spans = _collect_mapped_fields(page.fields, recipe.mapping)

        if not raw.get("first_name") and not raw.get("last_name"):
            pages_dropped += 1
            continue
        pages_extracted += 1
        rows.append((raw, spans))

    return stage_cache.ExtractedRows(
        rows=rows,
        pages_extracted=pages_extracted,
        pages_dropped=pages_dropped,
        cacheable=extraction.note is None,
    )


def _mint_document_records(
    rows: Iterable[stage_cache.Row],
    source: str,
    *,
    id_prefix: str,
    seen: dict[str, int],
) -> list[Record]:
    """Turn extracted rows into Records, minting content-derived ids.

    Minting happens outside the extraction (and outside its cache entry)
    because the duplicate-row counter spans every file of a source; a cached
    file's rows must take the same ids they would take in a fresh parse of
    the whole source.
    """

    return [
        Record(
            unique_id=_content_id(source, raw, id_prefix, seen),
            source=source,
            raw=raw,
            spans=spans,
        )
        for raw, spans in rows
    ]


def read_pdf_records(
    path: Path,
    source: str,
    *,
    recipe: Recipe,
    id_prefix: str,
    _seen: dict[str, int] | None = None,
    accounting: IngestAccumulator | None = None,
    active_cache: stage_cache.ActiveCache | None = None,
) -> list[Record]:
    """Extract records from a PDF, via the stage cache when one is active.

    Each page that yields at least a first_name or last_name becomes one
    Record; pages that produce nothing useful are dropped and accounted for.
    The parse itself (and its seam and sandbox behavior) is described on
    ``_extract_pdf_rows``. With an active cache and the plain ``pdfplumber``
    backend, the parse result is served content-addressed by the file's
    digest; backends whose output is not a pure function of the file bytes
    bypass the cache (``stage_cache.extraction_cacheable``).
    """

    extracted = stage_cache.extraction_via_cache(
        active_cache,
        path,
        recipe,
        reader="pdf",
        extract_fresh=lambda: _extract_pdf_rows(path, recipe),
    )
    if accounting is not None:
        accounting.pages_extracted += extracted.pages_extracted
        accounting.pages_dropped += extracted.pages_dropped
    seen = _seen if _seen is not None else {}
    return _mint_document_records(extracted.rows, source, id_prefix=id_prefix, seen=seen)


def read_text_records(
    path: Path,
    source: str,
    *,
    recipe: Recipe,
    id_prefix: str,
    _seen: dict[str, int] | None = None,
    accounting: IngestAccumulator | None = None,
    active_cache: stage_cache.ActiveCache | None = None,
) -> list[Record]:
    """Extract records from a .txt or .eml intake file, via the cache if active.

    A body that yields at least a first_name or last_name becomes one Record
    whose spans are line offsets into the text body. Text parsing is
    stdlib-only, so with an active cache the result is always served
    content-addressed by the file's digest.
    """

    extracted = stage_cache.extraction_via_cache(
        active_cache,
        path,
        recipe,
        reader="text",
        extract_fresh=lambda: _extract_text_rows(path, recipe),
    )
    if accounting is not None:
        accounting.pages_extracted += extracted.pages_extracted
        accounting.pages_dropped += extracted.pages_dropped
    seen = _seen if _seen is not None else {}
    return _mint_document_records(extracted.rows, source, id_prefix=id_prefix, seen=seen)


def _ingest_source(  # noqa: C901 - routes all supported source types and skips.
    path: Path,
    source: str,
    *,
    recipe: Recipe,
    id_prefix: str,
    accounting: IngestAccumulator | None = None,
    active_cache: stage_cache.ActiveCache | None = None,
) -> list[Record]:
    """Route a source path to the right reader based on file type.

    A directory is walked; each .csv is read as a structured source, each .pdf
    is run through the PDF extractor, and each .txt or .eml is run through the
    text extractor. A single file is routed by extension. Files with other
    extensions are skipped inside a directory; passed as a direct argument
    they fall through to the CSV reader. One duplicate-row counter spans every
    file of the source, so exact-duplicate rows across files stay distinct
    without spurious collisions. Every child of a directory is answered for in
    ``accounting`` when one is passed: read, or skipped with the reason why.
    """
    seen: dict[str, int] = {}
    if path.is_dir():
        records: list[Record] = []
        for child in sorted(path.iterdir()):
            suffix = child.suffix.lower()
            if child.is_dir():
                if accounting is not None:
                    accounting.note_skipped(child, "directory: not walked recursively")
            elif suffix == ".csv":
                chunk = read_records(
                    child,
                    source,
                    mapping=recipe.mapping,
                    id_column=recipe.id_column,
                    consent_column=recipe.consent_column,
                    consent_date_column=recipe.consent_date_column,
                    consent_expires_column=recipe.consent_expires_column,
                    consent_scope_column=recipe.consent_scope_column,
                    id_prefix=id_prefix,
                    _seen=seen,
                )
                records += chunk
                if accounting is not None:
                    accounting.note_read(child)
            elif suffix == ".pdf" and recipe.extract.backend != "none":
                chunk = read_pdf_records(
                    child,
                    source,
                    recipe=recipe,
                    id_prefix=id_prefix,
                    _seen=seen,
                    accounting=accounting,
                    active_cache=active_cache,
                )
                records += chunk
                if accounting is not None:
                    accounting.note_read(child)
            elif suffix in (".txt", ".eml") and recipe.extract.backend != "none":
                chunk = read_text_records(
                    child,
                    source,
                    recipe=recipe,
                    id_prefix=id_prefix,
                    _seen=seen,
                    accounting=accounting,
                    active_cache=active_cache,
                )
                records += chunk
                if accounting is not None:
                    accounting.note_read(child)
            elif accounting is not None:
                if suffix == ".pdf":
                    accounting.note_skipped(
                        child, 'pdf extraction disabled (extract.backend = "none")'
                    )
                elif suffix in (".txt", ".eml"):
                    accounting.note_skipped(
                        child, 'text extraction disabled (extract.backend = "none")'
                    )
                else:
                    accounting.note_skipped(child, f"unsupported extension: {suffix or '(none)'}")
        return records
    elif path.suffix.lower() == ".pdf" and recipe.extract.backend != "none":
        chunk = read_pdf_records(
            path,
            source,
            recipe=recipe,
            id_prefix=id_prefix,
            _seen=seen,
            accounting=accounting,
            active_cache=active_cache,
        )
        if accounting is not None:
            accounting.note_read(path)
        return chunk
    elif path.suffix.lower() in (".txt", ".eml") and recipe.extract.backend != "none":
        chunk = read_text_records(
            path,
            source,
            recipe=recipe,
            id_prefix=id_prefix,
            _seen=seen,
            accounting=accounting,
            active_cache=active_cache,
        )
        if accounting is not None:
            accounting.note_read(path)
        return chunk
    else:
        chunk = read_records(
            path,
            source,
            mapping=recipe.mapping,
            id_column=recipe.id_column,
            consent_column=recipe.consent_column,
            consent_date_column=recipe.consent_date_column,
            consent_expires_column=recipe.consent_expires_column,
            consent_scope_column=recipe.consent_scope_column,
            id_prefix=id_prefix,
            _seen=seen,
        )
        if accounting is not None:
            accounting.note_read(path)
        return chunk


def _check_distinct_ids(records: Sequence[Record]) -> None:
    """Refuse a run in which two records share an id.

    With namespaced user ids and content-derived generated ids a collision can
    only come from a duplicated ``id_column`` value within one source. Keying
    the run's record map on such an id would silently drop a record, so the run
    stops here and names the id instead.
    """

    sources_by_id: dict[str, list[str]] = {}
    for record in records:
        sources_by_id.setdefault(record.unique_id, []).append(record.source)
    duplicates = {
        unique_id: sources for unique_id, sources in sources_by_id.items() if len(sources) > 1
    }
    if duplicates:
        unique_id, sources = sorted(duplicates.items())[0]
        raise DuplicateIdError(
            f"record id {unique_id!r} appears {len(sources)} times in this run "
            f"(source: {', '.join(sorted(set(sources)))}). Every record needs a "
            f"distinct id; fix the duplicated value in the source file's id "
            f"column, or remove id_column from the recipe to generate ids from "
            f"row content."
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
        adjusted.append(Pair(pair.left, pair.right, pair.probability, band, pair.note))
    return adjusted


def _group_corrections(
    corrections: Iterable[Correction], *, fields: tuple[str, ...]
) -> dict[str, dict[str, str]]:
    """Group corrections by record and reject fields outside this recipe."""

    grouped: dict[str, dict[str, str]] = {}
    for correction in corrections:
        if correction.field not in fields:
            raise ValueError(
                f"correction targets field {correction.field!r}, which recipe {fields!r} "
                "does not map"
            )
        if not correction.value.strip():
            raise ValueError("a correction requires a non-blank replacement value")
        grouped.setdefault(correction.record_id, {})[correction.field] = correction.value
    return grouped


def _apply_corrections(
    records: list[Record], corrections_by_record: dict[str, dict[str, str]]
) -> list[Record]:
    """Replace corrected raw values before normalization, preserving consent."""

    known_ids = {record.unique_id for record in records}
    unknown = sorted(set(corrections_by_record) - known_ids)
    if unknown:
        raise ValueError(f"correction references record not present in this run: {unknown[0]!r}")
    corrected: list[Record] = []
    for record in records:
        fixes = corrections_by_record.get(record.unique_id)
        if not fixes:
            corrected.append(record)
            continue
        raw = dict(record.raw)
        raw.update(fixes)
        corrected.append(
            Record(
                unique_id=record.unique_id,
                source=record.source,
                raw=raw,
                consent=record.consent,
                spans={name: span for name, span in record.spans.items() if name not in fixes},
            )
        )
    return corrected


class _StageTimer:
    """Wall-clock seconds per pipeline stage, content-free by construction."""

    def __init__(self) -> None:
        self.durations: dict[str, float] = {}
        self._started = time.perf_counter()

    def mark(self, stage: str) -> None:
        now = time.perf_counter()
        self.durations[stage] = round(now - self._started, 6)
        self._started = now


def run(
    recipe: Recipe,
    *,
    force_auto: Iterable[frozenset[str]] = (),
    force_drop: Iterable[frozenset[str]] = (),
    corrections: Iterable[Correction] = (),
    cache: stage_cache.StageCache | None = None,
) -> RunResult:
    """Execute the pipeline and return the result.

    ``force_auto`` and ``force_drop`` carry human review decisions back in: an
    approved review pair becomes a confident merge, a rejected one is dropped.
    Corrections replace raw field values before normalization, so matching,
    golden-record reduction, lineage, and export all see the reviewed value.

    ``cache`` is the optional stage cache (UC-01). When one is passed, the
    deterministic extraction and normalization stages read and write it; the
    caller owns constructing it (``stage_cache.for_recipe``) and deciding to
    pass it, which keeps a plain ``run`` free of disk writes. Candidate
    generation, scoring, banding, and clustering never touch the cache:
    term frequencies and cross-batch candidates change pair probabilities
    whenever the population changes, so those stages are computed fresh on
    every run regardless of what is cached.
    """

    active = stage_cache.ActiveCache(cache) if cache is not None else None
    timer = _StageTimer()
    accounting = IngestAccumulator()
    raw_records: list[Record] = []
    if recipe.existing is not None:
        raw_records += _ingest_source(
            recipe.existing,
            "existing",
            recipe=recipe,
            id_prefix="E",
            accounting=accounting,
            active_cache=active,
        )
    raw_records += _ingest_source(
        recipe.incoming,
        "incoming",
        recipe=recipe,
        id_prefix="N",
        accounting=accounting,
        active_cache=active,
    )
    _check_distinct_ids(raw_records)
    raw_records = _apply_corrections(
        raw_records, _group_corrections(corrections, fields=recipe.fields)
    )
    timer.mark("ingest")

    records = {
        r.unique_id: stage_cache.normalize_via_cache(
            active,
            r,
            recipe,
            failures=accounting.normalization_failures,
        )
        for r in raw_records
    }
    timer.mark("normalize")

    scored = matching.score_pairs(records.values(), recipe.fields, prior=recipe.prior)
    timer.mark("score")
    pairs = decisions.band_pairs(
        scored,
        auto_threshold=recipe.auto_threshold,
        review_threshold=recipe.review_threshold,
    )
    auto_overrides = frozenset(force_auto)
    drop_overrides = frozenset(force_drop)
    pairs = _apply_overrides(pairs, auto_overrides, drop_overrides)
    pairs = decisions.enforce_cannot_links(records.keys(), pairs, drop_overrides)

    clusters = decisions.build_clusters(records.keys(), pairs)
    golden = decisions.golden_records(
        clusters, records, recipe.fields, fill_policy=recipe.fill_policy
    )
    timer.mark("resolve")

    stats = (
        active.stats.freeze(enabled=True)
        if active is not None
        else stage_cache.CacheStatsCollector().freeze(enabled=False)
    )
    return RunResult(
        records=records,
        pairs=tuple(pairs),
        clusters=tuple(clusters),
        golden=tuple(golden),
        ingest=accounting.freeze(),
        cache=stats,
        stage_durations=timer.durations,
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
        has_notes = any(pair.note for pair in result.review_pairs)
        if has_notes:
            header.append("note")
        writer.writerow(header)
        for pair in sorted(result.review_pairs, key=lambda p: (-p.probability, p.left, p.right)):
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
            if has_notes:
                row.append(pair.note)
            writer.writerow(row)
    return review_path


def _write_aggregate_summary(summary: AggregateSummary, out_dir: Path, *, fill_policy: str) -> Path:
    """Write the non-identifying, suppressed aggregate summary as JSON.

    This is the only artifact the DV pack considers shareable: counts with small
    cells suppressed and no field values, ids, or member lists. ``fill_policy``
    is run metadata (a policy name, not data), recorded so the report states
    how golden records were merged.
    """

    from constituent_reconciler.schema import REPORT_SCHEMA_VERSION

    summary_path = out_dir / "aggregate_summary.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "fill_policy": fill_policy,
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


def _write_comparable_report(report: ComparableReport, out_dir: Path) -> Path:
    """Write the CoC-shaped comparable-database report as JSON."""

    report_path = out_dir / "comparable_report.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = suppression.comparable_payload(report)
    report_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report_path


def _write_run_summary(
    result: RunResult,
    recipe: Recipe,
    withheld: Sequence[consent.Withheld],
    out_dir: Path,
) -> Path:
    """Write a count-only summary that can feed the narrative report.

    The cache block and the stage durations are counts and seconds; nothing
    in them names a path, a record, or a field value, so the summary stays
    content-free under every policy pack.
    """

    import json

    from constituent_reconciler.schema import REPORT_SCHEMA_VERSION

    summary_path = out_dir / "run_summary.json"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "policy_pack": recipe.policy_pack,
        "consent_required": recipe.require_consent,
        "records_in": len(result.records),
        "candidate_pairs": len(result.pairs),
        "auto_merged_pairs": len(result.auto_pairs),
        "review_pairs": len(result.review_pairs),
        "resolved_records": len(result.golden),
        "merged_records": sum(max(len(record.members) - 1, 0) for record in result.golden),
        "withheld_no_consent": len(withheld),
        "cache": {
            "enabled": result.cache.enabled,
            "hits": dict(result.cache.hits),
            "misses": dict(result.cache.misses),
        },
        "stage_durations_seconds": dict(result.stage_durations),
    }
    summary_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary_path


def _write_household_suggestions(
    suggestions: Sequence[household.HouseholdSuggestion],
    confirmed: frozenset[str],
    out_dir: Path,
) -> Path:
    """Write the household suggestion artifact: its own review queue section.

    Every suggestion is listed regardless of confirmation status, so a reviewer
    sees the full candidate list in one place; the ``confirmed`` column reflects
    only what was passed in as already confirmed (from a decisions file), never
    what the grouping itself proposes. Nothing here is read back by this
    function; confirmation flows back in through ``export``'s
    ``confirmed_households`` argument on a later run, the same shape as the
    pair-review decisions file.
    """

    path = out_dir / "household_suggestions.csv"
    out_dir.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["household_id", "members", "address", "surname", "confirmed", "note"])
        for suggestion in suggestions:
            writer.writerow(
                [
                    suggestion.household_id,
                    "|".join(suggestion.members),
                    suggestion.address,
                    suggestion.surname,
                    "yes" if suggestion.household_id in confirmed else "",
                    household.REVIEW_NOTE,
                ]
            )
    return path


def _write_withheld(withheld: Sequence[consent.Withheld], out_dir: Path) -> Path:
    withheld_path = out_dir / "withheld.csv"
    out_dir.mkdir(parents=True, exist_ok=True)
    with withheld_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["cluster_id", "members", "reason"])
        for item in sorted(withheld, key=lambda w: w.cluster_id):
            writer.writerow([item.cluster_id, "|".join(item.members), item.reason])
    return withheld_path


def build_connector(
    recipe: Recipe,
    out_dir: Path,
    *,
    transport: Transport | None = None,
    sf_transport: SalesforceTransport | None = None,
    webhook_transport: WebhookTransport | None = None,
    airtable_transport: AirtableTransport | None = None,
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
    if webhook_transport is not None:
        transports["webhook"] = webhook_transport
    if airtable_transport is not None:
        transports["airtable"] = airtable_transport
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
    withheld: tuple[consent.Withheld, ...]
    review_path: Path
    withheld_path: Path | None
    provenance_path: Path | None
    logged: int
    manifest_path: Path | None = None
    aggregate: AggregateSummary | None = None
    aggregate_path: Path | None = None
    comparable: ComparableReport | None = None
    comparable_path: Path | None = None
    household_suggestions: tuple[household.HouseholdSuggestion, ...] = ()
    household_path: Path | None = None

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


def _maybe_export_comparable(
    recipe: Recipe,
    exportable: Sequence[GoldenRecord],
    *,
    out_dir: Path,
    dry_run: bool,
) -> tuple[ComparableReport | None, Path | None]:
    """Build and (unless dry-run) write the comparable report, if opted in.

    The comparable-database export profile is explicit recipe opt-in (see
    config.py's ``[comparable]`` section): when set, ``run``/``apply`` write
    ``comparable_report.json`` alongside ``aggregate_summary.json`` using the
    same suppressed-report builder the standalone ``export-comparable``
    command uses (``export_comparable`` below), so the two paths cannot
    drift.
    """

    if not recipe.comparable_export:
        return None, None
    comparable = suppression.comparable_summary(
        exportable,
        threshold=recipe.suppression_threshold,
        breakdown_fields=recipe.comparable_breakdown_fields,
        period=recipe.comparable_period,
    )
    comparable_path = None if dry_run else _write_comparable_report(comparable, out_dir)
    return comparable, comparable_path


def _timestamp_authority_for(
    recipe: Recipe,
    authority: TimestampAuthority | None,
) -> TimestampAuthority | None:
    if not recipe.tsa_url:
        return authority
    if recipe.require_local_targets:
        raise PolicyViolation(
            f"policy pack {recipe.policy_pack!r} forbids network timestamp authorities; "
            "client information must stay on this machine"
        )
    return authority or Rfc3161Authority(recipe.tsa_url)


def export(
    result: RunResult,
    recipe: Recipe,
    *,
    out_dir: Path,
    dry_run: bool = False,
    authority: TimestampAuthority | None = None,
    transport: Transport | None = None,
    sf_transport: SalesforceTransport | None = None,
    webhook_transport: WebhookTransport | None = None,
    airtable_transport: AirtableTransport | None = None,
    confirmed_households: Iterable[str] = (),
) -> ExportSummary:
    """Write resolved records through the configured connector.

    Consent is enforced before the connector is touched: records without granted
    consent (under a consent-required policy) are withheld and never handed to a
    connector. Each real write is recorded in the append-only provenance log. A
    dry run performs no writes and logs nothing.

    Every non-dry-run export also stamps ``out/run_manifest.json`` (see
    ``manifest.py``): BLAKE2b digests of the recipe file and each input file,
    the resolved thresholds, and the policy pack; the manifest's hash is the
    log's ``run-start`` entry, appended ahead of the write entries so every
    write chains back to the exact configuration that produced it.

    ``confirmed_households`` carries household ids a reviewer confirmed from an
    earlier run's ``household_suggestions.csv`` (see ``cli.py``'s ``apply``
    command). It has no effect unless ``recipe.household.enabled`` is true: the
    grouping step itself never runs otherwise, under any policy pack, so there
    is nothing to confirm into.
    """

    out_dir.mkdir(parents=True, exist_ok=True)
    log_authority = _timestamp_authority_for(recipe, authority)
    exportable, withheld = consent.partition_by_consent(
        result.golden,
        require_consent=recipe.require_consent,
        destination=recipe.output.connector,
    )
    by_id = {record.cluster_id: record for record in exportable}

    household_suggestions: tuple[household.HouseholdSuggestion, ...] = ()
    household_path: Path | None = None
    household_map: dict[str, str] = {}
    if recipe.household.enabled:
        # Suggestions are built over exportable records only: a withheld
        # (no-consent) record must not appear in a household suggestion any
        # more than it appears in a CRM export.
        household_suggestions = tuple(household.suggest_households(exportable))
        confirmed = frozenset(confirmed_households)
        household_map = household.confirmed_member_map(household_suggestions, confirmed)
        if not dry_run:
            household_path = _write_household_suggestions(household_suggestions, confirmed, out_dir)

    connector = build_connector(
        recipe,
        out_dir,
        transport=transport,
        sf_transport=sf_transport,
        webhook_transport=webhook_transport,
        airtable_transport=airtable_transport,
    )
    if household_map and isinstance(connector, CrmCsvConnector):
        connector.set_household_column(household_map)
    write_results = connector.write_all(exportable, recipe.fields, dry_run=dry_run)

    provenance_path = out_dir / "provenance.jsonl"
    manifest_path: Path | None = None
    logged = 0
    if not dry_run:
        input_paths = [p for p in (recipe.existing, recipe.incoming) if p is not None]
        manifest = build_manifest(recipe.recipe_path, input_paths, recipe, cache=result.cache)
        manifest_path = write_manifest(manifest, out_dir)
        log = ProvenanceLog(provenance_path, log_authority)
        log.append_run_start(manifest_hash(manifest))
        for write_result in write_results:
            if not write_result.is_write:
                continue
            record = by_id[write_result.record_id]
            log.append(
                action=write_result.action,
                record_id=write_result.record_id,
                members=record.members,
                consent=record.consent.is_active(
                    as_of=date.today(), destination=recipe.output.connector
                ),
                payload=write_result.payload or {},
                external_id=write_result.external_id,
                # Field-level lineage: member ids only, never field values, so
                # the log stays within the DV pack's minimization posture.
                field_sources=record.field_sources,
                fill_policy=recipe.fill_policy,
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
            aggregate_path = _write_aggregate_summary(
                aggregate, out_dir, fill_policy=recipe.fill_policy
            )
    if not dry_run:
        _write_run_summary(result, recipe, withheld, out_dir)

    comparable, comparable_path = _maybe_export_comparable(
        recipe, exportable, out_dir=out_dir, dry_run=dry_run
    )

    return ExportSummary(
        write_results=tuple(write_results),
        withheld=tuple(withheld),
        review_path=review_path,
        withheld_path=withheld_path,
        # The log exists on every non-dry run: it holds at least the run-start
        # entry binding this run to its manifest, plus one entry per write.
        provenance_path=provenance_path if not dry_run else None,
        manifest_path=manifest_path,
        logged=logged,
        aggregate=aggregate,
        aggregate_path=aggregate_path,
        comparable=comparable,
        comparable_path=comparable_path,
        household_suggestions=household_suggestions,
        household_path=household_path,
    )


def export_comparable(
    result: RunResult,
    recipe: Recipe,
    *,
    out_dir: Path,
) -> tuple[ComparableReport, Path]:
    """Write only the suppressed comparable-database report.

    Backs the standalone ``reconcile export-comparable`` command: one call,
    independent of ``recipe.comparable_export`` (which instead controls
    whether ``export`` above writes this report as a side effect of a normal
    ``run``/``apply``). Uses the recipe's ``comparable_breakdown_fields`` and
    ``comparable_period`` the same way ``export`` does, so the standalone
    command and the recipe-driven path never disagree about what a given
    recipe's comparable report contains.
    """

    exportable, _ = consent.partition_by_consent(
        result.golden,
        require_consent=recipe.require_consent,
        destination=recipe.output.connector,
    )
    report = suppression.comparable_summary(
        exportable,
        threshold=recipe.suppression_threshold,
        breakdown_fields=recipe.comparable_breakdown_fields,
        period=recipe.comparable_period,
    )
    return report, _write_comparable_report(report, out_dir)
