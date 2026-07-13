"""Telemetry adapters are optional and cannot alter provider outcomes."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

import constituent_reconciler.telemetry as telemetry


class _ExplodingSpan:
    def set_attribute(self, name: str, value: object) -> None:
        raise RuntimeError("span exporter unavailable")


@contextmanager
def _exploding_span(operation: str, attributes: dict[str, object]) -> Iterator[_ExplodingSpan]:
    yield _ExplodingSpan()
    raise RuntimeError("span context exporter unavailable")


def _explode_log(message: object, *args: Any, **kwargs: Any) -> None:
    raise RuntimeError("log exporter unavailable")


def test_telemetry_export_failures_do_not_alter_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry.set_span_factory(_exploding_span)
    monkeypatch.setattr(telemetry._LOG, "info", _explode_log)
    try:
        with telemetry.genai_call("ollama", "test-model") as call:
            call.record_completion(
                model="test-model",
                input_tokens=4,
                output_tokens=2,
                cost_usd=0.0,
                finish_reason="stop",
            )
            result = "provider-result"
    finally:
        telemetry.set_span_factory(None)

    assert result == "provider-result"


def test_telemetry_export_failures_do_not_replace_provider_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    telemetry.set_span_factory(_exploding_span)
    monkeypatch.setattr(telemetry._LOG, "info", _explode_log)
    try:
        with pytest.raises(ValueError, match="provider failed"):
            with telemetry.genai_call("aws.bedrock", "test-model"):
                raise ValueError("provider failed")
    finally:
        telemetry.set_span_factory(None)
