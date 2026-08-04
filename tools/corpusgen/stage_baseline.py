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

``--pdf-share`` above zero measures the mixed CSV+PDF corpus variant
(the extract half of #78): that share of the incoming rows rides as seeded
text-layer PDF intake documents, the pipeline's pdfplumber extractor does
real work during ingest, and the extract row reports the time the ingest walk
spent in the PDF reader instead of an honest zero.

Determinism: the corpus is regenerated from a pinned seed, size, and PDF
share, all recorded in the output together with a digest of the generated
input files. A pre-existing corpus is reused only when its input bytes (CSVs,
and for the mixed variant everything in the incoming directory plus the PDF
manifest) digest to exactly what the pinned generator produces for the
parameters in its recipe header; a corpus modified or added to after
generation is refused before any measurement, so the recorded parameters
always describe the measured bytes.
Timing values vary by machine, so the environment (Python version,
platform, CPU count) is recorded alongside them, content-free. The output
carries counts, durations, parameters, and digests only: no field values,
and no filesystem path beyond the corpus directory's base name.

Run it with ``make perf-baseline`` (CSV-only, the committed baseline) or
``make perf-baseline-pdf`` (the mixed variant); see eval/README.md.
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
import tempfile
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from constituent_reconciler import decisions, matching, pipeline, stage_cache
from constituent_reconciler.config import Recipe, load_recipe
from constituent_reconciler.models import Record, RunResult
from constituent_reconciler.normalize import normalize_record
from tools.corpusgen.generate import PDF_PAGES_PER_DOC, generate, write_corpus
from tools.corpusgen.run_large_eval import peak_memory_mb

BASELINE_SCHEMA_VERSION = 1

STAGE_NAMES = ("ingest", "extract", "normalize", "score", "review_artifact", "write")

_DEFAULT_SEED = 20260707
_DEFAULT_RECORDS = 50000


def pdf_extraction_note(*, candidate_pairs: int, auto_pairs: int, review_pairs: int) -> str:
    """The mixed variant's extraction note, in the run's own banding counts.

    A PDF-carried row reaches matching with fewer fields than the same row as
    a CSV cell, because the extractor recovers only what a labeled line gives
    it. Both artifacts carry that, so nobody reads the mixed variant's
    different run counts as a matcher regression.

    The note reports what this run banded and stops there. The harness
    measures one corpus per run and never splits its counts between the
    PDF-carried rows and the CSV rows, so nothing here shows how either
    population banded on its own or how the same people would band as CSV
    cells. A cause stated for the difference would be an interpretation the
    numbers printed beside it cannot carry.
    """

    return (
        "Records read from PDF pages carry only what the extractor recovers from a "
        "labeled line: name, a numeric date of birth, and email or phone when the "
        "form has one. Address and consent have no extraction pattern, and a date "
        'written in prose ("26 November 1942") does not match the numeric date '
        "pattern, so a PDF-carried person reaches matching with fewer comparison "
        f"fields than the same person as a CSV row. This run scored {candidate_pairs} "
        f"candidate pairs, of which {auto_pairs} fell above the auto threshold and "
        f"{review_pairs} in the review band, the rest below the review threshold. "
        "Those counts cover the mixed corpus as one population: the run does not "
        "measure the PDF-carried rows apart from the CSV rows, so it attributes no "
        "part of them to either side, and they are not comparable to a CSV-only run "
        "of the same seed. Under a policy pack that requires consent, the missing "
        "consent value would also withhold these records at the export gate; the "
        "generated recipe uses the default pack, where that gate is a no-op."
    )


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
    """The pinned parameters and observed shape of the measured corpus.

    ``incoming_rows`` counts the whole incoming side; for the mixed variant
    that is the CSV rows plus the ``pdf_rows`` carried as PDF pages, spread
    over ``pdf_documents`` generated documents.
    """

    seed: int
    requested_records: int
    existing_rows: int
    incoming_rows: int
    input_digest: str
    pdf_share: float = 0.0
    pdf_documents: int = 0
    pdf_rows: int = 0


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

    # Extraction happens inside the pipeline's ingest walk, not as a separate
    # pass, so the PDF reader is timed in place: the same function does the
    # same work in the same order, and this harness only accumulates how long
    # each call took. Ingest's row reports its wall clock with that time
    # removed, and the extract row carries it, so the two rows partition the
    # walk instead of double-counting it.
    pdf_reader_seconds = 0.0
    pdf_reader_calls = 0
    real_read_pdf_records = pipeline.read_pdf_records

    def timed_read_pdf_records(
        path: Path,
        source: str,
        *,
        recipe: Recipe,
        id_prefix: str,
        _seen: dict[str, int] | None = None,
        accounting: pipeline.IngestAccumulator | None = None,
        active_cache: stage_cache.ActiveCache | None = None,
    ) -> list[Record]:
        # The baseline is the pre-cache "before" number, so the walk below
        # passes no cache; the parameter exists to match the reader the
        # pipeline calls, and is forwarded rather than dropped.
        nonlocal pdf_reader_seconds, pdf_reader_calls
        call_started = time.perf_counter()
        try:
            return real_read_pdf_records(
                path,
                source,
                recipe=recipe,
                id_prefix=id_prefix,
                _seen=_seen,
                accounting=accounting,
                active_cache=active_cache,
            )
        finally:
            pdf_reader_seconds += time.perf_counter() - call_started
            pdf_reader_calls += 1

    started = time.perf_counter()
    accounting = pipeline.IngestAccumulator()
    raw_records: list[Record] = []
    setattr(pipeline, "read_pdf_records", timed_read_pdf_records)  # noqa: B010 - timing swap
    try:
        if recipe.existing is not None:
            raw_records += pipeline._ingest_source(
                recipe.existing, "existing", recipe=recipe, id_prefix="E", accounting=accounting
            )
        raw_records += pipeline._ingest_source(
            recipe.incoming, "incoming", recipe=recipe, id_prefix="N", accounting=accounting
        )
    finally:
        setattr(pipeline, "read_pdf_records", real_read_pdf_records)  # noqa: B010 - swap back
    pipeline._check_distinct_ids(raw_records)
    ingest_wall = time.perf_counter() - started
    stages.append(
        StageTiming(
            "ingest",
            ingest_wall - pdf_reader_seconds,
            len(raw_records),
            peak_memory_mb(),
            note=(
                (
                    "excludes the time the ingest walk spent in the PDF reader, "
                    "which the extract row reports; pipeline.run itself runs "
                    "extraction inside ingest"
                )
                if pdf_reader_calls
                else ""
            ),
        )
    )

    if pdf_reader_calls:
        extract_stage = StageTiming(
            "extract",
            pdf_reader_seconds,
            accounting.pages_extracted,
            peak_memory_mb(),
            note=(
                "time the ingest walk spent in the PDF reader (sandboxed parse "
                f"included), over {pdf_reader_calls} PDF documents; the CSV rows "
                "of the mixed corpus need no extraction"
            ),
        )
    else:
        extract_stage = StageTiming(
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
    stages.append(extract_stage)

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


def _input_files(out_dir: Path, *, pdf_variant: bool) -> list[Path]:
    """The generated input files the digest (and the measurement) covers.

    For the mixed variant this is the existing CSV, everything the
    ``incoming/`` directory holds, and the PDF manifest the accounting
    cross-check reads, so none of them can drift unnoticed.

    The incoming side is listed by walking that directory rather than by
    globbing the names the generator writes. The recipe points the pipeline at
    the directory, so the pipeline ingests whatever is in it, and a file
    dropped there after generation would otherwise be outside the digest
    entirely. A dropped file that yields no records clears the source-row
    accounting check too, which would leave nothing to catch it.
    """

    if not pdf_variant:
        return [out_dir / "existing.csv", out_dir / "incoming.csv"]
    incoming_dir = out_dir / "incoming"
    files = [out_dir / "existing.csv"]
    files += sorted(path for path in incoming_dir.rglob("*") if path.is_file())
    files.append(out_dir / "pdf_manifest.json")
    return files


def _input_digest(out_dir: Path, *, pdf_variant: bool = False) -> str:
    """BLAKE2b digest over the generated input files, for exact-corpus diffs.

    The CSV-only digest hashes the same names and bytes it always has, so
    values stay comparable with the committed baseline's.
    """

    digest = hashlib.blake2b(digest_size=16)
    for file in _input_files(out_dir, pdf_variant=pdf_variant):
        digest.update(file.relative_to(out_dir).as_posix().encode("utf-8"))
        digest.update(file.read_bytes())
    return digest.hexdigest()


def _pdf_manifest_counts(out_dir: Path) -> tuple[int, int]:
    """(documents, rows carried as PDF pages) from the generated manifest."""

    manifest = json.loads((out_dir / "pdf_manifest.json").read_text(encoding="utf-8"))
    documents = manifest["documents"]
    return len(documents), sum(len(doc["incoming_ids"]) for doc in documents)


def _read_corpus_shape(
    out_dir: Path, *, seed: int, requested_records: int, pdf_share: float
) -> CorpusParams:
    """Count the source rows of the corpus on disk, per layout.

    For the mixed variant the incoming side is the rows left in
    ``incoming/incoming.csv`` plus the rows the manifest says ride as PDF
    pages, so ``incoming_rows`` means the same thing in both layouts and the
    accounting cross-check in ``main`` covers the PDF-carried rows too.
    """

    pdf_variant = pdf_share > 0.0
    incoming_csv = out_dir / ("incoming/incoming.csv" if pdf_variant else "incoming.csv")
    pdf_documents, pdf_rows = _pdf_manifest_counts(out_dir) if pdf_variant else (0, 0)
    return CorpusParams(
        seed=seed,
        requested_records=requested_records,
        existing_rows=_csv_data_rows(out_dir / "existing.csv"),
        incoming_rows=_csv_data_rows(incoming_csv) + pdf_rows,
        input_digest=_input_digest(out_dir, pdf_variant=pdf_variant),
        pdf_share=pdf_share,
        pdf_documents=pdf_documents,
        pdf_rows=pdf_rows,
    )


def build_payload(
    measurement: Measurement,
    *,
    corpus: CorpusParams,
    recipe: Recipe,
    corpus_dir_name: str,
    measured_on: str,
) -> dict[str, object]:
    """Assemble the machine-readable companion the cached run diffs against.

    The mixed variant adds a ``corpus.pdf`` block and one extra note. A
    CSV-only payload carries exactly the keys it carried before the variant
    existed, so the committed 2026-08-03 baseline stays comparable key for key
    with any later CSV-only run at the same schema version.
    """

    result = measurement.result
    rpm = (
        len(result.records) / measurement.stage_wall_seconds_total * 60
        if measurement.stage_wall_seconds_total > 0
        else 0.0
    )
    corpus_block: dict[str, object] = {
        "directory_name": corpus_dir_name,
        "generator": "tools.corpusgen.generate",
        "seed": corpus.seed,
        "requested_records": corpus.requested_records,
        "existing_rows": corpus.existing_rows,
        "incoming_rows": corpus.incoming_rows,
        "input_digest_blake2b": corpus.input_digest,
    }
    if corpus.pdf_share > 0.0:
        corpus_block["pdf"] = {
            "share": corpus.pdf_share,
            "documents": corpus.pdf_documents,
            "rows": corpus.pdf_rows,
            "pages_per_document": PDF_PAGES_PER_DOC,
        }
    return {
        "baseline_schema_version": BASELINE_SCHEMA_VERSION,
        "kind": "large-corpus-stage-baseline",
        "variant": "pre-cache",
        "measured_on": measured_on,
        "corpus": corpus_block,
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
            *(
                [
                    pdf_extraction_note(
                        candidate_pairs=len(result.pairs),
                        auto_pairs=len(result.auto_pairs),
                        review_pairs=len(result.review_pairs),
                    )
                ]
                if corpus.pdf_share > 0.0
                else []
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
    """Render the dated Markdown report, matching eval/ report conventions.

    A CSV-only run renders exactly the sections it rendered before the mixed
    variant existed. The mixed variant adds the PDF corpus row, the consent
    note, and its own reproduction command.
    """

    result = measurement.result
    pdf_variant = corpus.pdf_share > 0.0
    make_target = "make perf-baseline-pdf" if pdf_variant else "make perf-baseline"
    title_suffix = ", mixed CSV and PDF corpus" if pdf_variant else ""
    rpm = (
        len(result.records) / measurement.stage_wall_seconds_total * 60
        if measurement.stage_wall_seconds_total > 0
        else 0.0
    )
    lines = [
        f"# Large-corpus stage baseline (pre-cache{title_suffix})",
        "",
        f"Measured: {measured_on}. Dataset: `{corpus_dir_name}` (seeded synthetic corpus, "
        f"seed {corpus.seed}, {len(result.records)} records ingested). Written by "
        f"`tools/corpusgen/stage_baseline.py` via `{make_target}`, committed alongside "
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
        "`large-corpus-report.md` in this directory is regenerated on release rather than on "
        "every matcher change, so its run counts can describe older code than this file's "
        "run date. When the two disagree over the same seed, the counts here are the ones "
        "the code produced on the date above; `make eval-large` realigns the other report.",
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
    ]
    if pdf_variant:
        lines += [
            f"Of those {corpus.incoming_rows} incoming rows, {corpus.pdf_rows} ride as text-layer "
            f"PDF intake documents ({corpus.pdf_documents} files, {PDF_PAGES_PER_DOC} pages each "
            f"at most, a {corpus.pdf_share:.0%} share) and the rest stay CSV rows. The digest "
            "covers the existing CSV, the manifest, and everything the incoming directory "
            "holds, so a file edited or added there after generation is refused before "
            "any measurement runs.",
            "",
            pdf_extraction_note(
                candidate_pairs=len(result.pairs),
                auto_pairs=len(result.auto_pairs),
                review_pairs=len(result.review_pairs),
            ),
            "",
        ]
    lines += [
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
        make_target,
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


def _generation_params(recipe_path: Path) -> tuple[int, int, float] | None:
    """Read ``--records N --seed S [--pdf-share F]`` from a generated recipe header."""

    match = re.search(
        r"--records\s+(\d+)\s+--seed\s+(\d+)(?:\s+--pdf-share\s+([0-9.]+))?",
        recipe_path.read_text(encoding="utf-8"),
    )
    if match is None:
        return None
    share = float(match.group(3)) if match.group(3) else 0.0
    return int(match.group(1)), int(match.group(2)), share


def _expected_input_digest(*, records: int, seed: int, pdf_share: float) -> str:
    """The digest the pinned generator's output has for these parameters.

    Computed by regenerating the corpus into a scratch directory through the
    same writer the real corpus went through, so the comparison in
    ``_ensure_corpus`` is over exact file bytes rather than a parsed view.
    """

    corpus = generate(total_records=records, seed=seed)
    with tempfile.TemporaryDirectory() as scratch:
        scratch_dir = Path(scratch)
        write_corpus(corpus, scratch_dir, seed=seed, total_records=records, pdf_share=pdf_share)
        return _input_digest(scratch_dir, pdf_variant=pdf_share > 0.0)


def _corpus_files_present(out_dir: Path, *, pdf_share: float) -> str | None:
    """The name of a missing required corpus file, or None when all exist."""

    required = ["existing.csv"]
    if pdf_share > 0.0:
        required += ["incoming/incoming.csv", "pdf_manifest.json"]
    else:
        required += ["incoming.csv"]
    for name in required:
        if not (out_dir / name).is_file():
            return name
    return None


def _ensure_corpus(
    out_dir: Path, *, records: int, seed: int, regenerate: bool, pdf_share: float = 0.0
) -> bool:
    """Generate the corpus, or verify an existing one matches the parameters.

    Returns False (fail closed) when an existing corpus cannot be shown to
    match the requested seed, size, and PDF share, rather than stamping the
    baseline with parameters that may not describe the data it measured. The
    recipe header alone is not trusted: the generator is deterministic, so a
    reused corpus must also digest to exactly the input bytes the pinned
    parameters produce. A hand-modified CSV, PDF, or manifest is refused, and
    so is a file added to the incoming directory after generation, which the
    pipeline would ingest and the digest covers by walking that directory.
    """

    recipe_path = out_dir / "recipe.toml"
    if regenerate or not recipe_path.exists():
        corpus = generate(total_records=records, seed=seed)
        write_corpus(corpus, out_dir, seed=seed, total_records=records, pdf_share=pdf_share)
        return True
    found = _generation_params(recipe_path)
    if found != (records, seed, pdf_share):
        print(
            f"error: existing corpus in {out_dir} does not match --records {records} "
            f"--seed {seed} --pdf-share {pdf_share} (found {found}); rerun with --regenerate",
            file=sys.stderr,
        )
        return False
    missing = _corpus_files_present(out_dir, pdf_share=pdf_share)
    if missing is not None:
        print(
            f"error: existing corpus in {out_dir} is missing {missing}; rerun with --regenerate",
            file=sys.stderr,
        )
        return False
    expected = _expected_input_digest(records=records, seed=seed, pdf_share=pdf_share)
    if _input_digest(out_dir, pdf_variant=pdf_share > 0.0) != expected:
        print(
            f"error: the corpus inputs in {out_dir} are not what the generator produces "
            f"for --records {records} --seed {seed} --pdf-share {pdf_share}; a file was "
            "changed, added, or removed after generation; rerun with --regenerate",
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
        "--pdf-share",
        type=float,
        default=0.0,
        help=(
            "fraction of incoming rows carried as text-layer PDF intake documents "
            "instead of CSV rows (default: 0.0, the CSV-only committed baseline)"
        ),
    )
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
        out_dir,
        records=args.records,
        seed=args.seed,
        pdf_share=args.pdf_share,
        regenerate=args.regenerate,
    ):
        return 1

    recipe = load_recipe(out_dir / "recipe.toml")
    corpus_params = _read_corpus_shape(
        out_dir,
        seed=args.seed,
        requested_records=args.records,
        pdf_share=args.pdf_share,
    )

    measurement = measure(recipe, work_dir=out_dir / "stage-baseline-work")

    ingested = len(measurement.result.records)
    source_rows = corpus_params.existing_rows + corpus_params.incoming_rows
    if ingested != source_rows:
        print(
            f"error: {source_rows} source rows but {ingested} records "
            "ingested; refusing to write a baseline over unaccounted input",
            file=sys.stderr,
        )
        return 1

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
    pdf_summary = ""
    if corpus_params.pdf_rows:
        pdf_summary = (
            f"; {corpus_params.pdf_rows} of them extracted from "
            f"{corpus_params.pdf_documents} PDF documents"
        )
    print(
        f"stage wall clock {measurement.stage_wall_seconds_total:.1f}s for {ingested} records; "
        f"peak RSS {measurement.peak_rss_mib:,.1f} MiB{pdf_summary}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
