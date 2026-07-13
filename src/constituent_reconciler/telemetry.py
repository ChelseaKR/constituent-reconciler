"""PII-free GenAI call telemetry for the optional Bedrock and local seams."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
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

# Provider finish-reason fields are untrusted strings. Keep only documented,
# content-free enum values so reflected model output can never enter telemetry.
_SAFE_FINISH_REASONS = frozenset(
    {
        "content_filtered",
        "end_turn",
        "guardrail_intervened",
        "length",
        "load",
        "max_tokens",
        "stop",
        "stop_sequence",
        "tool_use",
        "unload",
    }
)


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
        if finish_reason in _SAFE_FINISH_REASONS:
            self.attributes[GEN_AI_RESPONSE_FINISH_REASONS] = [finish_reason]
        if cost_usd is not None:
            self.attributes[PORTFOLIO_COST_USD] = round(cost_usd, 6)


@contextmanager
def _optional_span(operation: str, attributes: dict[str, object]) -> Iterator[Any | None]:
    """Use the optional tracer without letting it alter provider behavior."""

    factory = _span_factory
    if factory is None:
        yield None
        return
    try:
        manager = factory(operation, attributes)
        span = manager.__enter__()
    except Exception:
        yield None
        return

    try:
        yield span
    except BaseException as error:
        try:
            manager.__exit__(type(error), error, error.__traceback__)
        except Exception:  # noqa: S110 - optional telemetry teardown is fail-open.
            pass
        raise
    else:
        try:
            manager.__exit__(None, None, None)
        except Exception:  # noqa: S110 - optional telemetry teardown is fail-open.
            pass


def _set_span_attributes(span: Any, call: GenAICall) -> None:
    """Best-effort span export; instrumentation must never fail the call."""

    try:
        setter = getattr(span, "set_attribute", None)
        if callable(setter):
            for name, value in call.attributes.items():
                setter(name, value)
            if call.error_type is not None:
                setter("error.type", call.error_type)
    except Exception:  # noqa: S110 - optional telemetry export is fail-open.
        pass


def _log_call(payload: dict[str, object]) -> None:
    """Best-effort structured log export with the same failure isolation."""

    try:
        _LOG.info(json.dumps(payload, sort_keys=True, ensure_ascii=False))
    except Exception:  # noqa: S110 - optional telemetry export is fail-open.
        pass


@contextmanager
def genai_call(system: str, model: str) -> Iterator[GenAICall]:
    """Measure one non-streaming call without capturing page, prompt, or output content."""
    request_attributes: dict[str, object] = {
        GEN_AI_OPERATION_NAME: "chat",
        GEN_AI_SYSTEM: system,
        GEN_AI_REQUEST_MODEL: model,
    }
    started = time.perf_counter()
    with _optional_span("chat", request_attributes) as span:
        call = GenAICall()
        try:
            yield call
        except Exception as exc:
            call.error_type = type(exc).__name__
            raise
        finally:
            duration = max(0.0, time.perf_counter() - started)
            _set_span_attributes(span, call)
            payload: dict[str, object] = {
                "event": "genai_call",
                **request_attributes,
                **call.attributes,
                METRIC_OPERATION_DURATION: duration,
                "error_type": call.error_type,
            }
            _log_call(payload)
