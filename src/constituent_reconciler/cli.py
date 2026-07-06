"""Command-line interface.

The subcommands: ``run`` produces resolved records and a review queue, ``eval``
scores a run against ground-truth clusters, ``apply`` carries human review
decisions back into a fresh run, ``review`` serves the local web queue,
``validate`` checks a recipe without running anything, ``verify`` checks a
provenance log's hash chain, and ``schema`` prints the declared schema
versions. The CLI uses argparse only, so the package has no runtime dependency
beyond the matcher.
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
from constituent_reconciler.evaluate import evaluate
from constituent_reconciler.pipeline import ExportSummary
from constituent_reconciler.policy import PolicyViolation
from constituent_reconciler.provenance import verify_log
from constituent_reconciler.report import render_eval_markdown, render_run_summary
from constituent_reconciler.suppression import render_summary


def _load_pairs(path: Path, keys: Sequence[str]) -> list[frozenset[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    pairs: list[frozenset[str]] = []
    for key in keys:
        for entry in data.get(key, []):
            if len(entry) != 2:
                raise ValueError(f"{key} entries must be 2-element [left, right] lists")
            pairs.append(frozenset((str(entry[0]), str(entry[1]))))
    return pairs


def _print_export(recipe: Recipe, summary: ExportSummary, *, dry_run: bool) -> None:
    mode = "dry run, nothing written" if dry_run else "wrote"
    print(f"\nconnector '{recipe.output.connector}' ({mode}): {summary.describe()}")
    print(f"  review queue: {summary.review_path}")
    if summary.withheld_path:
        print(f"  withheld:     {summary.withheld_path}")
    if summary.provenance_path:
        print(f"  provenance:   {summary.provenance_path} ({summary.logged} entries)")
    if summary.aggregate is not None:
        if summary.aggregate_path:
            print(f"  aggregate:    {summary.aggregate_path}")
        print()
        print(render_summary(summary.aggregate))


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
        summary = pipeline.export(result, recipe, out_dir=Path(args.out), dry_run=args.dry_run)
    except PolicyViolation as error:
        print(f"\npolicy error: {error}", file=sys.stderr)
        return 2
    except ConnectorError as error:
        print(f"\nconnector error: {error}", file=sys.stderr)
        return 2
    _print_export(recipe, summary, dry_run=args.dry_run)
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


def _cmd_apply(args: argparse.Namespace) -> int:
    try:
        recipe = load_recipe(args.config, policy_pack=args.policy_pack)
    except PolicyViolation as error:
        print(f"policy error: {error}", file=sys.stderr)
        return 2
    decisions_path = Path(args.decisions)
    force_auto = _load_pairs(decisions_path, ["approved"])
    force_drop = _load_pairs(decisions_path, ["rejected"])
    result = pipeline.run(recipe, force_auto=force_auto, force_drop=force_drop)
    _, withheld = partition_by_consent(result.golden, require_consent=recipe.require_consent)
    print(render_run_summary(result, withheld=len(withheld)))
    try:
        summary = pipeline.export(result, recipe, out_dir=Path(args.out), dry_run=False)
    except PolicyViolation as error:
        print(f"\npolicy error: {error}", file=sys.stderr)
        return 2
    except ConnectorError as error:
        print(f"\nconnector error: {error}", file=sys.stderr)
        return 2
    _print_export(recipe, summary, dry_run=False)
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


def _cmd_validate(args: argparse.Namespace) -> int:
    """Check a recipe without running anything: shape, files, policy switches."""

    try:
        recipe = load_recipe(args.config, policy_pack=args.policy_pack)
    except PolicyViolation as error:
        print(f"policy error: {error}", file=sys.stderr)
        return 2
    except (ValueError, OSError) as error:
        print(f"invalid recipe: {error}", file=sys.stderr)
        return 2

    problems: list[str] = []
    if not recipe.incoming.exists():
        problems.append(f"incoming source does not exist: {recipe.incoming}")
    if recipe.existing is not None and not recipe.existing.exists():
        problems.append(f"existing source does not exist: {recipe.existing}")

    print(f"recipe: {args.config}")
    print(f"  incoming:         {recipe.incoming}")
    print(f"  existing:         {recipe.existing if recipe.existing else '(none)'}")
    print(f"  mapped fields:    {', '.join(recipe.fields)}")
    print(f"  consent column:   {recipe.consent_column if recipe.consent_column else '(none)'}")
    print(f"  thresholds:       auto={recipe.auto_threshold} review={recipe.review_threshold}")
    print(f"  policy pack:      {recipe.policy_pack}")
    print(f"  require consent:  {recipe.require_consent}")
    print(f"  local-only write: {recipe.require_local_targets}")
    print(f"  connector:        {recipe.output.connector}")
    if problems:
        for problem in problems:
            print(f"problem: {problem}", file=sys.stderr)
        return 1
    print("recipe is valid")
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

    validate_parser = sub.add_parser(
        "validate", help="check a recipe's shape, files, and policy without running"
    )
    validate_parser.add_argument("--config", required=True, help="path to recipe.toml")
    validate_parser.add_argument(
        "--policy-pack",
        default=None,
        help="override the recipe's policy pack (e.g. dv); fail-closed on unknown",
    )
    validate_parser.set_defaults(func=_cmd_validate)

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
