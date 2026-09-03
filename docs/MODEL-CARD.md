# Model card: the optional Bedrock extraction seam

This card follows the model-card format of Mitchell et al., ["Model Cards for
Model Reporting"](https://arxiv.org/abs/1810.03993) (2019), adapted for a model
this project calls but does not build. It documents the one place the pipeline
can send data to a hosted model. The companion
[data card](DATA-CARD.md) documents exactly what data crosses. Source of truth
for every claim here: `src/constituent_reconciler/extract/seam.py` and the
`[extract]` recipe section in `src/constituent_reconciler/config.py`.

## Model details

* **What it is.** An optional cloud step, called the seam, that can send one
  low-confidence PDF page to a hosted model for field extraction. Everything
  else in the pipeline (normalization, matching, review, export) runs offline
  and deterministic; the seam is the only surface that could reach a model
  outside the machine.
* **Model and provider.** Claude, built by Anthropic, served through Amazon
  Bedrock and called with the Bedrock Converse API.
* **Default model id.** `us.anthropic.claude-sonnet-4-6`, which names Claude
  Sonnet 4.6 through a US cross-region Bedrock inference profile. A deployer
  can pass a different model id when constructing `BedrockSeam`.
* **Where it lives in the code.** `BedrockSeam` in
  `src/constituent_reconciler/extract/seam.py`, constructed by `make_seam()`.
  The recipe's `[extract]` section selects it with `backend = "bedrock"` and
  sets the page-level `confidence_threshold` (default 0.5).
* **Implementation status.** Implemented and opt-in. `BedrockSeam.refine()`
  renders the selected PDF page to a 150-DPI PNG, invokes Bedrock Converse,
  parses a strict JSON field list, and falls back to the offline extraction on
  a call or response failure. Tests inject a fake Converse client, so request,
  parsing, and fallback behavior are exercised without credentials or network
  access. `is_enabled()` constructs the boto3 client; the network call occurs
  only in `refine()` after all policy and confidence gates hold.
* **This project trains no model.** The card documents a third-party hosted
  model the pipeline can optionally call, not a model this project produced.

## Intended use

The seam refines low-confidence pages from constituent intake PDFs, and nothing
else. A page is offered to the seam only when all three gates hold:

1. **The active policy pack allows cloud calls.** The DV and HIPAA packs never
   do; see the next section.
2. **The page's offline extraction confidence is below the recipe's
   `confidence_threshold`.** Pages at or above the threshold keep their offline
   result and are never offered.
3. **A cloud client can be constructed.** `is_enabled()` returns true only when
   boto3 is installed and a `bedrock-runtime` client can be built from the
   machine's AWS configuration (region and credentials). Without that, the
   seam stays silent and the offline result stands.

Intended users are deployers who have decided, under a policy pack that permits
it and with their own AWS account, that page images from low-confidence intake
pages may leave the machine. The default recipe setting is `backend = "none"`,
which means no seam at all.

## Out-of-scope and prohibited use

* **Under the `dv` and `hipaa` policy packs the seam is fused off.**
  `make_seam()` checks the pack against the `_CLOUD_FORBIDDEN` frozenset
  (`{"dv", "hipaa"}`) and returns a `NoOpSeam` regardless of what the recipe
  requests. The non-egress invariant is enforced at construction time, not at
  call time, so there is no window where a misconfigured seam could call out.
  Merge-blocking tests assert this: `tests/test_extract.py` and
  `tests/test_no_egress.py`.
* **Not the default extraction path.** The deterministic offline extractor
  handles every page first; the seam only sees pages the offline path scored
  as low confidence.
* **Not for matching or merge decisions.** The seam extracts field values from
  one page. Scoring, banding, and clustering are local and deterministic, and
  uncertain matches still route to a human reviewer.
* **Not for whole documents or bulk upload.** The interface takes one page of
  one file per call.

## Limitations

* The request and parser behavior are covered with a fake Bedrock client, but
  this repository does not run a live hosted-model benchmark because it has no
  AWS account or credentials. Model quality can change independently of this
  code even when the model id remains configured.
* Extraction error is not evenly distributed. The bias section of
  [`RESPONSIBLE-TECH-AUDITS.md`](RESPONSIBLE-TECH-AUDITS.md) records the known
  risk classes for this domain (transliterated and hyphenated names,
  non-Western name order, rural and informal addresses); a hosted vision model
  reading scanned intake forms inherits the same risk plus handwriting and
  layout failure modes.
* When the seam returns fields for a page, those fields replace the page's
  offline result. A wrong cloud extraction on a low-confidence page therefore
  flows into matching like any other value. The downstream protections
  (fail-closed confidence gate, human review queue) still apply.
* Output records do not currently mark a field as cloud-refined. The
  [data card](DATA-CARD.md) describes this provenance gap.

## Evaluation status

The request/response contract, malformed-output fallback, policy fusing, and
PII-free telemetry are merge-blocking tests. The committed offline extraction
fixture reports 100.0% precision and 94.1% recall in
[`eval/extraction-report.md`](../eval/extraction-report.md), but that score does
not measure Claude and is not presented as a hosted-model accuracy claim. The
field-judge calibration gate is wired into `constituent-reconcile eval`: missing or drifting
labels fail closed below Cohen's kappa 0.60. A deployer enabling Bedrock must
benchmark its chosen model and document set before trusting refined fields.

Each model call emits the portfolio's pinned OpenTelemetry GenAI attribute
names, input/output token counts, duration, finish reason, and estimated cost.
Prompt, page, response, and extracted field content are excluded by default;
tests assert that representative PII never appears in the telemetry payload.

## Ethical considerations

Enabling the seam means personally identifying information from an intake page
(names, dates of birth, addresses, whatever the page holds) leaves the machine
and is processed by a third party under the deployer's AWS agreement. That is
exactly the disclosure the DV pack exists to prevent, which is why the pack
forbids the seam in code rather than in documentation. A deployer who enables
the seam for other populations should confirm that their own consent language
and legal obligations cover sending intake documents to a cloud service, and
should read the [data card](DATA-CARD.md) for what crosses and what AWS
controls apply. This project is a reference implementation, not legal advice.

## Caveats and recommendations

* Leave `backend = "none"` unless low-confidence pages are a measured problem
  the offline extractor cannot solve.
* If you enable `refine()`, benchmark the configured model against documents
  representative of your deployment before trusting the output. Cloud-refined
  field provenance remains a known gap, so keep the seam off when that
  distinction is required by your audit trail.
* Review your AWS account settings before enabling; the data card lists the
  ones that matter.
