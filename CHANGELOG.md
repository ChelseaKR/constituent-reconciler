# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project aims
for [Semantic Versioning](https://semver.org/spec/v2.0.0.html) from 1.0.

## [Unreleased]

### Fixed
- **The false-merge gate passed on zero evidence: a `0/0` rate published as a
  passing `0.0%`.** The gated metric was `false_merges / len(auto)` with a
  literal `0.0` substituted when nothing auto-merged. Zero is the best possible
  value for this metric, so a run that measured nothing was published as a run
  that came out perfectly. Measured on 2026-09-06 by replacing
  `SplinkBackend.score_pairs` with a constant `0.9` — above the review
  threshold, below the auto threshold, so no pair auto-merges: `constituent-reconcile
  eval` exited 0 and wrote `**False-merge rate (gated)** | **0.0%** (0/0)` and
  `False-merge gate at threshold 0.0%: **PASS**`. The matcher had been deleted
  and every gate that predates the negative controls was green.

  Every rate in `evaluate.py` is now `float | None`, and `None` means the
  denominator was zero. The same substitution was present eight more times, in
  each case substituting the *best* value: `0.0` for the two error rates and
  `1.0` for auto precision, auto recall, coverage precision, coverage recall,
  and both extraction precision and recall. `wilson_interval` was already
  honest about it, returning the widest interval `(0, 1)` on no trials; the
  point estimates now agree with the interval printed beside them. Reports
  render `no evidence` rather than a percentage, the false-merge gate is
  fail-closed on an unmeasured rate in the same shape the kappa gate already
  used for an empty label set, and `constituent-reconcile eval` exits 1. Two
  gate comparisons read `args.gate` and `args.precision_target` off an argparse
  namespace, so they are typed `Any` and `mypy --strict` could not have flagged
  them; both are now explicit. Two tests asserted the old behaviour as intended
  (`recall == 1.0` over zero labeled fields, and `false_merge_rate == 0.0` as
  "the premise of this test: the headline gate is green") and now assert the
  absence. Every committed eval report regenerates byte-identical, because none
  of them has an empty denominator. (#159)
- **`CITATION.cff` dated a release that was never cut, and the README's Status
  line was a minor version behind.** The citation file carried
  `date-released: "2026-09-02"` while `git tag -l` printed nothing here:
  `release.yml` has never fired, there is no GitHub Release, and nothing was
  published on that date. The README says so — "No `v*` tag has been cut yet" —
  but that sentence is four hundred lines from the claim, and `date-released` is
  the field GitHub's "Cite this repository" panel and Zenodo actually read, so a
  stranger citing this project cited a release that does not exist. The field is
  gone until a tag exists. Separately, the Status line under the title still read
  `Beta (v0.7)` after the manifest, the changelog and the citation file had all
  moved to 0.8.0.

  `tests/test_release_versions.py` holds all of it to the repository rather than
  to another copy of the number: no tag is a legitimate state and passes, but
  README.md must then say so; a tag that exists must be the declared version, and
  the no-tag sentence must go when one is cut; `date-released` must be absent
  while no tag exists and present once one is; the Status line must name the
  declared version; and `CITATION.cff`, the installed distribution metadata and
  the changelog heading must all agree with `pyproject.toml`. Because a missing
  tag and an unfetched tag look identical from inside a checkout, the tag checks
  skip with the reason on a shallow or tagless clone instead of reading absence
  as evidence, and the CI `verify` job now checks out with `fetch-depth: 0` and
  `fetch-tags: true` — a further test asserts that it does, so the checks cannot
  become a gate that always skips. No tag was created; cutting the first one is
  still the maintainer's decision (#68).

### Added
- **`constituent-reconcile demo`, and the bundled demos ship in the wheel.** `examples/`
  is committed at the repository root and was copied into the sdist and the
  Docker image, but a wheel carried the importable package only, so an
  operator who installed a release (`uvx --from git+...@v0.8.0`, or the
  wheel attached to it) had `constituent-reconcile --help` and nothing the README's
  Quickstart could run. The same tree is now package data under
  `constituent_reconciler/examples/`, and `constituent-reconcile demo` writes it to
  `./examples` (or `--dir`): byte-exact, leaving an identical file alone,
  refusing to touch one that differs, and writing nothing until every file
  has been checked. `tests/test_demo.py` pins the packaged tree and the
  committed one byte-identical in both directions, and
  `tests/test_wheel_quickstart.py` builds the wheel, installs it into a fresh
  virtual environment, and runs the README's Quickstart from a directory
  outside the repository. That test fails rather than skips when it cannot
  build or install, so a wheel that lost its demos is a red check and not a
  green one.

### Changed
- **The command is `constituent-reconcile`.** The console script was named
  `reconcile`, and PyPI already carries that name for an unrelated
  distribution (`reconcile`, "a reconciliation loop system"), with a
  `reconciler` beside it. Two packages that each install `bin/reconcile` do
  not error on collision; whichever was installed last owns the name, and
  nothing says so. Renaming before the first published release costs one
  alias; renaming after it would cost every operator a broken script.
  Subcommands, flags, recipes, and artifacts are unchanged:
  `constituent-reconcile run --config recipe.toml` is the run it was.
  `--help` and `--version` print the new name. The Makefile, the Docker
  image's entrypoint, the README, the adoption kit, the offline-install
  guide, the release runbook, and the retention and threat-model documents
  say `constituent-reconcile`, and the committed eval reports credit it as
  their generator. Dated records (`docs/reviews/`, the CiviCRM live
  demonstration transcript, the ADRs, and this changelog's history) keep the
  name they were written with.

### Deprecated
- **`reconcile` as a command name.** Installed alongside
  `constituent-reconcile` and wired to the same entry function. Invoking it
  prints one line to stderr naming the new command and the removal version;
  stdout is untouched, so a script that captures the command's output sees no
  change. Removed in 0.9.0.

### Fixed
- **A `--config` that did not exist was a traceback.** `load_recipe` opened
  the path with nothing around it, so `constituent-reconcile run
  --config examples/intake-demo/recipe.toml` from anywhere but a clone ended in
  `FileNotFoundError` and a stack trace, on the first command the README
  tells a new operator to run. A missing recipe, an unreadable one and one
  that is not valid TOML are now each a `RecipeError`, which every command
  that loads a recipe already reports as one line on stderr with exit code
  2; the missing-file message says what to pass, and that `constituent-reconcile demo`
  writes the bundled one.

## [0.8.0] - 2026-09-02

### Added
- **The matcher recognises a transposed name.** A duplicate filed with the
  given name and the family name in the opposite boxes was not merely
  unsupported by the model, it was penalised twice: both name comparisons saw
  values that disagreed and each fired its "different" level, so one mistake
  made once cost a factor of about 9,000 and vetoed every other field. Two
  name comparison levels now read all four name values and recognise a crossed
  pair, tolerant of a typo on either side; the given-name comparison carries
  the evidence and the surname comparison abstains, so the fact is counted
  once. A `name_pair_key` blocking rule (the two normalized names sorted and
  joined) generates the pair in the first place, which no per-field rule can:
  a crossed record agrees with its own duplicate on none of the existing keys.
  This is a naming-equity fix as much as a data-entry one. Family-name-first
  is the written convention in Chinese, Korean, Japanese, Hungarian and
  Vietnamese naming, so an intake form that assumes given-name-first collects
  transposed values from exactly those constituents. On the external benchmark
  the change lifted auto-band recall from 67.3% to 72.2% and coverage recall
  from 77.1% to 82.0% with zero false merges. It also closes a standing gap in
  the committed bias audit: the **non-Western name order** risk class in
  `docs/audits/bias-report.md` had been at 0% coverage recall since that audit
  was first written and is now at 100%, which is corroboration from a fixture
  the benchmark had nothing to do with. The transliterated-name class is still
  at 0% and is reported as such.

- **The matcher is now scored against an outside benchmark.** Every eval in
  this repository scored fixtures this repository also wrote, so a good number
  partly measured the fixture author's imagination. `make eval-benchmark`
  runs the same `pipeline.run` entry point the CLI uses against FEBRL4, a
  published record-linkage benchmark whose corpus, corruptions, and ground
  truth are all third-party. Measured on its 10,000 records and 5,000 known
  pairs: 100% precision and 74.3% recall at the auto-merge band, and 99.3%
  precision, 87.9% recall, F1 93.3% counting the review queue, with 61 true
  pairs never scored at all. Precision is the strong half and recall is not;
  tuned academic systems score higher on
  this benchmark, and the numbers are published as measured. The corpus is
  fetched on demand from a pinned upstream commit, verified against recorded
  SHA-256 digests, and written to gitignored `benchmarks/`; only the report is
  committed. A real-person corpus (the North Carolina voter registry, the
  standard choice) was considered and declined: a public voter file is a
  locating vector for exactly the people the DV pack protects.
  `docs/BENCHMARK.md` records the decision, the licensing, and the gaps.
- **Eval reports carry F1.** Reported at both the auto and the auto+review
  bands, alongside the existing precision and recall, so results here can be
  read against published record-linkage numbers. It is explicitly not a gate:
  F1 weighs a false merge and a missed match equally and this pipeline does
  not. The false-merge rate remains the gated metric.
- **The benchmark report proves its own input.** A harness can produce entirely
  plausible metrics while the corpus it claims to have scored never reached the
  resolver, and that failure is invisible in the metrics. The report now
  records the SHA-256 of the exact input bytes, the record counts ingested,
  per-field population before and after normalization, and named example pairs.
  The run aborts outright if the converter and the scorer disagree on how many
  ground-truth pairs exist, which is what a stale truth file looks like.

- **`apply_repair` for CiviCRM, behind an unconditional second-reviewer gate
  (UC-03, ADR 0012).** `reconcile plan-split` produced a local, read-only
  repair plan; `reconcile approve-repair` and `reconcile apply-repair` are the
  two new commands that let a reviewed plan actually reach a live CiviCRM
  instance. CiviCRM is now the repair pilot: `connectors/civicrm.py` declares
  the exact verified version (6.17.2) and the two operations checked against
  it, `field-restore` and `split-create`, both marked destructive. Every
  operation is idempotent by construction -- `field-restore` reads the
  survivor's current value and writes only when it differs from the plan's
  `restore_to`, and `split-create` looks a member up by its own record id
  before ever calling create, because CiviCRM rejects a second create against
  a live `external_identifier` as a DB uniqueness error rather than a clean
  "already exists" response. `apply-repair` is dry-run by default (zero
  network calls, no credential needed) and refuses to construct a connector
  at all -- not just to write -- with fewer than two distinct reviewers'
  recorded approval of the exact plan digest; the break-the-gate tests in
  `tests/test_repair_apply.py` prove this with a connector double that fails
  the test if any of its methods are ever called. A real apply reads the
  destination's live version first and refuses unless it is the exact
  declared one, withholds any `split-create` member whose current consent is
  not active when the recipe requires consent, and writes a local
  `repair_receipts.json` (added to `destruction.PII_ARTIFACTS`) alongside one
  `repair-apply` provenance entry per operation, naming the approvers and a
  receipt digest, never the raw values. `docs/THREAT-MODEL.md`'s T7 and T8
  entries, written ahead of implementation, are updated to say what is now
  mitigated rather than planned.

- **A live-CiviCRM demonstration, script and transcript rather than the video
  Gate 3 still wants (#67).** `docs/connectors/civicrm-live-demonstration-2026-08-21/`
  runs `reconcile validate`, a dry run, a real write, review of the two
  uncertain pairs against the fixture's own planted ground truth, `reconcile
  apply`, live reads confirming Contact/Email/Phone/external-id/consent
  behavior, a full rerun proving updates rather than duplicates, and the
  complete repair path (`plan-split` through two `apply-repair --execute`
  calls proving idempotency) against a disposable local
  `civicrm/civicrm-docker` 6.17.2 instance. The transcript is content-free by
  construction (counts, ids, hashes, never a raw field value), so it is
  committed outright. `docs/reviews/CIVICRM-LIVE-DEMONSTRATION-2026-08-21.md`
  is the dated pointer note and says plainly what this evidence does and does
  not close.

- **The stage-cache "after" measurement, closing UC-01's last acceptance
  criterion (#78).** `tools/corpusgen/stage_baseline.py --cached` pre-warms a
  stage cache with a discarded pass, then measures ingest and normalize
  against the warm cache through the same `pipeline.ingest_normalized_records`
  path `pipeline.run` itself uses, rather than a second hand-written cache
  integration the harness would have to get right on its own; the existing
  pre-cache path is untouched, so every previously committed baseline stays
  reproducible byte for byte. `render_cached_report`/`build_cached_payload`
  add a stage-by-stage before/after table and content-free hit/miss cache
  stats; `make perf-baseline-cached` runs it. A fresh before/after pair was
  measured back to back on the maintainer's machine over the full
  50,066-record seeded corpus (`eval/large-corpus-stage-baseline-2026-08-21.*`,
  `eval/large-corpus-stage-baseline-cached-2026-08-21.*`). Reported as
  measured, not reframed: normalize got *slower* under the warm cache
  (0.986s -> 5.263s) because 50,066 individual cache-file reads cost more
  than the cheap recompute they replace, and total stage wall clock rose
  from 58.6s to 62.9s. Score, never cached and the dominant cost at roughly
  10x normalize's share, moved by 0.2s -- consistent with run-to-run noise,
  not a cache effect. A single cold-to-warm pass over one unchanging batch
  is the wrong shape to see the cache's actual benefit, which is avoiding
  recompute across separate runs of the same records (a corrections re-run,
  `reconcile apply` after review), not reuse within one pass.

- **The external benchmark widens to FEBRL datasets 1-3, each with its own
  threshold sweep (#68).** FEBRL4 was the one external, third-party-ground-truth
  eval in this repository; `tools/benchmark/febrl_multi.py` gives the same
  treatment (pinned fetch, SHA-256 verification, no vendoring) to three more
  datasets from the same upstream commit, at three corruption levels FEBRL
  itself defines as low/medium/high. Datasets 1-3 ship as one file mixing
  originals and duplicates rather than FEBRL4's two-file split, and an
  original can carry more than one duplicate (dataset3: up to five, 1,165 of
  2,000 originals matched) -- closing a real gap FEBRL4 alone left, since its
  duplicates are strictly one-to-one and nothing tested clustering across
  three or more records of the same person. `truth_clusters` groups every id
  sharing one dsgen person number into a single cluster rather than a
  per-duplicate pair, which matters under Splink's `dedupe_only` mode: two
  duplicates of the same person are themselves a true pair the scorer must
  count. A dedicated test proves the grouping against dataset3's own
  five-duplicate shape, where the naive per-pair derivation this module does
  not use would silently under-count (114 vs. 1,934 true pairs on the real
  corpus). `threshold_sweep` re-bands the same scored candidates (no
  re-scoring) at six auto-merge thresholds per dataset, so each report shows
  the precision/recall trade-off directly rather than a single operating
  point. Measured, gated, zero false merges on every dataset: dataset1 (1,000
  records) 100% / 74.4% recall at auto; dataset2 (5,000 records, medium
  corruption) 100% / 59.7%; dataset3 (5,000 records, high corruption) 100% /
  56.3%. `docs/CLAIMS-AUDIT.md`'s FEBRL4 row is also corrected here: it had
  gone stale since a later matching change (#110) and still quoted 67.3%
  recall at auto, where `docs/BENCHMARK.md` and the committed
  `eval/febrl4-report.md` have carried the current 74.3% since that change
  landed.

### Changed
- **A name disagreement no longer vetoes every other field.**
  `defaults._NAME_DIFFERENT_M`, the m_probability that two records of the same
  person carry names that neither agree, nor form a known nickname pair, nor
  are Jaro-Winkler close, moves from 0.01 to 0.02. One percent was not a
  defensible reading of constituent intake data: a legal name against a chosen
  one, an anglicized given name, a marriage or divorce, a name changed after
  leaving an abusive partner, a transliteration, a nickname the vendored table
  does not carry, or a typo worse than one character each plausibly clear one
  percent alone. The level remains strong evidence against a match. Combined
  with the transposition levels, coverage recall on the external benchmark
  goes 77.1% to 87.9% and F1 86.8% to 93.3%, with auto-band precision
  unchanged at 100% (0 false merges in 3,714) and coverage precision up from
  99.25% to 99.34%. The 192 pairs added to the review queue are all true
  duplicates; the number of non-matches a reviewer sees is unchanged at 29.
  Because the change lets a shared address be heard, `test_matching.py` now
  asserts the household invariant directly: two people at one address with the
  same surname and different given names do not auto-merge, whether their dates
  of birth disagree or neither has one.
- **The README's standards-conformance table declares every standard.** It
  covered thirteen of the fifteen portfolio standards; Performance and AI
  Development Measurement had no row. Both new rows are drawn from what is
  already committed. Performance points at the dated per-stage baselines under
  `eval/` and says plainly that they are measured locally, not gated in CI, and
  that there is no hosted service and therefore no SLO. AI Development
  Measurement points at the metrics ledger and the solo-scale DORA section of
  `docs/ROADMAP.md`, and records that activity counters are deliberately not
  tracked. The state column is now headed "State" rather than "Applies?", which
  is the heading an automated read of the table looks for.

### Changed
- **The threat model covers the AI assistant surface, and its citations are
  now checked.** `docs/THREAT-MODEL.md` had no entry for the `assistant/`
  package at all, though the layer shipped in ADR 0014 and
  `ai_ocr_proposals.json` concentrates raw values and quoted source text in
  exactly the shape the document's own T6 describes for repair plans. Four
  threats are added: T9 prompt injection reaching a prompt from an intake
  document, T10 the proposals file's concentration of raw values and verbatim
  quotes, T11 egress to the model provider, and T12 grounding text read from
  outside the run. Each names the tests that pin its mitigations, and each
  states its residual risk rather than implying none: quote verification
  grounds a proposal against a string's presence in the document and not
  against its attribution to the right person, and the `default` pack, where
  most deployments run, permits the assistant while the subprocessor question
  ADR 0014 records stays open.
- **Documentation may no longer cite a test that does not exist.** The
  convention across the threat model, the model and data cards, the retention
  model and the ADRs is that a claim ends by naming the test that pins it,
  and nothing was checking those names. A renamed or deleted test left the
  prose asserting a guarantee nothing enforced, reading exactly as it did the
  day it was true. `tests/test_doc_test_citations.py` resolves every module
  path under `tests/`, and every such path carrying a pytest node name, in
  every committed markdown file. Run against the tree before this change it
  found two stale citations nobody had reported: `docs/connectors/webhook.md`
  attributed `test_webhook_export_honors_consent_scope_not_just_status` to
  `tests/test_connectors_webhook.py`, where it has never been defined, while
  the same sentence went on to say the test lives in `tests/test_pipeline.py`,
  which it does; and `docs/ideation/02-large-scale-fixes.md` dropped the
  `test_` prefix from `tests/test_connector_conformance.py`, naming a path
  that has never existed. Both are corrected. Bare test names written in prose
  with no file beside them are deliberately not checked, because documents
  quote them historically, including one `docs/CLAIMS-AUDIT.md` cites
  precisely to record that it never existed. Prose describing a citation that
  used to be wrong has to describe it rather than reproduce it, which this
  entry does; that is the price of a check with no allowlist, and it is
  cheaper than the allowlist.

### Fixed
- **The committed ruleset would have locked the owner out of the repository.**
  `docs/rulesets/main.json` declared `"bypass_actors": []` and
  `docs/rulesets/README.md` called that "no admin override", while the live
  `protect-main` ruleset (id 18752844) carries the repository owner's standing
  bypass — so the reconciliation `PUT` those documents prescribe would have
  stripped it. The committed file now records that bypass (`RepositoryRole` 5,
  `bypass_mode: always`), deliberately and permanently: an agent once applied a
  ruleset with no bypass and locked the owner out of their own repository, and
  restoring access took a sweep across eighteen repositories. An empty list
  there is not a stricter gate, it is the lockout. The ruleset README gains a
  "Why the owner can bypass" section, the README standards table and
  `docs/EXTERNAL-GATES-RUNBOOK.md` no longer claim "no bypass actors", both
  apply procedures now check that the bypass survived, and ADR 0008 carries a
  dated append-only note superseding the two clauses that argued the other way.
- **`ai-propose-corrections` read the wrong document, or none at all, depending
  on the working directory.** A source span records the intake document's bare
  filename, never a path, because every extractor builds it from `path.name`
  and the value reaches review screens and cache entries. `source_text.for_field`
  resolved that name against the process working directory. Run from anywhere
  but the intake directory, every field returned "no source text", the loop
  skipped all of them, and the command wrote `ai_ocr_proposals.json` holding an
  empty list and exited 0, saying nothing about having been unable to open a
  single document. Run from a directory that happened to hold an unrelated file
  of the same name, the model was sent that file and the quote was verified
  against it. Verification still passed, because the quote genuinely appeared in
  the text the model had been shown; it was the wrong text, about a different
  person, and reaching it meant sending an unrelated local document to the
  provider. Both were reproduced against the real pipeline: from the intake
  directory four fields grounded, from the repository root zero did, and from a
  directory holding a same-named decoy the command produced a verified
  correction for `Garciaintake` quoting `Last Name: Okonkwodecoy`.
  The name is now resolved against the directories the recipe actually named as
  sources (`source_text.document_roots`), which `for_field` requires and has no
  default for. A field with no span still returns `None` and is skipped, which
  is correct for a CSV-sourced record. Every other outcome now raises
  `SourceDocumentUnavailable` and the command exits 2 without writing a draft:
  a document missing from every source directory, a span carrying a directory
  component (no extractor writes one), a filename present in more than one
  source directory (the span does not record which one it came from, so the
  choice is refused rather than guessed), and a span naming a page the PDF does
  not have, which `pdfplumber` raises as an `IndexError` that nothing used to
  catch. `tests/test_cli_ai_propose_grounding.py` drives the real command for
  all of this; three of its four tests fail against the previous code and its
  fourth, the in-intake-directory case, passes against both.
- **The content sweep behind `reconcile destroy` was checking four artifacts
  out of fourteen, and said so only in a docstring.**
  `tests/test_destruction_leaves_nothing.py` is the test that proves a
  destruction certificate is honest: it plants sentinel field values, lets the
  real writers place them, destroys, and then reads every surviving byte. It
  drove `reconcile run` and `ai-propose-corrections` and nothing else, so of
  the fourteen names on `destruction.PII_ARTIFACTS` only `resolved.csv`,
  `civicrm_import.csv`, `household_suggestions.csv` and `ai_ocr_proposals.json`
  ever held a planted value. The cutover artifacts, both repair artifacts, both
  withheld lists, the reviewer corrections file, the Salesforce import file and
  the stage cache were left to the filename-driven tests, which are the tests
  that could not see the original defect. The limitation was written down
  honestly, in the module docstring, where nothing could fail on it.
  The sweep now drives every command that writes a destroyable artifact:
  `run` across the csv, civicrm_csv and salesforce_csv connectors with
  household grouping and the stage cache on, `ai-propose-corrections`,
  `compare`, the review session `compare-review` serves, `compare-apply`,
  `plan-split`, `approve-repair` twice, and `apply-repair --execute` against a
  CiviCRM transport double. Only the model call and the CiviCRM transport are
  doubles, because those are the two things a test here may not do for real;
  every gate, consent filter, quote verification and write around them is the
  real code.
  Coverage is now data rather than prose. Every name on `PII_ARTIFACTS` is
  classified in the test module as swept by content, or as exercised but
  checkable only by existence, and the guard fails on a name classified as
  neither, so a new destroyable artifact cannot be added without a scenario for
  it. Two artifacts are in the second class and the reason is recorded:
  `withheld.csv` and `cutover_withheld.csv` carry cluster ids, member ids and a
  withhold reason and no field value at all, so no sentinel can reach them, and
  planting one in a record id would also reach `provenance.jsonl`, the one
  artifact destruction must refuse to touch.
- **A repair plan told a person to create a record for every split member,
  including one whose consent had lapsed.** `reconcile plan-split` proposes one
  destination record per member of a merged cluster. For every destination but
  the CiviCRM pilot the plan is manual: the tool executes none of it and a
  person follows `manual_instructions` by hand. Those instructions said to
  create a record for each member and said nothing about consent, while the
  verified path's `_withheld_split_members` applied exactly that gate. The path
  with no gate was the one told nothing.
  Every `split_records` entry now carries a `consent` object:
  `withhold_reason` is what `Consent.reason` returns, the same value the
  ordinary write path gates on, and `blocks_creation` is true only when the
  recipe requires consent and that reason is not null. That is the write path's
  own rule, not a stricter one. A recipe that does not require consent still
  gets the label, because the state is a fact about the record and the operator
  was previously shown nothing, but nothing is blocked, since refusing there
  would invent a policy the recipe does not state. Under a consent-requiring
  recipe the instructions add the rule and, when anyone is blocked, name them.
  `REPAIR_PLAN_SCHEMA_VERSION` is 2. Additive: every version 1 key keeps its
  meaning, both readers of the plan use `.get` and ignore unknown keys, and no
  release has been tagged, so no published artifact carries version 1.
  `plan_split` gains an `as_of` argument, defaulting to today and mirroring
  `consent.partition_by_consent`, which is also how the lapse is reached in a
  test without waiting for a date.
  The reachable window turned out to be narrower than the backlog triage
  recorded, and the note in `_withheld_split_members` claiming this could not
  fire through today's CLI was wrong. A revocation would change a source byte
  and the manifest check refuses that; corrections cannot touch the consent
  column; and a cluster written at all had every member active. What none of
  those stop is time crossing a recorded `expires` date, which lapses a consent
  with no byte changing anywhere. Both notes are corrected.


### Fixed
- **`reconcile destroy` certified destruction it had not performed.**
  `destruction.PII_ARTIFACTS` is a hand-maintained list of filenames, and
  `destroy` considers nothing else, so an artifact missing from it is left on
  disk while the command exits 0 and appends destruction certificates for
  everything else. Two artifacts were missing. `ai_ocr_proposals.json`
  (`reconcile ai-propose-corrections`) holds each field's raw
  `original_value`, the model's `proposed_value`, and a `quote` copied
  verbatim out of the intake document. `household_suggestions.csv`
  (`reconcile run` with `[household] enabled = true`) holds a standardized
  street address and a surname for every candidate household. Reproduced end
  to end: after a real run and a real destruction pass with `--older-than
  0d`, `corrections.json` and `resolved.csv` were destroyed and certified
  while both of those files remained, readable, with an email address, a
  street address, and a quoted line of intake text in them. Both are now on
  the list. Reported as #121; PR #122 proposed the one-line addition for the
  first of the two.
- **The same omission can no longer happen quietly.** The reason the miss
  survived the test suite is that every destruction test planted its sentinel
  in a file whose name it read off `PII_ARTIFACTS` first, so no test could
  see a name that was never there. Two new checks derive the question from
  the code instead. `tests/test_destruction_inventory.py` parses the package
  for every filename it builds a path to and fails unless each one is
  classified, either on `PII_ARTIFACTS` or on the new
  `destruction.NOT_DESTROYED`, which records why each retained artifact holds
  no field values; adding a writer without classifying its artifact now fails
  the merge gate. `tests/test_destruction_leaves_nothing.py` covers the other
  half, a wrong classification rather than a missing one, by running the real
  writers over sentinel-laced input and failing if any planted value is still
  readable under the out directory after a destruction pass. Both fail
  against the pre-fix code, naming both files.
- **Seven artifacts were missing from the retention inventory.**
  `docs/DATA-FLOW-AND-RETENTION.md` now carries rows for
  `ai_ocr_proposals.json`, `household_suggestions.csv`, `ai_usage.json`,
  `run_manifest.json`, `run_summary.json`, `run_report.json`, and
  `comparable_report.json`, and the table's completeness is now enforced by
  the inventory test rather than asserted in prose.
- **Two eval-report labels were pointing fixes at the wrong module.** "Candidate
  pairs after blocking" was counting pairs kept above the 0.001 scoring floor,
  not pairs blocking generated, understating blocking by a factor of eighteen
  on the external benchmark (21,295 against 384,499). "Blocking misses" was
  counting every true pair absent from the scorer's output, whether blocking
  never generated it or the matcher scored it below the floor. Read as a
  blocking count it produced a wrong diagnosis: of 344 such pairs, 287 had
  been blocked and scored all along and only 57 were genuinely unblocked. Both
  rows are relabelled, the report says what the number combines, and
  `EvalReport.blocking_misses` carries a comment so the field name stops
  implying a cause.
- **`normalize_dob` silently discarded every date in ISO 8601 basic format.**
  `19151111` is the compact form of `1915-11-11`. The normalizer handled the
  extended form and eight other layouts but not that one, so a source exporting
  compact dates had its entire date-of-birth column normalize to the empty
  string with nothing logged and nothing raised. It went unnoticed because
  every fixture in this repository writes dates in a format the normalizer
  already knew; the FEBRL4 benchmark writes them compactly, and there all 9,707
  populated dates were dropped. Measured effect of the fix on that corpus:
  coverage F1 from 69.2% to 86.8%, coverage recall from 58.5% to 77.1%,
  missed-match rate from 41.5% to 22.9%, and true pairs never scored from 676
  to 344.
  The compact form is accepted only when its leading four digits are a
  plausible year, so `12041990` and `04121990` still normalize to empty rather
  than being guessed at, and impossible dates such as `19960094` stay empty
  instead of rolling over into a valid one.
- **Eval reports credited a command that had not been run.** The generator line
  was hard-coded to `reconcile eval`, so the reports written by `make
  eval-large` and `make eval-benchmark` named a command that would not
  reproduce them. Callers now pass the generator; the default is unchanged.
- **A kappa failure was reported for a field judge that never ran.** The
  calibration section is fail-closed by design, but the large-corpus and
  benchmark runs take structured CSV with no extraction seam anywhere in the
  path, so there are no confidence verdicts for labels to agree with. Those
  reports now say the section is not applicable and why. This waives no gate:
  `reconcile eval` still fails closed on absent labels wherever the judge is
  actually in the path.
- **The two-person review gate is now enforced where the merge happens, not
  only where the decisions file is written.** Under the `dv` pack
  `require_second_reviewer` is on, and the documented behavior is that a merge
  takes effect only after two distinct reviewers approve the same pair. That
  flag was read in exactly one place, the review session, which decides
  whether to hold a lone approval out of the file's `approved` list. Neither
  `reconcile apply` nor `reconcile compare-apply` read it. Their only check
  was derived from the file's shape: a pair recorded in `audit` but absent
  from `approved` is awaiting a second reviewer. A file reviewed under a
  permissive pack holds nothing back, so every single approval sits in
  `approved` and that check found nothing to flag, and the merge was applied
  under the strict pack anyway. The same check also returned an empty list
  outright when the `audit` section was missing or not an object, so a file
  carrying no reviewer attribution at all read as "nothing awaiting".
  Both commands now positively count distinct approvers, through the new
  `review.session.approved_without_second_approval`, on every pair they are
  about to merge, using the same case-and-spacing-insensitive identity rule
  `approvers()` already applies. A pack requiring two reviewers refuses any
  approved pair the audit trail cannot show two distinct people for,
  including a file with no audit trail, and names the pairs.
- **`make install` can now fail on a stale lockfile.** The target ran
  `uv sync --frozen`, and the Makefile header claimed that "refuses to run
  (and exits non-zero) if uv.lock is stale relative to pyproject.toml". It
  does not: `--frozen` skips the up-to-date check by design and exits 0
  against a drifted lock, so CQ-09's reproducible-install gate passed
  identically whether the lock matched pyproject.toml or not. Switched to
  `uv sync --locked`, which asserts the lock is current and exits 1 when it
  is not, in `make install` and in the release workflow's sync step (whose
  environment the published SBOM describes). README and CONTRIBUTING updated
  to match.
- **The per-source data-quality report is now reachable from `reconcile run`
  (#96).** `quality.py` (field completeness, normalization failure rates,
  consent coverage, duplicate density, small-cell suppressed under the DV
  pack) and its renderer in `report.py` were complete and 100% unit-tested,
  but `source_quality()`'s only caller was its own test and
  `render_source_quality()` had no caller at all: no command produced this
  section and no artifact carried it. `ExportSummary` now carries
  `data_quality`, computed on every run (not gated by `recipe.
  aggregate_export`, since "which of my intake channels is bad" is an
  operator question independent of the DV pack's external-sharing posture),
  with suppression applied under that posture the same way the aggregate
  summary's is. Printed in the CLI's export summary and written into
  `run_report.json`'s new `data_quality` array.
- **The two-person review gate no longer accepts one reviewer's own name
  typed twice (#97).** Under the `dv` pack, `require_second_reviewer` is
  documented as requiring "two different reviewers" to approve the same
  pair. `record()`'s dedup filter and `approvers()`'s distinctness check both
  compared raw strings, so `('Jane Doe', 'jane doe')` and `('Jane Doe',
  'Jane  Doe')` (a doubled internal space) each satisfied the gate as two
  people. Both now compare through a new `_reviewer_identity_key` (case-fold,
  collapse internal whitespace), applied only for "is this the same
  reviewer" decisions, never to the name actually stored: the audit trail
  still shows exactly what each reviewer typed. `next_undecided`'s own-pair
  check is fixed the same way, so a reviewer resuming under a different
  capitalization is not shown their own already-approved pair as open.
- **The published aggregate total can no longer hand back a suppressed cell by
  subtraction (#94).** Complementary suppression already guaranteed that any
  breakdown with a hidden cell has at least two hidden cells, so the hidden
  ones cannot be solved for from each other. It could not protect against the
  total published alongside the breakdown: when every other cell in a
  breakdown is a true zero, there is no valid second cell to suppress (a true
  zero must never be marked hidden), so exactly one cell stays suppressed, and
  the total minus every visible cell equals it exactly. `AggregateSummary.total`
  and `ComparableReport.total` (`aggregate_summary.json`, `comparable_report.json`,
  the plain-text run summary, and the EN/ES narrative report) are now the
  string `"suppressed"` in that case, not the raw count. Exhaustive test
  covers every count from 1 through the threshold against a true-zero sibling.
- **Release authority now starts from reviewed `main`.** A maintainer supplies
  an existing SSH-signed stable tag; the read-only verifier checks signer,
  main ancestry, version, changelog, and the full gate before exact artifacts
  reach a checkout-free publisher that rechecks the tag object.
- **`apply_repair`'s field-restore looked the survivor contact up by the wrong
  column against CiviCRM (#113, found live for #67).** `old_external_id`, as
  recorded in the repair plan and the provenance log, is CiviCRM's own
  numeric contact id (whatever `write_all` reported as `WriteResult.external_id`),
  never the `external_identifier` upsert-key string `plan_split` also carries.
  Field-restore queried `Contact.get where external_identifier = old_external_id`,
  a query that could never match a real contact, so every real field-restore
  repair against CiviCRM would have failed closed (reported as an error, never
  corrupted anything) rather than actually restoring a field. Caught by
  building a live-CiviCRM demonstration whose first cluster happened to have
  an empty `restore_fields` list, then a targeted follow-up case that forced a
  real one. `connectors/civicrm.py` now takes `old_external_id` as the numeric
  primary key directly (`_existing_contact_id`, confirming the contact still
  exists via `Contact.get where id = <int>`) instead of re-deriving it through
  the wrong column. `split-create`'s own `external_identifier` lookups are
  unaffected -- those name a column this code itself populates, a different
  and correct use.

### Added
- **Progress events for `run` and `apply` (UC-01 remainder, #77).**
  `pipeline.run` and `pipeline.export` accept a `ProgressSink` (new
  `progress.py`); the default sink discards every event, so library callers
  see no change unless they pass one. The pipeline emits started, advanced,
  and finished events for ingest, extract, normalize, score, write, and
  review artifact, with completed/total counts where a denominator exists:
  file and document totals come from a pre-read ingest plan, record totals
  from the batch itself, and export totals from the exportable set and the
  review queue. Stage durations on finish events come from the same
  `_StageTimer` marks the run summary records. Events are content-free by
  construction (stage name, status, counts, seconds), and a test scans a
  fixture run's payloads for planted values, paths, and record ids. The CLI
  renders events on stderr: one line updated in place on a TTY, stable
  newline records on anything else, and never a control character to a
  non-TTY stream. A command that skips a stage emits nothing for it, so a
  run with no PDF or text sources reports no extract stage, while `--dry-run`
  emits the same write and review-artifact events as a real run because both
  stages still execute there. The UC-01 large-corpus before/after numbers
  remain outstanding (#78).
- **Mixed CSV and PDF corpus variant for the stage baseline (issue #78).**
  `tools/corpusgen/generate.py --pdf-share` writes part of the incoming side
  as seeded, digitally created text-layer PDF intake documents, one record
  per page, with a manifest accounting for which rows each document carries.
  `make perf-baseline-pdf` measures the six stages over that corpus, so the
  extract row reports the time the pipeline's own pdfplumber reader spent
  instead of the honest 0.0s a CSV-only corpus produces, and the UC-01 stage
  cache has a real before number for the extraction half. Ingest reports its
  wall clock with the reader's time removed, so the two rows partition the
  walk. The measured artifacts are committed at
  `eval/large-corpus-stage-baseline-pdf-2026-08-04.{md,json}`: 36.9s of
  extraction over 3,756 pages in 151 documents, on the same machine class as
  the CSV-only baseline that reports 0.0s. The PDF writer
  (`tools/corpusgen/pdfwrite.py`) is stdlib-only dev tooling: no new package
  dependency, no runtime import, deterministic byte for byte, and able to
  encode the non-ASCII names the transliteration channel plants, which the
  existing one-page `testing.make_pdf` helper cannot. The harness refuses a
  corpus whose PDFs, CSVs, or manifest changed after generation, or that
  gained a file in its incoming directory afterwards, and the generator
  refuses to clear an `--out-dir` it cannot recognize as a corpus it wrote.
  Both artifacts state which fields a PDF-carried record loses (address and
  consent have no extraction pattern, and prose dates do not match the
  numeric date pattern) and quote the run's own banding counts, so the
  run-count difference is documented without claiming a cause the run did
  not measure.
- **Read-only split repair planning (UC-03, second pull request).**
  `reconcile plan-split --manifest <run manifest> --cluster <id>` turns one
  written cluster a reviewer identified as a bad merge into a local repair
  plan. Planning requires a stated reason and a reviewer identity, verifies
  the recipe and source batch against the manifest's digests, and takes the
  written cluster's members, external id, fill policy, and field lineage from
  the intact provenance chain before recomputing the golden record over
  exactly that member set; any drift refuses rather than guessing.
  Reconstruction is fully offline and contacts no destination. The plan file
  (`repair_plan.json`, schema version 1) records the old external id, one
  proposed split record per member, the fields whose written value came from
  a member being split away, and the operations the destination supports;
  its raw values live only in that local file, which joins
  `destruction.PII_ARTIFACTS`, the retention inventory, and the threat model,
  while provenance stores the plan's digest in a new `repair-plan` entry.
  Every split pair becomes a binding rejected cannot-link in the decisions
  file, and a test proves the next run cannot re-form the cluster. Beside the
  planner, `connectors/repair.py` adds the ADR 0012 capability-declaration
  surface: exact enumerated destination versions, operations marked
  destructive or not, and required vendor-evidence fields, with wildcard or
  range versions refused at construction. No adapter publishes a declaration,
  conformance tests assert that undeclared means no repair operations, and
  unsupported destinations get manual instructions with no flag to force a
  generic operation. `apply_repair` execution and the CiviCRM pilot remain
  unimplemented.
- **Cutover review and correction-file export (UC-02, second PR).** Two new
  subcommands finish the migration-assurance flow. `reconcile compare-review`
  serves the same local web review queue, session, and decisions machinery
  `reconcile run` uses, over a comparison's undecided pairs; verdicts save to
  `compare_decisions.json` and reviewer field corrections to
  `corrections.json`, both re-scored on apply. `reconcile compare-apply` then
  emits `target_corrections.csv`, a local, import-ready correction file for
  the target side, built with the same import field maps and local writer as
  the `salesforce_csv` and `civicrm_csv` exports (`--format` chooses the
  shape; the default keeps canonical column names). The export fails closed
  three ways: it refuses while any review pair is undecided or awaiting a
  second reviewer (a comparison with zero review pairs may export without a
  review step), it refuses when `compare_manifest.json` is missing or no
  longer matches the inputs, and identities without active consent are
  withheld and counted (`cutover_withheld.csv`, ids and reason only) whenever
  either side's recipe requires consent. After a successful export the
  comparison manifest gains an `export` section binding the correction and
  decisions files by digest with counts only, under the new versioned
  `cutover_corrections` schema. No comparison command can reach a live
  connector; `tests/test_compare_apply.py` holds that invariant alongside
  the review, manifest, and consent gates. `target_corrections.csv`,
  `cutover_withheld.csv`, and `corrections.json` join the destruction
  inventory, closing a pre-existing gap for the run pipeline's corrections
  file, and docs/DATA-FLOW-AND-RETENTION.md covers all of them.
- **Large-corpus stage-timing baseline (UC-01 "before" side).**
  `tools/corpusgen/stage_baseline.py`, run with `make perf-baseline`, times
  the six pipeline stages (ingest, extract, normalize, score, review
  artifact, write) over the seeded 50k synthetic corpus and records peak
  resident memory per stage, with the pinned corpus parameters, an input
  digest, and the measuring machine's environment captured content-free:
  counts and durations only, no field values, no user paths. The dated
  report and its JSON companion are committed at
  `eval/large-corpus-stage-baseline-2026-08-03.{md,json}` as the pre-cache
  numbers the UC-01 stage cache is measured against; the future cached run
  diffs its own JSON against this one. Both artifacts state plainly that
  they describe one run on one named machine class and are not a
  performance promise. A CI-sized smoke test proves the harness on a tiny
  corpus and asserts byte-for-byte that its composed stages produce the
  same artifacts `pipeline.run` and `pipeline.export` produce, so harness
  drift fails CI instead of skewing a committed baseline.
- **External-gates runbook.** `docs/EXTERNAL-GATES-RUNBOOK.md` writes down the
  maintainer's exact hand-run steps for the five "External gate" rows in
  `docs/ROADMAP-CLOSEOUT.md`'s canonical 1.0 gates table: the screen-reader
  walkthrough with reviewed Spanish copy and ACR, the ruleset apply plus the
  signed-tag release ceremony, the recorded CiviCRM demonstration, real
  adopting organizations, and the schema-stability window. Each section names
  the repository prerequisites that already exist, the ordered steps, the
  evidence artifact and where it gets recorded, and honest failure notes. The
  closeout's no-fabricated-evidence principle governs throughout; the runbook
  never substitutes a fixture for a human result.
- **Content-addressed stage cache for extraction and normalization (UC-01).**
  A recipe's new `[cache]` section (validated fail-closed; absent means off)
  stores extraction and normalization results as content-addressed files
  under `stage_cache/` inside the output root, or under an explicitly
  configured local `dir` boundary; URL-shaped values are refused at load
  time. Keys digest the input, the declared recipe schema version, the
  active field mapping, the package version, the installed version of the
  library doing the stage's work (pdfplumber for PDF extraction, the postal
  package under the libpostal address backend), and the stage's backend
  configuration, so editing one source row re-keys that row alone, a
  dependency upgrade orphans that dependency's old entries, and any
  mismatched entry is ignored rather than coerced. A stage whose backing
  library version cannot be determined is not cached at all, and a parse the
  sandbox killed against a resource limit is returned fail-closed but never
  stored, so a transient timeout or memory cap cannot freeze a document out
  of reconciliation. Scoring, banding, and clustering never touch the
  cache: term frequencies and cross-batch candidates change pair
  probabilities whenever the population changes, and a merge-blocking test
  proves cached and uncached runs byte-identical. OCR and model-seam
  extraction backends bypass the cache because their output is not a pure
  function of the file bytes. The run manifest and `run_summary.json` record
  cache policy, hit/miss counts, and stage durations, all content-free
  (report schema version 4). The cache directory is a PII artifact:
  `reconcile destroy` covers it, `--cache-dir` reaches an explicit boundary
  but refuses any directory that does not have the stage-cache shape, the
  cache walk deletes nothing outside `extract/` and `normalize/` entry
  files, and each deleted entry gets its own destruction certificate. One
  UC-01 item is deliberately not part of this change: the large-corpus
  wall-clock and peak-memory before/after numbers from the plan item's
  acceptance criteria. No benchmark has been run, so none is claimed;
  docs/CLAIMS-AUDIT.md records the gap (#78). The progress-event work the
  cache change deferred (see docs/NOVEL-USE-CASES-PLAN.md) lands in this
  same release; see the progress-events entry above.
- **Read-only migration cutover comparison (UC-02, first PR).** `reconcile
  compare --left <recipe or csv> --right <recipe or csv>` resolves a legacy
  CRM export against a target export without building any connector. Records
  carry a left/right side label through the existing mapping, normalization,
  and matcher backend, and the command classifies every identity as matched,
  left-only, or right-only, flags value conflicts on matched people, and
  marks identities an undecided pair touches as needing review. Field values
  stay in two local artifacts, `cutover_report.csv` and `cutover_review.csv`,
  both added to the destruction inventory. The count-only
  `migration_summary.json` carries its own versioned schema
  (`migration_summary` in `reconcile schema`), and `compare_manifest.json`
  binds both mapping recipes and the digest of every input file. Recipes that
  disagree on thresholds or address backend are refused rather than silently
  reconciled, and a merge-blocking test proves no compare code path can
  construct a write connector, in the spirit of `tests/test_no_egress.py`.
  The post-review correction-file export is the second UC-02 change and is
  not part of this one.
- **Multiyear roadmap, 2026 H2 through 2029.** `docs/ROADMAP-MULTIYEAR.md`
  arranges the Now/Next/Later plan and the closeout's external 1.0 gates into
  four horizons, each organized by workstream, under the one-maintainer
  60/30/10 capacity model. External gates stay visibly external and are never
  booked as engineering; where the future depends on adopters or partners the
  document states the gate instead of a date. It carries the closeout's
  product principles and the plan's exclusions forward as permanent non-goals
  and sets a per-release and per-half review cadence in which adopter
  evidence outranks synthetic personas. `docs/ROADMAP.md` gains a pointer
  marking it a historical record, and `docs/NOVEL-USE-CASES-PLAN.md` one
  marking it the near-term detail under the new umbrella document.
- **Repair-capability decision record (UC-03 study).**
  `docs/adr/0012-connector-repair-capabilities.md` decides the protocol for
  post-write split repair ahead of implementation: `inspect_repair` and
  `apply_repair` as optional connector capabilities separate from
  `Connector.write_all`, a declaration that names the exact destination and
  version pairs an adapter verified, read-only repeatable planning, a
  mandatory second reviewer for remote destructive operations, manual
  instructions instead of a forced generic delete on unsupported
  destinations, CiviCRM as the pilot, and the threat-model and
  destruction-inventory updates required before any repair plan is stored.
  Vendor delete/merge/restore semantics are named as open inputs to be read
  from current documentation and a live disposable instance, not asserted.
  `docs/NOVEL-USE-CASES-PLAN.md` and `docs/ROADMAP-CLOSEOUT.md` cross-reference
  the record. Documentation only; no code changes.
- **Airtable native-upsert connector (roadmap E3).** The new `airtable`
  destination batches at Airtable's ten-record limit, uses
  `performUpsert` on the configured external-id field, reads a personal access
  token from the environment, and fails closed on response-count drift.
  Injected-transport and registry-conformance tests cover dry-run purity,
  create/update classification, rate-limit reporting, and the DV pack's
  non-local-target refusal. Apricot remains externally blocked and Sheets is
  explicitly closed as a direct target because a client-side read/write
  pseudo-upsert would not meet the connector contract.
- **OpenSSF Scorecard in CI (roadmap D8).** `.github/workflows/scorecard.yml`
  runs the Scorecard analysis weekly and on every push to `main`, publishes
  results to the OpenSSF API, and uploads SARIF to Code Scanning. The first
  dated snapshot (aggregate 6.8, run 2026-07-17 with the CLI) is committed at
  `docs/audits/scorecard-2026-07.md` with an honest reading of each low score.
- **Sandboxed PDF parsing is now the pipeline default (FIX-10 wiring).**
  `read_pdf_records` runs every PDF parse in the resource-limited child
  process that `extract/sandbox.py` introduced but nothing previously used:
  a malformed or hostile intake file fails closed to human review instead of
  crashing the run. `backend = "pdfplumber+ocr"` gets the same containment
  through a dedicated OCR child worker. Recipes opt out with `[extract]
  sandbox = false`. The threat model's "missing process boundary" finding and
  its residual-risk list are updated to match; the boundary is containment,
  not privilege separation, and the docs say so.
- **Every non-dry-run export stamps `out/run_manifest.json` (FIX-08 wiring).**
  `pipeline.export` now builds the reproducibility manifest that
  `manifest.py` introduced but nothing previously called: BLAKE2b digests of
  the recipe file and each input file, package and Splink versions, resolved
  thresholds, and policy pack. The provenance log opens with a `run-start`
  entry carrying the manifest's hash, ahead of the run's write entries, so
  every write chains back to the exact configuration that produced it; dry
  runs stamp nothing. A Recipe built in code (no recipe file) records a null
  recipe hash rather than inventing one.
- **Definition of done, PR template, hygiene gate, and ledger ownership
  (maturity batch M7/M9/M10).** `DEFINITION_OF_DONE.md` writes down the
  working agreement the quality bar implied; `.github/pull_request_template.md`
  asks for the same items at review time, including eval-regeneration,
  audit re-stamping on release, and new-dependency rationale. `make hygiene`
  (tools/hygiene.py, inside `make verify` and CI) fails on debt markers,
  uncoded suppressions, unexplained coverage exclusions, and bare Semgrep
  waivers, with its own test suite. The metrics ledger gains Measured-by and
  Owner columns plus a quarterly solo-scale DORA note that states plainly
  what is not yet measurable.
- **Canonical GenAI observability for opt-in extraction seams.** Bedrock
  Converse and loopback-local model calls now emit the reviewed, pinned
  STANDARDS telemetry schema: provider/model identity, input/output tokens,
  duration, finish reason, and estimated cost. Page images, prompts, responses,
  record ids, and extracted values are excluded; regression tests exercise the
  JSON payload with representative PII. The vendored shim records a public
  projection label and exact per-file SHA-256 manifest without exposing a
  private source-control identifier.
- **Disaggregated matching-risk audit (R5).** `evaluate()` accepts explicit
  planted pairs by risk class, the Markdown report renders per-class surfaced
  and blocking-miss counts, and `examples/bias-demo/` covers transliterated,
  hyphenated/punctuated, non-Western-order, rural-route, and informal-address
  cases. `make eval-bias` regenerates `docs/audits/bias-report.md`, and CI
  blocks report drift while preserving measured misses.
- **Binding human rejections (FIX-02)**: rejected pairs are cannot-link
  constraints on final clustering. If transitive AUTO edges would reunite a
  rejected pair, the component is refused and its automatic edges return to
  review with an explicit explanation; no golden record may contain both
  rejected endpoints.
- **Attributed field correction in review (EXP-01)**: reviewers can fix a field
  while approving a pair. Values live only in local `corrections.json`; the
  decisions audit remains PII-free. A correction invalidates earlier verdicts,
  and two-person mode requires a later distinct reviewer to see and approve the
  corrected evidence before apply. Corrections are applied before normalization
  and flow through lineage, matching, and export.
- **Reviewer calibration with planted pairs (EXP-09)**: `[review] calibration = N`
  deterministically mixes clearly synthetic known-answer pairs into the local
  review queue. A persistent banner discloses their presence, the CLI reports
  reviewer agreement and Cohen's kappa, and planted records and verdicts are
  excluded from `decisions.json` and its audit trail so they can never reach
  `reconcile apply` or a connector.

### Changed
- **A merged identity now takes its most restrictive member's consent, not its
  survivor's (#83, ADR 0013).** Golden-record consent was the survivor's, so a
  cluster whose surviving record carried a grant and whose other record carried
  a revocation exported under the grant. `decisions.golden_records` now derives
  the merged lifecycle with the new `Consent.most_restrictive`: one revoked
  member revokes the merge, one absent or unrecognized member makes it absent,
  the latest `granted_on` and the earliest `expires_on` govern, and scopes
  intersect (members sharing no destination become
  `models.NO_COMMON_DESTINATION`, which is out of scope everywhere). The
  property the regression test asserts over every combination of member
  lifecycles, dates, and destinations: a merged consent is active only where
  every member's is, so a merge can narrow what a person granted and can never
  widen it. The write path and the `compare-apply` correction export change
  together because both merge through `golden_records`. Single-record clusters
  are unaffected. Under a consent-requiring pack, organizations whose sources
  disagree about consent will see more records withheld, each with its reason,
  which is the intended trade. ADR 0013 records the VAWA, FVPSA, OVW, NNEDV,
  and HIPAA sources read for the decision, including their silence on record
  merges.
- **The cutover correction file carries the target system's own record ids, and
  merges under the recipe's fill policy (#84).** `target_corrections.csv` keyed
  every row on the reconciler's identity id, which the target has never seen, so
  an upsert on it would add a record rather than update the matching one. Rows
  now also carry `target_record_ids`, the ids the target export itself supplied
  (its `input.id_column` values, pipe-separated for a multi-record identity,
  empty for an identity the target does not hold yet and for an export with no
  id column). `compare-apply` also merged golden values under the package
  default fill policy while `pipeline.run` threaded the recipe's setting;
  `compare.run_compare` now resolves one policy for both sides, refusing two
  recipes that disagree the way it already refuses mismatched thresholds, and
  the manifest's `export` section records which policy governed.
  `CUTOVER_CORRECTIONS_SCHEMA_VERSION` is 2. Migration: both changes are
  additive, so a consumer reading `target_corrections.csv` by column name finds
  every column it had; a consumer reading by position must account for the new
  final column.
- **Docs now record the live branch ruleset and current gates (standards
  conformance pass, 2026-08-07).** A `protect-main` ruleset has been active
  on the repository since 2026-07-09, but the README, ROADMAP,
  RESEARCH-ROADMAP, the external-gates runbook, and `docs/rulesets/README.md`
  still said no live ruleset existed. They now record the applied state and
  its remaining delta from the committed desired-state profile (no
  pull-request or linear-history rule live yet, non-strict up-to-date
  policy, six more required check contexts), with a dated follow-up note
  appended to ADR 0008 per its append-only convention. The README Standards
  Conformance table gains the Incident Response and Data Governance rows the
  Documentation Standard's thirteen-standard table requires, SECURITY.md's
  threat surface catches up with shipped PDF/OCR extraction and the
  sandboxed parse default, and a stale coverage-floor comment in `ci.yml`
  now matches the enforced 85%. No behavior changes; no test or privacy
  gate is touched.
- **Field-level lineage and a named survivorship fill policy (FIX-07)**
  (`models.py`, `decisions.py`, `config.py`, `pipeline.py`, `provenance.py`,
  `schema.py`). Every golden record now carries `field_sources`, mapping each
  non-empty merged field to the member record id that supplied its value, and
  blank-fill order is a named policy (`[policy] fill` in the recipe, default
  `survivor-then-lowest-id`, the previous implicit behavior) validated at
  load time. Provenance entries record the lineage as member ids only, never
  field values, and `aggregate_summary.json` names the active policy.
  `REPORT_SCHEMA_VERSION` bumps 2 to 3 for the new keys; version-2 artifacts
  still verify unchanged.
- **CiviCRM email and phone write through dedicated entities (R7)**
  (`connectors/civicrm.py`). Email and phone move off the API v4 join-field
  shorthand onto the dedicated Email and Phone entities: once the contact id
  is resolved, the connector updates the contact's primary row when one exists
  and creates it when none does. A record with no value for a field makes no
  call for it, so an empty value never blanks a stored row, and
  `Contact.create` returning no id now raises `ConnectorError` before any
  entity write instead of reporting a created row without an id. Dry-run
  payloads and the provenance hash input still carry email and phone. The
  recorded end-to-end demo against a running CiviCRM stays open in
  `docs/ROADMAP.md` v0.2.
- **Record identity is content-derived and collision-safe (FIX-03)**
  (`pipeline.py`). Generated record ids are now a BLAKE2b digest of the source
  name and the mapped raw values (for example `N3f9a2c1b0d4`) instead of the
  row position (`N0001`), for CSV rows, extracted PDF pages, and .txt/.eml
  bodies alike. Inserting or reordering rows in a source file between
  `reconcile review` and `reconcile apply` no longer re-binds recorded
  verdicts to different people; editing a row changes that row's id and no
  other. Exact-duplicate rows share the digest and carry a deterministic `-2`,
  `-3`, ... suffix in read order.
- **User-supplied ids are namespaced by source.** A value read from the
  recipe's `id_column` becomes `existing:E003` or `incoming:N002`, so the same
  id in two source files can no longer collide. An id that still appears twice
  in one run (a duplicated `id_column` value within one source) raises
  `pipeline.DuplicateIdError` naming the id and source, instead of silently
  dropping one of the records.
- **The decisions file is a versioned surface.** `decisions.json` now carries
  a `decisions_schema` field (`schema.DECISIONS_SCHEMA_VERSION`),
  reported by `reconcile schema` alongside the other declared versions. A
  resumed review session warns on stderr when a saved decision references a
  pair that is not in the current run's review queue, instead of ignoring it
  silently.

### Fixed
- Canonicalized the architecture decision log under `docs/adr/`, added the
  portfolio meta-ADR and authoring template, and resolved the duplicate 0009
  identifier by renumbering the automated axe decision to 0011. Repository
  references and the README conformance table now use the canonical path and
  labels; the declared but unimplemented internationalization commitment
  remains an explicit gap.
- GenAI telemetry now rejects negative and boolean token counts, drops unknown
  provider finish reasons, emits content-free fallback warnings, and isolates
  span/log exporter failures so instrumentation cannot change a provider result.
- `make test` no longer nests pytest-cov inside `coverage run`, which overwrote
  the valid pytest coverage data with an empty outer report. Pytest now owns the
  single coverage session, and the temporary 84% threshold is raised to the
  documented 85% branch-coverage gate (87.65% observed on this change).
- The review server accepts the IPv6 loopback host form it actually binds,
  without weakening the loopback-only and Host-header checks.
- Consent withholding summaries now evaluate the destination-scoped consent
  lifecycle, so an out-of-scope grant cannot be reported as exportable.
- CiviCRM contact creation fails closed when the API returns an empty response,
  before any email or phone entity write is attempted.

#### Migration note (FIX-03)
Record ids embedded in artifacts written by earlier versions (decisions files,
provenance logs, review queues, CRM external-id columns keyed on cluster ids)
will not match the ids this version mints from the same data. Finish and apply
any in-flight review with the version that produced it, then re-run
`reconcile run` under this version before recording new decisions. Existing
provenance logs remain verifiable as written; only newly appended entries carry
the new ids. Per the ADR 0006 stability contract this is a pre-1.0 surface
change, shipped with this changelog entry.

### Added
- **Generic webhook connector** (`connector = "webhook"`, `connectors/webhook.py`),
  the self-contained slice of roadmap item E3 (Apricot, Airtable, Sheets,
  generic webhook): pushes resolved, consented records as one JSON POST per
  record to any HTTP(S) endpoint, with an optional bearer token and an
  optional HMAC-SHA256 request signature (`[output] signing_secret_env`) so a
  receiver can verify authenticity. Registered on the connector registry
  (`connectors.register`, FIX-09) and passes the same conformance suite
  every connector does; `is_local = False`, so the `dv` policy pack refuses
  it as a write target the same way it refuses CiviCRM and Salesforce.
  Consent, including the destination-scoped lifecycle gate
  (`Consent.reason(destination=...)`), is enforced upstream by the existing
  export gate, not reimplemented. Documented payload shape, worked example,
  and signature verification code: `docs/connectors/webhook.md`. Example
  recipe: `examples/intake-demo/recipe-webhook.toml`.
- Design briefs (research, not implementation) for E3's three remaining,
  proprietary-vendor connectors -- Apricot (Bonterra), Airtable, Google
  Sheets -- each covering auth model, rate limits, pagination, export shape,
  and `is_local` classification, cited against each vendor's current API
  docs: `docs/connectors/{apricot,airtable,sheets}-design.md`. Deferred as
  implementation pending a priority decision and API credentials this
  environment does not have.

### Security
- **Standards-conformance remediation (2026-07-10), release_workflow
  (REL-14)**: added `.github/workflows/release.yml`, a tag-triggered
  (`v*`) release pipeline that re-verifies the tagged commit (`make
  verify` + `make security`) independent of the PR's green check, checks
  tag/`pyproject.toml` version consistency, builds the sdist + wheel,
  generates a CycloneDX 1.7 SBOM, attests build provenance via keyless
  OIDC (Sigstore), and publishes a GitHub Release with the matching
  CHANGELOG section as notes. Closes the SBOM gap (P1-7) previously
  declared in `docs/RESPONSIBLE-TECH-AUDITS.md`. No PyPI publish stage yet
  (not published to PyPI); no `v*` tag has been cut, so the workflow is
  unexercised end-to-end pending the maintainer cutting the first release.

- **Web-boundary hardening for the review server (FIX-01)**: `reconcile
  review` now checks the `Host` header on every request against the address
  it actually bound (closing a DNS-rebinding path to the loopback-only
  server), checks a POST's `Origin` header, if present, against the server's
  own origin, and requires a per-run session token embedded in every rendered
  form on every POST. Together these mean a hostile page the reviewer has
  open elsewhere can no longer forge a verdict or read a pair over the
  loopback interface by guessing a port. `docs/ideation/02-large-scale-fixes.md`
  named this the most serious observed gap between the DV pack's stated
  "cannot become an egress path" claim and the code.

### Added
- **Fail-closed recipe validation and `reconcile validate` (FIX-04)**:
  `config.load_recipe` now rejects an unknown `[section]` or an unknown key
  inside a known section — naming the nearest valid spelling — instead of
  silently ignoring it via `dict.get`. A mapping key outside the canonical
  fields (a typo'd `frist_name`) now raises instead of vanishing from the
  mapping. A new `reconcile validate --config recipe.toml` command loads and
  shape-checks a recipe, confirms `incoming`/`existing` point at files that
  exist, and prints the active policy pack, thresholds, and switches, without
  running the pipeline. `docs/ADOPTION-KIT.md` gets a "validate before you
  run" step.
- **Local OCR backend for scanned intake (EXP-04)**: `extract/ocr.py` adds a
  `PdfplumberOcrExtractor`, selected by `[extract] backend = "pdfplumber+ocr"`,
  that OCRs (via Tesseract, the new optional `ocr` extra) any PDF page with no
  embedded text layer instead of yielding an empty record. Reuses the existing
  label-adjacent field patterns and confidence gate; OCR word boxes become
  `SourceSpan`s so the review queue's source-location columns work the same
  for scanned and digitally-created pages. Closes the gap FIX-12 documented
  between the README's ingest claim and what the code did.

### Security
- **Standards-conformance remediation (2026-07-05)**, closing the audit's
  headline gap — this repo held the portfolio's most sensitive PII (DV-survivor
  constituent records) with zero security scanning and no lockfile:
  - Committed `uv.lock`; `make install` now runs `uv sync --frozen`, so CI and
    local installs use the exact locked dependency set (no floating
    transitive deps under Splink).
  - Dependency-vulnerability gate: `make security` (`pip-audit` +
    `osv-scanner --lockfile uv.lock`), wired as its own blocking CI job.
  - Secret scanning: `.pre-commit-config.yaml` (gitleaks + ruff, staged
    changes), a `secrets` CI job (gitleaks full-history on push/PR), and a
    scheduled weekly TruffleHog (verified-credentials-only) workflow. A
    one-time full-history gitleaks scan on 2026-07-05 found nothing to rotate.
  - `ruff` now runs with the `S` (bandit) and `C90` (complexity) rule sets and
    `ruff format --check`; Python floor raised to 3.12 (`.python-version`,
    `pyproject.toml`, `Dockerfile`).
  - ASVS level (L2) and container-scan/SBOM/secret-management/VEX posture
    declared in `docs/RESPONSIBLE-TECH-AUDITS.md`; AI-Evaluation-Standard N/A
    and an Observability Tier-C declaration added to `docs/ROADMAP.md`; a new
    `docs/I18N.md` declares EN/ES parity as deferred-not-dropped.
  - README gained the standards-conformance table this repo previously
    omitted (silent omission is itself a defect under the portfolio's
    documentation standard); status line now leads with "Beta" per the
    standard vocabulary.
  - `docs/adr/0008-solo-maintainer-review-waiver.md` records, dated and
    reasoned, that the ≥1/≥2-human-reviewer control is waived while this repo
    has one maintainer, and names the compensating automated gates.
    `docs/rulesets/main.json` is the matching desired-state branch ruleset —
    committed as an artifact but **not yet applied**; applying it is a
    repository-settings action for the maintainer to run (see
    `docs/rulesets/README.md`).
  - SAST: a `sast` CI job runs Semgrep (`p/security-audit`, `p/secrets`, and a
    repo-specific `no-pii-in-logs` rule at `.semgrep/no-pii-in-logs.yml`); a
    `codeql` workflow runs CodeQL for `python` and `actions` on push, PR, and a
    weekly schedule; a `zizmor` job lints the workflow files themselves.
  - A merge-blocking coverage floor (`--cov-fail-under=84`, branch coverage,
    `pytest-cov`) closes the gap between the ROADMAP's earlier claimed figure
    and CI actually enforcing it. Set to 84 rather than the 85 target because
    this PR excludes a pre-existing, in-progress feature branch (see
    `docs/ROADMAP.md`'s metrics ledger note); raise it to 85 once that branch
    lands with its own tests.
  - `__version__` is now derived from installed package metadata
    (`importlib.metadata.version`) instead of being hand-copied alongside
    `pyproject.toml`'s `version`, so the two can no longer drift (REL-02).
  - The Dockerfile's base image is pinned by digest, not just tag
    (`python:3.12-slim@sha256:...`), and a `container-scan` CI job
    (`make docker` + Trivy, blocking CRITICAL/HIGH) was added — **not yet
    locally exercised**, since no Docker daemon was available in the
    environment this remediation ran in; verify on the first real PR.
  - The portfolio-level conformance audit and this remediation's full
    item-by-item execution log live outside this repo, alongside the
    portfolio's other project audits; what remains open here is container
    scanning, the release pipeline, and the branch ruleset actually being
    applied (docs/rulesets/README.md).
- **Reviewer audit trail** (roadmap E4): every review verdict is attributed.
  `reconcile review` now requires `--reviewer NAME`, and `decisions.json` grows a
  versioned shape (`decisions_schema: 2`) with an `audit` section mapping each pair
  to the reviewers who decided it, their verdicts, and UTC timestamps. The
  top-level `approved`/`rejected` lists keep the version-1 shape, so
  `reconcile apply` reads both versions unchanged, and the file still carries no
  field value of a reviewed record. A blank reviewer name is refused,
  fail-closed. Sessions resume from either file shape; verdicts from a version-1
  file resume attributed to `unrecorded`.
- **Two-person review** for sensitive merges: with
  `reconcile review --require-second-reviewer`, or
  `require_second_reviewer = true` in the recipe's new optional `[review]`
  section (on by default under the `dv` policy pack), a merge only lands in
  `approved` after two distinct reviewer names approve it. A lone approval is
  held in the audit section as awaiting a second reviewer, the same name cannot
  supply both approvals, and any rejection rejects immediately; disagreement
  never merges. `reconcile apply` refuses a decisions file that still holds
  half-approved pairs, naming them. The review pages show who is reviewing and
  which pairs await a second reviewer. A recipe or flag may turn the requirement
  on under any pack; nothing may turn off a pack that imposes it. 19 new tests
  (149 total).

## [0.7.0] - 2026-06-29

The WCAG 2.2 AA web review queue and the offline-first CRM export files, two of
the items the README named as remaining before the 1.0 tag.

### Added
- **Local web review UI** (`review/`, `reconcile review`): a non-technical
  reviewer steps through the uncertain candidate pairs in a browser, sees the two
  records side by side with their source spans, and approves or rejects each
  merge. Verdicts are written to `decisions.json` in the same shape
  `reconcile apply` consumes, so the web step replaces the hand-edited CSV without
  changing the rest of the pipeline. Built on `http.server` with no web-framework
  dependency.
  - **Offline by construction**: binds the loopback interface only, inlines all
    CSS and script (no CDN or network fetch), and under the DV pack refuses a
    non-loopback bind, fail-closed, mirroring the connector local-target gate.
  - **Minimization**: the only artifact the review step persists is
    `decisions.json`, which carries record ids and verdicts and no field value;
    request logging is suppressed. A test asserts no reviewed field value reaches
    the file.
  - **Accessibility (WCAG 2.2 AA)**: a real comparison table with scoped headers,
    status carried by text and a symbol rather than colour alone, decision
    controls that work with no JavaScript, keyboard shortcuts as enhancement.
- **Import-ready CRM export connectors** (`connectors/crm_csv.py`):
  `salesforce_csv` and `civicrm_csv` write a CSV mapped to the target CRM's import
  schema (NPSP Contact columns; CiviCRM import columns) plus an external-id column
  keyed on the cluster id, for an idempotent CRM-side upsert. This is the
  offline-first default path: no network call, no secret. The live API push
  connectors stay the explicit opt-in. The column schema is shared with the live
  connectors (`salesforce.FIELD_MAP`, `civicrm.IMPORT_FIELD_MAP`) so the file and
  the API payload cannot drift. Both targets are `is_local = True`, so the DV pack
  permits them while still refusing the network push.
- **ADR 0007** (`docs/adr/0007-review-ui-and-crm-export.md`).
- `examples/intake-demo/recipe-salesforce-csv.toml` and `recipe-civicrm-csv.toml`;
  20 new tests (128 total).

### Note
- The review UI's structural WCAG 2.2 AA work is in place; a full axe audit and a
  screen-reader walkthrough remain a REVIEW gate in `docs/ROADMAP.md`.

## [0.6.0] - 2026-06-27

The v1.0 engineering deliverables, shipped without the 1.0 tag (the tag is gated
on real-organization adoption, per `docs/ROADMAP.md`).

### Added
- **Salesforce NPSP connector** (`connectors/salesforce.py`): a second
  destination on the same `Connector` interface, using the REST
  upsert-by-external-id endpoint (`PATCH /sobjects/Contact/<ExternalIdField>/...`)
  for idempotent re-runs. Built on an injected transport, fully tested offline.
  `is_local = False`, so the DV pack refuses it like any network target.
- **`[output]` recipe keys** `api_version` and `object_name` for Salesforce;
  `auth_env` now names the token env var for either CRM.
- **One-command Docker self-host** (`Dockerfile`, `.dockerignore`,
  `make docker`), with the PDF extraction extra included.
- **Declared schema/interface versions** (`schema.py`): config, connector
  interface, and report schema versions, exposed by a new `reconcile schema`
  command and stamped into `aggregate_summary.json` as `schema_version`.
- **DPG Standard conformance note** (`docs/DPG-CONFORMANCE.md`) mapping the
  project against the nine indicators.
- **ADR 0006** (`docs/adr/0006-schema-stability.md`) defining the
  stability contract.
- `examples/intake-demo/recipe-salesforce.toml`; 13 new tests (108 total).

### Note
- The Docker image was not built end-to-end in CI in this release (the
  Dockerfile follows standard patterns); `make docker` builds it locally.

## [0.5.0] - 2026-06-27

### Added
- **DV policy pack** (`src/constituent_reconciler/policy.py`): a declarative
  bundle of VAWA/FVPSA confidentiality invariants enforced as merge-blocking
  behavior. `policy_for` maps a pack name to a `Policy`; an unknown pack raises
  `PolicyViolation`, fail-closed.
- **`--policy-pack` CLI flag** on `run` and `apply`, and a `policy_pack` override
  on `load_recipe`, so the DV posture can be applied to any recipe without
  editing it.
- **No-egress enforcement**: connectors now declare `is_local`; under a
  local-targets policy the export refuses a non-local target (CiviCRM) before any
  write. The cloud extraction seam was already fused off for the dv/hipaa packs.
- **Aggregate, suppression-aware export** (`suppression.py`): the DV pack writes
  `aggregate_summary.json` of non-identifying counts with CMS-style small-cell
  suppression — counts of 1-10 suppressed, true zeros preserved, complementary
  suppression so a lone suppressed cell is not recoverable by subtraction.
- **`PolicyViolation`** exception, surfaced by the CLI with a clear message and a
  non-zero exit.
- **ADR 0005** (`docs/adr/0005-dv-policy-pack.md`) and an expanded privacy
  section in `docs/RESPONSIBLE-TECH-AUDITS.md` with primary VAWA/FVPSA/CMS
  citations and three honesty corrections (the statutory verb is "disclose,
  reveal, or release"; revocable consent is NNEDV best practice not statute; the
  n<11 threshold is the CMS rule, not a HUD/DV mandate).
- 30 new tests across `test_policy.py`, `test_suppression.py`, and the
  merge-blocking `test_no_egress.py` (95 total).

### Changed
- `Recipe` gains `require_local_targets`, `aggregate_export`, and
  `suppression_threshold`, derived from the active pack. `require_consent` now
  comes from the pack and cannot be turned off below what the pack imposes.
- `ExportSummary` gains `aggregate` and `aggregate_path`.
- Version bumped to `0.5.0`.

### Not yet
- The WCAG 2.2 AA web review UI, RFC 3161 timestamping wired to a TSA, and a
  recorded end-to-end demo into a running CiviCRM instance. The `hipaa` pack is
  partial (consent plus no cloud seam) and does not claim the DV pack's full
  invariant set. See `docs/ROADMAP.md`.

## [0.4.0] - 2026-06-27

### Added
- **Address normalization** (`src/constituent_reconciler/address.py`): a
  vendored, deterministic CASS-style standardizer that maps an address to
  USPS-style abbreviations (USPS Publication 28 street suffixes, directionals,
  unit designators), so two writings of the same address reduce to one matching
  key. Idempotent and offline.
- **`address` canonical field** with a three-level matcher comparison (exact,
  close by Jaro-Winkler at 0.90, else), weighted below email.
- **Optional libpostal backend**: `[normalize] address_backend = "libpostal"`.
  Never required; selecting it without the `postal` package and libpostal C
  library raises a clear `ImportError` instead of silently falling back.
- **`[normalize]` recipe section** with `address_backend` (default
  `"deterministic"`).
- **`examples/address-demo/`**: fixture and recipe demonstrating address-format
  variation resolving to a merge.
- **ADR 0004** (`docs/adr/0004-address-normalization.md`).

### Changed
- `Record` gains an `address` slot in the canonical schema, active only when a
  recipe maps it; the committed demo eval is unchanged (CI-verified).
- `normalize_record` now preserves `Record.spans` (a latent v0.3 bug where spans
  were dropped before reaching the review queue in a full pipeline run).
- Version bumped to `0.4.0`.

### Not yet
- The DV policy pack full invariant set (v0.5), the WCAG 2.2 AA web review UI,
  and RFC 3161 trusted timestamping wired to a TSA. The address standardizer is
  CASS-style and is **not** USPS-certified. See `docs/ROADMAP.md`.

## [0.3.0] - 2026-06-27

### Added
- **Extraction seam** (`src/constituent_reconciler/extract/`): an offline
  pdfplumber-based PDF extractor that pulls canonical fields from form-like PDFs
  using label-adjacent patterns and returns a confidence score and source-span
  pointer per field.
- **Source-span pointers** on `Record.spans` (`dict[str, SourceSpan]`): each
  PDF-sourced field carries the source filename, page number, and bounding box.
  The review queue CSV gains `{field}_left_span` and `{field}_right_span` columns
  when any record has spans, so a reviewer can navigate back to the original.
- **`SourceSpan`** type in `constituent_reconciler.models`, re-exported from the
  top-level package.
- **Policy-gated cloud seam**: `NoOpSeam` (default) and `BedrockSeam` (the
  documented extension point for deployers with AWS credentials). `make_seam()`
  returns `NoOpSeam` unconditionally for `dv` and `hipaa` policy packs; the
  non-egress invariant is enforced at construction time and covered by tests.
- **Folder-based ingestion**: the recipe's `incoming` field can point to a
  directory; the pipeline routes `.csv` files through the structured reader and
  `.pdf` files through the extractor.
- **`[extract]` recipe section**: `backend` (default `"none"`) and
  `confidence_threshold` (default `0.5`).
- **`pdfplumber>=0.11`** added as an optional `[extract]` dependency and to the
  `[dev]` extras so extraction tests run in CI.
- **`cohen_kappa(predicted, actual)`** in `evaluate.py`: the calibration seam
  for comparing an LLM extraction judge's confidence against human-labeled field
  accuracy. Not yet wired into the eval report; the function is the planned seam.
- **ADR 0003** (`docs/adr/0003-extraction-seam.md`): documents the
  pdfplumber choice, the regex-over-text-layer approach, the confidence
  heuristic, and the cloud-seam protocol.

### Changed
- `Record` gains `spans: dict[str, SourceSpan]` (default empty dict). Existing
  CSV-only code and tests are unaffected.
- `Recipe` gains `extract: ExtractConfig` (default `backend="none"`). Existing
  recipes with no `[extract]` section use the CSV-only path unchanged.
- Version bumped to `0.3.0`.

### Not yet
- `BedrockSeam.refine()` raises `NotImplementedError` until a deployer wires in
  page-to-image conversion and the Bedrock response parser.
- Address normalization, the WCAG 2.2 AA web review UI, and the DV policy pack
  full invariant set. RFC 3161 trusted timestamping is a pluggable authority, not
  yet wired to a TSA. See `docs/ROADMAP.md`.

## [0.2.0] - 2026-06-24

### Added
- CiviCRM write-back via API v4, an upsert keyed on an external identifier
  so re-runs update contacts instead of duplicating them, built on an injected
  transport for testability. A connector interface with the CSV writer refactored
  onto it. An append-only, tamper-evident provenance log (BLAKE2b hash chain)
  with a `reconcile verify` command and a pluggable timestamp authority. An
  `[output]` recipe section that selects the connector.

## [0.1.0] - 2026-06-24

### Added
- Resolve and review core. Reads existing and incoming CSVs, normalizes,
  scores candidate pairs with a Splink matcher configured by pre-tuned m and u
  defaults (no training, no labeled pairs), assigns each pair to an auto, review,
  or drop band, clusters confident merges, and writes resolved records plus a
  review queue.
- Fail-closed gate: uncertain pairs go to review, never to an auto-merge.
- Consent export gate: under a consent-required policy pack, a record without
  granted consent is withheld and recorded without field values.
- `reconcile run`, `reconcile eval`, and `reconcile apply` commands.
- Committed eval (`eval/report.md`) on seeded synthetic fixtures with planted
  ground truth, reporting a gated false-merge rate with Wilson intervals.
