# Large-corpus stage baseline (pre-cache, mixed CSV and PDF corpus)

Measured: 2026-08-04. Dataset: `large-corpus-pdf` (seeded synthetic corpus, seed 20260707, 50066 records ingested). Written by `tools/corpusgen/stage_baseline.py` via `make perf-baseline-pdf`, committed alongside `large-corpus-report.md`. The JSON companion `large-corpus-stage-baseline-pdf-2026-08-04.json` carries the same numbers for machine diffing. There is no real personal data in the corpus.

This is the before side of the UC-01 stage-cache comparison (docs/NOVEL-USE-CASES-PLAN.md): no stage cache was active in this run. The numbers describe one pre-cache run on the single machine class recorded below. They are not a performance promise; wall clock and memory vary with hardware, and a comparison is only meaningful against a run on the same machine class with the same corpus parameters.

`large-corpus-report.md` in this directory is regenerated on release rather than on every matcher change, so its run counts can describe older code than this file's run date. When the two disagree over the same seed, the counts here are the ones the code produced on the date above; `make eval-large` realigns the other report.

## Environment

| Python | System | Machine | CPU count |
|---|---|---|---|
| 3.12.13 (CPython) | Darwin 25.4.0 | arm64 | 10 |

## Corpus parameters

| Seed | Requested records | Existing rows | Incoming rows | Input digest (BLAKE2b) |
|---|---|---|---|---|
| 20260707 | 50000 | 25025 | 25041 | `0c5fc3d027160bd0cb17f0851078a41d` |

Of those 25041 incoming rows, 3756 ride as text-layer PDF intake documents (151 files, 25 pages each at most, a 15% share) and the rest stay CSV rows. The digest covers the PDF documents and their manifest as well as both CSVs.

Records read from PDF pages carry only what the extractor recovers from a labeled line: name, a numeric date of birth, and email or phone when the form has one. Address and consent have no extraction pattern, and a date written in prose ("26 November 1942") does not match the numeric date pattern, so a PDF-carried person reaches matching with fewer comparison fields than the same person as a CSV row. Weaker evidence per pair sends far more pairs to the review band instead of auto-merging them, which is the fail-closed gate working; the run counts here are not comparable to a CSV-only run of the same seed. Under a policy pack that requires consent, the missing consent value would also withhold these records at the export gate; the generated recipe uses the default pack, where that gate is a no-op.

## Stage timings

| Stage | Wall clock (s) | Items | Peak RSS after (MiB) |
|---|---|---|---|
| ingest | 0.238 | 50066 | 109.9 |
| extract | 36.893 | 3756 | 109.9 |
| normalize | 0.773 | 50066 | 136.0 |
| score | 45.518 | 2477659 | 2,292.0 |
| review_artifact | 1.599 | 160906 | 2,292.0 |
| write | 3.979 | 34235 | 2,292.0 |

Stage notes:

- ingest: excludes the time the ingest walk spent in the PDF reader, which the extract row reports; pipeline.run itself runs extraction inside ingest.
- extract: time the ingest walk spent in the PDF reader (sandboxed parse included), over 151 PDF documents; the CSV rows of the mixed corpus need no extraction.
- score: matcher scoring plus banding, clustering, and golden-record reduction, the span pipeline.run covers between normalize and its returned result.
- review_artifact: rendered into a scratch directory; the write stage renders it again inside pipeline.export, so these two stages overlap by one render.
- write: pipeline.export end to end: consent gate, connector write, run manifest, provenance log, run summary, and a second render of the review artifact.

## Run counts

| Records | Candidate pairs | Auto | Review | Golden records | Written | Withheld |
|---|---|---|---|---|---|---|
| 50066 | 2477659 | 16779 | 160906 | 34235 | 34235 | 0 |

## Totals

Stage wall clock: 89.0s for 50066 records (33,753 records/minute). Peak resident memory: 2,292.0 MiB, process-wide; 109.9 MiB was already resident after corpus generation, before the first stage ran.

## Reproducing

```sh
make perf-baseline-pdf
```

The command regenerates the corpus from the pinned seed, times the six stages, and rewrites this report and its JSON companion under the current date. The committed numbers come from the maintainer's machine; different hardware produces different absolute values, so regenerate a fresh before/after pair on one machine rather than comparing against this file across machines.
