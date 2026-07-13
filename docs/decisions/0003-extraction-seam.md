# 0003 — Extraction seam and cloud gate

Status: accepted (v0.3); implementation amendment recorded 2026-07-12

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
empty page or near-empty page, which without a further step routes to the
cloud seam by design -- and, absent a cloud seam (the default, and the only
option under `dv`/`hipaa`), produces nothing at all.

pdfplumber is an optional dependency under a new `[extract]` optional group.
The import is deferred to extraction time so the rest of the package works
without it installed, and the CI `dev` extra includes it so the extraction tests
always run in CI.

### Local OCR fallback for image-only pages (EXP-04)

Paper intake is the default reality for the target segment, not an edge case,
and an offline-only deployment (`dv`, `hipaa`) can never reach the cloud seam.
Leaving an image-only scan to produce an empty record was a real gap, not a
documented limitation: the README claimed scanned-intake support before the
code did (closed by this change; see `docs/ideation/03-expansions.md` EXP-04).

`extract/ocr.py` adds a `PdfplumberOcrExtractor`, selected by
`[extract] backend = "pdfplumber+ocr"`, that runs page-by-page: a page with a
text layer takes the existing pdfplumber path unchanged; a page with none is
rasterized with pdfplumber's own renderer and OCR'd via Tesseract
(`pytesseract`, a new optional `ocr` extra -- the "contribution is the chain,
not a new OCR engine" principle above still holds; Tesseract is a vendored,
well-understood offline engine, not a bespoke one). OCR'd pages reuse the same
label-adjacent field patterns and the same `_page_confidence` plausibility
gate as the text-layer path, blended with Tesseract's own per-word
confidence -- the lower of the two wins, so a scanned page is never easier to
pass than a native PDF. OCR word boxes become `SourceSpan`s in the same PDF
point-space coordinates the text-layer path already produces, so the review
queue's source-span navigation works identically for both. Both `pytesseract`
and the system `tesseract` binary are optional; a PDF whose pages are all
digitally created never imports either.

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

### Cloud seam: protocol with an opt-in integration

`CloudSeam` is a Protocol. `NoOpSeam` always returns nothing. `BedrockSeam`
checks for boto3 at `is_enabled()` time. The original v0.3 decision shipped
`refine()` as an explicit placeholder; the later implementation now renders
one page to PNG, calls Bedrock Converse, parses strict JSON, and falls back to
the offline extraction on call or response failure. The protocol and
construction-time policy gate did not change.

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

`cohen_kappa(predicted, actual)` is in `evaluate.py` and is now wired into the
eval report. Missing, malformed, or below-0.6 calibration labels fail closed.
That gate measures agreement on the committed synthetic labels; it is not a
live Bedrock accuracy claim.

## Consequences

- Records from PDFs carry `SourceSpan` values in `spans`, which the review queue
  surfaces as source-location columns.
- The DV/HIPAA non-egress invariant now applies to the extraction step as well as
  the write step, enforced in `make_seam()` and covered by tests.
- pdfplumber is an optional runtime dependency; the core package still works for
  CSV-only runs without it.
- `BedrockSeam.refine()` is an opt-in implementation with a fake-able client,
  local fallback, and PII-free canonical token/duration/cost telemetry.
- `cohen_kappa()` is a fail-closed calibration gate over committed labels.
- `backend = "pdfplumber+ocr"` OCRs any page with no text layer via Tesseract
  (`pytesseract`, the new `ocr` extra); a text-layer PDF behaves identically to
  `backend = "pdfplumber"` and never imports the OCR path.
