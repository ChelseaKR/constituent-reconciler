# 0010 — Local-model extraction seam

Status: accepted

## Context

0003 shipped the extraction seam as a protocol with two implementations:
`NoOpSeam` and `BedrockSeam`. Under the `dv` and `hipaa` policy packs the
cloud seam is always a `NoOpSeam`, which means exactly the highest-need
segment (VAWA/FVPSA-bound victim-service providers, per 0005) gets the
weakest extraction: pdfplumber's regex heuristic, with no model-assisted
fallback for low-confidence pages.

A local model changes the calculus for that segment. Run entirely on the
deployer's own hardware (for example via Ollama), it never sends a page off
the machine, so it does not implicate the non-egress invariant that fuses
`BedrockSeam` off under `dv`. But "does not egress" is not the same question
as "may an LLM touch this PII at all under a given org's VAWA reading." This
codebase does not have that legal analysis on file, and per the project's own
ground rule (CLAUDE.md: "never invent legal or compliance facts"), it is not
this codebase's place to assume the answer is yes.

## Decisions

### `forbid_cloud_seam` and `allow_local_seam` are separate policy dimensions

`Policy` (policy.py) gains `allow_local_seam: bool = False`, distinct from
`forbid_cloud_seam`. Before this change, "no cloud calls" and "no model at
all" were the same switch by implication: `forbid_cloud_seam=True` was the
only lever, and there was no way to say "cloud calls are forbidden, but a
local model is fine" without changing what that switch meant. Splitting them
means a future policy analysis can turn `allow_local_seam` on for `dv`
without touching `forbid_cloud_seam`, and the code that reads each stays
narrow.

Both `dv` and `hipaa` leave `allow_local_seam` at its `False` default. This
is deliberate: the excellence bar in docs/ideation/03-expansions.md (EXP-05)
says explicitly that the default under `dv` should remain off until an org's
counsel has done that analysis, and `hipaa`'s invariant set is already
incompletely specified here (see its docstring in policy.py), so it gets the
same conservative default rather than a bespoke one.

### A recipe-level override, kept separate from `backend`

Setting `extract.backend = "local"` alone is not enough to enable the seam
under a pack with `allow_local_seam=False`. A deployer whose own legal review
has cleared model-assisted extraction sets `extract.local_model_override =
true` as well: a second, explicit key, not inferable from the backend choice.
`make_seam()` checks both `policy.allow_local_seam` and the override, and
returns `NoOpSeam` unless one of them is true. This mirrors 0003's
`make_seam(policy_pack, backend)` gate, which is the enforcement point for
the cloud seam, extended rather than special-cased.

### `LocalSeam` talks to loopback only, enforced at construction

`LocalSeam` calls a local model server's HTTP API (the shape matches
Ollama's `/api/tags` and `/api/generate` endpoints) using only the standard
library (`urllib.request`), so there is no new runtime dependency. The
target host is validated against a fixed set of loopback hostnames
(`127.0.0.1`, `localhost`, `::1`) in the constructor; a non-loopback
`OLLAMA_HOST` raises `ValueError` before any request is attempted. This
follows 0003's rule for the non-egress invariant: enforced at construction
time, not at call time, so there is no window where a misconfigured seam
could accidentally call out.

### `LocalSeam.refine()` is a working implementation, not a placeholder

`LocalSeam.refine()` is implemented: it reads a low-confidence page's text
layer via the private `extract.seam._page_text()` helper, asks the local model
to return the six canonical fields (`first_name`, `last_name`, `dob`, `email`,
`phone`, `address`) as JSON, and parses the response. No page-to-image
step is needed because a local text model works directly from pdfplumber's
already-extracted text; that keeps the implementation stdlib-only and avoids
the dependency weight EXP-05 flags as a risk (an image pipeline would need
Pillow and a PDF rasterizer). Bedrock's later implementation does render a PNG;
that does not change this local seam's text-only design. A vision-capable local
model for pages with no text layer at all remains a gap this decision does not
close.

### Confidence is a routing signal, not a calibrated probability

`LocalSeam`'s extracted fields carry a fixed confidence of 0.6, the same
honesty as the page heuristic in extract/pdf.py: it marks a field as
model-assisted rather than heuristic-extracted, and is not a claim of
accuracy. `cohen_kappa()` in evaluate.py (R10) remains the seam for
calibrating it against human-labeled extraction accuracy once that labeled
corpus exists.

## Consequences

- `extract/seam.py` exports `LocalSeam` alongside `NoOpSeam` and
  `BedrockSeam`; `make_seam()` gains keyword-only `local_model_override`,
  `local_model_id`, and `local_host` parameters.
- `Policy` gains `allow_local_seam`; both `dv` and `hipaa` leave it `False`.
- `ExtractConfig` gains `local_model_override` and `local_model_id`, read
  from the recipe's `[extract]` section.
- No new runtime or optional dependency: `LocalSeam` uses only
  `urllib.request` and the already-optional `pdfplumber`.
- `tests/test_no_egress.py` and `tests/test_extract.py` cover: `dv` and
  `hipaa` default to `NoOpSeam` for `backend="local"`; a non-loopback host
  raises `ValueError`; and an explicit `local_model_override` under `dv`
  produces a `LocalSeam` whose host is still forced to loopback.
- Whether the `dv` pack's `allow_local_seam` default should ever flip to
  `True` is not decided here; that requires the VAWA/FVPSA analysis EXP-05
  calls for, tracked as follow-up work, not assumed by this change.
