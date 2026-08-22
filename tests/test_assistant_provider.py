"""Tests for the AI provider seam, using fake clients -- no network access.

Mirrors ``extract/seam.py``'s own test convention: a fake object with the
right method shape is injected via the ``client`` constructor argument, so
these tests exercise the request-building and response-parsing logic
without needing ``anthropic``/``boto3`` credentials or network access.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from constituent_reconciler.assistant.errors import ProviderCallFailed, ProviderNotConfigured
from constituent_reconciler.assistant.provider import (
    AnthropicProvider,
    BedrockProvider,
    make_provider,
)


@dataclass
class _FakeUsage:
    input_tokens: int = 10
    output_tokens: int = 5
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass
class _FakeBlock:
    type: str
    text: str = ""


@dataclass
class _FakeMessage:
    content: list[_FakeBlock]
    usage: _FakeUsage = field(default_factory=_FakeUsage)
    stop_reason: str = "end_turn"


class _FakeAnthropicMessages:
    def __init__(self, message: _FakeMessage) -> None:
        self._message = message
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeMessage:
        self.calls.append(kwargs)
        return self._message


class _FakeAnthropicClient:
    def __init__(self, message: _FakeMessage) -> None:
        self.messages = _FakeAnthropicMessages(message)


class _RaisingAnthropicClient:
    class messages:  # noqa: N801 - mirrors the SDK's attribute shape
        @staticmethod
        def create(**kwargs: Any) -> None:
            raise RuntimeError("boom")


def test_anthropic_provider_is_enabled_reflects_api_key_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert AnthropicProvider().is_enabled() is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    assert AnthropicProvider().is_enabled() is True


def test_anthropic_provider_with_injected_client_is_always_enabled() -> None:
    client = _FakeAnthropicClient(_FakeMessage(content=[_FakeBlock(type="text", text="hello")]))
    assert AnthropicProvider(client=client).is_enabled() is True


def test_anthropic_provider_complete_parses_text_and_usage() -> None:
    message = _FakeMessage(
        content=[_FakeBlock(type="text", text="ok")],
        usage=_FakeUsage(input_tokens=100, output_tokens=20),
    )
    client = _FakeAnthropicClient(message)
    provider = AnthropicProvider(client=client)

    result = provider.complete(system="be terse", user="hello", max_tokens=64)

    assert result.text == "ok"
    assert result.provider == "anthropic"
    assert result.input_tokens == 100
    assert result.output_tokens == 20
    assert result.stop_reason == "end_turn"
    # cache_control system framing: the request must carry the system text.
    sent = client.messages.calls[0]
    assert sent["system"][0]["text"] == "be terse"
    assert sent["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_provider_ignores_non_text_blocks() -> None:
    message = _FakeMessage(
        content=[
            _FakeBlock(type="thinking", text="secret"),
            _FakeBlock(type="text", text="visible"),
        ]
    )
    provider = AnthropicProvider(client=_FakeAnthropicClient(message))
    result = provider.complete(system="s", user="u")
    assert result.text == "visible"


def test_anthropic_provider_wraps_sdk_failures() -> None:
    provider = AnthropicProvider(client=_RaisingAnthropicClient())
    with pytest.raises(ProviderCallFailed):
        provider.complete(system="s", user="u")


def test_anthropic_provider_without_credentials_raises_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = AnthropicProvider()
    with pytest.raises(ProviderNotConfigured):
        provider.complete(system="s", user="u")


class _FakeBedrockClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def converse(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return self._response


class _RaisingBedrockClient:
    def converse(self, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("boom")


def _bedrock_response(text: str = "ok") -> dict[str, Any]:
    return {
        "output": {"message": {"content": [{"text": text}]}},
        "stopReason": "end_turn",
        "usage": {"inputTokens": 50, "outputTokens": 10},
    }


def test_bedrock_provider_complete_parses_text_and_usage() -> None:
    client = _FakeBedrockClient(_bedrock_response("hello from bedrock"))
    provider = BedrockProvider(client=client)

    result = provider.complete(system="be terse", user="hi", max_tokens=64)

    assert result.text == "hello from bedrock"
    assert result.provider == "aws.bedrock"
    assert result.input_tokens == 50
    assert result.output_tokens == 10
    sent = client.calls[0]
    assert sent["system"][0]["text"] == "be terse"
    assert sent["system"][1] == {"cachePoint": {"type": "default"}}


def test_bedrock_provider_is_enabled_with_injected_client() -> None:
    assert BedrockProvider(client=_FakeBedrockClient(_bedrock_response())).is_enabled() is True


def test_bedrock_provider_wraps_sdk_failures() -> None:
    provider = BedrockProvider(client=_RaisingBedrockClient())
    with pytest.raises(ProviderCallFailed):
        provider.complete(system="s", user="u")


def test_make_provider_defaults_to_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RECONCILER_AI_PROVIDER", raising=False)
    monkeypatch.delenv("RECONCILER_AI_MODEL", raising=False)
    provider = make_provider()
    assert isinstance(provider, AnthropicProvider)
    assert provider.model == "claude-sonnet-5"


def test_make_provider_selects_bedrock_by_name() -> None:
    provider = make_provider(name="bedrock")
    assert isinstance(provider, BedrockProvider)
    assert provider.model == "global.anthropic.claude-sonnet-4-6"


def test_make_provider_honors_env_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECONCILER_AI_PROVIDER", "bedrock")
    monkeypatch.setenv("RECONCILER_AI_MODEL", "some-other-model")
    provider = make_provider()
    assert isinstance(provider, BedrockProvider)
    assert provider.model == "some-other-model"


def test_make_provider_rejects_unknown_provider() -> None:
    with pytest.raises(ProviderNotConfigured):
        make_provider(name="not-a-real-provider")


def test_bedrock_provider_is_enabled_false_when_boto3_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = BedrockProvider()
    monkeypatch.setitem(__import__("sys").modules, "boto3", None)
    assert provider.is_enabled() is False
