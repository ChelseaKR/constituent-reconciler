# Large-corpus stage baseline (pre-cache)

Measured: 2026-08-03. Dataset: `large-corpus` (seeded synthetic corpus, seed 20260707, 50066 records ingested). Written by `tools/corpusgen/stage_baseline.py` via `make perf-baseline`, committed alongside `large-corpus-report.md`. The JSON companion `large-corpus-stage-baseline-2026-08-03.json` carries the same numbers for machine diffing. There is no real personal data in the corpus.

This is the before side of the UC-01 stage-cache comparison (docs/NOVEL-USE-CASES-PLAN.md): no stage cache was active in this run. The numbers describe one pre-cache run on the single machine class recorded below. They are not a performance promise; wall clock and memory vary with hardware, and a comparison is only meaningful against a run on the same machine class with the same corpus parameters.

## Environment

| Python | System | Machine | CPU count |
|---|---|---|---|
| 3.12.13 (CPython) | Darwin 25.4.0 | arm64 | 10 |

## Corpus parameters

| Seed | Requested records | Existing rows | Incoming rows | Input digest (BLAKE2b) |
|---|---|---|---|---|
| 20260707 | 50000 | 25025 | 25041 | `38f44cff24459be73c92bc068e57a6fe` |

## Stage timings

| Stage | Wall clock (s) | Items | Peak RSS after (MiB) |
|---|---|---|---|
| ingest | 0.238 | 50066 | 114.0 |
| extract | 0.000 | 0 | 114.0 |
| normalize | 0.757 | 50066 | 150.1 |
| score | 39.973 | 2356658 | 3,527.5 |
| review_artifact | 0.117 | 749 | 3,527.5 |
| write | 2.669 | 38263 | 3,541.7 |

Stage notes:

- extract: the seeded corpus is CSV-only, so extraction did no work in this baseline; for PDF, text, and .eml sources extraction runs inside the ingest stage.
- score: matcher scoring plus banding, clustering, and golden-record reduction, the span pipeline.run covers between normalize and its returned result.
- review_artifact: rendered into a scratch directory; the write stage renders it again inside pipeline.export, so these two stages overlap by one render.
- write: pipeline.export end to end: consent gate, connector write, run manifest, provenance log, run summary, and a second render of the review artifact.

## Run counts

| Records | Candidate pairs | Auto | Review | Golden records | Written | Withheld |
|---|---|---|---|---|---|---|
| 50066 | 2356658 | 11824 | 749 | 38263 | 38263 | 0 |

## Totals

Stage wall clock: 43.8s for 50066 records (68,655 records/minute). Peak resident memory: 3,541.7 MiB, process-wide; 106.1 MiB was already resident after corpus generation, before the first stage ran.

## Reproducing

```sh
make perf-baseline
```

The command regenerates the corpus from the pinned seed, times the six stages, and rewrites this report and its JSON companion under the current date. The committed numbers come from the maintainer's machine; different hardware produces different absolute values, so regenerate a fresh before/after pair on one machine rather than comparing against this file across machines.
