# Threat model: the untrusted-document parse path

This document models the threats to the path that parses operator-supplied
files, PDF extraction in particular, because that is the one place the tool
runs complex parsing logic over bytes an adversary may have crafted. It closes
the security TODO in [`RESPONSIBLE-TECH-AUDITS.md`](./RESPONSIBLE-TECH-AUDITS.md)
and pairs with [`SECURITY.md`](../SECURITY.md), which owns reporting and the
out-of-scope list.

Status: committed 2026-07-02, re-verified 2026-07-12 against the implemented
Bedrock and local-model seams, updated 2026-07-17 when the sandboxed
extraction path became the pipeline default, and extended 2026-08-03 with the
repair-plan surface that ADR 0012 names as a prerequisite for storing plans.
Revisit whenever the extraction or repair surface changes.

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
2. **The process boundary at the parser.** Since 2026-07-17 the pipeline
   parses each PDF in a spawned child process by default
   (`src/constituent_reconciler/extract/sandbox.py`, wired through
   `read_pdf_records()`): best-effort rlimits on CPU and address space inside
   the child, a wall-clock timeout and input-size cap in the parent, and a
   fail-closed zero-confidence result that routes the document to review. The
   boundary is containment, not privilege separation — the child runs the
   same interpreter with the same filesystem view, `RLIMIT_AS` is not
   enforced on macOS, and Windows has only the timeout. A recipe may also
   turn it off (`[extract] sandbox = false`), returning to in-process
   parsing.
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

### Present (added 2026-07-17)

- **Sandboxed, resource-limited extraction (T1, T2, T4).** The
  pdfplumber/pdfminer parse runs in a spawned child with rlimits (CPU,
  address space where the platform enforces it), a parent-side wall-clock
  timeout, and an input-size cap refused before any parse. Every failure leg
  fails closed to a zero-confidence page whose note names the reason without
  embedding page content, so the document lands in human review.
  `tests/test_sandbox.py` exercises the happy path and each fail-closed leg;
  `tests/test_extract.py` proves the pipeline default contains a corrupt PDF
  that crashes an in-process parse. Two honest limits: the child keeps the
  pipeline's privileges (containment, not a syscall sandbox), and when a
  cloud or local seam is enabled for a low-confidence page, the page render
  for the seam still happens in the parent process.

### Added 2026-08-03: the repair-plan surface (UC-03, ADR 0012)

`reconcile plan-split` writes `repair_plan.json`, a local file that
concentrates the raw field values of everyone caught in one bad merge, and a
future pull request will add `apply_repair`, a remote-mutation path. Both are
modeled here before the first plan is stored, as the ADR requires.

- **T6, plan-file theft or exposure (information disclosure).** The plan is
  the one artifact that gathers a bad merge's restoration values into a
  single small file. Mitigations present: the plan is written only into the
  operator's `--out` directory and is never transmitted; the provenance log
  stores its BLAKE2b digest, never its content; `destruction.PII_ARTIFACTS`
  lists it, so `reconcile destroy` removes it with a certificate
  (`tests/test_destruction.py`); and because planning is repeatable from the
  manifest and sources, the file can be destroyed the moment the repair is
  done without losing anything. The full-disk-encryption caveat from the
  retention model applies to it as to every other local PII artifact.
- **T7, a tampered plan applied remotely (tampering).** An edited plan could
  redirect a restoration or a split at apply time. Mitigation present: the
  digest recorded at planning time identifies the exact plan bytes.
  Mitigation that arrives with the apply path, which is not implemented yet:
  both required approvals bind to that digest, and any destructive remote
  operation requires two distinct reviewers in every policy pack, so a
  swapped plan invalidates the approvals rather than riding them.
- **T8, credential scope (elevation of privilege).** An API token that can
  delete or merge destination records is a larger asset than the
  upsert-scoped token `write_all` needs. No adapter holds such a token today
  because no adapter declares repair capabilities
  (`tests/test_connector_conformance.py` asserts the absence). When the
  CiviCRM pilot lands, its declaration and adoption docs must state the
  minimum token scope, and operators should issue repair credentials
  separately from routine write credentials and revoke them after use.

Planning itself stays inside the existing boundaries: it reconstructs the
cluster offline from the manifest-verified sources and the provenance chain,
constructs no connector, and opens no network connection. The ADR permits a
non-mutating `inspect_repair` read of the destination, which the DV pack will
refuse for non-local destinations through the same policy gate as every other
egress; that read does not exist yet, and the boundary test lands with it.

### Planned

- **Typed, content-free parse errors (T4).** Wrap extraction failures in an
  error type that names the file and page but never embeds page text, so a
  traceback cannot carry PII.
- **Model and data cards plus content-free telemetry (T5).** The cards are
  committed as [`MODEL-CARD.md`](./MODEL-CARD.md) and
  [`DATA-CARD.md`](./DATA-CARD.md). GenAI telemetry records only canonical
  model, token, duration, finish, and estimated-cost fields; tests assert that
  page content and representative PII are absent.

## Residual risks and out of scope

- **The sandbox is containment, not privilege separation.** The child parser
  keeps the pipeline's user, filesystem view, and network reach; a parser
  exploit that achieves code execution is slowed, not stopped. An
  organization handling PDFs from unknown senders should still prefer the
  Docker self-host image, which confines a parser compromise to the
  container's view of the world, and should not set `sandbox = false`.
- **Egress under permissive packs is by design.** A non-DV pack with the
  Bedrock backend configured will send low-confidence page content to AWS.
  That is an explicit deployer choice, not a defect; the model and data cards
  document its terms and the account-level controls the deployer must review.
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
