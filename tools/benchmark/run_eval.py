"""Score the matcher against the FEBRL4 benchmark and render a report.

This is the release-time counterpart to ``make eval`` for an external corpus,
and it follows ``tools/corpusgen/run_large_eval.py`` rather than shelling out to
``constituent-reconcile eval`` for the same reason that script does: ``constituent-reconcile eval``
also arms the fail-closed kappa gate for the LLM field judge, and no field judge
runs here. FEBRL4 is CSV in, deterministic matcher, CSV out, with no extraction
seam anywhere in the path, so a kappa verdict would be reporting on a component
that never executed. The false-merge gate, which does apply, is enforced.

The pipeline call is ``pipeline.run(recipe)``, the same entry point the CLI uses,
so what is scored here is the production path and not a test double.

The report carries a flow-through section on purpose. A benchmark harness can
produce entirely plausible metrics while the corpus it claims to have scored
never reached the resolver, and that failure is invisible in the metrics
themselves. The section records the SHA-256 of the exact input bytes, the record
counts the pipeline actually ingested, how many values survived each normalizer,
and named example pairs, so a reader can confirm the numbers came from FEBRL4
and not from a leftover fixture.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from constituent_reconciler import pipeline
from constituent_reconciler.config import load_recipe
from constituent_reconciler.evaluate import EvalReport, evaluate, format_rate, gate_holds
from constituent_reconciler.models import Band, Record
from constituent_reconciler.report import render_eval_markdown
from tools.benchmark.febrl4 import SOURCES, UPSTREAM_COMMIT, _digest, fetch, prepare

#: Canonical fields FEBRL4 supplies. Email and phone are absent from the corpus
#: entirely, so they are not reported as empty; they were never offered.
BENCHMARK_FIELDS = ("first_name", "last_name", "dob", "address")


def _population_rows(records: dict[str, Record]) -> list[tuple[str, int, int]]:
    """Per field: how many records carried a raw value, and how many normalized.

    The gap between the two columns is the interesting number. A field that
    arrives populated and leaves empty is one the normalizer rejected, and at
    corpus scale that is a silent recall loss rather than a visible error.
    """

    rows = []
    for field_name in BENCHMARK_FIELDS:
        raw = sum(1 for r in records.values() if r.raw.get(field_name, "").strip())
        norm = sum(1 for r in records.values() if r.normalized.get(field_name, "").strip())
        rows.append((field_name, raw, norm))
    return rows


def _flow_through_section(
    raw_dir: Path,
    records: dict[str, Record],
    report: EvalReport,
    examples: list[tuple[str, str]],
) -> list[str]:
    lines = [
        "",
        "## Flow-through evidence",
        "",
        "Confirmation that the scored records are the benchmark's and not a "
        "cached or fixture corpus. Digests are of the raw upstream files as "
        "downloaded; the pipeline read the converted form of exactly these bytes.",
        "",
        "| Source file | SHA-256 |",
        "|-------------|---------|",
    ]
    for name in SOURCES:
        lines.append(f"| `{name}` | `{_digest(raw_dir / name)}` |")
    lines += [
        "",
        f"Upstream commit `{UPSTREAM_COMMIT}`. Records ingested: "
        f"{len(records)}. Ground-truth pairs derived from upstream record ids: "
        f"{report.n_true_pairs}.",
        "",
        "### Field population after normalization",
        "",
        "| Canonical field | Raw non-empty | Normalized non-empty | Dropped |",
        "|-----------------|---------------|----------------------|---------|",
    ]
    for field_name, raw, norm in _population_rows(records):
        lines.append(f"| {field_name} | {raw} | {norm} | {raw - norm} |")
    lines += [
        "",
        "FEBRL4 carries no email and no phone column, so those two canonical "
        "fields are absent from the corpus rather than empty in it.",
        "",
        "### Example scored pairs",
        "",
        "Ground-truth duplicates the matcher auto-merged, named by their upstream "
        "ids so they can be looked up in the source files:",
        "",
    ]
    lines += [f"* `{left}` = `{right}`" for left, right in examples]
    return lines


def run(
    out_dir: Path,
    *,
    gate: float,
    offline: bool,
    raw_dir: Path | None = None,
) -> tuple[str, EvalReport, bool]:
    """Prepare, run, and score the benchmark. Returns (markdown, report, gate_pass)."""

    raw = raw_dir or (out_dir / "raw")
    fetch(raw, offline=offline)
    prepared = prepare(raw, out_dir)

    recipe = load_recipe(str(out_dir / "recipe.toml"))
    result = pipeline.run(recipe)

    truth = json.loads((out_dir / "ground_truth.json").read_text(encoding="utf-8"))
    report = evaluate(result.pairs, truth["clusters"], n_records=len(result.records))

    truth_keys = {frozenset(cluster) for cluster in truth["clusters"]}
    examples: list[tuple[str, str]] = []
    for pair in result.pairs:
        if pair.band is not Band.AUTO or pair.key() not in truth_keys:
            continue
        left, right = sorted(pair.key())
        examples.append((left, right))
        if len(examples) == 3:
            break

    markdown = render_eval_markdown(
        report,
        dataset=out_dir.name,
        gate_threshold=gate,
        provenance=truth.get("provenance"),
        generator="make eval-benchmark",
        field_judge_ran=False,
    )
    # The converter and the scorer count ground-truth pairs independently. If
    # they disagree, the truth file on disk is not the one just derived from the
    # corpus, which is precisely the stale-input failure this harness is meant
    # to make impossible.
    if prepared.n_true_pairs != report.n_true_pairs:
        raise SystemExit(
            f"ground-truth mismatch: prepared {prepared.n_true_pairs} pairs but "
            f"scored {report.n_true_pairs}; {out_dir} holds a stale truth file"
        )

    section = _flow_through_section(raw, result.records, report, examples)
    full = markdown + "\n".join(section) + "\n"
    return full, report, gate_holds(report.false_merge_rate, gate)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=Path("benchmarks/febrl4"))
    parser.add_argument("--report-out", type=Path, default=Path("eval/febrl4-report.md"))
    parser.add_argument(
        "--gate",
        type=float,
        default=0.01,
        help="false-merge rate gate threshold (default: 0.01, i.e. 1%%)",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="fail instead of downloading if the sources are not already cached",
    )
    args = parser.parse_args(argv)

    markdown, report, gate_pass = run(args.out_dir, gate=args.gate, offline=args.offline)
    args.report_out.parent.mkdir(parents=True, exist_ok=True)
    args.report_out.write_text(markdown, encoding="utf-8")
    print(f"wrote benchmark eval report: {args.report_out}")
    print(
        f"false-merge rate {format_rate(report.false_merge_rate, digits=2)} "
        f"({report.false_merges}/{report.n_auto}), gate "
        f"{'PASS' if gate_pass else 'FAIL'}"
    )
    print(
        f"coverage precision {format_rate(report.precision_coverage)}, "
        f"recall {format_rate(report.recall_coverage)}, "
        f"F1 {format_rate(report.f1_coverage)}"
    )
    return 0 if gate_pass else 1


if __name__ == "__main__":
    raise SystemExit(main())
