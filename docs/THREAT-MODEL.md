# Threat model: the untrusted-document parse path

This document models the threats to the path that parses operator-supplied
files, PDF extraction in particular, because that is the one place the tool
runs complex parsing logic over bytes an adversary may have crafted. It closes
the security TODO in [`RESPONSIBLE-TECH-AUDITS.md`](./RESPONSIBLE-TECH-AUDITS.md)
and pairs with [`SECURITY.md`](../SECURITY.md), which owns reporting and the
out-of-scope list.

Status: committed 2026-07-02 against the v0.7 code. Revisit whenever the
extraction surface changes, and at minimum when the sandboxed extraction path
lands.

## System description and trust boundaries

`reconcile run --config recipe.toml` (`src/constituent_reconciler/cli.py`)
hands the recipe to the orchestrator in `src/constituent_reconciler/pipeline.py`.
`_ingest_source()` routes each source path by extension: a `.csv` is read with
the standard-library `csv` module in `read_records()`, and a `.pdf` is routed
to `read_pdf_records()` only when the recipe sets `extract.backend` to
something other than `"none"`. PDF extraction lives in
`src/constituent_reconciler/extract/pdf.py`. It opens the file with
pdfplumber, an optional dependency installed via the `extract` extra, which in
turn runs the pdfminer.six parsing stack. Scanned intake forms enter through
the same path; a scan whose text layer is missing or garbled is exactly the
low-confidence case the heuristics below are built for.

The boundaries that matter:

1. **The file boundary.** Every byte of an operator-supplied CSV, PDF, or scan
   is untrusted. Intake documents arrive from the public: a constituent, a
   partner agency, an email inbox. The operator who runs the tool is trusted;
   the files they feed it are not.
2. **The missing process boundary.** pdfplumber and pdfminer run in the same
   process as the pipeline, with the pipeline's full privileges and its view of
   the filesystem. There is no sandbox between hostile input and the parser
   today. This is the central finding of this document; the planned mitigation
   is below.
3. **The network boundary.** The pipeline is offline by default. It has two
   deliberate egress points, both policy-gated. The cloud extraction seam
   (`src/constituent_reconciler/extract/seam.py`) may send a low-confidence
   page to a Claude model on Amazon Bedrock, and only when the active policy
   pack allows cloud calls, the page falls below the recipe's confidence
   threshold, and credentials exist. Under the DV and HIPAA packs
   `make_seam()` returns a `NoOpSeam` at construction time, so no code path
   can reach a network call. The CRM connectors are the second egress; under a
   pack that requires local targets, `build_connector()` refuses a non-local
   connector before anything is written.
4. **The local web boundary.** `reconcile review`
   (`src/constituent_reconciler/review/server.py`) is a second untrusted-input
   surface: the reviewer's own browser can be turned against a local server.
   It is already hardened and is cited under mitigations as a resolved
   finding.

## Assets

- **Constituent PII.** Names, dates of birth, email addresses, phone numbers,
  and street addresses, in the source files, in memory, and in the outputs.
- **Consent state.** The per-record consent field that decides whether a
  record may be exported at all. Corrupting it converts a withheld record into
  an exported one.
- **DV-pack suppressed artifacts.** `withheld.csv` (ids and a reason, never
  field values) and `aggregate_summary.json` (suppressed counts only). Their
  value is precisely what they leave out.
- **Exports and the provenance log.** `resolved.csv`, the CRM import files,
  and the append-only BLAKE2b hash chain that records every write.

## Threats

| ID | Threat | Vector | Impact |
| --- | --- | --- | --- |
| T1 | Parser exploitation (elevation of privilege) | A malformed PDF triggers a memory-safety or logic bug in pdfminer/pdfplumber | Code execution in the operator's context, with access to every asset above |
| T2 | Resource exhaustion (denial of service) | A decompression bomb, a document with thousands of dense pages, or pathological text fed to the field regexes | The run hangs or exhausts memory; intake stops |
| T3 | Data poisoning (tampering) | Crafted or garbled-OCR content plants another person's identifiers in a page, steering the matcher toward a false merge | A wrong merge corrupts or exposes a constituent's record across programs, the exact harm the system is built to avoid |
| T4 | PII leakage on failure (information disclosure) | A parse failure raises an exception whose traceback or log line carries page content or identifying values | Sensitive data lands in terminals, logs, or bug reports |
| T5 | Cloud-seam egress (information disclosure) | Misconfiguration sends a low-confidence page containing PII to the cloud despite a policy that forbids it | Disclosure that VAWA/FVPSA prohibit for a victim-service provider |

## Mitigations

### Present

- **Construction-time seam fusing (T5).** Under the DV and HIPAA packs the
  cloud seam is a `NoOpSeam` from the moment it is built, so there is no
  window where a misconfigured seam could call out. Enforced by
  `tests/test_extract.py` (`test_dv_pack_forces_no_op_seam`,
  `test_hipaa_pack_forces_no_op_seam`) and
  `tests/test_no_egress.py` (`test_dv_pack_fuses_the_cloud_extraction_seam_off`).
  The non-local write target is refused before any write
  (`test_dv_pack_refuses_a_non_local_write_target`).
- **Confidence heuristics on every page (T3).** `_page_confidence()` in
  `extract/pdf.py` scores near-empty pages and garbled-OCR pages (average word
  length above 15 characters) below 0.5. Low-confidence values inherit that
  score, and the pipeline never auto-merges on uncertainty: ambiguous pairs are
  banded to the human review queue, where the reviewer sees the source span
  beside the candidate. A page that yields neither a first nor a last name
  produces no record at all.
- **Consent enforced before the connector (T3, T4).** Records without granted
  consent under a consent-required policy are withheld before any connector is
  touched. Enforced by `tests/test_consent.py` and the DV-pack pipeline test
  in `tests/test_pipeline.py`.
- **Minimized failure artifacts (T4).** `withheld.csv` lists cluster id,
  member ids, and a reason, never field values, so the record of what was
  withheld cannot itself leak the data
  (`test_dv_pack_withholds_non_consented_records_without_field_values`).
  Span lookup in `extract/pdf.py` swallows its own errors rather than
  propagating them with page content attached.
- **Optional parser (T1, T2).** pdfplumber is imported only when a PDF is
  actually routed to extraction and only if the `extract` extra is installed.
  A CSV-only deployment never loads the PDF parsing stack, which removes this
  surface entirely for the deployments that do not need it.
- **Review-server web boundary (resolved finding).** The local review server
  binds loopback and refuses a non-loopback host under the DV pack
  (`build_server()`), and it enforces a web boundary beyond the bind: every
  POST requires the per-session CSRF token from the server's own rendered
  form, a request whose Host header does not name the bound loopback server is
  refused (defeating DNS rebinding), and a POST with a foreign Origin header
  or a non-form content type is refused. Each check is asserted in
  `tests/test_review.py`, at the handler and over the socket. The security
  section of `RESPONSIBLE-TECH-AUDITS.md` records this as resolved.

### Planned

- **Sandboxed, resource-limited extraction (T1, T2, T4).** The audits'
  security section tracks this as the planned hardening: run the
  pdfplumber/pdfminer parse in a separate process with resource limits (CPU
  time, address space, an input-size cap) and a timeout, returning typed
  results over a narrow channel. A parser crash or bomb then fails that file
  with a clear, content-free error instead of taking down or compromising the
  run. Until it lands, parsing runs in-process and T1/T2 are accepted risks,
  reduced by the compensating controls below.
- **Typed, content-free parse errors (T4).** Wrap extraction failures in an
  error type that names the file and page but never embeds page text, so a
  traceback cannot carry PII.
- **Model and data cards for the Bedrock seam (T5).** Tracked as R9 in
  [`RESEARCH-ROADMAP.md`](./RESEARCH-ROADMAP.md), so a deployer who does
  enable the seam can see what leaves the machine and what the model was
  evaluated on.

## Residual risks and out of scope

- **In-process parsing until the sandbox lands.** An organization handling
  PDFs from unknown senders should treat the machine running extraction as
  exposed to that content. The Docker self-host image is a reasonable
  compensating control: it confines a parser compromise to the container's
  view of the world.
- **Egress under permissive packs is by design.** A non-DV pack with the
  Bedrock backend configured will send low-confidence page content to AWS.
  That is an explicit deployer choice, not a defect, and the planned cards
  (R9) document its terms.
- **The host itself.** Software already running on the operator's machine,
  including anything able to read loopback traffic or the reviewer's browser,
  is outside every boundary here and belongs to the host's own security
  posture.
- **Out of scope for v0.x**, mirroring `SECURITY.md`: network hardening,
  multi-tenant isolation, and authentication, while the tool runs locally
  against an operator's own data.

This is a reference implementation, not a security guarantee. An adopting
organization needs its own assessment against its own environment and
obligations.
