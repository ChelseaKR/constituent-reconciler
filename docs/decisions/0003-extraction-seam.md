# 0003 — Extraction seam and cloud gate

Status: accepted (v0.3)

## Context

v0.3 adds the extraction step that was deferred in v0.1 and v0.2: turning a PDF
or scanned intake form into structured constituent fields, before normalization
and matching can run. Three constraints shaped the design.

**Offline-first is non-negotiable for the DV use case.** Victim-service
providers operate under VAWA and FVPSA confidentiality requirements that treat
any cloud service as a potential prohibited disclosure, regardless of encryption.
Any extraction that calls a remote API must be fused off under the `dv` policy
pack, and that invariant must be enforced at construction time, not at call time.

**The contribution is the chain, not a new OCR engine.** pdfplumber already
handles digitally-created PDFs well; complex scanned documents are a harder
problem that a cloud model handles more reliably. The right architecture wraps
these two things behind a protocol, not a bespoke OCR stack.

**Confidence must be honest.** A confidence score from a heuristic extractor
(word count, average word length) is not calibrated to field-level precision.
The score is used only to route low-confidence pages to a human or to the cloud
seam, not to weight match scores or claim probabilistic meaning it does not have.

## Decisions

### pdfplumber as the offline default

pdfplumber operates on the PDF text layer directly, requires no system-level
OCR dependencies (Tesseract, etc.), and produces clean text from
digitally-created PDFs with pdfminer-six under the hood. It is honest about
what it cannot do: a scanned-only page (no embedded text layer) produces an
empty page or near-empty page, which routes to the cloud seam by design.

pdfplumber is an optional dependency under a new `[extract]` optional group.
The import is deferred to extraction time so the rest of the package works
without it installed, and the CI `dev` extra includes it so the extraction tests
always run in CI.

### Field extraction by regex over the text layer

For the MVP, fields are extracted by label-adjacent regex patterns
(`First Name: <value>`, `DOB: <value>`, etc.). This is explicit and auditable.
The `[^\n]+` anchor stops a match at the next newline, preventing one label's
value from bleeding into the next. Patterns are ordered; first match wins.
The heuristic is honest: it only works for form-like PDFs where labels precede
values on the same line. Complex layouts score as low-confidence and route
elsewhere.

### Page-level confidence from word count and word-length plausibility

A page with fewer than five words, or where the average word length exceeds 15
characters (a sign of garbled OCR output), scores below 0.5. Otherwise the
page scores 1.0. This is a deliberate simplification: the score is a routing
signal, not a calibrated probability. When an LLM extraction judge is wired in
(a later hardening step), Cohen's kappa against human labels will be the
calibration gate, and `cohen_kappa()` in `evaluate.py` is the seam.

### Cloud seam: protocol, not integration

`CloudSeam` is a Protocol. `NoOpSeam` always returns nothing. `BedrockSeam`
checks for boto3 at `is_enabled()` time and defines the interface that a
deployer wires in; `refine()` raises `NotImplementedError` until the
page-to-image conversion and response parser are implemented, which makes the
gap explicit rather than silent.

The gate in `make_seam(policy_pack, backend)` is the policy enforcement point.
DV and HIPAA packs always get `NoOpSeam` regardless of what the recipe sets for
`backend`. This is enforced at construction time: there is no path from a DV
recipe to a live network call, and that absence is covered by a test
(`test_dv_pack_forces_no_op_seam`, `test_hipaa_pack_forces_no_op_seam`).

### Source-span pointers on Records

`Record` carries a `spans: dict[str, SourceSpan]` field, defaulting to empty.
CSV-sourced records have no spans; PDF-sourced records carry spans for each
field that pdfplumber could locate. The review queue CSV gains `{field}_left_span`
and `{field}_right_span` columns when any record in the run has spans, so a
reviewer can navigate back to the source document. The spans are informational:
the review queue and match scores do not depend on them, and a missing span does
not fail the record.

### Cohen's kappa for extraction calibration

`cohen_kappa(predicted, actual)` is now in `evaluate.py`. It is not yet wired
into the eval report because v0.3 uses regex extraction rather than an LLM judge,
and labeling a corpus of extraction outputs for calibration is out of scope for
this phase. The function is the seam: when a cloud seam's confidence scores are
compared against human-labeled extraction accuracy, kappa below 0.6 is the
drift threshold at which the gate fails closed.

## Consequences

- Records from PDFs carry `SourceSpan` values in `spans`, which the review queue
  surfaces as source-location columns.
- The DV/HIPAA non-egress invariant now applies to the extraction step as well as
  the write step, enforced in `make_seam()` and covered by tests.
- pdfplumber is an optional runtime dependency; the core package still works for
  CSV-only runs without it.
- `BedrockSeam.refine()` raises `NotImplementedError` until a deployer wires in
  the full implementation; the placeholder is intentional and honest.
- `cohen_kappa()` is available in `evaluate.py` for when a labeled extraction
  corpus and an LLM field-judge are in scope.
