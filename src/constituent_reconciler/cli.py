"""Command-line interface.

The subcommands: ``run`` produces resolved records and a review queue, ``eval``
scores a run against ground-truth clusters, ``eval-extraction`` scores the PDF
extractor against labeled fixtures, ``apply`` carries human review decisions
back into a fresh run, ``compare`` reports how two read-only exports line up
for a migration cutover, ``compare-review`` serves the same local web queue
over a comparison's undecided pairs, ``compare-apply`` exports the local
correction file once that review is complete, ``plan-split`` writes a
read-only repair plan for a written cluster a reviewer found to be a bad
merge, ``approve-repair`` records one reviewer's approval of a repair plan's
exact bytes, ``apply-repair`` applies a repair plan's verified operations to
the live destination (dry-run by default; ``--execute`` requires two distinct
recorded approvals), ``review`` serves the local web queue for a run,
``validate`` checks a recipe without running anything,
``destroy`` deletes retained artifacts, ``verify`` checks a provenance log's
hash chain, and ``schema`` prints the declared schema versions. The CLI uses
argparse only, so the package has no runtime dependency beyond the matcher.

``ai-explain``, ``ai-ask``, ``ai-propose-corrections``, and ``ai-triage`` are
the opt-in AI assistant surface (``constituent_reconciler.assistant``,
docs/adr/0014-runtime-ai-at-the-edges.md): every ``constituent_reconciler.
assistant`` import in this file is inside its ``_cmd_ai_*`` function body,
never at module level, so running any command above -- the offline-first
deterministic pipeline -- never imports the ``anthropic`` or ``boto3`` SDKs
and never calls a model provider. Output from every ``ai-*`` command is
always labeled AI-generated and advisory; none of them can write a merge
decision or apply a correction.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from constituent_reconciler import __version__, compare, compare_apply, pipeline, stage_cache

if TYPE_CHECKING:
    # Only for type hints: the deterministic pipeline commands never import
    # the assistant package or its provider/rate-limit types at runtime.
    from constituent_reconciler.assistant.provider import Provider
    from constituent_reconciler.assistant.rate_limit import RateLimiter
from constituent_reconciler.config import Recipe, RecipeError, load_recipe
from constituent_reconciler.connectors.base import ConnectorError
from constituent_reconciler.consent import partition_by_consent
from constituent_reconciler.destruction import destroy, parse_retention
from constituent_reconciler.evaluate import (
    KAPPA_GATE,
    CalibrationReport,
    calibrate,
    cohen_kappa,
    evaluate,
    extraction_metrics,
)
from constituent_reconciler.matching.evidence import PairEvidence, comparison_evidence
from constituent_reconciler.models import Correction, RunResult
from constituent_reconciler.narrative import LANGUAGES, render_narrative
from constituent_reconciler.pipeline import ExportSummary
from constituent_reconciler.policy import Policy, PolicyViolation, policy_for
from constituent_reconciler.progress import ConsoleProgressRenderer
from constituent_reconciler.provenance import ProvenanceLog, verify_log
from constituent_reconciler.quality import SourceQuality
from constituent_reconciler.report import (
    render_eval_markdown,
    render_extraction_markdown,
    render_run_summary,
    render_source_quality,
)
from constituent_reconciler.review import session as review_session
from constituent_reconciler.schema import REPORT_SCHEMA_VERSION
from constituent_reconciler.suppression import render_comparable, render_summary


def _load_pairs(data: dict[str, object], keys: Sequence[str]) -> list[frozenset[str]]:
    pairs: list[frozenset[str]] = []
    for key in keys:
        entries = data.get(key, [])
        if not isinstance(entries, list):
            raise ValueError(f"{key} must be a list of [left, right] pairs")
        for entry in entries:
            if not isinstance(entry, list) or len(entry) != 2:
                raise ValueError(f"{key} entries must be 2-element [left, right] lists")
            pairs.append(frozenset((str(entry[0]), str(entry[1]))))
    return pairs


def _load_household_ids(data: dict[str, object], key: str) -> frozenset[str]:
    entries = data.get(key, [])
    if not isinstance(entries, list):
        raise ValueError(f"{key} must be a list of household ids")
    return frozenset(str(entry) for entry in entries)


def _load_corrections(path: Path) -> list[Correction]:
    """Load the separately persisted, PII-bearing field corrections."""

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("corrections", []), list):
        raise ValueError("corrections file must contain a corrections list")
    corrections: list[Correction] = []
    for entry in data.get("corrections", []):
        if not isinstance(entry, dict):
            raise ValueError("corrections entries must be objects")
        left = str(entry.get("left", ""))
        right = str(entry.get("right", ""))
        side = str(entry.get("side", ""))
        field_name = str(entry.get("field", ""))
        value = str(entry.get("value", ""))
        reviewer = str(entry.get("reviewer", ""))
        corrected_at = str(entry.get("corrected_at", ""))
        if not left or not right:
            raise ValueError("correction must name both pair record ids")
        if side not in ("left", "right"):
            raise ValueError(f"correction side must be 'left' or 'right', got {side!r}")
        if not field_name or not value.strip() or not reviewer.strip() or not corrected_at.strip():
            raise ValueError(
                "correction requires field, non-blank value, reviewer, and corrected_at"
            )
        corrections.append(
            Correction(
                record_id=left if side == "left" else right,
                field=field_name,
                value=value,
                reviewer=reviewer,
                corrected_at=corrected_at,
                pair=frozenset((left, right)),
            )
        )
    return corrections


def _pairs_awaiting_second_review(data: dict[str, object]) -> list[str]:
    """Audit-trail pairs that are neither approved nor rejected yet.

    A version-2 decisions file holds every recorded verdict in its ``audit``
    section. A pair present there but absent from both top-level lists carries
    a single approval under two-person review; applying such a file must fail,
    naming the pairs, rather than silently skipping them.

    This check is derived from the file's shape and only sees approvals the
    writing session chose to hold back, which it does only when that session
    was itself in two-person mode. It is therefore a complement to, never a
    substitute for, ``review.session.approved_without_second_approval``, which
    the caller applies under a pack that requires two reviewers.
    """

    audit = data.get("audit")
    if not isinstance(audit, dict):
        return []
    decided = set(_load_pairs(data, ["approved", "rejected"]))
    return sorted(key for key in audit if frozenset(str(key).split("|")) not in decided)


def _print_export(recipe: Recipe, summary: ExportSummary, *, dry_run: bool) -> None:
    mode = "dry run, nothing written" if dry_run else "wrote"
    print(f"\nconnector '{recipe.output.connector}' ({mode}): {summary.describe()}")
    print(f"  review queue: {summary.review_path}")
    if summary.withheld_path:
        print(f"  withheld:     {summary.withheld_path}")
    if summary.household_path:
        print(
            f"  households:   {summary.household_path} "
            f"({len(summary.household_suggestions)} suggested, review before use)"
        )
    if summary.provenance_path:
        print(f"  provenance:   {summary.provenance_path} ({summary.logged} entries)")
    if summary.manifest_path:
        print(f"  manifest:     {summary.manifest_path}")
    if summary.aggregate is not None:
        if summary.aggregate_path:
            print(f"  aggregate:    {summary.aggregate_path}")
        print()
        print(render_summary(summary.aggregate))
    if summary.comparable is not None:
        if summary.comparable_path:
            print(f"  comparable:   {summary.comparable_path}")
        print()
        print(render_comparable(summary.comparable))
    if summary.data_quality:
        print()
        print(render_source_quality(summary.data_quality))


def _write_run_report(
    result: RunResult, out_dir: Path, *, data_quality: Sequence[SourceQuality] = ()
) -> Path:
    ingest = result.ingest
    path = out_dir / "run_report.json"
    payload = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "ingest": {
            "files_read": list(ingest.files_read),
            "files_skipped": [
                {"path": skipped.path, "reason": skipped.reason} for skipped in ingest.files_skipped
            ],
            "pages_extracted": ingest.pages_extracted,
            "pages_dropped": ingest.pages_dropped,
            "normalization_failures": ingest.normalization_failures,
        },
        # Per-source completeness, normalization failures, consent coverage,
        # and duplicate density (quality.py), suppressed under the active
        # policy's small-cell rules exactly as the aggregate summary is
        # (issue #96). Empty when the run had no sources to measure.
        "data_quality": [dataclasses.asdict(source) for source in data_quality],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        recipe = load_recipe(args.config, policy_pack=args.policy_pack, tsa_url=args.tsa_url)
    except PolicyViolation as error:
        print(f"policy error: {error}", file=sys.stderr)
        return 2
    out_dir = Path(args.out)
    # A dry run must not touch disk, so it also runs without the stage cache:
    # neither reading a stale entry nor writing a fresh one.
    cache = None if args.dry_run else stage_cache.for_recipe(recipe, out_dir)
    # Progress renders on stderr, keeping stdout as the summary channel; the
    # renderer detects a TTY itself and close() terminates a line an error
    # left open so the message that follows starts on its own line.
    progress = ConsoleProgressRenderer(sys.stderr)
    try:
        result = pipeline.run(recipe, cache=cache, progress=progress)
        _, withheld = partition_by_consent(
            result.golden,
            require_consent=recipe.require_consent,
            destination=recipe.output.connector,
        )
        print(render_run_summary(result, withheld=len(withheld)))
        try:
            summary = pipeline.export(
                result, recipe, out_dir=out_dir, dry_run=args.dry_run, progress=progress
            )
        except PolicyViolation as error:
            print(f"\npolicy error: {error}", file=sys.stderr)
            return 2
        except ConnectorError as error:
            print(f"\nconnector error: {error}", file=sys.stderr)
            return 2
    finally:
        progress.close()
    _print_export(recipe, summary, dry_run=args.dry_run)
    if not args.dry_run:
        report_path = _write_run_report(result, out_dir, data_quality=summary.data_quality)
        print(f"  run report:   {report_path}")
    return 0


def _load_calibration(path: Path | None) -> CalibrationReport | None:
    if path is None:
        return None
    if not path.is_file():
        print(f"calibration labels file not found: {path}", file=sys.stderr)
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("calibration labels file must be a JSON object")
        labels = payload.get("labels", [])
        if not isinstance(labels, list):
            raise ValueError("calibration labels must be a list")
        threshold = payload.get("threshold", KAPPA_GATE)
        if not isinstance(threshold, int | float):
            raise ValueError("calibration threshold must be numeric")
        return calibrate(labels, threshold=float(threshold))
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"calibration error: {error}", file=sys.stderr)
        return None


def _cmd_eval(args: argparse.Namespace) -> int:
    recipe = load_recipe(args.config)
    result = pipeline.run(recipe)
    truth = json.loads(Path(args.truth).read_text(encoding="utf-8"))
    clusters = truth.get("clusters", [])
    segments = truth.get("segments", {})
    report = evaluate(
        result.pairs,
        clusters,
        n_records=len(result.records),
        segments=segments,
    )
    calibration = _load_calibration(Path(args.calibration) if args.calibration else None)
    # resolve() first: a relative --config like "recipe.toml" has an empty parent,
    # which rendered the report's dataset label as an empty backtick pair.
    markdown = render_eval_markdown(
        report,
        dataset=Path(args.config).resolve().parent.name,
        gate_threshold=args.gate,
        calibration=calibration,
        # A truth file scoring anything other than the committed fixtures declares
        # so with an explicit "provenance" key. This is deliberately not `note`:
        # note describes how ground truth was constructed, which is a different
        # claim from where the scored records came from, and silently promoting
        # one to the other would rewrite every committed report.
        provenance=truth.get("provenance"),
    )
    if args.out:
        Path(args.out).write_text(markdown, encoding="utf-8")
        print(f"wrote eval report: {args.out}")
    else:
        print(markdown)
    gates_pass = (
        report.false_merge_rate <= args.gate and calibration is not None and calibration.passed
    )
    return 0 if gates_pass else 1


def _cmd_eval_extraction(args: argparse.Namespace) -> int:
    from constituent_reconciler.extract.base import ExtractedField
    from constituent_reconciler.extract.pdf import PdfplumberExtractor

    fixtures = Path(args.fixtures)
    labels_path = fixtures / "labels.json"
    if not labels_path.is_file():
        print(f"labels file not found: {labels_path}", file=sys.stderr)
        return 2
    labels = json.loads(labels_path.read_text(encoding="utf-8"))

    pdf_paths = sorted(fixtures.glob("*.pdf"))
    if not pdf_paths:
        print(f"no fixture PDFs found in: {fixtures}", file=sys.stderr)
        return 2

    extractor = PdfplumberExtractor()
    predicted: dict[str, list[ExtractedField]] = {}
    try:
        for pdf_path in pdf_paths:
            result = extractor.extract(pdf_path)
            predicted[pdf_path.name] = [field for page in result.pages for field in page.fields]
    except ImportError as error:
        print(f"extraction error: {error}", file=sys.stderr)
        return 2

    report = extraction_metrics(predicted, labels)
    markdown = render_extraction_markdown(
        report,
        dataset=fixtures.name,
        precision_target=args.precision_target,
        recall_target=args.recall_target,
    )
    if args.out:
        Path(args.out).write_text(markdown, encoding="utf-8")
        print(f"wrote extraction eval report: {args.out}")
    else:
        print(markdown)
    met = report.precision >= args.precision_target and report.recall >= args.recall_target
    return 0 if met else 1


def _load_json_object(path: Path) -> dict[str, object]:
    data: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return {str(key): value for key, value in data.items()}


def _cmd_report(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir)
    summary_path = run_dir / "run_summary.json"
    aggregate_path = run_dir / "aggregate_summary.json"
    if not summary_path.is_file():
        print(f"report error: run summary not found: {summary_path}", file=sys.stderr)
        return 2
    try:
        result_summary = _load_json_object(summary_path)
        aggregate = _load_json_object(aggregate_path) if aggregate_path.is_file() else None
        markdown = render_narrative(result_summary, aggregate, lang=args.lang)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"report error: {error}", file=sys.stderr)
        return 2

    if args.out:
        Path(args.out).write_text(markdown, encoding="utf-8")
        print(f"wrote narrative report: {args.out}")
    else:
        print(markdown, end="")
    return 0


def _refuse_incomplete_review(
    recipe: Recipe, decisions_path: Path, data: dict[str, object]
) -> bool:
    """Whether the review behind this decisions file is too thin to apply.

    Two checks, both fail-closed, both printing what has to happen next.

    The first is derived from the file's shape: a pair recorded in ``audit``
    but in neither top-level list is a single approval a two-person session
    held back, and applying it would drop the verdict silently.

    The second runs only under a pack that requires two reviewers, and is a
    positive count of distinct approvers on the pairs that did reach
    ``approved``. It is not redundant with the first: a session held nothing
    back unless it was itself in two-person mode, so a decisions file reviewed
    under a permissive pack (or written by hand, or carried over from
    schema version 1) lists every single approval as fully approved and gives
    the first check nothing to notice. Without this count, ``reconcile apply
    --policy-pack dv`` would merge pairs one person approved.
    """

    awaiting = _pairs_awaiting_second_review(data)
    if awaiting:
        print(
            f"error: {decisions_path} holds {len(awaiting)} pair(s) still awaiting "
            "a second reviewer; they cannot be applied:",
            file=sys.stderr,
        )
        for key in awaiting:
            print(f"  {key.replace('|', ' and ')}", file=sys.stderr)
        print(
            "Have a second reviewer finish the review "
            "(reconcile review --reviewer <other-name>), or reject the pairs.",
            file=sys.stderr,
        )
        return True
    if not recipe.require_second_reviewer:
        return False
    unconfirmed = review_session.approved_without_second_approval(data)
    if not unconfirmed:
        return False
    print(
        f"error: policy pack {recipe.policy_pack!r} requires two reviewers, but "
        f"{decisions_path} cannot show two distinct approvers for "
        f"{len(unconfirmed)} approved pair(s):",
        file=sys.stderr,
    )
    for key in unconfirmed:
        print(f"  {key.replace('|', ' and ')}", file=sys.stderr)
    print(
        "Have a second reviewer review these pairs under this pack "
        "(reconcile review --config <this recipe> --reviewer <other-name>). "
        "A decisions file that records no reviewer attribution cannot be "
        "applied under this pack at all.",
        file=sys.stderr,
    )
    return True


def _cmd_apply(args: argparse.Namespace) -> int:
    try:
        recipe = load_recipe(args.config, policy_pack=args.policy_pack, tsa_url=args.tsa_url)
    except PolicyViolation as error:
        print(f"policy error: {error}", file=sys.stderr)
        return 2
    decisions_path = Path(args.decisions)
    decisions_data = json.loads(decisions_path.read_text(encoding="utf-8"))
    if not isinstance(decisions_data, dict):
        print(f"error: {decisions_path} is not a decisions JSON object", file=sys.stderr)
        return 2
    if _refuse_incomplete_review(recipe, decisions_path, decisions_data):
        return 2
    force_auto = _load_pairs(decisions_data, ["approved"])
    force_drop = _load_pairs(decisions_data, ["rejected"])
    corrections_path = (
        Path(args.corrections) if args.corrections else decisions_path.parent / "corrections.json"
    )
    try:
        corrections = _load_corrections(corrections_path) if corrections_path.exists() else []
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"error: invalid corrections file {corrections_path}: {error}", file=sys.stderr)
        return 2
    orphan = next(
        (correction.pair for correction in corrections if correction.pair not in force_auto),
        None,
    )
    if orphan is not None:
        print(
            f"error: correction for {sorted(orphan)!r} is not attached to a fully approved pair",
            file=sys.stderr,
        )
        return 2
    # "households_confirmed" is a list of household ids copied from an earlier
    # run's household_suggestions.csv by a reviewer; a suggestion never applies
    # to the CRM export column until it appears here (household.py).
    confirmed_households = _load_household_ids(decisions_data, "households_confirmed")
    # Same progress surface as `run`: apply executes the same stages, so it
    # emits the same events, rendered on stderr.
    progress = ConsoleProgressRenderer(sys.stderr)
    try:
        result = pipeline.run(
            recipe,
            force_auto=force_auto,
            force_drop=force_drop,
            corrections=corrections,
            cache=stage_cache.for_recipe(recipe, Path(args.out)),
            progress=progress,
        )
        _, withheld = partition_by_consent(
            result.golden,
            require_consent=recipe.require_consent,
            destination=recipe.output.connector,
        )
        print(render_run_summary(result, withheld=len(withheld)))
        try:
            summary = pipeline.export(
                result,
                recipe,
                out_dir=Path(args.out),
                dry_run=False,
                confirmed_households=confirmed_households,
                progress=progress,
            )
        except PolicyViolation as error:
            print(f"\npolicy error: {error}", file=sys.stderr)
            return 2
        except ConnectorError as error:
            print(f"\nconnector error: {error}", file=sys.stderr)
            return 2
    finally:
        progress.close()
    _print_export(recipe, summary, dry_run=False)
    return 0


def _cmd_compare(args: argparse.Namespace) -> int:
    """Compare two read-only exports for a migration cutover (UC-02).

    Both sides are sources; no connector is constructed on any path of this
    command, and ``tests/test_compare.py`` holds that as an invariant. The
    artifacts are all local: the cutover report and review pairs (field
    values, PII), the count-only migration summary, and the comparison
    manifest binding both recipes and input digests.
    """

    try:
        left = compare.load_side(args.left, label="left")
        right = compare.load_side(args.right, label="right")
        result = compare.run_compare(left, right)
    except (compare.CompareError, RecipeError, OSError) as error:
        print(f"compare error: {error}", file=sys.stderr)
        return 2
    except PolicyViolation as error:
        print(f"policy error: {error}", file=sys.stderr)
        return 2

    out_dir = Path(args.out)
    report_path = compare.write_cutover_report(result, out_dir)
    review_path = compare.write_cutover_review(result, out_dir)
    summary_path = compare.write_migration_summary(result, out_dir)
    manifest_path = compare.write_compare_manifest(
        compare.build_compare_manifest(left, right, result), out_dir
    )
    print(compare.render_compare_summary(result))
    print(f"\n  cutover report:  {report_path}")
    print(f"  review pairs:    {review_path}")
    print(f"  count summary:   {summary_path}")
    print(f"  manifest:        {manifest_path}")
    return 0


def _cmd_compare_review(args: argparse.Namespace) -> int:
    """Serve the local review queue over a comparison's undecided pairs.

    The session, queue, and server are the same surfaces ``reconcile review``
    uses; only the pairs come from the comparison. Verdicts save to the
    compare decisions file so ``reconcile compare-apply`` can enforce that
    every uncertain pair was decided before the correction file exists.
    """

    from constituent_reconciler.review.server import serve
    from constituent_reconciler.review.session import ReviewSession

    try:
        left = compare.load_side(args.left, label="left")
        right = compare.load_side(args.right, label="right")
        result = compare.run_compare(left, right)
    except (compare.CompareError, RecipeError, OSError) as error:
        print(f"compare error: {error}", file=sys.stderr)
        return 2
    except PolicyViolation as error:
        print(f"policy error: {error}", file=sys.stderr)
        return 2
    print(compare.render_compare_summary(result))
    if not result.review_pairs:
        print(
            "\nno undecided pairs: this comparison has nothing to review, and "
            "reconcile compare-apply may export without a review step"
        )
        return 0
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    decisions_path = (
        Path(args.decisions)
        if args.decisions
        else out_dir / compare_apply.COMPARE_DECISIONS_FILENAME
    )
    # Either side's pack can require two-person review or a local-only server;
    # the stricter side governs, fail-closed, and the flag can only add.
    require_second = (
        bool(args.require_second_reviewer)
        or left.recipe.require_second_reviewer
        or right.recipe.require_second_reviewer
    )
    privacy = left.recipe.require_local_targets or right.recipe.require_local_targets
    try:
        session = ReviewSession(
            compare.as_run_result(result),
            result.fields,
            decisions_path,
            reviewer=args.reviewer,
            privacy_mode=privacy,
            require_second_reviewer=require_second,
        )
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    try:
        serve(
            session,
            host=args.host,
            port=args.port,
            open_browser=not args.no_browser,
        )
    except PolicyViolation as error:
        print(f"\npolicy error: {error}", file=sys.stderr)
        return 2
    counts = session.counts()
    line = (
        f"\nreview saved to {decisions_path}: "
        f"{counts.approved} approved, {counts.rejected} rejected, {counts.pending} pending"
    )
    if counts.awaiting_second:
        line += f", {counts.awaiting_second} awaiting a second reviewer"
    print(line)
    return 0


def _cmd_compare_apply(args: argparse.Namespace) -> int:
    """Export the reviewed, consent-gated correction file for the target side.

    Refuses while any review pair is undecided, when the comparison manifest
    is missing or no longer matches the inputs, or when the decisions file
    belongs to a different comparison. Writes only local files; no connector
    is constructed on any path of this command.
    """

    out_dir = Path(args.out)
    decisions_path = (
        Path(args.decisions)
        if args.decisions
        else out_dir / compare_apply.COMPARE_DECISIONS_FILENAME
    )
    corrections_path = (
        Path(args.corrections) if args.corrections else decisions_path.parent / "corrections.json"
    )
    try:
        corrections = _load_corrections(corrections_path) if corrections_path.exists() else []
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(
            f"compare error: invalid corrections file {corrections_path}: {error}", file=sys.stderr
        )
        return 2
    try:
        left = compare.load_side(args.left, label="left")
        right = compare.load_side(args.right, label="right")
        result = compare.run_compare(left, right, corrections=corrections)
        stored_manifest = compare_apply.verify_compare_manifest(out_dir, left, right, result)
        approved, rejected = compare_apply.read_decisions(
            decisions_path,
            result,
            # The stricter side governs, the same rule compare-review applies
            # when it decides whether to hold a lone approval back.
            require_second_reviewer=compare_apply.requires_second_reviewer(left, right),
        )
        orphan = next(
            (correction.pair for correction in corrections if correction.pair not in approved),
            None,
        )
        if orphan is not None:
            raise compare.CompareError(
                f"correction for {sorted(orphan)!r} is not attached to a fully approved pair"
            )
        applied = compare_apply.apply_review(result, approved, rejected)
        export = compare_apply.export_corrections(
            left,
            right,
            applied,
            out_dir,
            fmt=args.format,
            stored_manifest=stored_manifest,
            decisions_path=decisions_path,
            corrections_path=corrections_path if corrections_path.exists() else None,
        )
    except (compare.CompareError, RecipeError, OSError) as error:
        print(f"compare error: {error}", file=sys.stderr)
        return 2
    except PolicyViolation as error:
        print(f"policy error: {error}", file=sys.stderr)
        return 2
    print(compare_apply.render_export_summary(export))
    print(f"\n  correction file: {export.path} (format: {export.format})")
    if export.withheld_path:
        print(f"  withheld:        {export.withheld_path}")
    print(f"  manifest:        {export.manifest_path}")
    print(
        "\nThis file is local. Nothing was sent to either live system; load it "
        "with the target CRM's own import tool."
    )
    return 0


def _cmd_plan_split(args: argparse.Namespace) -> int:
    """Plan the repair of one written cluster, read-only (UC-03, ADR 0012).

    Everything printed here is ids, counts, paths, and hashes. The raw field
    values a restoration needs live only in the local plan file, which
    ``reconcile destroy`` covers and the provenance log references by digest.
    """

    from constituent_reconciler import repair

    try:
        recipe = load_recipe(args.config, policy_pack=args.policy_pack)
    except (RecipeError, PolicyViolation) as error:
        print(f"plan-split error: {error}", file=sys.stderr)
        return 2
    manifest_path = Path(args.manifest)
    decisions_path = (
        Path(args.decisions) if args.decisions else manifest_path.parent / "decisions.json"
    )
    corrections_path = (
        Path(args.corrections) if args.corrections else decisions_path.parent / "corrections.json"
    )
    # An explicit --corrections path asserts the written run applied
    # corrections. The lineage check cannot see a correction that changed a
    # value without changing which member supplied it, so degrading to "no
    # corrections" here would plan stale restoration values with a clean exit.
    # Only the default location may be probed for existence.
    if args.corrections and not corrections_path.exists():
        print(
            f"plan-split error: corrections file not found: {corrections_path}; "
            "planning cannot replay corrections from a missing file, so fix the "
            "path or omit --corrections",
            file=sys.stderr,
        )
        return 2
    try:
        corrections = _load_corrections(corrections_path) if corrections_path.exists() else []
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(
            f"plan-split error: invalid corrections file {corrections_path}: {error}",
            file=sys.stderr,
        )
        return 2
    try:
        planned = repair.plan_split(
            recipe,
            manifest_path=manifest_path,
            cluster_id=args.cluster,
            reason=args.reason,
            reviewer=args.reviewer,
            corrections=corrections,
            decisions_path=decisions_path,
        )
    except repair.RepairPlanError as error:
        print(f"plan-split error: {error}", file=sys.stderr)
        return 2
    print(f"repair plan: {planned.plan_path}")
    print(f"  cluster:      {planned.cluster_id} ({len(planned.members)} members)")
    print(f"  external id:  {planned.external_id}")
    if planned.supported_operations:
        print(
            f"  destination:  {planned.destination} "
            f"(verified operations: {', '.join(planned.supported_operations)})"
        )
    else:
        print(
            f"  destination:  {planned.destination} "
            "(no verified repair operations; the plan is manual)"
        )
    print(f"  plan digest:  {planned.digest} (recorded in the provenance log)")
    print(f"  cannot-links: {len(planned.cannot_links)} pair(s) bound in {planned.decisions_path}")
    if planned.displaced_cluster is not None:
        print(
            f"warning: {planned.plan_path} previously held the plan for cluster "
            f"{planned.displaced_cluster!r}; that plan was replaced and must be "
            "regenerated with plan-split before its repair continues",
            file=sys.stderr,
        )
    print("planning is read-only: nothing was sent to or changed in the destination.")
    return 0


def _cmd_approve_repair(args: argparse.Namespace) -> int:
    """Record one reviewer's approval of the exact bytes of a repair plan.

    Touches no destination and needs no recipe: it binds a reviewer identity
    and a timestamp to the plan file's digest (ADR 0012). ``apply-repair
    --execute`` refuses until two distinct identities have approved that
    exact digest.
    """

    from constituent_reconciler import repair

    plan_path = Path(args.plan)
    approvals_path = (
        Path(args.approvals)
        if args.approvals
        else plan_path.parent / repair.REPAIR_APPROVALS_FILENAME
    )
    try:
        digest, approvers = repair.record_repair_approval(
            plan_path, approvals_path, reviewer=args.reviewer, verdict=args.verdict
        )
    except repair.RepairApplyError as error:
        print(f"approve-repair error: {error}", file=sys.stderr)
        return 2
    print(f"recorded: {args.verdict} by {args.reviewer!r} for plan digest {digest}")
    print(f"  approvals file: {approvals_path}")
    print(
        f"  distinct approvers of this exact plan digest: {len(approvers)} "
        f"({', '.join(sorted(approvers)) or '-'})"
    )
    if args.verdict == repair.APPROVED_VERDICT and len(approvers) < repair.MINIMUM_APPLY_APPROVERS:
        remaining = repair.MINIMUM_APPLY_APPROVERS - len(approvers)
        print(f"  {remaining} more distinct approver(s) needed before apply-repair --execute runs.")
    return 0


def _cmd_apply_repair(args: argparse.Namespace) -> int:
    """Apply one repair plan's verified operations, gated by two reviewers.

    Dry-run (the default; omit ``--execute``) makes no network call and
    needs no credential: it previews what execution would do from the
    plan's own bytes. ``--execute`` requires two distinct recorded approvals
    of this exact plan digest and a live repair declaration covering the
    destination's current version (ADR 0012); either gap refuses before any
    connector is constructed.
    """

    from constituent_reconciler import repair

    try:
        recipe = load_recipe(args.config, policy_pack=args.policy_pack)
    except (RecipeError, PolicyViolation) as error:
        print(f"apply-repair error: {error}", file=sys.stderr)
        return 2
    manifest_path = Path(args.manifest)
    out_dir = manifest_path.parent
    plan_path = Path(args.plan) if args.plan else out_dir / repair.REPAIR_PLAN_FILENAME
    approvals_path = (
        Path(args.approvals) if args.approvals else out_dir / repair.REPAIR_APPROVALS_FILENAME
    )
    corrections_path = Path(args.corrections) if args.corrections else out_dir / "corrections.json"
    if args.corrections and not corrections_path.exists():
        print(
            f"apply-repair error: corrections file not found: {corrections_path}; "
            "the consent check cannot replay corrections from a missing file, so fix the "
            "path or omit --corrections",
            file=sys.stderr,
        )
        return 2
    try:
        corrections = _load_corrections(corrections_path) if corrections_path.exists() else []
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(
            f"apply-repair error: invalid corrections file {corrections_path}: {error}",
            file=sys.stderr,
        )
        return 2
    try:
        applied = repair.apply_repair_plan(
            recipe,
            manifest_path=manifest_path,
            plan_path=plan_path,
            approvals_path=approvals_path,
            corrections=corrections,
            dry_run=not args.execute,
        )
    except (repair.RepairApplyError, PolicyViolation, ConnectorError) as error:
        print(f"apply-repair error: {error}", file=sys.stderr)
        return 2
    version = f" {applied.destination_version}" if applied.destination_version else ""
    print(
        f"{'dry run' if applied.dry_run else 'applied'}: cluster {applied.cluster_id} "
        f"-> {applied.destination}{version}"
    )
    for result in applied.operations:
        detail = f" ({result.detail})" if result.detail else ""
        field = f" {result.field}" if result.field else ""
        print(f"  {result.operation}{field} {result.record_id}: {result.action}{detail}")
    if applied.dry_run:
        print("dry run: no network call was made; nothing was written to the destination.")
        print(
            "re-run with --execute once two distinct reviewers have approved this plan "
            f"digest ({applied.plan_digest}) via `reconcile approve-repair`."
        )
    else:
        print(f"  approvers: {', '.join(applied.approvers)}")
        if applied.receipts_path is not None:
            print(f"  receipts:  {applied.receipts_path}")
    return 0


def _cmd_export_comparable(args: argparse.Namespace) -> int:
    """One command: resolve, then emit only the suppressed comparable report.

    No connector is built and no resolved record is written; the CoC-shaped
    ``comparable_report.json`` is the only artifact.
    """

    try:
        recipe = load_recipe(args.config, policy_pack=args.policy_pack)
    except PolicyViolation as error:
        print(f"policy error: {error}", file=sys.stderr)
        return 2
    result = pipeline.run(recipe)
    try:
        report, report_path = pipeline.export_comparable(result, recipe, out_dir=Path(args.out))
    except PolicyViolation as error:
        print(f"policy error: {error}", file=sys.stderr)
        return 2
    print(render_comparable(report))
    print(f"\ncomparable report: {report_path}")
    return 0


def _calibration_summary(session: object) -> str:
    """Summarize reviewer agreement for the planted pairs they decided."""

    reviewer_verdicts, known_answers = session.calibration_results()  # type: ignore[attr-defined]
    decided = len(reviewer_verdicts)
    if decided == 0:
        return "calibration: no planted pair was decided, so there is no agreement to report"
    agreed = sum(v == a for v, a in zip(reviewer_verdicts, known_answers, strict=True))
    line = f"calibration: {agreed} of {decided} decided planted pair(s) matched the known answer"
    if decided >= 2:
        kappa = cohen_kappa(reviewer_verdicts, known_answers)
        line += f"; reviewer agreement (Cohen's kappa) {kappa:.2f}"
    else:
        line += "; kappa needs at least 2 decided planted pairs"
    return line


def _cmd_review(args: argparse.Namespace) -> int:
    from constituent_reconciler.review.calibration import generate_calibration_pairs
    from constituent_reconciler.review.server import serve
    from constituent_reconciler.review.session import ReviewSession

    try:
        recipe = load_recipe(args.config, policy_pack=args.policy_pack)
    except PolicyViolation as error:
        print(f"policy error: {error}", file=sys.stderr)
        return 2
    result = pipeline.run(recipe)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    decisions_path = Path(args.decisions) if args.decisions else out_dir / "decisions.json"
    # The flag may turn two-person review on for any pack; it cannot turn off
    # a pack that requires it (the dv pack defaults it on), fail-closed.
    require_second = bool(args.require_second_reviewer) or recipe.require_second_reviewer
    calibration = generate_calibration_pairs(recipe.review_calibration, recipe.fields)
    try:
        session = ReviewSession(
            result,
            recipe.fields,
            decisions_path,
            reviewer=args.reviewer,
            privacy_mode=recipe.require_local_targets,
            require_second_reviewer=require_second,
            calibration=calibration,
        )
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    print(render_run_summary(result))
    if calibration:
        print(
            f"calibration: {len(calibration)} planted known-answer pair(s) are mixed "
            "into the queue; the reviewer is told, and they are never applied to records"
        )
    try:
        serve(
            session,
            host=args.host,
            port=args.port,
            open_browser=not args.no_browser,
        )
    except PolicyViolation as error:
        print(f"\npolicy error: {error}", file=sys.stderr)
        return 2
    counts = session.counts()
    line = (
        f"\nreview saved to {decisions_path}: "
        f"{counts.approved} approved, {counts.rejected} rejected, {counts.pending} pending"
    )
    if counts.awaiting_second:
        line += f", {counts.awaiting_second} awaiting a second reviewer"
    print(line)
    if calibration:
        print(_calibration_summary(session))
    return 0


def _cmd_validate(args: argparse.Namespace) -> int:
    """Load and shape-check a recipe, and report its active switches.

    Runs nothing: no ingest, no matcher, no connector. Meant as the first step
    of the adoption-kit flow (E8) and as the fast way to catch a typo'd section
    or key (FIX-04) before a run.
    """

    config_path = Path(args.config)
    try:
        recipe = load_recipe(config_path, policy_pack=args.policy_pack)
    except RecipeError as error:
        print(f"invalid recipe: {error}", file=sys.stderr)
        return 2
    except PolicyViolation as error:
        print(f"policy error: {error}", file=sys.stderr)
        return 2

    problems: list[str] = []
    if not recipe.incoming.exists():
        problems.append(f"input.incoming does not exist: {recipe.incoming}")
    if recipe.existing is not None and not recipe.existing.exists():
        problems.append(f"input.existing does not exist: {recipe.existing}")

    print(f"recipe: {config_path}")
    print(f"  incoming: {recipe.incoming}")
    if recipe.existing is not None:
        print(f"  existing: {recipe.existing}")
    print(f"  mapped fields: {', '.join(recipe.fields)}")
    print(f"  policy pack: {recipe.policy_pack}")
    print(
        "  switches: "
        f"require_consent={recipe.require_consent}, "
        f"require_local_targets={recipe.require_local_targets}, "
        f"aggregate_export={recipe.aggregate_export}, "
        f"household.enabled={recipe.household.enabled}"
    )
    print(
        "  thresholds: "
        f"prior={recipe.prior}, auto={recipe.auto_threshold}, review={recipe.review_threshold}"
    )
    print(f"  extract backend: {recipe.extract.backend}")
    print(f"  address backend: {recipe.normalize.address_backend}")
    print(f"  output connector: {recipe.output.connector}")
    if recipe.cache.enabled:
        boundary = (
            str(recipe.cache.dir)
            if recipe.cache.dir is not None
            else "stage_cache under the output root"
        )
        print(f"  cache: enabled=True ({boundary})")
    else:
        print("  cache: enabled=False")

    if problems:
        print("\nproblems:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 2

    print("\nrecipe is valid.")
    return 0


def _cmd_destroy(args: argparse.Namespace) -> int:
    out_dir = Path(args.out)
    if not out_dir.is_dir():
        print(f"destroy error: no such output directory: {out_dir}", file=sys.stderr)
        return 2
    try:
        older_than = parse_retention(args.older_than)
    except ValueError as error:
        print(f"destroy error: {error}", file=sys.stderr)
        return 2
    cache_dir = Path(args.cache_dir) if args.cache_dir else None
    log = ProvenanceLog(out_dir / "provenance.jsonl")
    try:
        summary = destroy(
            out_dir,
            older_than,
            policy=args.older_than,
            log=log,
            dry_run=args.dry_run,
            cache_dir=cache_dir,
        )
    except ValueError as error:
        # A refusal (a --cache-dir without the stage-cache shape, or the
        # provenance log on the candidate list) happens before any deletion.
        print(f"destroy error: {error}", file=sys.stderr)
        return 2
    if args.dry_run:
        for name in summary.candidates:
            print(f"would destroy: {name}")
        print(
            f"\ndry run: {len(summary.candidates)} artifact(s) eligible under "
            f"--older-than {summary.policy}; nothing deleted, nothing logged"
        )
        return 0
    for artifact in summary.destroyed:
        print(f"destroyed: {artifact.name} (sha256 {artifact.sha256}, {artifact.size} bytes)")
    print(f"\ndestroyed {len(summary.destroyed)} artifact(s) under --older-than {summary.policy}")
    if summary.destroyed:
        print(f"  certificates: {log.path}")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    ok, message = verify_log(Path(args.provenance))
    print(message)
    return 0 if ok else 1


def _cmd_schema(args: argparse.Namespace) -> int:
    from constituent_reconciler.schema import versions

    for name, version in versions().items():
        print(f"{name}: {version}")
    return 0


def _ai_pair_evidence(
    result: RunResult, recipe: Recipe, left_id: str, right_id: str
) -> PairEvidence | None:
    """Resolve two record ids to their real Splink comparison evidence, or None."""

    if left_id not in result.records or right_id not in result.records:
        return None
    evidence_map = comparison_evidence(
        result.records.values(), recipe.fields, [(left_id, right_id)]
    )
    return evidence_map.get((left_id, right_id))


def _ai_withheld_fields(
    result: RunResult, recipe: Recipe, policy: Policy, *ids: str
) -> tuple[str, ...]:
    from constituent_reconciler.assistant import filter_record

    withheld: set[str] = set()
    for record_id in ids:
        filtered = filter_record(result.records[record_id], policy=policy, fields=recipe.fields)
        withheld.update(filtered.withheld_fields())
    return tuple(sorted(withheld))


def _ai_load_recipe_and_policy(args: argparse.Namespace) -> tuple[Recipe, Policy] | int:
    """Load the recipe and confirm the AI assistant is allowed under its policy pack.

    Every ``_cmd_ai_*`` command starts with
    ``setup = _ai_load_recipe_and_policy(args); if isinstance(setup, int): return setup``.
    Shared so the dv/hipaa cloud gate (``assert_cloud_ai_allowed``) is checked
    in exactly one place, not reimplemented per command.
    """
    from constituent_reconciler.assistant import assert_cloud_ai_allowed

    try:
        recipe = load_recipe(args.config, policy_pack=args.policy_pack)
    except PolicyViolation as error:
        print(f"policy error: {error}", file=sys.stderr)
        return 2
    policy = policy_for(recipe.policy_pack)
    try:
        assert_cloud_ai_allowed(policy)
    except PolicyViolation as error:
        print(f"policy error: {error}", file=sys.stderr)
        return 2
    return recipe, policy


def _ai_provider_or_error(args: argparse.Namespace) -> Provider | int:
    """Construct the configured provider, or report why none is available.

    Does not itself apply the rate limit: a caller that will make more than
    one provider call (``ai-propose-corrections``, one call per field) must
    call ``RateLimiter.check_and_record()`` before each individual call, not
    once for the whole command.
    """
    from constituent_reconciler.assistant import make_provider

    provider = make_provider(name=args.ai_provider, model=args.ai_model)
    if not provider.is_enabled():
        print(
            "error: no AI provider is configured "
            "(set ANTHROPIC_API_KEY, or pass --ai-provider bedrock with AWS credentials)",
            file=sys.stderr,
        )
        return 2
    return provider


def _ai_rate_limiter(out_dir: Path) -> RateLimiter:
    from constituent_reconciler.assistant.rate_limit import RateLimiter

    return RateLimiter(state_path=out_dir / "ai_usage.json")


def _cmd_ai_explain(args: argparse.Namespace) -> int:
    """AI-GENERATED, ADVISORY: explain one pair's real comparison evidence.

    The model narrates the field-by-field evidence Splink already computed;
    it never re-scores, and nothing here changes a match probability, a
    band, or a decision. Every per-field claim is checked against the real
    evidence before display; an unverifiable claim is withheld and counted,
    not shown. See docs/adr/0014-runtime-ai-at-the-edges.md.
    """
    from constituent_reconciler.assistant import explain_match
    from constituent_reconciler.assistant.errors import AssistantError

    setup = _ai_load_recipe_and_policy(args)
    if isinstance(setup, int):
        return setup
    recipe, policy = setup

    result = pipeline.run(recipe)
    left_id, right_id = sorted(args.pair)
    pair_evidence = _ai_pair_evidence(result, recipe, left_id, right_id)
    if pair_evidence is None:
        print(
            f"error: no real comparison evidence for ({left_id}, {right_id}) -- unknown "
            "id, or Splink's own blocking rules never scored this pair",
            file=sys.stderr,
        )
        return 2
    withheld = _ai_withheld_fields(result, recipe, policy, left_id, right_id)

    try:
        provider = _ai_provider_or_error(args)
        if isinstance(provider, int):
            return provider
        _ai_rate_limiter(Path(args.out)).check_and_record()
        explanation = explain_match(pair_evidence, provider=provider, withheld_fields=withheld)
    except AssistantError as error:
        print(f"AI error: {error}", file=sys.stderr)
        return 2

    print("AI-GENERATED, ADVISORY -- narrates real evidence only; never a merge decision")
    print(f"pair: {explanation.left_id} / {explanation.right_id}")
    print(f"match probability: {explanation.match_probability:.3f}\n")
    print(explanation.summary)
    for claim in explanation.claims:
        if claim.verified:
            print(f"  [{claim.field}] {claim.narrative}")
    if explanation.withheld_claim_count():
        print(
            f"\n({explanation.withheld_claim_count()} claim(s) withheld: "
            "could not be verified against real evidence)"
        )
    if withheld:
        print(f"withheld from the model by consent/policy: {', '.join(withheld)}")
    print(
        f"\nprovider={explanation.provider} model={explanation.model} "
        f"prompt_version={explanation.prompt_version}"
    )
    return 0


def _cmd_ai_ask(args: argparse.Namespace) -> int:
    """AI-GENERATED, ADVISORY: answer a grounded question about one pair.

    Refuses, by design and by a deterministic scanner on the response, to
    ever recommend a merge, claim two records are the same person, or tell
    a reviewer which to keep. See eval/ai/adversarial_refusal.py, the
    zero-tolerance eval this surface is held to.
    """
    from constituent_reconciler.assistant import ask
    from constituent_reconciler.assistant.errors import AssistantError

    setup = _ai_load_recipe_and_policy(args)
    if isinstance(setup, int):
        return setup
    recipe, policy = setup

    result = pipeline.run(recipe)
    left_id, right_id = sorted(args.pair)
    pair_evidence = _ai_pair_evidence(result, recipe, left_id, right_id)
    if pair_evidence is None:
        print(f"error: no real comparison evidence for ({left_id}, {right_id})", file=sys.stderr)
        return 2
    withheld = _ai_withheld_fields(result, recipe, policy, left_id, right_id)

    try:
        provider = _ai_provider_or_error(args)
        if isinstance(provider, int):
            return provider
        _ai_rate_limiter(Path(args.out)).check_and_record()
        response = ask(
            args.question, evidence=pair_evidence, provider=provider, withheld_fields=withheld
        )
    except AssistantError as error:
        print(f"AI error: {error}", file=sys.stderr)
        return 2

    print("AI-GENERATED, ADVISORY -- never a merge decision")
    print(f"\nQ: {response.question}\nA: {response.answer}\n")
    if response.scrubbed:
        print("(this response was withheld by the safety scanner and replaced with a redirect)")
    print(
        f"provider={response.provider} model={response.model} "
        f"prompt_version={response.prompt_version}"
    )
    return 0


def _cmd_ai_propose_corrections(args: argparse.Namespace) -> int:
    """AI-GENERATED DRAFT, never applied: propose quote-bound OCR corrections.

    Every accepted proposal quotes the exact source-document text it is
    based on, verified as an exact substring before it is ever shown.
    Nothing here writes to a record or to ``out/corrections.json``; the
    output is a draft file a human reviews, and turning an accepted
    proposal into a real correction still goes through the ordinary
    review-and-correction path.
    """
    from constituent_reconciler.assistant import filter_record, propose_correction
    from constituent_reconciler.assistant.errors import AssistantError
    from constituent_reconciler.assistant.source_text import for_field

    setup = _ai_load_recipe_and_policy(args)
    if isinstance(setup, int):
        return setup
    recipe, policy = setup

    result = pipeline.run(recipe)
    record = result.records.get(args.record)
    if record is None:
        print(f"error: unknown record id {args.record!r}", file=sys.stderr)
        return 2
    fields = tuple(args.field) if args.field else recipe.fields
    filtered = filter_record(record, policy=policy, fields=recipe.fields)

    proposals = []
    try:
        provider = _ai_provider_or_error(args)
        if isinstance(provider, int):
            return provider
        limiter = _ai_rate_limiter(Path(args.out))
        for field in fields:
            if filtered.value(field) is None:
                continue  # withheld by consent/policy, or no extracted value at all
            source = for_field(record, field)
            if source is None:
                continue  # no source-document text available to ground a quote in
            limiter.check_and_record()
            proposals.append(
                propose_correction(
                    record_id=record.unique_id,
                    field=field,
                    original_value=record.raw.get(field, ""),
                    source_text=source,
                    provider=provider,
                )
            )
    except AssistantError as error:
        print(f"AI error: {error}", file=sys.stderr)
        return 2

    out_path = Path(args.out) / "ai_ocr_proposals.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(
            {
                "label": "AI-GENERATED DRAFT -- not applied to any record; review required",
                "record_id": record.unique_id,
                "proposals": [dataclasses.asdict(p) for p in proposals],
            },
            indent=2,
            sort_keys=True,
        )
    )

    accepted = [p for p in proposals if p.verified]
    print(
        f"AI-GENERATED, DRAFT ONLY -- {len(accepted)} of {len(proposals)} checked field(s) "
        "have a verified proposed correction"
    )
    for proposal in proposals:
        if proposal.verified:
            print(
                f"  [{proposal.field}] {proposal.original_value!r} -> "
                f"{proposal.proposed_value!r}  (quote: {proposal.quote!r})"
            )
        else:
            print(f"  [{proposal.field}] abstained: {proposal.abstain_reason}")
    print(f"\nwritten to {out_path} -- a draft; nothing was applied to any record")
    return 0


def _cmd_ai_triage(args: argparse.Namespace) -> int:
    """Order the review queue by real signal (score, disagreement, consent).

    Ranking only -- this command never calls a model and never needs an AI
    provider configured, so it runs the same under every policy pack
    including dv/hipaa. The order is a suggestion for where to look first,
    never a decision: every pair still goes through the ordinary review UI.
    """
    from constituent_reconciler.assistant.triage import triage_queue

    try:
        recipe = load_recipe(args.config, policy_pack=args.policy_pack)
    except PolicyViolation as error:
        print(f"policy error: {error}", file=sys.stderr)
        return 2

    result = pipeline.run(recipe)
    pairs = list(result.review_pairs)
    consents = {record_id: record.consent for record_id, record in result.records.items()}
    pair_ids: list[tuple[str, str]] = [
        (a, b) for a, b in (sorted((pair.left, pair.right)) for pair in pairs)
    ]
    evidence_map: dict[tuple[str, str], PairEvidence] = (
        comparison_evidence(result.records.values(), recipe.fields, pair_ids) if pairs else {}
    )

    items = triage_queue(pairs, consents=consents, evidence=evidence_map)
    print(f"review-queue triage -- ordering only, never a decision ({len(items)} pair(s))")
    for item in items:
        flag = " [CONSENT CONFLICT]" if item.consent_conflict else ""
        print(
            f"  {item.priority_rank:>3}. {item.left_id} / {item.right_id}{flag} -- "
            f"{'; '.join(item.reasons)}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reconcile",
        description="Resolve and deduplicate nonprofit constituent records.",
    )
    parser.add_argument("--version", action="version", version=f"reconcile {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="resolve records and write a review queue")
    run_parser.add_argument("--config", required=True, help="path to recipe.toml")
    run_parser.add_argument("--out", default="out", help="output directory")
    run_parser.add_argument("--dry-run", action="store_true", help="do not write files")
    run_parser.add_argument(
        "--policy-pack",
        default=None,
        help="override the recipe's policy pack (e.g. dv); fail-closed on unknown",
    )
    run_parser.add_argument(
        "--tsa-url",
        default=None,
        help="override [provenance].tsa_url for RFC 3161 trusted timestamps",
    )
    run_parser.set_defaults(func=_cmd_run)

    eval_parser = sub.add_parser("eval", help="score a run against ground-truth clusters")
    eval_parser.add_argument("--config", required=True, help="path to recipe.toml")
    eval_parser.add_argument("--truth", required=True, help="ground-truth clusters JSON")
    eval_parser.add_argument("--out", help="write the report here instead of stdout")
    eval_parser.add_argument(
        "--gate", type=float, default=0.0, help="max allowed false-merge rate (default 0.0)"
    )
    eval_parser.add_argument(
        "--calibration",
        help="calibration labels JSON for the fail-closed kappa gate",
    )
    eval_parser.set_defaults(func=_cmd_eval)

    exeval_parser = sub.add_parser(
        "eval-extraction",
        help="score the PDF extractor against a labeled fixture set",
    )
    exeval_parser.add_argument(
        "--fixtures",
        required=True,
        help="directory containing the fixture PDFs and labels.json",
    )
    exeval_parser.add_argument("--out", help="write the report here instead of stdout")
    exeval_parser.add_argument(
        "--precision-target",
        type=float,
        default=0.95,
        help="minimum field precision for exit status 0 (default 0.95, the ledger target)",
    )
    exeval_parser.add_argument(
        "--recall-target",
        type=float,
        default=0.90,
        help="minimum field recall for exit status 0 (default 0.90, the ledger target)",
    )
    exeval_parser.set_defaults(func=_cmd_eval_extraction)

    report_parser = sub.add_parser("report", help="render a count-only run report")
    report_parser.add_argument(
        "--run-dir", default="out", help="directory containing run artifacts"
    )
    report_parser.add_argument(
        "--format",
        choices=("narrative",),
        default="narrative",
        help="report format to render",
    )
    report_parser.add_argument(
        "--lang",
        choices=LANGUAGES,
        default="en",
        help="language for narrative reports",
    )
    report_parser.add_argument("--out", help="write the report here instead of stdout")
    report_parser.set_defaults(func=_cmd_report)

    apply_parser = sub.add_parser("apply", help="apply review decisions and re-resolve")
    apply_parser.add_argument("--config", required=True, help="path to recipe.toml")
    apply_parser.add_argument("--decisions", required=True, help="decisions JSON")
    apply_parser.add_argument(
        "--corrections",
        default=None,
        help="corrections JSON (default: corrections.json beside --decisions)",
    )
    apply_parser.add_argument("--out", default="out", help="output directory")
    apply_parser.add_argument(
        "--policy-pack",
        default=None,
        help="override the recipe's policy pack (e.g. dv); fail-closed on unknown",
    )
    apply_parser.add_argument(
        "--tsa-url",
        default=None,
        help="override [provenance].tsa_url for RFC 3161 trusted timestamps",
    )
    apply_parser.set_defaults(func=_cmd_apply)

    compare_parser = sub.add_parser(
        "compare",
        help=(
            "compare two read-only exports for a migration cutover; "
            "writes local artifacts only, never a connector"
        ),
    )
    compare_parser.add_argument(
        "--left",
        required=True,
        help="legacy side: a recipe .toml, or a .csv whose header uses canonical field names",
    )
    compare_parser.add_argument(
        "--right",
        required=True,
        help="target side: a recipe .toml, or a .csv whose header uses canonical field names",
    )
    compare_parser.add_argument("--out", default="out", help="output directory")
    compare_parser.set_defaults(func=_cmd_compare)

    creview_parser = sub.add_parser(
        "compare-review",
        help="review a comparison's undecided pairs in the local web queue",
    )
    creview_parser.add_argument(
        "--left",
        required=True,
        help="legacy side: a recipe .toml, or a .csv whose header uses canonical field names",
    )
    creview_parser.add_argument(
        "--right",
        required=True,
        help="target side: a recipe .toml, or a .csv whose header uses canonical field names",
    )
    creview_parser.add_argument(
        "--reviewer",
        required=True,
        help="name recorded with every verdict; the decisions file attributes each decision",
    )
    creview_parser.add_argument(
        "--require-second-reviewer",
        action="store_true",
        help=(
            "hold each approval until a second, different reviewer also approves "
            "(a side whose policy pack requires it turns this on regardless)"
        ),
    )
    creview_parser.add_argument("--out", default="out", help="output directory")
    creview_parser.add_argument(
        "--decisions",
        default=None,
        help="decisions file to write (default <out>/compare_decisions.json)",
    )
    creview_parser.add_argument(
        "--host", default="127.0.0.1", help="bind host (loopback only under the dv pack)"
    )
    creview_parser.add_argument(
        "--port", type=int, default=8765, help="bind port (0 picks a free one)"
    )
    creview_parser.add_argument(
        "--no-browser", action="store_true", help="do not open a browser window"
    )
    creview_parser.set_defaults(func=_cmd_compare_review)

    capply_parser = sub.add_parser(
        "compare-apply",
        help=(
            "export the local, import-ready correction file for the target side; "
            "refuses while any comparison review pair is undecided"
        ),
    )
    capply_parser.add_argument(
        "--left",
        required=True,
        help="legacy side: a recipe .toml, or a .csv whose header uses canonical field names",
    )
    capply_parser.add_argument(
        "--right",
        required=True,
        help="target side: a recipe .toml, or a .csv whose header uses canonical field names",
    )
    capply_parser.add_argument(
        "--out", default="out", help="output directory holding the comparison manifest"
    )
    capply_parser.add_argument(
        "--decisions",
        default=None,
        help="decisions JSON from compare-review (default <out>/compare_decisions.json)",
    )
    capply_parser.add_argument(
        "--corrections",
        default=None,
        help="corrections JSON (default: corrections.json beside --decisions)",
    )
    capply_parser.add_argument(
        "--format",
        choices=sorted(compare_apply.CORRECTION_FORMATS),
        default="csv",
        help=(
            "correction-file column shape: canonical csv, or a CRM import map "
            "(salesforce_csv, civicrm_csv); the file is local in every case"
        ),
    )
    capply_parser.set_defaults(func=_cmd_compare_apply)

    plan_parser = sub.add_parser(
        "plan-split",
        help=(
            "write a read-only repair plan for a written cluster a reviewer found to be a bad merge"
        ),
    )
    plan_parser.add_argument(
        "--config", required=True, help="path to the recipe.toml the written run used"
    )
    plan_parser.add_argument(
        "--manifest", required=True, help="the written run's run_manifest.json"
    )
    plan_parser.add_argument(
        "--cluster", required=True, help="cluster id of the written record to split"
    )
    plan_parser.add_argument(
        "--reason",
        required=True,
        help="why this cluster is a bad merge; recorded in the plan, never guessed",
    )
    plan_parser.add_argument(
        "--reviewer",
        required=True,
        help="name recorded with the plan and with the cannot-links it binds",
    )
    plan_parser.add_argument(
        "--decisions",
        default=None,
        help="decisions JSON to bind cannot-links into (default <manifest dir>/decisions.json)",
    )
    plan_parser.add_argument(
        "--corrections",
        default=None,
        help="corrections JSON the written run applied; an explicitly passed path must "
        "exist (default corrections.json beside --decisions)",
    )
    plan_parser.add_argument(
        "--policy-pack",
        default=None,
        help="override the recipe's policy pack to match the written run; fail-closed on unknown",
    )
    plan_parser.set_defaults(func=_cmd_plan_split)

    approve_repair_parser = sub.add_parser(
        "approve-repair",
        help="record one reviewer's approval of a repair plan's exact bytes (ADR 0012)",
    )
    approve_repair_parser.add_argument(
        "--plan", required=True, help="the repair_plan.json to approve"
    )
    approve_repair_parser.add_argument(
        "--reviewer", required=True, help="name recorded with this approval"
    )
    approve_repair_parser.add_argument(
        "--approvals",
        default=None,
        help="approvals JSON to record into (default: repair_approvals.json beside --plan)",
    )
    approve_repair_parser.add_argument(
        "--verdict",
        choices=("approved", "rejected"),
        default="approved",
        help="the reviewer's verdict on this exact plan (default: approved)",
    )
    approve_repair_parser.set_defaults(func=_cmd_approve_repair)

    apply_repair_parser = sub.add_parser(
        "apply-repair",
        help=(
            "apply a repair plan's verified operations to the live destination; "
            "dry-run by default, --execute requires two distinct approvals"
        ),
    )
    apply_repair_parser.add_argument(
        "--config", required=True, help="path to the recipe.toml the written run used"
    )
    apply_repair_parser.add_argument(
        "--manifest", required=True, help="the written run's run_manifest.json"
    )
    apply_repair_parser.add_argument(
        "--plan",
        default=None,
        help="the repair plan to apply (default: repair_plan.json beside --manifest)",
    )
    apply_repair_parser.add_argument(
        "--approvals",
        default=None,
        help="approvals JSON to read (default: repair_approvals.json beside --manifest)",
    )
    apply_repair_parser.add_argument(
        "--corrections",
        default=None,
        help="corrections JSON the written run applied, replayed for the consent check "
        "(default corrections.json beside --manifest)",
    )
    apply_repair_parser.add_argument(
        "--execute",
        action="store_true",
        help="actually contact the destination; omit for a network-free, credential-free dry run",
    )
    apply_repair_parser.add_argument(
        "--policy-pack",
        default=None,
        help="override the recipe's policy pack to match the written run; fail-closed on unknown",
    )
    apply_repair_parser.set_defaults(func=_cmd_apply_repair)

    comparable_parser = sub.add_parser(
        "export-comparable",
        help="emit only the suppressed, CoC-shaped comparable report (no CRM write)",
    )
    comparable_parser.add_argument("--config", required=True, help="path to recipe.toml")
    comparable_parser.add_argument("--out", default="out", help="output directory")
    comparable_parser.add_argument(
        "--policy-pack",
        default=None,
        help="override the recipe's policy pack (e.g. dv); fail-closed on unknown",
    )
    comparable_parser.set_defaults(func=_cmd_export_comparable)

    review_parser = sub.add_parser(
        "review", help="open the local web review queue for uncertain pairs"
    )
    review_parser.add_argument("--config", required=True, help="path to recipe.toml")
    review_parser.add_argument(
        "--reviewer",
        required=True,
        help="name recorded with every verdict; the decisions file attributes each decision",
    )
    review_parser.add_argument(
        "--require-second-reviewer",
        action="store_true",
        help=(
            "hold each approval until a second, different reviewer also approves "
            "(the dv policy pack turns this on by default)"
        ),
    )
    review_parser.add_argument("--out", default="out", help="output directory")
    review_parser.add_argument(
        "--decisions", default=None, help="decisions file to write (default <out>/decisions.json)"
    )
    review_parser.add_argument(
        "--host", default="127.0.0.1", help="bind host (loopback only under the dv pack)"
    )
    review_parser.add_argument(
        "--port", type=int, default=8765, help="bind port (0 picks a free one)"
    )
    review_parser.add_argument(
        "--no-browser", action="store_true", help="do not open a browser window"
    )
    review_parser.add_argument(
        "--policy-pack",
        default=None,
        help="override the recipe's policy pack (e.g. dv); fail-closed on unknown",
    )
    review_parser.set_defaults(func=_cmd_review)

    validate_parser = sub.add_parser(
        "validate", help="check a recipe's shape and report its switches, without running"
    )
    validate_parser.add_argument("--config", required=True, help="path to recipe.toml")
    validate_parser.add_argument(
        "--policy-pack",
        default=None,
        help="override the recipe's policy pack (e.g. dv); fail-closed on unknown",
    )
    validate_parser.set_defaults(func=_cmd_validate)

    destroy_parser = sub.add_parser(
        "destroy",
        help="delete PII-bearing output artifacts per retention policy, with certificates",
    )
    destroy_parser.add_argument("--out", default="out", help="output directory")
    destroy_parser.add_argument(
        "--older-than",
        required=True,
        help=(
            "retention window, e.g. 30d or 12h (0d means regardless of age); "
            "required because no default window ships"
        ),
    )
    destroy_parser.add_argument(
        "--cache-dir",
        default=None,
        help=(
            "also destroy stage-cache entries under this explicitly configured "
            "[cache] dir boundary; the stage_cache directory under --out is "
            "always covered"
        ),
    )
    destroy_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="list eligible artifacts without deleting or logging",
    )
    destroy_parser.set_defaults(func=_cmd_destroy)

    verify_parser = sub.add_parser("verify", help="check a provenance log's hash chain")
    verify_parser.add_argument("--provenance", required=True, help="path to provenance.jsonl")
    verify_parser.set_defaults(func=_cmd_verify)

    schema_parser = sub.add_parser(
        "schema", help="print the declared config, connector, and report schema versions"
    )
    schema_parser.set_defaults(func=_cmd_schema)

    ai_explain_parser = sub.add_parser(
        "ai-explain",
        help="AI-GENERATED, ADVISORY: explain one pair's real comparison evidence",
    )
    ai_explain_parser.add_argument("--config", required=True, help="path to recipe.toml")
    ai_explain_parser.add_argument(
        "--out", default="out", help="output directory (AI usage/rate-limit state)"
    )
    ai_explain_parser.add_argument(
        "--pair",
        nargs=2,
        metavar=("LEFT_ID", "RIGHT_ID"),
        required=True,
        help="the two record ids to explain",
    )
    ai_explain_parser.add_argument(
        "--policy-pack",
        default=None,
        help="override the recipe's policy pack; dv/hipaa disable the AI assistant entirely",
    )
    ai_explain_parser.add_argument(
        "--ai-provider",
        default=None,
        help="anthropic (default) or bedrock; also $RECONCILER_AI_PROVIDER",
    )
    ai_explain_parser.add_argument(
        "--ai-model", default=None, help="override the provider's default model"
    )
    ai_explain_parser.set_defaults(func=_cmd_ai_explain)

    ai_ask_parser = sub.add_parser(
        "ai-ask",
        help="AI-GENERATED, ADVISORY: answer a grounded question about one pair",
    )
    ai_ask_parser.add_argument("--config", required=True, help="path to recipe.toml")
    ai_ask_parser.add_argument(
        "--out", default="out", help="output directory (AI usage/rate-limit state)"
    )
    ai_ask_parser.add_argument("--pair", nargs=2, metavar=("LEFT_ID", "RIGHT_ID"), required=True)
    ai_ask_parser.add_argument(
        "--question", required=True, help="free-text question about this pair"
    )
    ai_ask_parser.add_argument(
        "--policy-pack",
        default=None,
        help="override the recipe's policy pack; dv/hipaa disable the AI assistant entirely",
    )
    ai_ask_parser.add_argument("--ai-provider", default=None)
    ai_ask_parser.add_argument("--ai-model", default=None)
    ai_ask_parser.set_defaults(func=_cmd_ai_ask)

    ai_propose_parser = sub.add_parser(
        "ai-propose-corrections",
        help="AI-GENERATED DRAFT, never applied: propose quote-bound OCR corrections",
    )
    ai_propose_parser.add_argument("--config", required=True, help="path to recipe.toml")
    ai_propose_parser.add_argument("--out", default="out", help="output directory")
    ai_propose_parser.add_argument(
        "--record", required=True, help="record id to propose corrections for"
    )
    ai_propose_parser.add_argument(
        "--field",
        action="append",
        default=None,
        help="field to check (repeatable; default: every mapped field)",
    )
    ai_propose_parser.add_argument(
        "--policy-pack",
        default=None,
        help="override the recipe's policy pack; dv/hipaa disable the AI assistant entirely",
    )
    ai_propose_parser.add_argument("--ai-provider", default=None)
    ai_propose_parser.add_argument("--ai-model", default=None)
    ai_propose_parser.set_defaults(func=_cmd_ai_propose_corrections)

    ai_triage_parser = sub.add_parser(
        "ai-triage",
        help="order the review queue by real signal (score, disagreement, consent)",
    )
    ai_triage_parser.add_argument("--config", required=True, help="path to recipe.toml")
    ai_triage_parser.add_argument(
        "--policy-pack",
        default=None,
        help="override the recipe's policy pack (e.g. dv); fail-closed on unknown",
    )
    ai_triage_parser.set_defaults(func=_cmd_ai_triage)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = args.func
    result: int = func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
