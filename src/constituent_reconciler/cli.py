"""Command-line interface.

The subcommands: ``run`` produces resolved records and a review queue, ``eval``
scores a run against ground-truth clusters, ``eval-extraction`` scores the PDF
extractor against labeled fixtures, ``apply`` carries human review decisions
back into a fresh run, ``review`` serves the local web queue, ``destroy``
deletes retained artifacts, ``verify`` checks a provenance log's hash chain,
and ``schema`` prints the declared schema versions. The CLI uses argparse only,
so the package has no runtime dependency beyond the matcher.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from constituent_reconciler import __version__, pipeline
from constituent_reconciler.config import Recipe, load_recipe
from constituent_reconciler.connectors.base import ConnectorError
from constituent_reconciler.consent import partition_by_consent
from constituent_reconciler.destruction import destroy, parse_retention
from constituent_reconciler.evaluate import evaluate, extraction_metrics
from constituent_reconciler.models import RunResult
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


def _load_pairs(path: Path, keys: Sequence[str]) -> list[frozenset[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    pairs: list[frozenset[str]] = []
    for key in keys:
        for entry in data.get(key, []):
            if len(entry) != 2:
                raise ValueError(f"{key} entries must be 2-element [left, right] lists")
            pairs.append(frozenset((str(entry[0]), str(entry[1]))))
    return pairs


def _load_household_ids(path: Path, key: str) -> frozenset[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return frozenset(str(entry) for entry in data.get(key, []))


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
        recipe = load_recipe(args.config, policy_pack=args.policy_pack)
    except PolicyViolation as error:
        print(f"policy error: {error}", file=sys.stderr)
        return 2
    result = pipeline.run(recipe)
    _, withheld = partition_by_consent(result.golden, require_consent=recipe.require_consent)
    print(render_run_summary(result, withheld=len(withheld)))
    try:
        out_dir = Path(args.out)
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


def _cmd_eval(args: argparse.Namespace) -> int:
    recipe = load_recipe(args.config)
    result = pipeline.run(recipe)
    truth = json.loads(Path(args.truth).read_text(encoding="utf-8"))
    clusters = truth.get("clusters", [])
    report = evaluate(result.pairs, clusters, n_records=len(result.records))
    markdown = render_eval_markdown(
        report, dataset=Path(args.config).parent.name, gate_threshold=args.gate
    )
    if args.out:
        Path(args.out).write_text(markdown, encoding="utf-8")
        print(f"wrote eval report: {args.out}")
    else:
        print(markdown)
    return 0 if report.false_merge_rate <= args.gate else 1


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


def _cmd_apply(args: argparse.Namespace) -> int:
    try:
        recipe = load_recipe(args.config, policy_pack=args.policy_pack)
    except PolicyViolation as error:
        print(f"policy error: {error}", file=sys.stderr)
        return 2
    decisions_path = Path(args.decisions)
    force_auto = _load_pairs(decisions_path, ["approved"])
    force_drop = _load_pairs(decisions_path, ["rejected"])
    # "households_confirmed" is a list of household ids copied from an earlier
    # run's household_suggestions.csv by a reviewer; a suggestion never applies
    # to the CRM export column until it appears here (household.py).
    confirmed_households = _load_household_ids(decisions_path, "households_confirmed")
    result = pipeline.run(recipe, force_auto=force_auto, force_drop=force_drop)
    _, withheld = partition_by_consent(result.golden, require_consent=recipe.require_consent)
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


def _cmd_review(args: argparse.Namespace) -> int:
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
    session = ReviewSession(
        result,
        recipe.fields,
        decisions_path,
        privacy_mode=recipe.require_local_targets,
    )
    print(render_run_summary(result))
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
    print(
        f"\nreview saved to {decisions_path}: "
        f"{counts.approved} approved, {counts.rejected} rejected, {counts.pending} pending"
    )
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
    log = ProvenanceLog(out_dir / "provenance.jsonl")
    summary = destroy(out_dir, older_than, policy=args.older_than, log=log, dry_run=args.dry_run)
    if args.dry_run:
        for name in summary.candidates:
            print(f"would destroy: {out_dir / name}")
        print(
            f"\ndry run: {len(summary.candidates)} artifact(s) eligible under "
            f"--older-than {summary.policy}; nothing deleted, nothing logged"
        )
        return 0
    for artifact in summary.destroyed:
        print(
            f"destroyed: {out_dir / artifact.name} (sha256 {artifact.sha256}, "
            f"{artifact.size} bytes)"
        )
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
    run_parser.set_defaults(func=_cmd_run)

    eval_parser = sub.add_parser("eval", help="score a run against ground-truth clusters")
    eval_parser.add_argument("--config", required=True, help="path to recipe.toml")
    eval_parser.add_argument("--truth", required=True, help="ground-truth clusters JSON")
    eval_parser.add_argument("--out", help="write the report here instead of stdout")
    eval_parser.add_argument(
        "--gate", type=float, default=0.0, help="max allowed false-merge rate (default 0.0)"
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

    apply_parser = sub.add_parser("apply", help="apply review decisions and re-resolve")
    apply_parser.add_argument("--config", required=True, help="path to recipe.toml")
    apply_parser.add_argument("--decisions", required=True, help="decisions JSON")
    apply_parser.add_argument("--out", default="out", help="output directory")
    apply_parser.add_argument(
        "--policy-pack",
        default=None,
        help="override the recipe's policy pack (e.g. dv); fail-closed on unknown",
    )
    apply_parser.set_defaults(func=_cmd_apply)

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
