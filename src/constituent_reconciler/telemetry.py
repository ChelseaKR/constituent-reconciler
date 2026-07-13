"""PII-free GenAI call telemetry for the optional Bedrock and local seams."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass, field
from typing import Any

from constituent_reconciler._vendor.genai_telemetry.attributes import (
    GEN_AI_OPERATION_NAME,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_RESPONSE_FINISH_REASONS,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_SYSTEM,
    GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS,
    GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    METRIC_OPERATION_DURATION,
    PORTFOLIO_COST_USD,
)

_LOG = logging.getLogger("constituent_reconciler")
SpanFactory = Callable[[str, dict[str, object]], AbstractContextManager[Any]]
_span_factory: SpanFactory | None = None


def set_span_factory(factory: SpanFactory | None) -> None:
    """Install an optional tracer adapter; ``None`` restores the no-op default."""
    global _span_factory
    _span_factory = factory


@dataclass
class GenAICall:
    attributes: dict[str, object] = field(default_factory=dict)
    error_type: str | None = None

    def record_completion(
        self,
        *,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float | None,
        cache_creation_input_tokens: int = 0,
        cache_read_input_tokens: int = 0,
        finish_reason: str | None = None,
    ) -> None:
        self.attributes.update(
            {
                GEN_AI_RESPONSE_MODEL: model,
                GEN_AI_USAGE_INPUT_TOKENS: input_tokens,
                GEN_AI_USAGE_OUTPUT_TOKENS: output_tokens,
                GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS: cache_creation_input_tokens,
                GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS: cache_read_input_tokens,
            }
        )
        if finish_reason:
            self.attributes[GEN_AI_RESPONSE_FINISH_REASONS] = [finish_reason]
        if cost_usd is not None:
            self.attributes[PORTFOLIO_COST_USD] = round(cost_usd, 6)


@contextmanager
def genai_call(system: str, model: str) -> Iterator[GenAICall]:
    """Measure one non-streaming call without capturing page, prompt, or output content."""
    request_attributes: dict[str, object] = {
        GEN_AI_OPERATION_NAME: "chat",
        GEN_AI_SYSTEM: system,
        GEN_AI_REQUEST_MODEL: model,
    }
    started = time.perf_counter()
    manager = _span_factory("chat", request_attributes) if _span_factory else nullcontext(None)
    with manager as span:
        call = GenAICall()
        try:
            yield call
        except Exception as exc:
            call.error_type = type(exc).__name__
            raise
        finally:
            duration = max(0.0, time.perf_counter() - started)
            setter = getattr(span, "set_attribute", None)
            if callable(setter):
                for name, value in call.attributes.items():
                    setter(name, value)
                if call.error_type is not None:
                    setter("error.type", call.error_type)
            payload: dict[str, object] = {
                "event": "genai_call",
                **request_attributes,
                **call.attributes,
                METRIC_OPERATION_DURATION: duration,
                "error_type": call.error_type,
            }
            _LOG.info(json.dumps(payload, sort_keys=True, ensure_ascii=False))
