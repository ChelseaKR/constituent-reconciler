"""OpenTelemetry GenAI semantic-convention attribute names — pinned shim.

The OTel GenAI semantic conventions are **pre-stable (Development status)** as of
July 2026 and live in a dedicated repo with no tagged releases; attribute names
can still change. This module is the single place the portfolio names them, so
no repo writes `gen_ai.*` string literals directly — when the spec moves, only
this file changes and every vendoring repo re-fetches it (metrics plan Phase 2.1).

Pin: opentelemetry.io/docs/specs/semconv/gen-ai/ as of SEMCONV_VERSION below.
Reference: OBSERVABILITY-STANDARD.md (GenAI section).
"""

from __future__ import annotations

# The semconv snapshot these names were taken from. Bump when re-syncing.
SEMCONV_VERSION = "gen-ai/1.38.0-dev (2026-07-11 snapshot)"

# ---- Request / response identity (spans) -------------------------------------
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"  # "chat" | "execute_tool" | ...
GEN_AI_SYSTEM = "gen_ai.system"  # "anthropic" | "aws.bedrock" | ...
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"

# ---- Token usage (spans + metrics) -------------------------------------------
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_TOKEN_TYPE = "gen_ai.token.type"  # "input" | "output"
# Cache accounting. The canonical input total includes fresh input plus both
# cache-creation and cache-read buckets. Cache hit rate is therefore
# cache_read / input_tokens (no double counting).
GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS = "gen_ai.usage.cache_creation.input_tokens"
GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS = "gen_ai.usage.cache_read.input_tokens"

# ---- Tool / agent -----------------------------------------------------------
GEN_AI_TOOL_NAME = "gen_ai.tool.name"
GEN_AI_TOOL_CALL_ID = "gen_ai.tool.call.id"

# ---- Latency / streaming (attributes; the durations live on metrics) ---------
GEN_AI_RESPONSE_TIME_TO_FIRST_CHUNK = "gen_ai.response.time_to_first_chunk"  # seconds

# ---- Portfolio addendum (not in the OTel spec) -------------------------------
# Computed cost is a portfolio concept, not an OTel-standard attribute. Namespaced
# to avoid colliding if OTel later standardizes cost.
PORTFOLIO_COST_USD = "portfolio.gen_ai.cost.usd"

# ---- Metric names (histograms) ----------------------------------------------
METRIC_OPERATION_DURATION = "gen_ai.client.operation.duration"  # seconds histogram
METRIC_TOKEN_USAGE = "gen_ai.client.token.usage"  # noqa: S105 - metric name, not a secret

# ---- Content capture is OPT-IN and OFF by default ----------------------------
# gen_ai.system_instructions / gen_ai.input.messages / gen_ai.output.messages are
# sensitive; conforming instrumentations must not capture them by default. The
# portfolio does not opt in — do not add these names here without a per-repo
# documented privacy decision.
