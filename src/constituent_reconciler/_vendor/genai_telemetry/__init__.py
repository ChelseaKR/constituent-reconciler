"""Portfolio GenAI telemetry shim (metrics plan Phase 2).

Vendorable, dependency-light helpers so AI repos emit consistent, pinned OTel
GenAI telemetry and compute cost the same way. Import the attribute-name
constants from `attributes` (never write `gen_ai.*` strings inline) and cost from
`pricing`.
"""

from .attributes import GEN_AI_TOKEN_TYPE, SEMCONV_VERSION
from .pricing import (
    Usage,
    conversation_cost_usd,
    cost_usd,
    load_prices,
    price_for_model,
)

__all__ = [
    "SEMCONV_VERSION",
    "GEN_AI_TOKEN_TYPE",
    "Usage",
    "conversation_cost_usd",
    "cost_usd",
    "load_prices",
    "price_for_model",
]
