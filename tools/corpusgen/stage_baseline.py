"""Stage-timing baseline for the large synthetic corpus (the UC-01 before side).

``run_large_eval.py`` reports one wall-clock and one peak-memory number for
the whole large-corpus pass. The stage cache planned in
docs/NOVEL-USE-CASES-PLAN.md (UC-01) needs a finer before picture: how the
time and memory divide across the six pipeline stages (ingest, extract,
normalize, score, review artifact, write), so the cached run can show what it
changed and what it left alone. This harness times each stage by composing
the same calls ``pipeline.run`` and ``pipeline.export`` make, in the same
order, and writes a dated Markdown report plus a machine-readable JSON
companion for the cached run to diff against.

tests/test_stage_baseline.py holds the composition to account: on a small
corpus the composed stages must produce the same artifacts ``pipeline.run``
and ``pipeline.export`` produce, so drift between this harness and the
pipeline fails CI instead of skewing a committed baseline.

Determinism: the corpus is regenerated from a pinned seed and size, and both
are recorded in the output together with a digest of the generated input
files. Timing values vary by machine, so the environment (Python version,
platform, CPU count) is recorded alongside them, content-free. The output
carries counts, durations, parameters, and digests only: no field values,
and no filesystem path beyond the corpus directory's base name.

Run it with ``make perf-baseline``; see eval/README.md.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import platform
import re
import shutil
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from constituent_reconciler import decisions, matching, pipeline
from constituent_reconciler.config import Recipe, load_recipe
from constituent_reconciler.models import Record, RunResult
from constituent_reconciler.normalize import normalize_record
from tools.corpusgen.generate import generate, write_corpus
from tools.corpusgen.run_large_eval import peak_memory_mb

BASELINE_SCHEMA_VERSION = 1

STAGE_NAMES = ("ingest", "extract", "normalize", "score", "review_artifact", "write")

_DEFAULT_SEED = 20260707
_DEFAULT_RECORDS = 50000


@dataclass(frozen=True)
class StageTiming:
    """One timed stage: wall clock, item count, and peak RSS at stage end."""

    name: str
    wall_seconds: float
    items: int
    peak_rss_mib_after: float
    note: str = ""


@dataclass(frozen=True)
class CorpusParams:
    """The pinned parameters and observed shape of the measured corpus."""

    seed: int
    requested_records: int
    existing_rows: int
    incoming_rows: int
    input_digest: str


@dataclass(frozen=True)
class Measurement:
    """The result of one timed pass over the corpus."""

    stages: tuple[StageTiming, ...]
    result: RunResult
    written: int
    withheld: int
    stage_wall_seconds_total: float
    peak_rss_mib_before: float
    peak_rss_mib: float


def measure(recipe: Recipe, *, work_dir: Path) -> Measurement:
    """Time the six pipeline stages over the recipe's sources.

    The stage sequence composes the same calls ``pipeline.run`` and
    ``pipeline.export`` make, in the same order; the equivalence test in
    tests/test_stage_baseline.py asserts the composed output matches the
    pipeline's own. ``work_dir`` is recreated from scratch on every call so a
    leftover provenance log cannot skew the write stage's timing.
    """

    if work_dir.exists():
        shutil.rmtree(work_dir)
    review_scratch = work_dir / "review-artifact"
    export_out = work_dir / "out"
    review_scratch.mkdir(parents=True)
    peak_before = peak_memory_mb()
    stages: list[StageTiming] = []

    started = time.perf_counter()
    accounting = pipeline.IngestAccumulator()
    raw_records: list[Record] = []
    if recipe.existing is not None:
        raw_records += pipeline._ingest_source(
            recipe.existing, "existing", recipe=recipe, id_prefix="E", accounting=accounting
        )
    raw_records += pipeline._ingest_source(
        recipe.incoming, "incoming", recipe=recipe, id_prefix="N", accounting=accounting
    )
    pipeline._check_distinct_ids(raw_records)
    stages.append(
        StageTiming("ingest", time.perf_counter() - started, len(raw_records), peak_memory_mb())
    )

    stages.append(
        StageTiming(
            "extract",
            0.0,
            accounting.pages_extracted,
            peak_memory_mb(),
            note=(
                "the seeded corpus is CSV-only, so extraction did no work in this "
                "baseline; for PDF, text, and .eml sources extraction runs inside "
                "the ingest stage"
            ),
        )
    )

    started = time.perf_counter()
    records = {
        r.unique_id: normalize_record(
            r,
            recipe.fields,
            address_backend=recipe.normalize.address_backend,
            failures=accounting.normalization_failures,
        )
        for r in raw_records
    }
    stages.append(
        StageTiming("normalize", time.perf_counter() - started, len(records), peak_memory_mb())
    )

    started = time.perf_counter()
    scored = matching.score_pairs(records.values(), recipe.fields, prior=recipe.prior)
    pairs = decisions.band_pairs(
        scored,
        auto_threshold=recipe.auto_threshold,
        review_threshold=recipe.review_threshold,
    )
    pairs = decisions.enforce_cannot_links(records.keys(), pairs, frozenset())
    clusters = decisions.build_clusters(records.keys(), pairs)
    golden = decisions.golden_records(
        clusters, records, recipe.fields, fill_policy=recipe.fill_policy
    )
    result = RunResult(
        records=records,
        pairs=tuple(pairs),
        clusters=tuple(clusters),
        golden=tuple(golden),
        ingest=accounting.freeze(),
    )
    stages.append(
        StageTiming(
            "score",
            time.perf_counter() - started,
            len(scored),
            peak_memory_mb(),
            note=(
                "matcher scoring plus banding, clustering, and golden-record "
                "reduction, the span pipeline.run covers between normalize and "
                "its returned result"
            ),
        )
    )

    started = time.perf_counter()
    pipeline._write_review_queue(result, recipe, review_scratch)
    stages.append(
        StageTiming(
            "review_artifact",
            time.perf_counter() - started,
            len(result.review_pairs),
            peak_memory_mb(),
            note=(
                "rendered into a scratch directory; the write stage renders it "
                "again inside pipeline.export, so these two stages overlap by "
                "one render"
            ),
        )
    )

    started = time.perf_counter()
    summary = pipeline.export(result, recipe, out_dir=export_out, dry_run=False)
    stages.append(
        StageTiming(
            "write",
            time.perf_counter() - started,
            len(summary.write_results),
            peak_memory_mb(),
            note=(
                "pipeline.export end to end: consent gate, connector write, run "
                "manifest, provenance log, run summary, and a second render of "
                "the review artifact"
            ),
        )
    )

    return Measurement(
        stages=tuple(stages),
        result=result,
        written=sum(1 for w in summary.write_results if w.is_write),
        withheld=len(summary.withheld),
        stage_wall_seconds_total=sum(stage.wall_seconds for stage in stages),
        peak_rss_mib_before=peak_before,
        peak_rss_mib=peak_memory_mb(),
    )


def _csv_data_rows(path: Path) -> int:
    """Count the data rows of one CSV, header excluded."""

    with path.open(newline="", encoding="utf-8-sig") as handle:
        return max(sum(1 for _ in csv.reader(handle)) - 1, 0)


def _input_digest(out_dir: Path) -> str:
    """BLAKE2b digest over the generated input files, for exact-corpus diffs."""

    digest = hashlib.blake2b(digest_size=16)
    for name in ("existing.csv", "incoming.csv"):
        digest.update(name.encode("utf-8"))
        digest.update((out_dir / name).read_bytes())
    return digest.hexdigest()


def build_payload(
    measurement: Measurement,
    *,
    corpus: CorpusParams,
    recipe: Recipe,
    corpus_dir_name: str,
    measured_on: str,
) -> dict[str, object]:
    """Assemble the machine-readable companion the cached run diffs against."""

    result = measurement.result
    rpm = (
        len(result.records) / measurement.stage_wall_seconds_total * 60
        if measurement.stage_wall_seconds_total > 0
        else 0.0
    )
    return {
        "baseline_schema_version": BASELINE_SCHEMA_VERSION,
        "kind": "large-corpus-stage-baseline",
        "variant": "pre-cache",
        "measured_on": measured_on,
        "corpus": {
            "directory_name": corpus_dir_name,
            "generator": "tools.corpusgen.generate",
            "seed": corpus.seed,
            "requested_records": corpus.requested_records,
            "existing_rows": corpus.existing_rows,
            "incoming_rows": corpus.incoming_rows,
            "input_digest_blake2b": corpus.input_digest,
        },
        "recipe": {
            "policy_pack": recipe.policy_pack,
            "prior": recipe.prior,
            "auto_threshold": recipe.auto_threshold,
            "review_threshold": recipe.review_threshold,
            "connector": recipe.output.connector,
        },
        "environment": {
            "python": platform.python_version(),
            "implementation": platform.python_implementation(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
        },
        "results": {
            "stage_wall_seconds_total": round(measurement.stage_wall_seconds_total, 3),
            "records_per_minute": round(rpm),
            "peak_rss_mib_before_stages": round(measurement.peak_rss_mib_before, 1),
            "peak_rss_mib": round(measurement.peak_rss_mib, 1),
            "records": len(result.records),
            "candidate_pairs": len(result.pairs),
            "auto_pairs": len(result.auto_pairs),
            "review_pairs": len(result.review_pairs),
            "golden_records": len(result.golden),
            "written": measurement.written,
            "withheld": measurement.withheld,
            "stages": [
                {
                    "name": stage.name,
                    "wall_seconds": round(stage.wall_seconds, 3),
                    "items": stage.items,
                    "peak_rss_mib_after": round(stage.peak_rss_mib_after, 1),
                    "note": stage.note,
                }
                for stage in measurement.stages
            ],
        },
        "notes": [
            "Pre-cache baseline: no stage cache was active in this run.",
            (
                "Timings come from one run on the machine class named in "
                "'environment' and are not a performance promise."
            ),
            (
                "Peak RSS is process-wide and monotonic; per-stage values are "
                "the peak observed by the end of that stage."
            ),
            (
                "The write stage re-renders the review artifact inside "
                "pipeline.export, so the stage sum can slightly exceed an "
                "end-to-end run."
            ),
        ],
    }


def render_report(
    measurement: Measurement,
    *,
    corpus: CorpusParams,
    corpus_dir_name: str,
    measured_on: str,
    json_name: str,
) -> str:
    """Render the dated Markdown report, matching eval/ report conventions."""

    result = measurement.result
    rpm = (
        len(result.records) / measurement.stage_wall_seconds_total * 60
        if measurement.stage_wall_seconds_total > 0
        else 0.0
    )
    lines = [
        "# Large-corpus stage baseline (pre-cache)",
        "",
        f"Measured: {measured_on}. Dataset: `{corpus_dir_name}` (seeded synthetic corpus, "
        f"seed {corpus.seed}, {len(result.records)} records ingested). Written by "
        "`tools/corpusgen/stage_baseline.py` via `make perf-baseline`, committed alongside "
        f"`large-corpus-report.md`. The JSON companion `{json_name}` carries the same numbers "
        "for machine diffing. There is no real personal data in the corpus.",
        "",
        "This is the before side of the UC-01 stage-cache comparison "
        "(docs/NOVEL-USE-CASES-PLAN.md): no stage cache was active in this run. The numbers "
        "describe one pre-cache run on the single machine class recorded below. They are not "
        "a performance promise; wall clock and memory vary with hardware, and a comparison is "
        "only meaningful against a run on the same machine class with the same corpus "
        "parameters.",
        "",
        "## Environment",
        "",
        "| Python | System | Machine | CPU count |",
        "|---|---|---|---|",
        f"| {platform.python_version()} ({platform.python_implementation()}) "
        f"| {platform.system()} {platform.release()} | {platform.machine()} "
        f"| {os.cpu_count()} |",
        "",
        "## Corpus parameters",
        "",
        "| Seed | Requested records | Existing rows | Incoming rows | Input digest (BLAKE2b) |",
        "|---|---|---|---|---|",
        f"| {corpus.seed} | {corpus.requested_records} | {corpus.existing_rows} "
        f"| {corpus.incoming_rows} | `{corpus.input_digest}` |",
        "",
        "## Stage timings",
        "",
        "| Stage | Wall clock (s) | Items | Peak RSS after (MiB) |",
        "|---|---|---|---|",
    ]
    for stage in measurement.stages:
        lines.append(
            f"| {stage.name} | {stage.wall_seconds:.3f} | {stage.items} "
            f"| {stage.peak_rss_mib_after:,.1f} |"
        )
    lines += ["", "Stage notes:", ""]
    for stage in measurement.stages:
        if stage.note:
            lines.append(f"- {stage.name}: {stage.note}.")
    lines += [
        "",
        "## Run counts",
        "",
        "| Records | Candidate pairs | Auto | Review | Golden records | Written | Withheld |",
        "|---|---|---|---|---|---|---|",
        f"| {len(result.records)} | {len(result.pairs)} | {len(result.auto_pairs)} "
        f"| {len(result.review_pairs)} | {len(result.golden)} | {measurement.written} "
        f"| {measurement.withheld} |",
        "",
        "## Totals",
        "",
        f"Stage wall clock: {measurement.stage_wall_seconds_total:.1f}s for "
        f"{len(result.records)} records ({rpm:,.0f} records/minute). Peak resident memory: "
        f"{measurement.peak_rss_mib:,.1f} MiB, process-wide; "
        f"{measurement.peak_rss_mib_before:,.1f} MiB was already resident after corpus "
        "generation, before the first stage ran.",
        "",
        "## Reproducing",
        "",
        "```sh",
        "make perf-baseline",
        "```",
        "",
        "The command regenerates the corpus from the pinned seed, times the six stages, and "
        "rewrites this report and its JSON companion under the current date. The committed "
        "numbers come from the maintainer's machine; different hardware produces different "
        "absolute values, so regenerate a fresh before/after pair on one machine rather than "
        "comparing against this file across machines.",
        "",
    ]
    return "\n".join(lines)


def _generation_params(recipe_path: Path) -> tuple[int, int] | None:
    """Read the ``--records N --seed S`` pair from a generated recipe header."""

    match = re.search(
        r"--records\s+(\d+)\s+--seed\s+(\d+)", recipe_path.read_text(encoding="utf-8")
    )
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _ensure_corpus(out_dir: Path, *, records: int, seed: int, regenerate: bool) -> bool:
    """Generate the corpus, or verify an existing one matches the parameters.

    Returns False (fail closed) when an existing corpus cannot be shown to
    match the requested seed and size, rather than stamping the baseline with
    parameters that may not describe the data it measured.
    """

    recipe_path = out_dir / "recipe.toml"
    if regenerate or not recipe_path.exists():
        corpus = generate(total_records=records, seed=seed)
        write_corpus(corpus, out_dir, seed=seed, total_records=records)
        return True
    found = _generation_params(recipe_path)
    if found != (records, seed):
        print(
            f"error: existing corpus in {out_dir} does not match --records {records} "
            f"--seed {seed} (found {found}); rerun with --regenerate",
            file=sys.stderr,
        )
        return False
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=Path("eval/large-corpus"))
    parser.add_argument(
        "--records",
        type=int,
        default=_DEFAULT_RECORDS,
        help="corpus size; the default matches the committed large-corpus report",
    )
    parser.add_argument("--seed", type=int, default=_DEFAULT_SEED)
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="regenerate the corpus even if --out-dir already has one",
    )
    parser.add_argument("--report-out", type=Path, default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument(
        "--date",
        default=None,
        help="ISO date stamped into the report (default: today)",
    )
    args = parser.parse_args(argv)

    measured_on = args.date or date.today().isoformat()
    out_dir: Path = args.out_dir
    report_out: Path = args.report_out or Path("eval") / (
        f"large-corpus-stage-baseline-{measured_on}.md"
    )
    json_out: Path = args.json_out or report_out.with_suffix(".json")

    if not _ensure_corpus(
        out_dir, records=args.records, seed=args.seed, regenerate=args.regenerate
    ):
        return 1

    recipe = load_recipe(out_dir / "recipe.toml")
    existing_rows = _csv_data_rows(out_dir / "existing.csv")
    incoming_rows = _csv_data_rows(out_dir / "incoming.csv")

    measurement = measure(recipe, work_dir=out_dir / "stage-baseline-work")

    ingested = len(measurement.result.records)
    if ingested != existing_rows + incoming_rows:
        print(
            f"error: {existing_rows + incoming_rows} source rows but {ingested} records "
            "ingested; refusing to write a baseline over unaccounted input",
            file=sys.stderr,
        )
        return 1

    corpus_params = CorpusParams(
        seed=args.seed,
        requested_records=args.records,
        existing_rows=existing_rows,
        incoming_rows=incoming_rows,
        input_digest=_input_digest(out_dir),
    )
    payload = build_payload(
        measurement,
        corpus=corpus_params,
        recipe=recipe,
        corpus_dir_name=out_dir.name,
        measured_on=measured_on,
    )
    report = render_report(
        measurement,
        corpus=corpus_params,
        corpus_dir_name=out_dir.name,
        measured_on=measured_on,
        json_name=json_out.name,
    )
    report_out.parent.mkdir(parents=True, exist_ok=True)
    report_out.write_text(report, encoding="utf-8")
    json_out.parent.mkdir(parents=True, exist_ok=True)
    json_out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"wrote stage-baseline report: {report_out}")
    print(f"wrote stage-baseline JSON:   {json_out}")
    print(
        f"stage wall clock {measurement.stage_wall_seconds_total:.1f}s for {ingested} records; "
        f"peak RSS {measurement.peak_rss_mib:,.1f} MiB"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
