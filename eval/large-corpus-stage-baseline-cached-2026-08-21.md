# Large-corpus stage baseline (cached, after)

Measured: 2026-08-21. Dataset: `large-corpus` (seeded synthetic corpus, seed 20260707, 50066 records ingested). Written by `tools/corpusgen/stage_baseline.py --cached` via `make perf-baseline-cached`. The JSON companion `large-corpus-stage-baseline-cached-2026-08-21.json` carries the same numbers for machine diffing. There is no real personal data in the corpus.

This is the after side of the UC-01 stage-cache comparison (docs/NOVEL-USE-CASES-PLAN.md), completing UC-01's final acceptance criterion (issue #78): a stage cache was active and pre-warmed by an identical prior pass over the same corpus before this measurement ran, so ingest and normalize hit the cache rather than recomputing. Candidate generation, scoring, banding, and clustering are never cached, so their timings are expected to match the pre-cache baseline within run-to-run noise; only ingest and normalize should move. The numbers describe one cached run on the single machine class recorded below. They are not a performance promise.

## Environment

| Python | System | Machine | CPU count |
|---|---|---|---|
| 3.12.13 (CPython) | Darwin 25.4.0 | arm64 | 10 |

## Corpus parameters

| Seed | Requested records | Existing rows | Incoming rows | Input digest (BLAKE2b) |
|---|---|---|---|---|
| 20260707 | 50000 | 25025 | 25041 | `38f44cff24459be73c92bc068e57a6fe` |

## Cache stats

| Stage | Hits | Misses |
|---|---|---|
| extract | 0 | 0 |
| normalize | 50066 | 0 |

A miss on the pre-warming pass and a hit on this measured pass is the expected shape: the cache was populated once, discarded from this run's timing, then read fresh for the numbers below.

## Stage timings

| Stage | Wall clock (s) | Items | Peak RSS after (MiB) |
|---|---|---|---|
| ingest | 0.242 | 50066 | 2,129.0 |
| extract | 0.000 | 0 | 2,129.0 |
| normalize | 5.263 | 50066 | 2,129.0 |
| score | 53.746 | 2356639 | 2,210.5 |
| review_artifact | 0.132 | 59 | 2,210.5 |
| write | 3.510 | 37575 | 2,210.5 |

Stage notes:

- ingest: extraction and normalization ran through the stage cache; see cache_stats.
- extract: folded into the ingest row above in the cached path.
- score: matcher scoring plus banding, clustering, and golden-record reduction, the span pipeline.run covers between normalize and its returned result.
- review_artifact: rendered into a scratch directory; the write stage renders it again inside pipeline.export, so these two stages overlap by one render.
- write: pipeline.export end to end: consent gate, connector write, run manifest, provenance log, run summary, and a second render of the review artifact.

## Comparison to large-corpus-stage-baseline-2026-08-21.json

Before measured: 2026-08-21.

| Stage | Before (s) | After (s) | Delta (s) |
|---|---|---|---|
| ingest | 0.307 | 0.242 | -0.065 |
| extract | 0.000 | 0.000 | +0.000 |
| normalize | 0.986 | 5.263 | +4.277 |
| score | 53.538 | 53.746 | +0.208 |
| review_artifact | 0.125 | 0.132 | +0.007 |
| write | 3.658 | 3.510 | -0.148 |
| **total** | 58.614 | 62.894 | +4.280 |

Candidate generation, scoring, banding, and clustering (the 'score' row) are never cached, so their delta is run-to-run noise on the same machine, not a cache effect; only 'ingest' and 'normalize' are expected to move.

## Run counts

| Records | Candidate pairs | Auto | Review | Golden records | Written | Withheld |
|---|---|---|---|---|---|---|
| 50066 | 2356639 | 12512 | 59 | 37575 | 37575 | 0 |

## Totals

Stage wall clock: 62.9s for 50066 records (47,762 records/minute). Peak resident memory: 2,210.5 MiB, process-wide; 2,129.0 MiB was already resident after corpus generation, before the first stage ran.

## Reproducing

```sh
make perf-baseline-cached
```

The command reuses the corpus the pre-cache baseline generated (refusing to run if its bytes have drifted), pre-warms a fresh stage cache with a discarded pass, times the six stages against the warm cache, and rewrites this report and its JSON companion under the current date. The committed numbers come from the maintainer's machine; different hardware produces different absolute values, so regenerate a fresh before/after pair on one machine rather than comparing across machines.
