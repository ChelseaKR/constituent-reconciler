"""Command-line interface.

The subcommands: ``run`` produces resolved records and a review queue, ``eval``
scores a run against ground-truth clusters, ``eval-extraction`` scores the PDF
extractor against labeled fixtures, ``apply`` carries human review decisions
back into a fresh run, ``compare`` reports how two read-only exports line up
for a migration cutover, ``review`` serves the local web queue, ``validate``
checks a recipe without running anything, ``destroy`` deletes retained
artifacts, ``verify`` checks a provenance log's hash chain, and ``schema``
prints the declared schema versions. The CLI uses argparse only, so the
package has no runtime dependency beyond the matcher.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from constituent_reconciler import __version__, compare, pipeline, stage_cache
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
from constituent_reconciler.models import Correction, RunResult
from constituent_reconciler.narrative import LANGUAGES, render_narrative
from constituent_reconciler.pipeline import ExportSummary
from constituent_reconciler.policy import PolicyViolation
from constituent_reconciler.provenance import ProvenanceLog, verify_log
from constituent_reconciler.report import (
    render_eval_markdown,
    render_extraction_markdown,
    render_run_summary,
)
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


def _write_run_report(result: RunResult, out_dir: Path) -> Path:
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
    result = pipeline.run(recipe, cache=cache)
    _, withheld = partition_by_consent(
        result.golden,
        require_consent=recipe.require_consent,
        destination=recipe.output.connector,
    )
    print(render_run_summary(result, withheld=len(withheld)))
    try:
        summary = pipeline.export(result, recipe, out_dir=out_dir, dry_run=args.dry_run)
    except PolicyViolation as error:
        print(f"\npolicy error: {error}", file=sys.stderr)
        return 2
    except ConnectorError as error:
        print(f"\nconnector error: {error}", file=sys.stderr)
        return 2
    _print_export(recipe, summary, dry_run=args.dry_run)
    if not args.dry_run:
        print(f"  run report:   {_write_run_report(result, out_dir)}")
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
    markdown = render_eval_markdown(
        report,
        dataset=Path(args.config).parent.name,
        gate_threshold=args.gate,
        calibration=calibration,
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
    awaiting = _pairs_awaiting_second_review(decisions_data)
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
    result = pipeline.run(
        recipe,
        force_auto=force_auto,
        force_drop=force_drop,
        corrections=corrections,
        cache=stage_cache.for_recipe(recipe, Path(args.out)),
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
        )
    except PolicyViolation as error:
        print(f"\npolicy error: {error}", file=sys.stderr)
        return 2
    except ConnectorError as error:
        print(f"\nconnector error: {error}", file=sys.stderr)
        return 2
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

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = args.func
    result: int = func(args)
    return result


if __name__ == "__main__":
    sys.exit(main())
