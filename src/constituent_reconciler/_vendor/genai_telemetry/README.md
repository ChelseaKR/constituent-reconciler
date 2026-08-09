# `genai_telemetry` — vendorable GenAI telemetry + cost + prod-eval shim

One small, dependency-light Python package so every AI-bearing application
emits **consistent, pinned** OpenTelemetry
GenAI telemetry, computes cost the same way, and runs the same minimal
production-eval loop — instead of each repo re-deriving `gen_ai.*` names and
"cost per conversation" from scratch.

## Why a shim
The OTel GenAI semantic conventions are **pre-stable** (Development status, no
tagged releases). Attribute names can change. `attributes.py` is the single
place the portfolio names them; when the spec moves, only this file changes and
consumers re-vendor. **No repo writes `gen_ai.*` string literals directly.**

## Modules
| Module | Purpose |
|---|---|
| `attributes.py` | Pinned `gen_ai.*` attribute-name constants + `SEMCONV_VERSION` + metric names. Content-capture names deliberately absent (opt-in, off by default). |
| `pricing.py` + `pricing.json` | `Usage` → estimated USD via the pinned price table. `cost_usd()` returns `None` for an unknown model or invalid usage shape (never a silent 0/negative estimate). `conversation_cost_usd()` sums a session + reports cache-hit rate. Replaces the "deferred — no cost harness yet" ledger rows. |
| `prod_eval.py` | `run_batch()` — score 100% of a weekly trace batch with the repo's **already-calibrated** judge, and route every failure to a deidentified benchmark case / prompt change / dated waiver (the anti-checkpoint-only loop). Raw production content is never persisted by default. `corpus_drift()` — per-source hash+age staleness for a RAG corpus. |

## Use
```python
from genai_telemetry import Usage, cost_usd, conversation_cost_usd
from genai_telemetry.attributes import GEN_AI_USAGE_INPUT_TOKENS, GEN_AI_REQUEST_MODEL

# `input_tokens` is the OTel-normalized total: fresh + cache creation + cache read.
cost = cost_usd(Usage("claude-opus-4-8", input_tokens=130_000, output_tokens=5_000,
                      cache_read_input_tokens=20_000, cache_creation_input_tokens=10_000))
# → 0.6975  (estimate, not billing)

# Exact, table-declared Bedrock profile IDs select endpoint pricing: `global.`
# uses the base rate; documented geographic profiles and direct regional IDs
# use the 1.1x rate. Unknown aliases, suffixes, revisions, and ARNs return None.
bedrock_cost = cost_usd(Usage("us.anthropic.claude-haiku-4-5-20251001-v1:0",
                              input_tokens=1_000_000, output_tokens=1_000_000))
# → 6.6

# AWS Bedrock prices can differ by region, so regional rows require an exact
# region. Missing/unsupported regions return None rather than using a false $0.
embedding_cost = cost_usd(Usage("amazon.titan-embed-text-v2:0",
                                input_tokens=1_000_000, region="us-west-2"))
# → 0.02  (on-demand in-region estimate, not billing)
```

```python
from genai_telemetry.prod_eval import run_batch, Verdict

def build_deidentified_case(trace, verdict):
    return {
        "deidentified": True,
        "input": sanitize_and_minimize(trace),
        "expected": None,  # human-label after the privacy review
        "tags": ["production-failure"],
    }

res = run_batch(weekly_traces, my_calibrated_judge,
                benchmark_path=Path("tests/eval/benchmark/prod-failures.jsonl"),
                waived_trace_ids=load_waivers(),
                case_builder=build_deidentified_case)
# res.new_benchmark_cases failures are now permanent regression cases
```

Production traces remain in the controlled telemetry source under that
system's retention policy. Commit-bound benchmark JSONL is durable: write only
minimal, deidentified content; never raw prompts, model outputs, user metadata,
emails, names, or other identifiers. Trace IDs must be opaque and non-PII.

## Projection integrity
This public projection carries a version label and an adjacent SHA-256 manifest
covering every payload file. The consumer test suite verifies both before the
package ships.

## Maintenance
- `pricing.json` carries `last_verified` plus explicit provider/source metadata;
  update it against the pinned source catalog when prices change. AWS regional
  entries also retain the source SKU and catalog version; Claude rows retain
  exact accepted first-party aliases and Bedrock model/profile IDs, the global
  versus regional/multi-region endpoint rule, and as-of date. Cost is always an
  estimate — billing is authoritative in the provider console.
- Bump `SEMCONV_VERSION` in `attributes.py` when re-syncing the spec, and check
  no attribute names changed under you.

## Scope
Supports applications with full or partial generative-AI surfaces that need
consistent token and cost telemetry. Deterministic applications can declare
these controls not applicable.
