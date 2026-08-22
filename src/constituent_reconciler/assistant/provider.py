"""Model provider seam for the AI assistant package.

Two providers, selected by name, never inferred silently. ``AnthropicProvider``
talks to the public Anthropic API via the ``anthropic`` SDK and is the code
default (model default ``claude-sonnet-5``), matching the project's settled
choice. ``BedrockProvider`` talks to Claude on Amazon Bedrock via boto3's
``bedrock-runtime`` Converse API -- the same call shape ``extract/seam.py``'s
``BedrockSeam`` already uses -- because this portfolio's dev/eval AWS account
can invoke ``global.anthropic.claude-sonnet-4-6`` on Bedrock but returns
``AccessDeniedException`` for ``claude-sonnet-5`` directly (verified live; see
docs/adr/0014-runtime-ai-at-the-edges.md). Live evals in this repo therefore
run on Bedrock; the code's own default provider and model stay
Anthropic/``claude-sonnet-5`` for a deployer with normal API access.

Both providers do a lazy import of their SDK (``anthropic`` or ``boto3``), so
importing this module -- and therefore importing ``constituent_reconciler.
assistant`` at all -- never requires either package installed.
``reconcile run``, ``review``, and ``apply`` never import this module; the
offline-first pipeline does not know it exists.

Neither provider filters its input. Every caller is responsible for passing
a payload already filtered through ``consent_filter`` -- this module trusts
what it is given because filtering happens upstream of it, once, not here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from constituent_reconciler._vendor.genai_telemetry import Usage, cost_usd
from constituent_reconciler.assistant.errors import ProviderCallFailed, ProviderNotConfigured
from constituent_reconciler.telemetry import genai_call

#: Bumped whenever a provider's request shape changes in a way that could
#: change model behavior (not on doc/comment-only edits). Eval provenance
#: records this so a result can be traced back to exactly what was sent.
PROVIDER_INTERFACE_VERSION = "1"

DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"
#: Confirmed live against this portfolio's AWS account (docs/adr/0014):
#: "global." is the profile prefix genai_telemetry's pricing table already
#: carries a multiplier of 1.0 for (no regional markup), matching the
#: cheaper of the two endpoints available; extract/seam.py's BedrockSeam
#: uses the pricier "us." regional prefix for an unrelated, earlier reason.
DEFAULT_BEDROCK_MODEL = "global.anthropic.claude-sonnet-4-6"
_DEFAULT_BEDROCK_REGION = "us-west-2"


@dataclass(frozen=True)
class ProviderResult:
    """One completed model call, with usage telemetry already recorded."""

    text: str
    model: str
    provider: str
    stop_reason: str | None
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    cost_usd: float | None = None


@runtime_checkable
class Provider(Protocol):
    name: str
    model: str

    def is_enabled(self) -> bool: ...

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        cache_system: bool = True,
    ) -> ProviderResult: ...


class AnthropicProvider:
    """Default provider: the public Anthropic API via the ``anthropic`` SDK."""

    name = "anthropic"

    def __init__(self, model: str = DEFAULT_ANTHROPIC_MODEL, client: Any | None = None) -> None:
        self.model = model
        self._client: Any | None = client

    def is_enabled(self) -> bool:
        if self._client is not None:
            return True
        return bool(os.environ.get("ANTHROPIC_API_KEY"))

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ProviderNotConfigured(
                "ANTHROPIC_API_KEY is not set; the anthropic provider is disabled"
            )
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderNotConfigured(
                "the 'anthropic' package is not installed; install with "
                "pip install 'constituent-reconciler[ai]'"
            ) from exc
        self._client = anthropic.Anthropic()
        return self._client

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        cache_system: bool = True,
    ) -> ProviderResult:
        client = self._get_client()
        system_param: str | list[dict[str, Any]] = system
        if cache_system:
            system_param = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        try:
            with genai_call("anthropic", self.model) as call:
                response = client.messages.create(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system_param,
                    messages=[{"role": "user", "content": user}],
                )
                usage = response.usage
                cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
                cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
                total_input = usage.input_tokens + cache_creation + cache_read
                estimated_cost = cost_usd(
                    Usage(
                        self.model,
                        input_tokens=total_input,
                        output_tokens=usage.output_tokens,
                        cache_creation_input_tokens=cache_creation,
                        cache_read_input_tokens=cache_read,
                        provider="anthropic",
                    )
                )
                call.record_completion(
                    model=self.model,
                    input_tokens=total_input,
                    output_tokens=usage.output_tokens,
                    cache_creation_input_tokens=cache_creation,
                    cache_read_input_tokens=cache_read,
                    cost_usd=estimated_cost,
                    finish_reason=response.stop_reason,
                )
        except ProviderNotConfigured:
            raise
        except Exception as exc:
            raise ProviderCallFailed(f"anthropic call failed: {exc}") from exc

        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        return ProviderResult(
            text=text,
            model=self.model,
            provider=self.name,
            stop_reason=response.stop_reason,
            input_tokens=total_input,
            output_tokens=usage.output_tokens,
            cache_creation_input_tokens=cache_creation,
            cache_read_input_tokens=cache_read,
            cost_usd=estimated_cost,
        )


class BedrockProvider:
    """Claude on Amazon Bedrock, via boto3's bedrock-runtime Converse API.

    Mirrors ``extract/seam.py``'s ``BedrockSeam`` call shape rather than the
    ``anthropic`` SDK's own Bedrock client (``AnthropicBedrockMantle``): the
    Mantle client returned a 404 for ``global.anthropic.claude-sonnet-4-6``
    in live testing against this account, while the boto3 Converse call
    succeeds, so the already-proven path is used rather than a second,
    unverified one. See docs/adr/0014-runtime-ai-at-the-edges.md.
    """

    name = "aws.bedrock"

    def __init__(
        self,
        model: str = DEFAULT_BEDROCK_MODEL,
        *,
        region: str = _DEFAULT_BEDROCK_REGION,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self._region = region
        self._client: Any | None = client

    def is_enabled(self) -> bool:
        if self._client is not None:
            return True
        try:
            import boto3

            self._client = boto3.client("bedrock-runtime", region_name=self._region)
            return True
        except ImportError:
            return False
        except Exception:
            return False

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 1024,
        cache_system: bool = True,
    ) -> ProviderResult:
        if self._client is None and not self.is_enabled():
            raise ProviderNotConfigured("boto3 bedrock-runtime client is not available")
        client = self._client
        if client is None:  # pragma: no cover - is_enabled() above guarantees this
            raise ProviderNotConfigured("boto3 bedrock-runtime client is not available")
        system_blocks: list[dict[str, Any]] = [{"text": system}]
        if cache_system:
            system_blocks.append({"cachePoint": {"type": "default"}})
        try:
            with genai_call("aws.bedrock", self.model) as call:
                result = client.converse(
                    modelId=self.model,
                    system=system_blocks,
                    messages=[{"role": "user", "content": [{"text": user}]}],
                    inferenceConfig={"maxTokens": max_tokens},
                )
                usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
                input_tokens = int((usage or {}).get("inputTokens", 0) or 0)
                output_tokens = int((usage or {}).get("outputTokens", 0) or 0)
                cache_write = int((usage or {}).get("cacheWriteInputTokens", 0) or 0)
                cache_read = int((usage or {}).get("cacheReadInputTokens", 0) or 0)
                total_input = input_tokens + cache_write + cache_read
                estimated_cost = cost_usd(
                    Usage(
                        self.model,
                        input_tokens=total_input,
                        output_tokens=output_tokens,
                        cache_creation_input_tokens=cache_write,
                        cache_read_input_tokens=cache_read,
                        provider="aws.bedrock",
                    )
                )
                stop_reason = result.get("stopReason")
                call.record_completion(
                    model=self.model,
                    input_tokens=total_input,
                    output_tokens=output_tokens,
                    cache_creation_input_tokens=cache_write,
                    cache_read_input_tokens=cache_read,
                    cost_usd=estimated_cost,
                    finish_reason=stop_reason if isinstance(stop_reason, str) else None,
                )
        except ProviderNotConfigured:
            raise
        except Exception as exc:
            raise ProviderCallFailed(f"bedrock call failed: {exc}") from exc

        content = result["output"]["message"]["content"]
        text = "".join(
            block["text"] for block in content if isinstance(block, dict) and "text" in block
        )
        return ProviderResult(
            text=text,
            model=self.model,
            provider=self.name,
            stop_reason=stop_reason if isinstance(stop_reason, str) else None,
            input_tokens=total_input,
            output_tokens=output_tokens,
            cache_creation_input_tokens=cache_write,
            cache_read_input_tokens=cache_read,
            cost_usd=estimated_cost,
        )


def make_provider(name: str | None = None, model: str | None = None) -> Provider:
    """Construct a provider by name (default: ``$RECONCILER_AI_PROVIDER`` or
    ``"anthropic"``), with an optional model override (default:
    ``$RECONCILER_AI_MODEL`` or the provider's own default). An unrecognized
    provider name raises, fail-closed, rather than silently substituting a
    different one.
    """

    chosen = name or os.environ.get("RECONCILER_AI_PROVIDER", "anthropic")
    override = model or os.environ.get("RECONCILER_AI_MODEL")
    if chosen == "anthropic":
        return AnthropicProvider(model=override or DEFAULT_ANTHROPIC_MODEL)
    if chosen == "bedrock":
        return BedrockProvider(model=override or DEFAULT_BEDROCK_MODEL)
    raise ProviderNotConfigured(
        f"unknown AI provider {chosen!r}; known providers: anthropic, bedrock"
    )
