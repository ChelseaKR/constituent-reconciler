"""Cost-per-request/conversation from token usage (metrics plan Phase 2.2).

Turns the `gen_ai.usage.*` token counts into an estimated USD cost using the
pinned price table (`pricing.json`). Deletes the "deferred — no cost harness
yet" aspirational rows from the AI-repo ledgers by giving them a real, shared
implementation.

Cost is an ESTIMATE — reconcile against the provider console for billing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_PRICING_PATH = Path(__file__).with_name("pricing.json")
_BEDROCK_PROVIDER = "aws.bedrock"
_ENDPOINT_TYPES = frozenset({"global", "regional", "multi_region"})
_RATE_KEYS = ("input", "output", "cache_write_5m", "cache_write_1h", "cache_read")


def load_prices(path: Path | None = None) -> dict:
    return json.loads((path or _PRICING_PATH).read_text())["models"]


def _string_list(value: object) -> tuple[str, ...] | None:
    if type(value) is not list or any(
        type(item) is not str or not item for item in value
    ):
        return None
    return tuple(value)


def _bedrock_endpoint_for_id(model: str, row: dict) -> str | None:
    """Return the endpoint only when ``model`` is an explicitly supported ID."""
    bedrock = row.get("aws_bedrock")
    if not isinstance(bedrock, dict):
        return None
    model_ids = _string_list(bedrock.get("model_ids"))
    profile_model_ids = _string_list(bedrock.get("profile_model_ids"))
    profile_prefixes = _string_list(bedrock.get("profile_prefixes"))
    if model_ids is None or profile_model_ids is None or profile_prefixes is None:
        return None
    if model in model_ids:
        return "regional"
    for prefix in profile_prefixes:
        if model in (f"{prefix}.{profile_id}" for profile_id in profile_model_ids):
            return "global" if prefix == "global" else "multi_region"
    return None


def resolve_model(model: str, prices: dict | None = None) -> str | None:
    """Resolve only exact, table-declared first-party aliases and Bedrock IDs.

    Broad family-prefix matching is intentionally forbidden: a future model,
    typo, provider revision, or ARN must stay unpriced until it is reviewed and
    added to the pinned table.
    """
    if type(model) is not str or not model:
        return None
    prices = load_prices() if prices is None else prices
    if not isinstance(prices, dict):
        return None
    if model in prices:
        return model
    for key, row in prices.items():
        if type(key) is not str or not isinstance(row, dict):
            continue
        aliases = _string_list(row.get("aliases", []))
        if aliases is None:
            continue
        if model in aliases or _bedrock_endpoint_for_id(model, row) is not None:
            return key
    return None


def price_for_model(
    model: str,
    prices: dict | None = None,
    *,
    region: str | None = None,
    provider: str | None = None,
    endpoint_type: str | None = None,
) -> dict | None:
    """Look up a model's price row using only exact table-declared IDs.

    A regional price row requires an exact ``region``. Returning ``None`` when
    it is missing or unsupported prevents a model-only lookup from silently
    applying one region's rate to another. Callers that need a fail-safe (for
    example, a cost gate) can then reject the request or use an explicitly
    configured ceiling rather than treating unknown cost as free.
    """
    if not isinstance(model, str) or not model:
        return None
    if region is not None and (not isinstance(region, str) or not region):
        return None
    if provider is not None and (not isinstance(provider, str) or not provider):
        return None
    if endpoint_type is not None and (
        not isinstance(endpoint_type, str) or endpoint_type not in _ENDPOINT_TYPES
    ):
        return None
    prices = load_prices() if prices is None else prices
    if not isinstance(prices, dict):
        return None
    resolved = resolve_model(model, prices)
    row = prices.get(resolved) if resolved is not None else None
    if not isinstance(row, dict):
        return None

    row = _provider_price(row, model, provider=provider, endpoint_type=endpoint_type)
    if row is None:
        return None

    regional_rates = row.get("regions")
    if regional_rates is None:
        return row
    if region is None or region not in regional_rates:
        return None
    # Keep provider/source metadata on the selected row without exposing every
    # other region to callers that only need one price.
    return {
        **{key: value for key, value in row.items() if key != "regions"},
        **regional_rates[region],
        "region": region,
    }


def _provider_price(
    row: dict, model: str, *, provider: str | None, endpoint_type: str | None
) -> dict | None:
    """Select provider/endpoint pricing without guessing an ambiguous route."""
    # Amazon-authored Bedrock rows describe in-region service pricing directly.
    if row.get("service") == _BEDROCK_PROVIDER:
        service_provider = provider or _BEDROCK_PROVIDER
        if service_provider != _BEDROCK_PROVIDER:
            return None
        if endpoint_type not in {None, "regional"}:
            return None
        return row

    if row.get("provider") is None:
        return row if provider is None and endpoint_type is None else None
    inferred_endpoint = _bedrock_endpoint_for_id(model, row)
    if inferred_endpoint is None:
        if provider not in {None, "anthropic"}:
            return None
        # This table's base Claude rates are first-party/global. A regional
        # first-party residency request is a different modifier and must not be
        # smuggled through the Bedrock endpoint field.
        return row if endpoint_type in {None, "global"} else None
    if provider not in {None, _BEDROCK_PROVIDER}:
        return None

    if endpoint_type is not None and inferred_endpoint != endpoint_type:
        return None
    effective_endpoint = inferred_endpoint
    multipliers = row.get("aws_bedrock")
    if not isinstance(multipliers, dict):
        return None
    multiplier = multipliers.get(effective_endpoint)
    if isinstance(multiplier, bool) or not isinstance(multiplier, (int, float)):
        return None

    adjusted = {key: value for key, value in row.items() if key != "aws_bedrock"}
    for key in _RATE_KEYS:
        if key in adjusted:
            adjusted[key] = round(adjusted[key] * multiplier, 12)
    adjusted.update(
        {
            "base_source": row.get("source"),
            "source": multipliers.get("source"),
            "service": _BEDROCK_PROVIDER,
            "endpoint_type": effective_endpoint,
        }
    )
    return adjusted


@dataclass(frozen=True)
class Usage:
    """One LLM call's normalized token usage.

    Canonical OTel ``input_tokens`` includes fresh, cache-creation, and
    cache-read input. Provider adapters whose APIs report those buckets
    separately must add both cache fields before constructing ``Usage``.
    """

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_ttl: str = "5m"  # "5m" | "1h" — which write price applies
    region: str | None = None  # Required when provider pricing is regional.
    provider: str | None = None  # e.g. "anthropic" or "aws.bedrock"
    endpoint_type: str | None = None  # "global" | "regional" | "multi_region"


def _valid_usage(usage: Usage) -> bool:
    """Reject shapes that could make an estimate negative or understated."""
    if not isinstance(usage.model, str) or not usage.model:
        return False
    if usage.region is not None and (
        not isinstance(usage.region, str) or not usage.region
    ):
        return False
    if usage.provider is not None and (
        not isinstance(usage.provider, str) or not usage.provider
    ):
        return False
    if usage.endpoint_type is not None and (
        not isinstance(usage.endpoint_type, str)
        or usage.endpoint_type not in _ENDPOINT_TYPES
    ):
        return False
    if not isinstance(usage.cache_ttl, str) or usage.cache_ttl not in {"5m", "1h"}:
        return False
    counts = (
        usage.input_tokens,
        usage.output_tokens,
        usage.cache_creation_input_tokens,
        usage.cache_read_input_tokens,
    )
    if any(type(count) is not int or count < 0 for count in counts):
        return False
    return (
        usage.cache_creation_input_tokens + usage.cache_read_input_tokens
        <= usage.input_tokens
    )


def cost_usd(usage: Usage, prices: dict | None = None) -> float | None:
    """Estimated USD cost for one call. Returns None for an unknown model
    or invalid usage (never a silent 0 — unpriceable input must be visible)."""
    if not _valid_usage(usage):
        return None
    prices = load_prices() if prices is None else prices
    p = price_for_model(
        usage.model,
        prices,
        region=usage.region,
        provider=usage.provider,
        endpoint_type=usage.endpoint_type,
    )
    if p is None:
        return None
    write_key = "cache_write_1h" if usage.cache_ttl == "1h" else "cache_write_5m"
    # Canonical input_tokens includes both cache buckets; split all three so
    # cache creation is not double-charged at both the base and write rates.
    fresh_input = (
        usage.input_tokens
        - usage.cache_creation_input_tokens
        - usage.cache_read_input_tokens
    )
    per_m = 1_000_000
    components = (
        (fresh_input, "input"),
        (usage.cache_read_input_tokens, "cache_read"),
        (usage.cache_creation_input_tokens, write_key),
        (usage.output_tokens, "output"),
    )
    total = 0.0
    for token_count, rate_key in components:
        rate = p.get(rate_key)
        if rate is None:
            # Input-only models have no output/cache token rates. A non-zero
            # unsupported usage component is unpriceable, never implicitly free.
            if token_count:
                return None
            continue
        total += token_count / per_m * rate
    return round(total, 6)


def conversation_cost_usd(usages: list[Usage], prices: dict | None = None) -> dict:
    """Sum per-call costs into a conversation total + a cache-hit-rate readout.
    Unknown-model calls are counted separately so the total isn't silently low."""
    prices = load_prices() if prices is None else prices
    total = 0.0
    unpriced = 0
    input_tok = cache_read = 0
    for u in usages:
        valid = _valid_usage(u)
        c = cost_usd(u, prices)
        if c is None:
            unpriced += 1
        else:
            total += c
        if valid:
            input_tok += u.input_tokens
            cache_read += u.cache_read_input_tokens
    return {
        "cost_usd": round(total, 6),
        "calls": len(usages),
        "unpriced_calls": unpriced,
        "cache_hit_rate": round(cache_read / input_tok, 4) if input_tok else None,
    }
