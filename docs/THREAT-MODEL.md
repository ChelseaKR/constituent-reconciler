# Threat model: the untrusted-document parse path

This document models the threats to the path that parses operator-supplied
files, PDF extraction in particular, because that is the one place the tool
runs complex parsing logic over bytes an adversary may have crafted. It closes
the security TODO in [`RESPONSIBLE-TECH-AUDITS.md`](./RESPONSIBLE-TECH-AUDITS.md)
and pairs with [`SECURITY.md`](../SECURITY.md), which owns reporting and the
out-of-scope list. Two surfaces added since have been folded in rather than
given documents of their own, because both are reached from that same parse
path and inherit its boundaries: the repair-plan surface (T6 through T8) and
the AI assistant surface (T9 through T12).

Status: committed 2026-07-02, re-verified 2026-07-12 against the implemented
Bedrock and local-model seams, updated 2026-07-17 when the sandboxed
extraction path became the pipeline default, and extended 2026-08-03 with the
repair-plan surface that ADR 0012 names as a prerequisite for storing plans.
Extended again on 2026-08-27 with the AI assistant surface of ADR 0014, which
had shipped without any entry here. Revisit whenever the extraction, repair,
or assistant surface changes.

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
concentrates the raw field values of everyone caught in one bad merge.
`reconcile apply-repair` (added 2026-08-21) executes it against the CiviCRM
pilot behind the second-reviewer gate described below; a real apply also
writes `repair_receipts.json`, the before/after values each operation
actually changed.

- **T6, plan-file (and receipt-file) theft or exposure (information
  disclosure).** The plan and the receipt are the two artifacts that gather
  a bad merge's raw values into one small file. Mitigations present: both
  are written only into the operator's `--out` directory and are never
  transmitted; the provenance log stores each one's BLAKE2b digest, never
  its content; `destruction.PII_ARTIFACTS` lists both, so `reconcile
  destroy` removes them with a certificate (`tests/test_destruction.py`);
  and because planning is repeatable from the manifest and sources, the
  plan file can be destroyed the moment the repair is done without losing
  anything (the receipt, being the record of what was actually written to
  a live system, is evidence rather than regenerable state, and its
  retention should follow the same policy-pack window as other output).
  The full-disk-encryption caveat from the retention model applies to both
  as to every other local PII artifact.
- **T7, a tampered plan applied remotely (tampering).** An edited plan
  could redirect a restoration or a split at apply time. Both mitigations
  the ADR called for are implemented: `apply_repair_plan` recomputes the
  plan file's digest and refuses unless it matches the digest the
  provenance log recorded at planning time (`repair._verified_plan`,
  `tests/test_repair_apply.py::test_tampered_plan_is_refused_before_the_gate`),
  and every remote operation this pilot supports (`field-restore`,
  `split-create`) is declared destructive and therefore requires two
  distinct reviewers' recorded approval of that exact digest before
  `apply_repair_plan` will construct a connector at all -- fewer than two
  approvals refuses before any credential is read or any network call is
  reachable
  (`tests/test_repair_apply.py::test_single_approval_never_reaches_the_connector`
  proves this with a connector double that fails the test if any of its
  methods are called). A swapped plan therefore invalidates the approvals
  recorded against the old digest rather than riding them.
- **T8, credential scope (elevation of privilege).** An API token that can
  create new remote contacts under `apply_repair` is a larger asset than
  the upsert-scoped token `write_all` needs, even though this pilot's two
  operations never delete or merge a CiviCRM record. The CiviCRM
  declaration (`connectors/civicrm.py`) names the exact verified version
  (6.17.2) and the disposable instance it was checked against; operators
  should still issue repair credentials separately from routine write
  credentials and revoke them after use, since the same API key that can
  create a repair-declared contact can also run routine writes for as long
  as it is live.

Planning itself stays inside the existing boundaries: it reconstructs the
cluster offline from the manifest-verified sources and the provenance chain,
constructs no connector, and opens no network connection. `apply_repair_plan`
crosses that boundary only past the gate: a dry run (the default) makes no
network call either, deriving its preview entirely from the plan's own
bytes; only `--execute`, past two recorded approvals, calls
`inspect_repair` (a non-mutating read of the live destination version) and
then `apply_repair`. The DV pack refuses both through the same
`require_local_targets` policy gate `write_all` is refused under, since
`pipeline.build_connector` is the single place that gate is enforced and
`apply_repair_plan` uses it unless a connector is injected for testing.

### Added 2026-08-27: the AI assistant surface (ADR 0014)

`reconcile ai-explain`, `ai-ask`, `ai-propose-corrections` and `ai-triage`
put an opt-in advisory layer over the review queue. Three of the four call a
model provider, which makes this the second network path in the system after
the extraction seam of T5, and `ai-propose-corrections` is the one command
that feeds untrusted document text straight into a prompt. `ai-triage` calls
no model and is unaffected by everything below.

- **T9, prompt injection from an intake document (tampering).** The source
  text `ai-propose-corrections` grounds a quote in is the same
  operator-supplied, possibly crafted bytes T1 through T3 already model, and
  it reaches a model prompt verbatim. Mitigations present: nothing the model
  returns is ever applied, because the command's only output is the labeled
  draft `ai_ocr_proposals.json` and turning a proposal into a correction is
  the ordinary human path (`models.Correction`, the review server's correct
  action, or `reconcile apply --decisions`); a proposal is accepted only when
  its quote is an exact whitespace-normalized substring of the real source
  text (`ocr_propose._quote_verifies`,
  `tests/test_assistant_ocr_propose.py::test_never_invents_a_value_the_source_does_not_support`
  and `tests/test_assistant_ocr_propose.py::test_quote_not_present_in_source_is_withheld`);
  `refusal.enforce` scans every response deterministically before display and
  replaces a flagged one with a canned message whatever the model said
  (`tests/test_assistant_refusal.py::test_enforce_replaces_prohibited_text_with_canned_message`);
  and the package cannot reach the deterministic path at all, since nothing
  in `pipeline.py`, `decisions.py`, or the `run`, `review` and `apply`
  commands imports it
  (`tests/test_no_ai_in_deterministic_path.py::test_pipeline_module_never_imports_the_assistant_package_or_its_sdks`).
  The residual risk is stated in the assistant model card and repeated here
  because it is the part a reader should not have to infer: quote
  verification grounds a proposal against a string's presence in the
  document, not against its attribution to the right person. ADR 0014's
  `wrong_person_trap` eval fixture is exactly that case. A crafted document
  can therefore still produce a proposal that verifies, which is why the
  draft-only rule above is load-bearing rather than a formality.

- **T10, the proposals file concentrates raw values and quoted source text
  (information disclosure).** `ai_ocr_proposals.json` is written from
  `dataclasses.asdict()` over `OCRProposal`, whose fields include
  `original_value` and `quote`, so one small file holds a raw field value
  and a verbatim line of the intake document for every field checked. That
  is the same shape as T6, and it should be read the same way. Mitigations
  present: the file is written only into the operator's `--out` directory
  and is never transmitted; it is listed in `destruction.PII_ARTIFACTS`, so
  `reconcile destroy` removes it and certifies the removal, checked both by
  the classification scan
  (`tests/test_destruction_inventory.py::test_every_artifact_the_code_writes_is_classified`)
  and, without consulting any list, by its bytes after a real destruction
  pass
  (`tests/test_destruction_leaves_nothing.py::test_no_sentinel_survives_a_destruction_pass`);
  it carries a row in the artifact inventory of
  [`DATA-FLOW-AND-RETENTION.md`](./DATA-FLOW-AND-RETENTION.md); and it is
  never written under the DV or HIPAA packs, which refuse the command before
  any record is read. This artifact is named here rather than folded into
  T6 because it was missing from `PII_ARTIFACTS` until 2026-08-27: a real
  run exited 0, certified three other artifacts as destroyed, and left this
  one on disk holding a raw value, a verbatim intake quote, and an email
  address.

- **T11, egress to the model provider (information disclosure).** A
  configured assistant sends filtered record values and pipeline evidence to
  Anthropic or to Bedrock. Mitigations present: `assert_cloud_ai_allowed`
  gates every `ai-*` command on the same `policy.forbid_cloud_seam` field
  that fuses the extraction seam, deliberately not a second field that could
  drift out of step with it, so the DV and HIPAA packs disable the package
  outright
  (`tests/test_assistant_consent_filter.py::test_dv_pack_forbids_the_assistant_entirely`,
  `tests/test_assistant_consent_filter.py::test_hipaa_pack_forbids_the_assistant_entirely`);
  `consent_filter.filter_record` reduces a record to values cleared for the
  named `ai-assistant` destination before a prompt is built rather than
  after
  (`tests/test_assistant_consent_filter.py::test_consent_scoped_away_from_ai_destination_withholds`);
  `evidence_payload` is the single boundary deciding what text a provider
  sees and never sends an email address or a phone number by literal value
  (`tests/test_assistant_evidence_payload.py::test_a_withheld_field_present_in_real_evidence_never_shows_its_value`);
  credentials are read from the environment and never written to disk; and a
  per-minute rate and a hard daily cap are enforced before any call
  (`tests/test_assistant_rate_limit.py::test_exceeding_the_daily_cap_raises_even_with_gaps`).

- **T12, grounding text read from outside the run (information disclosure and
  tampering).** A source span records the intake document's bare filename,
  never a path. Until 2026-08-27 `source_text.for_field` resolved that name
  against the process working directory, which made two things possible: run
  from a directory holding an unrelated file of the same name, that file was
  sent to the provider and the quote was verified against it, so a proposal
  about one person could be supported by a sentence from another person's
  document and by a document the operator had never named as a source; run
  from any other directory, every field reported no source text and the
  command wrote an empty draft and exited 0 without saying it had opened
  nothing. Mitigations present: `source_text.document_roots` derives the
  permitted directories from the recipe's own sources, `for_field` requires
  them and has no default, and a name that resolves to no file, to more than
  one file, or that carries a directory component is refused rather than
  guessed, with the command exiting 2 and writing no draft
  (`tests/test_cli_ai_propose_grounding.py::test_a_same_named_file_in_the_working_directory_is_never_read`,
  `tests/test_cli_ai_propose_grounding.py::test_a_filename_in_two_source_directories_is_refused_not_guessed`).

The assistant adds no new trust boundary of its own. It sits behind the
network boundary already described for the extraction seam, reads documents
through the same file boundary, and writes only into the same `--out`
directory. What it changes is how much crosses that network boundary and how
concentrated one local artifact is, which is what T9 through T12 record.

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
- **Assistant egress under permissive packs is also by design, and one
  question about it is open.** The `default` pack permits the `ai-*`
  commands, and most deployments run under `default`. Whether sending
  constituent values to a model provider needs a subprocessor agreement or a
  specific donor consent is a question ADR 0014 records as needing counsel
  and does not answer, and the code does not refuse on the project's behalf.
  A deployer enabling the `ai` extra is making that call. Nothing in the
  assistant is required to run the pipeline: `run`, `review` and `apply`
  behave identically with the package uninstalled.
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
