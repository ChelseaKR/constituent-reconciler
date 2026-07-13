# Data card: what crosses the optional Bedrock extraction seam

This card documents the data that leaves the machine when the optional cloud
seam is enabled, who receives it, what governs it once it has left, and what
never leaves under any configuration. It is the companion to the
[model card](MODEL-CARD.md). Source of truth for every claim:
`src/constituent_reconciler/extract/seam.py`,
`src/constituent_reconciler/pipeline.py` (`read_pdf_records`), and the
`[extract]` recipe section in `src/constituent_reconciler/config.py`.

## What crosses when the seam is enabled

One page at a time. A page from a constituent intake PDF is offered to the seam
only when its offline extraction confidence falls below the recipe's
`confidence_threshold` (default 0.5) and the seam's gates all hold. The design
sends that page as an image for field extraction.

An intake page can contain any personally identifying information the form
collects: names, dates of birth, home addresses, phone numbers, email
addresses, household details, consent notes, case information. Assume the whole
page crosses, because it does: the selected page is rendered to a 150-DPI PNG
without redaction or cropping. The request also includes a fixed instruction
listing the six allowed output fields. Bedrock returns extracted field values
and confidence scores, so response content can contain the same PII. The
default backend remains `none`; enabling `bedrock` under a permissive policy is
an explicit egress decision.

## Who receives it

Amazon Bedrock, in the AWS region the deployer's own AWS configuration selects,
under the deployer's own AWS account. The receiving model is Claude (built by
Anthropic), served by AWS; the default model id is
`us.anthropic.claude-sonnet-4-6`. This project holds no keys and operates no
service; the AWS relationship belongs entirely to the deployer.

## Retention and processing once data has left

Handling of a page after it leaves the machine is governed by the deployer's
AWS agreement and the AWS Bedrock service terms, not by this project. Once a
page image has been sent, this project cannot enforce deletion, retention
limits, or access controls on the other side.

Before enabling the seam, a deployer should review the AWS controls that shape
what happens to the data, in particular Bedrock model invocation logging (an
account-level setting the deployer controls, which if switched on stores
request and response content in the deployer's account) and the AWS
documentation on how Bedrock handles prompts. Verify the current terms
yourself; this card intentionally does not restate them, because they are AWS's
to change.

## What never crosses

* **Anything under the `dv` or `hipaa` policy packs.** `make_seam()` returns a
  `NoOpSeam` for those packs at construction time, regardless of the recipe's
  backend setting, so no page image or field value leaves the machine. The
  merge-blocking tests `tests/test_no_egress.py` and `tests/test_extract.py`
  assert this.
* **Pages at or above the confidence threshold.** They keep their offline
  extraction result and are never offered to the seam.
* **CSV sources.** Only PDF pages route through extraction; rows read from CSV
  files never touch the seam.
* **Every other stage's data.** Normalization, matching, the review queue, the
  provenance log, and the export all run locally whether or not the seam is
  enabled.
* **Telemetry content.** GenAI telemetry includes model/provider identity,
  token counts, duration, finish reason, and estimated cost. It excludes the
  page image, prompt, response, field values, and record ids by default, with a
  regression test using representative PII.

## Provenance of cloud-refined fields

When the seam returns fields for a page, `read_pdf_records` in `pipeline.py`
replaces that page's offline fields with the returned ones. Each returned field
uses the same `ExtractedField` shape as the offline extractor: a field name, a
value, a confidence score, and an optional source span pointing back into the
document.

The outputs do not currently mark a field as cloud-refined. A reviewer looking
at a record cannot tell whether a value came from the offline extractor or from
the seam. This is a known gap rather than a designed guarantee: if your audit
trail needs that distinction, keep the seam off, or add provenance tagging when
you implement `refine()`.

## Training data

None exists. This project trains no model, so there is no training dataset to
document. The seeded synthetic fixtures in `examples/intake-demo/`, scored by
the committed eval in `eval/`, contain planted ground truth and no real
personal data; they exercise the pipeline in CI and never cross the seam.
