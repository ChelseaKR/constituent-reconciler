# External benchmark: FEBRL4

Every other eval in this repository scores the matcher against fixtures this
repository also wrote. The corpus, the error channels, and the ground truth all
come from one hand, so a good number partly measures the fixture author's
imagination rather than the matcher. This page covers the one eval where that is
not true.

Regenerate it with:

```sh
make eval-benchmark
```

That downloads the corpus, verifies it against pinned digests, converts it into a
recipe, runs `pipeline.run` (the same entry point the CLI uses), scores the
result against third-party ground truth, and writes
[`../eval/febrl4-report.md`](../eval/febrl4-report.md). The corpus lands in
gitignored `benchmarks/` and is never committed. The run takes a few seconds and
needs network access on first use; afterwards `--offline` works against the
cached copy.

## Headline numbers

Measured on 10,000 records with 5,000 published ground-truth pairs.

| Metric | Value |
|--------|-------|
| Precision, auto-merge band | 100.0% (0 false merges in 3,365) |
| Recall, auto-merge band | 67.3% |
| F1, auto-merge band | 80.5% |
| Precision, auto + review coverage | 99.3% |
| Recall, auto + review coverage | 77.1% |
| **F1, auto + review coverage** | **86.8%** |
| Missed-match rate | 22.9% (1,144 / 5,000) |
| True pairs never scored (blocking misses) | 344 |

Read honestly: precision is the strong half and recall is the weak half. Nothing
was merged that should not have been, which is the behaviour the fail-closed
design is built for, but close to a quarter of the true duplicates never reached
a reviewer at all. Published FEBRL4 results from tuned academic systems sit well
above this. These defaults are tuned for small nonprofit batches where a false
merge is the expensive error, and this is the price of that choice, measured
rather than asserted.

The 344 blocking misses are the part worth attention. Those pairs were never
scored, so no threshold change reaches them; only the blocking rules in
`defaults.py` would.

## What FEBRL4 is, and what it is not

* Two files of 5,000 person records: `dataset4a` (originals) and `dataset4b`
  (one corrupted duplicate of each). Exactly 5,000 true pairs, no duplicates
  within either file.
* Ground truth is carried in the upstream record ids rather than asserted here:
  `rec-N-org` and `rec-N-dup-0` are the same person. `tools/benchmark/febrl4.py`
  derives clusters from those ids and nothing else.
* Fields offered: given name, surname, date of birth, and a split address. There
  is no email, no phone, and no consent column, so the recipe maps four canonical
  fields and omits the `[consent]` section rather than inventing values.
* **The records are generated, not collected.** FEBRL's `dsgen` samples names,
  addresses, and dates from real Australian frequency tables, then applies
  typographic, phonetic, and field-swap corruption.

So this benchmark does not make the project's demo "real data". What changes is
narrower and still worth having: the corpus, the difficulty, and the ground truth
are now fixed by a third party, years before this repository existed, and cannot
be tuned to flatter the result. Self-authored fixtures cannot make that claim.

## Why not a corpus of real people

The obvious upgrade is a benchmark built from real person records, and the
standard one is the North Carolina voter registry (the Leipzig NCVR sets, CC
licensed and freely downloadable). This project declines it.

A public voter file is a recognised locating vector for exactly the people the DV
policy pack exists to protect. Pulling one onto a contributor's disk, and wiring
a public repository to fetch it on demand, to make a portfolio number look better
is not a trade this project should make.

That constraint is not incidental to this benchmark. It is the reason open
person-linkage corpora with real ground truth are rare in the first place: real
identity data with known matches is confidential nearly everywhere it exists, for
the same reasons this pipeline has a no-egress mode. A reader who wants the
project measured on real personal records is asking for something the field
mostly cannot supply openly, and that is worth saying plainly rather than
papering over.

**Open question for the maintainer.** If NCVR is judged acceptable after all, it
would raise the realism of the corpus at a real privacy cost, and it is a
judgement call rather than an engineering one. Nothing here forecloses it.

## Provenance and licensing

The FEBRL datasets originate with the Febrl project by Peter Christen
(Australian National University). They are redistributed inside the
[`recordlinkage`](https://github.com/J535D165/recordlinkage) Python package by
Jonathan de Bruin under a 3-clause BSD licence.

Nothing is vendored. `tools/benchmark/febrl4.py` fetches the two files at run
time from a pinned upstream commit (`b93d9764`, recordlinkage v0.16) and verifies
each against a recorded SHA-256 digest. A mismatch aborts the run rather than
scoring whatever arrived, so a changed upstream cannot quietly move the published
numbers. The digests are in the report as well as the source.

## Proving the corpus actually reached the resolver

A benchmark harness can produce entirely plausible metrics while the corpus it
claims to have scored never reached the resolver. That failure is invisible in
the metrics themselves, so the report carries a flow-through section instead of
asking to be trusted: the SHA-256 of the exact input bytes, the record counts the
pipeline ingested, per-field population before and after normalization, and named
example pairs that can be looked up in the source files. The harness also fails
the run outright if the converter and the scorer disagree about how many
ground-truth pairs exist, which is what a stale truth file looks like.

## What the real corpus found that the fixtures did not

**The date-of-birth normalizer silently discarded every date in the corpus.**
FEBRL4 writes dates in ISO 8601 *basic* format (`19151111`). `normalize_dob`
handled the extended form (`1915-11-11`) and eight other layouts, but not the
compact one, so all 9,707 populated dates normalized to the empty string. Nothing
errored. The matcher simply scored 10,000 records as though no one had a date of
birth, and every fixture in this repository writes dates in a format the
normalizer already knew, so no test could see it.

Measured effect of teaching the normalizer that one format:

| | Before | After |
|---|--------|-------|
| DOB values parsed | 0 / 9,707 (0.0%) | 9,643 / 9,707 (99.3%) |
| Precision, coverage | 84.7% | 99.3% |
| Recall, coverage | 58.5% | 77.1% |
| **F1, coverage** | **69.2%** | **86.8%** |
| Missed-match rate | 41.5% | 22.9% |
| Blocking misses | 676 | 344 |

The remaining 64 unparsed dates are FEBRL corruptions that are not calendar dates
at all (`19960094`, `19450493`). Rejecting those is correct: the normalizer
returns empty rather than rolling an impossible date over into a valid one.

The compact format is deliberately gated on a plausible leading year, so
`12041990` and `04121990` still normalize to empty. A registry exporting
DDMMYYYY should produce a missing date, not a confidently wrong one.

## Documented claims checked against measured counts

Claims in this repository that could be checked against 10,000 external records
were checked. None was contradicted; two were imprecise enough to be worth
quantifying.

| Claim | Where | Measured |
|-------|-------|----------|
| Address tables follow USPS Publication 28 | `address.py`, `adr/0004` | Accurate, and costly off-shore: 71.8% of street lines end in a token the table knows. **10.9% are real Commonwealth street types it does not carry** — `CIRCUIT` (698), `CLOSE` (331), `GARDEN` (31), `GROVE` (15), `RETREAT` (15). The other 17.3% are FEBRL's injected typos, which should not normalize. |
| Nickname table is "small, curated, English-centric" | `nicknames.py`, `adr/0009` | Accurate. 249 variants; 4.9% of first names here map to a different canonical key. |
| Soundex blocking costs "a few more comparisons" | `defaults.py` | Accurate. 3,018 distinct surnames fall into 1,429 buckets; 674 buckets hold more than one surname, mean 2.11, largest 14. |
| Compound-surname heuristic takes the last two tokens | `normalize.py`, `adr/0009` | Rarely engaged on this corpus: 1.29% of records have a multi-token surname. |

The address result is a scope limit rather than a defect. The standardizer is
documented as CASS-style and US-oriented, and this corpus is Australian, so the
number measures how much that scope costs outside its intended deployment. It is
recorded here so the recall figures above are read with it in mind: some share of
the missed matches is address normalization declining to canonicalize a street
type it was never given.

## Known gaps in this measurement

* The corpus is Australian, and both the address standardizer and the nickname
  table are US and English oriented. Recall here is a floor for a US deployment,
  not an estimate of one.
* No email and no phone means two of the strongest matching signals are absent.
  The defaults weight those heavily, so this corpus exercises a weaker feature set
  than a typical intake batch.
* Duplicates are one-to-one. Nothing here tests clustering across three or more
  records of the same person.
* The LLM field judge and the whole extraction seam are out of the path: the input
  is structured CSV. The report says so rather than reporting a kappa failure for
  a component that never ran.
