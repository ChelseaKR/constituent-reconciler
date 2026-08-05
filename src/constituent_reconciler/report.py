"""Rendering for the run summary and the committed eval report.

Output is plain text for the run summary and Markdown for the eval report. Both
state what is true without dressing it up, and the eval report shows the gated
metric and whether it passed, not a single headline number.
"""

from __future__ import annotations

from collections.abc import Sequence

from constituent_reconciler.evaluate import (
    KAPPA_GATE,
    CalibrationReport,
    EvalReport,
    ExtractionReport,
)
from constituent_reconciler.models import IngestReport, RunResult
from constituent_reconciler.quality import SourceQuality
from constituent_reconciler.suppression import SUPPRESSED


def render_run_summary(result: RunResult, *, withheld: int = 0) -> str:
    n_records = len(result.records)
    n_clusters = len(result.clusters)
    merged_clusters = sum(1 for c in result.clusters if len(c.members) > 1)
    lines = [
        f"records read:        {n_records}",
        f"candidate pairs:     {len(result.pairs)}",
        f"auto-merged pairs:   {len(result.auto_pairs)}",
        f"pairs to review:     {len(result.review_pairs)}",
        f"resolved records:    {n_clusters} ({merged_clusters} formed by merging)",
    ]
    if withheld:
        lines.append(f"withheld (no consent): {withheld}")
    lines += _render_ingest(result.ingest)
    return "\n".join(lines)


def _quality_pct(value: float | str) -> str:
    return value if isinstance(value, str) else f"{value * 100:.1f}%"


def _worst_field(quality: SourceQuality) -> str:
    """Name the source's lowest-completeness field, e.g. ``phone 40.0% complete``.

    When any completeness cell is suppressed, the true worst field may be one
    of the hidden ones, so naming a published field would mislabel it; the
    label says ``suppressed`` instead. A result measured over no fields
    reports ``-``.
    """

    published = {
        name: value for name, value in quality.completeness.items() if isinstance(value, float)
    }
    if not quality.completeness:
        return "-"
    if len(published) != len(quality.completeness):
        return SUPPRESSED
    worst = min(published, key=lambda name: published[name])
    return f"{worst} {_quality_pct(published[worst])} complete"


def _failure_total(quality: SourceQuality) -> str:
    """Sum the per-field normalization-failure counts, suppression-aware."""

    counts = quality.normalization_failures.values()
    if any(isinstance(count, str) for count in counts):
        return SUPPRESSED
    return str(sum(count for count in counts if isinstance(count, int)))


def render_source_quality(sources: Sequence[SourceQuality]) -> str:
    """Render the per-source data-quality table as plain text.

    One row per source: record count, the worst (least complete) field,
    normalization failures, consent coverage, and duplicate density, so an
    operator can name the weakest field of each intake channel from one
    screen. Cells withheld by the active policy's small-cell rules print as
    ``suppressed``.
    """

    header = ("source", "records", "worst field", "failures", "consent", "duplicates")
    rows = [header]
    for quality in sources:
        rows.append(
            (
                quality.source,
                str(quality.records),
                _worst_field(quality),
                _failure_total(quality),
                _quality_pct(quality.consent_coverage),
                _quality_pct(quality.duplicate_density),
            )
        )

    widths = [max(len(row[i]) for row in rows) for i in range(len(header))]
    lines = ["data quality by source:"]
    if any(SUPPRESSED in row for row in rows[1:]):
        lines[0] = "data quality by source (small cells suppressed under the active policy):"
    for row in rows:
        cells = [row[0].ljust(widths[0]), row[1].rjust(widths[1]), row[2].ljust(widths[2])]
        cells += [row[i].rjust(widths[i]) for i in range(3, len(header))]
        lines.append("  " + "  ".join(cells).rstrip())
    return "\n".join(lines)


def _render_ingest(ingest: IngestReport) -> list[str]:
    """Render the ingest accounting: every file, page, and failure answered for.

    Empty when there is nothing to report (a result built without ingest
    accounting), so summaries for hand-built results stay unchanged.
    """

    if not ingest.files_read and not ingest.files_skipped:
        return []
    lines = ["", "ingest:", f"  files read:        {len(ingest.files_read)}"]
    lines += [f"    {path}" for path in ingest.files_read]
    if ingest.files_skipped:
        lines.append(f"  files skipped:     {len(ingest.files_skipped)}")
        lines += [f"    {skipped.path} ({skipped.reason})" for skipped in ingest.files_skipped]
    if ingest.pages_extracted or ingest.pages_dropped:
        lines.append(
            f"  pdf pages:         {ingest.pages_extracted} extracted, "
            f"{ingest.pages_dropped} dropped (no name found)"
        )
    if ingest.normalization_failures:
        lines.append("  normalization failures (value present, nothing parseable):")
        for field_name in sorted(ingest.normalization_failures):
            per_source = ingest.normalization_failures[field_name]
            counts = ", ".join(f"{source}: {count}" for source, count in sorted(per_source.items()))
            lines.append(f"    {field_name}: {counts}")
    return lines


def _pct(value: float) -> str:
    return f"{value * 100:.1f}%"


def _ci(interval: tuple[float, float]) -> str:
    low, high = interval
    return f"[{_pct(low)}, {_pct(high)}]"


def _calibration_lines(calibration: CalibrationReport | None) -> list[str]:
    lines = [
        "",
        "## Calibration (LLM field judge)",
        "",
        "Cohen's kappa measures agreement between the extractor's confidence "
        "verdicts and human field labels on a committed calibration fixture. "
        "Kappa below 0.60 means confidence scores are not tracking accuracy "
        "well enough to trust, so the gate fails.",
        "",
    ]
    if calibration is None:
        lines += [
            "No calibration labels were supplied. The gate is fail-closed: "
            "absent or unreadable labels count as a failure, not a skip.",
            "",
            f"Kappa gate at {KAPPA_GATE:.2f}: **FAIL** (no labels).",
        ]
    else:
        gate_word = "PASS" if calibration.passed else "FAIL"
        lines += [
            f"Cohen's kappa: **{calibration.kappa:.2f}** over {calibration.n_labels} labels.",
            "",
            f"Kappa gate at {calibration.threshold:.2f}: **{gate_word}** "
            f"(observed {calibration.kappa:.2f}).",
        ]
    return lines


#: Used when a ground-truth file documents no provenance of its own. It describes
#: the committed fixtures, which is what this repository's own evals run on.
DEFAULT_PROVENANCE = (
    "Numbers come from running the matcher on seeded synthetic fixtures with "
    "planted ground truth; there is no real personal data in the fixtures."
)


def render_eval_markdown(
    report: EvalReport,
    *,
    dataset: str,
    gate_threshold: float = 0.0,
    calibration: CalibrationReport | None = None,
    provenance: str | None = None,
) -> str:
    """Render the eval report.

    ``provenance`` describes where the scored data came from. It used to be a
    fixed sentence asserting the input was synthetic and contained no real
    personal data -- printed unconditionally, so pointing `reconcile eval` at a
    real dataset produced a report that stated the opposite of the truth about
    itself. On a tool whose whole discipline is provenance, a generated false
    provenance claim is the wrong default, so callers now pass what they know
    (the ground-truth file's own ``note`` is the natural source) and the
    fixture sentence is only the fallback.
    """
    gate_pass = report.false_merge_rate <= gate_threshold
    gate_word = "PASS" if gate_pass else "FAIL"
    provenance_text = (
        provenance.strip() if provenance and provenance.strip() else DEFAULT_PROVENANCE
    )

    lines = [
        "# Eval report",
        "",
        f"Dataset: `{dataset}`. Generated by `reconcile eval`. This file is "
        f"committed and regenerated on release. {provenance_text}",
        "",
        "## Why these metrics",
        "",
        "A false merge joins two different people and can corrupt a record "
        "irreversibly. A missed match leaves a duplicate, which is recoverable. "
        "The two errors are not equal, so the gated metric is the false-merge "
        "rate among auto-merged pairs, and it is reported with a Wilson "
        "confidence interval because the denominator is small.",
        "",
        "## Results",
        "",
        "| Metric | Value | 95% CI |",
        "|--------|-------|--------|",
        f"| Records | {report.n_records} | |",
        f"| True duplicate pairs (ground truth) | {report.n_true_pairs} | |",
        f"| Candidate pairs after blocking | {report.n_candidate_pairs} | |",
        f"| Auto-merged pairs | {report.n_auto} | |",
        f"| Pairs sent to review | {report.n_review} | |",
        f"| **False-merge rate (gated)** | **{_pct(report.false_merge_rate)}** "
        f"({report.false_merges}/{report.n_auto}) | {_ci(report.false_merge_ci)} |",
        f"| Missed-match rate | {_pct(report.missed_match_rate)} "
        f"({report.missed}/{report.n_true_pairs}) | {_ci(report.missed_match_ci)} |",
        f"| Precision, auto | {_pct(report.precision_auto)} | |",
        f"| Recall, auto | {_pct(report.recall_auto)} | |",
        f"| Precision, auto+review coverage | {_pct(report.precision_coverage)} | |",
        f"| Recall, auto+review coverage | {_pct(report.recall_coverage)} | |",
        f"| Blocking misses (true pairs never scored) | {report.blocking_misses} | |",
        "",
        "## Gate",
        "",
        f"False-merge gate at threshold {_pct(gate_threshold)}: **{gate_word}** "
        f"(observed {_pct(report.false_merge_rate)}).",
        "",
        "Recall at the auto level is intentionally below 100%: pairs the matcher "
        "is unsure about are not auto-merged, they are sent to review. The "
        "auto+review coverage recall is the share of true duplicates the system "
        "surfaces to a human one way or another.",
    ]
    if report.segments:
        lines += [
            "",
            "## Disaggregated error by documented risk class",
            "",
            "Each row is a planted true-duplicate pair representing a risk class "
            "called out in the model card. `Surfaced` includes both auto and review; "
            "blocking misses were never scored.",
            "",
            "| Risk class | True pairs | Surfaced | Missed | Coverage recall | Blocking misses |",
            "|------------|-----------:|----------:|-------:|----------------:|----------------:|",
        ]
        lines += [
            f"| {segment.name} | {segment.n_true_pairs} | {segment.n_surfaced} | "
            f"{segment.n_missed} | {_pct(segment.coverage_recall)} | "
            f"{segment.blocking_misses} |"
            for segment in report.segments
        ]
    lines += _calibration_lines(calibration)
    return "\n".join(lines) + "\n"


def render_extraction_markdown(
    report: ExtractionReport,
    *,
    dataset: str,
    precision_target: float = 0.95,
    recall_target: float = 0.90,
) -> str:
    precision_ok = report.precision >= precision_target
    recall_ok = report.recall >= recall_target
    verdict = "MET" if precision_ok and recall_ok else "NOT MET"

    lines = [
        "# Extraction eval report",
        "",
        f"Fixture set: `{dataset}`. Generated by `reconcile eval-extraction`. "
        "This file is committed and regenerated when the extractor or the "
        "labeled fixtures change. The fixtures are deterministic synthetic "
        "intake forms with hand-written ground-truth labels; there is no real "
        "personal data in them.",
        "",
        "## What is measured",
        "",
        "Field-level precision and recall of the offline PDF extractor. A "
        "predicted field is correct when its field name and normalized value "
        "match a labeled field in the same document, using the same "
        "normalizers the matching pipeline applies, so formatting differences "
        "do not count as errors. Unmatched predictions are false positives; "
        "unmatched labels are false negatives. This is a REVIEW metric in the "
        "metrics ledger, read by a person, and a test also holds the committed "
        "fixture at or above the ledger targets so the numbers cannot drift "
        "silently.",
        "",
        "## Results",
        "",
        "| Metric | Value | 95% CI |",
        "|--------|-------|--------|",
        f"| Documents | {report.n_docs} | |",
        f"| Labeled fields (ground truth) | {report.n_truth_fields} | |",
        f"| Predicted fields | {report.n_predicted_fields} | |",
        f"| Precision | {_pct(report.precision)} "
        f"({report.tp}/{report.tp + report.fp}) | {_ci(report.precision_ci)} |",
        f"| Recall | {_pct(report.recall)} "
        f"({report.tp}/{report.tp + report.fn}) | {_ci(report.recall_ci)} |",
        "",
        "## Per-field breakdown",
        "",
        "| Field | TP | FP | FN | Precision | Recall |",
        "|-------|----|----|----|-----------|--------|",
    ]
    for name, score in sorted(report.per_field.items()):
        lines.append(
            f"| {name} | {score.tp} | {score.fp} | {score.fn} "
            f"| {_pct(score.precision)} | {_pct(score.recall)} |"
        )
    lines += [
        "",
        "## Targets",
        "",
        f"Ledger targets: precision at least {_pct(precision_target)}, recall "
        f"at least {_pct(recall_target)}. Observed: precision "
        f"{_pct(report.precision)}, recall {_pct(report.recall)}. **{verdict}**.",
    ]
    if report.fn > 0:
        lines += [
            "",
            f"False negatives ({report.fn} here) are shown, not hidden: the "
            "fixture set deliberately includes at least one field the "
            "deterministic extractor is known not to parse (see the fixture "
            "README), so the measurement demonstrably catches a real miss "
            "rather than scoring a set the extractor is guaranteed to ace.",
        ]
    return "\n".join(lines) + "\n"
