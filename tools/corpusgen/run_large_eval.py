"""Run the matcher against a large synthetic corpus and render a full report.

Unlike `reconcile eval` (which the demo fixture uses, and which CI reruns on
every push), this script also reports:

* wall-clock time and records-per-minute, so E9 (incremental re-resolution)
  has a real "before" number to improve on;
* peak resident memory, for the same reason;
* a per-name-class recall breakdown with Wilson intervals (feeds R5, "bias by
  name and address class"), using the labels ``tools/corpusgen/generate.py``
  plants alongside the ground truth;
* a per-error-channel recall breakdown (typo, nickname, transliteration,
  compound surname, date-format drift, DOB typo, address variant).

This is the "run on release" eval FIX-11 describes; it is not wired into the
fast CI gate because a 10^4-10^5 record corpus through Splink/DuckDB takes
materially longer than the 27-record demo. Run it with ``make eval-large``.
"""

from __future__ import annotations

import argparse
import json
import resource
import sys
import time
from collections.abc import Iterable
from pathlib import Path

from constituent_reconciler import pipeline
from constituent_reconciler.config import load_recipe
from constituent_reconciler.evaluate import EvalReport, evaluate, wilson_interval
from constituent_reconciler.models import Band
from constituent_reconciler.report import render_eval_markdown
from tools.corpusgen.generate import generate, write_corpus


def _peak_memory_mb() -> float:
    """Peak resident set size of this process, in MiB.

    ``ru_maxrss`` is bytes on macOS/BSD and kibibytes on Linux; both are
    normalized to MiB. This is the peak since process start (monotonically
    increasing), so it covers corpus generation plus the pipeline run when
    both happen in this process, which is the honest "how much memory does
    this whole large-eval pass need" number.
    """

    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return raw / (1024 * 1024)
    return raw / 1024


def _load_labels(out_dir: Path) -> list[dict[str, str]]:
    data = json.loads((out_dir / "labels.json").read_text(encoding="utf-8"))
    labels: list[dict[str, str]] = data["labels"]
    return labels


def _class_breakdown(
    labels: Iterable[dict[str, str]],
    coverage_pairs: set[frozenset[str]],
    auto_pairs: set[frozenset[str]],
    *,
    group_key: str,
    only_kind: str = "duplicate",
) -> list[tuple[str, int, int, int, tuple[float, float]]]:
    """Per-``group_key`` value: (label, n_true, n_caught_auto, n_caught_coverage, coverage_ci).

    ``n_true_pairs`` is the planted duplicate pairs whose label matches;
    recall (with a Wilson interval) is computed against auto+review coverage,
    matching the coverage-recall definition ``evaluate.py`` already uses for
    the whole-corpus metric.
    """

    groups: dict[str, list[frozenset[str]]] = {}
    for label in labels:
        if label["kind"] != only_kind:
            continue
        key = label[group_key]
        pair = frozenset((label["existing_id"], label["incoming_id"]))
        groups.setdefault(key, []).append(pair)

    rows: list[tuple[str, int, int, int, tuple[float, float]]] = []
    for key in sorted(groups):
        pairs = groups[key]
        n_true = len(pairs)
        caught_auto = sum(1 for p in pairs if p in auto_pairs)
        caught_coverage = sum(1 for p in pairs if p in coverage_pairs)
        ci = wilson_interval(caught_coverage, n_true)
        rows.append((key, n_true, caught_auto, caught_coverage, ci))
    return rows


def _render_breakdown_table(
    title: str, rows: list[tuple[str, int, int, int, tuple[float, float]]]
) -> list[str]:
    lines = [
        f"## {title}",
        "",
        "| Class | True pairs | Auto | Auto+review | Coverage recall (95% CI) |",
        "|---|---|---|---|---|",
    ]
    for key, n_true, caught_auto, caught_cov, ci in rows:
        recall = caught_cov / n_true if n_true else 0.0
        lines.append(
            f"| {key} | {n_true} | {caught_auto} | {caught_cov} | "
            f"{recall * 100:.1f}% [{ci[0] * 100:.1f}%, {ci[1] * 100:.1f}%] |"
        )
    lines.append("")
    return lines


def run(
    *,
    out_dir: Path,
    records: int,
    seed: int,
    gate: float,
    regenerate: bool,
) -> tuple[str, EvalReport, bool]:
    wall_start = time.perf_counter()

    if regenerate or not (out_dir / "recipe.toml").exists():
        corpus = generate(total_records=records, seed=seed)
        write_corpus(corpus, out_dir, seed=seed, total_records=records)

    recipe = load_recipe(out_dir / "recipe.toml")
    result = pipeline.run(recipe)

    truth = json.loads((out_dir / "ground_truth.json").read_text(encoding="utf-8"))
    clusters = truth.get("clusters", [])

    report = evaluate(result.pairs, clusters, n_records=len(result.records))

    wall_seconds = time.perf_counter() - wall_start
    peak_mb = _peak_memory_mb()
    records_per_minute = (len(result.records) / wall_seconds) * 60 if wall_seconds > 0 else 0.0

    labels = _load_labels(out_dir)
    auto_pairs = {p.key() for p in result.pairs if p.band is Band.AUTO}
    review_pairs = {p.key() for p in result.pairs if p.band is Band.REVIEW}
    coverage_pairs = auto_pairs | review_pairs

    class_rows = _class_breakdown(labels, coverage_pairs, auto_pairs, group_key="name_class")
    channel_rows = _class_breakdown(labels, coverage_pairs, auto_pairs, group_key="channel")

    markdown = render_eval_markdown(report, dataset=out_dir.name, gate_threshold=gate)
    perf_lines = [
        "",
        "## Performance",
        "",
        f"Wall clock: {wall_seconds:.1f}s for {len(result.records)} records "
        f"({records_per_minute:,.0f} records/minute). Peak resident memory: "
        f"{peak_mb:,.1f} MiB. Includes corpus generation when regenerated in "
        "this process; run with a pre-generated `--out-dir` to time the "
        "pipeline alone.",
        "",
    ]
    breakdown_lines = _render_breakdown_table("Recall by name-origin class (R5)", class_rows)
    breakdown_lines += _render_breakdown_table("Recall by error channel", channel_rows)
    breakdown_lines.append(
        "Name-origin classes and error channels are generator labels, not "
        "measured demographic data; see `tools/corpusgen/__init__.py` for "
        "what this breakdown does and does not claim."
    )

    full_markdown = markdown + "\n".join([*perf_lines, *breakdown_lines]) + "\n"
    gate_pass = report.false_merge_rate <= gate
    return full_markdown, report, gate_pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=Path("eval/large-corpus"))
    parser.add_argument(
        "--records",
        type=int,
        default=50000,
        help="default matches the 50k-record bar in docs/ideation/02-large-scale-fixes.md FIX-11",
    )
    parser.add_argument("--seed", type=int, default=20260707)
    parser.add_argument(
        "--gate",
        type=float,
        default=0.01,
        help="false-merge rate gate threshold (default: 0.01, i.e. 1%%)",
    )
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="regenerate the corpus even if --out-dir already has one",
    )
    parser.add_argument("--report-out", type=Path, default=None)
    args = parser.parse_args(argv)

    markdown, report, gate_pass = run(
        out_dir=args.out_dir,
        records=args.records,
        seed=args.seed,
        gate=args.gate,
        regenerate=args.regenerate,
    )

    report_out = args.report_out or (args.out_dir.parent / f"{args.out_dir.name}-report.md")
    report_out.write_text(markdown, encoding="utf-8")
    print(f"wrote large-corpus eval report: {report_out}")
    print(
        f"false-merge rate {report.false_merge_rate * 100:.2f}% "
        f"({report.false_merges}/{report.n_auto}), gate {'PASS' if gate_pass else 'FAIL'}"
    )
    return 0 if gate_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
