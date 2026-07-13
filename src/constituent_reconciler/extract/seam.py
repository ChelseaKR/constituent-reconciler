"""Policy-gated cloud seam for low-confidence page refinement.

The cloud seam is invoked only when all three conditions hold:
  1. The active policy pack allows cloud calls (DV and HIPAA packs forbid them).
  2. A page's confidence is below the recipe's confidence_threshold.
  3. Cloud credentials are available.

Under DV/VAWA and HIPAA policy packs this module returns a NoOpSeam regardless
of what the recipe requests. The non-egress invariant is enforced at
construction time, not at call time, so there is no window where a misconfigured
seam could accidentally call out.
"""

from __future__ import annotations

import io
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from constituent_reconciler._vendor.genai_telemetry import Usage, cost_usd
from constituent_reconciler.extract.base import ExtractedField
from constituent_reconciler.policy import policy_for
from constituent_reconciler.telemetry import genai_call

logger = logging.getLogger(__name__)

# Canonical field names the cloud seam may return, mirroring the local
# extractor's field set (extract/pdf.py) plus the canonical address field.
_CANONICAL_FIELDS: tuple[str, ...] = (
    "first_name",
    "last_name",
    "dob",
    "email",
    "phone",
    "address",
)

# Rendering resolution for page images sent to the model. 150 DPI keeps the
# payload small while remaining legible for form-like intake documents.
_RENDER_DPI = 150
_LOCAL_HOST = "http://127.0.0.1:11434"
_LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})

_MISSING_RENDER_DEPS = (
    "pdfplumber (and its Pillow dependency) is required to render "
    "low-confidence pages for cloud refinement. Install it with: "
    "pip install 'constituent-reconciler[extract]'"
)


def _usage_count(usage: dict[str, Any], key: str, *, default: int | None = None) -> int | None:
    value = usage.get(key, default)
    return value if type(value) is int and value >= 0 else None


def _page_to_png(path: Path, page_num: int) -> bytes:
    """Render one PDF page (1-indexed) to PNG bytes for the Converse image block.

    Raises ``RuntimeError`` with an install hint when pdfplumber or its Pillow
    rendering dependency is missing — a clear error rather than a silent
    fallback, matching the libpostal convention in docs/ROADMAP.md.
    """
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError(_MISSING_RENDER_DEPS) from exc

    buffer = io.BytesIO()
    with pdfplumber.open(path) as pdf:
        page = pdf.pages[page_num - 1]
        try:
            image = page.to_image(resolution=_RENDER_DPI).original
        except ImportError as exc:  # Pillow missing despite pdfplumber present
            raise RuntimeError(_MISSING_RENDER_DEPS) from exc
        image.save(buffer, format="PNG")
    return buffer.getvalue()


def _build_prompt() -> str:
    """Instruction asking Claude for constituent fields as strict JSON."""
    field_list = ", ".join(_CANONICAL_FIELDS)
    return (
        "This image is one page of a constituent intake document. Extract any "
        f"of the following fields that appear on the page: {field_list}. "
        "Respond with ONLY a JSON object of exactly this shape: "
        '{"fields": [{"name": "<field name>", "value": "<extracted value>", '
        '"confidence": <number between 0 and 1>}]}. '
        "Use only the field names listed above, omit fields that are absent, "
        "and include no prose outside the JSON object."
    )


def _strip_json_fence(text: str) -> str:
    """Remove a Markdown code fence (``` or ```json) wrapping a JSON payload."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    lines = lines[1:]  # drop the opening fence line, with or without a language tag
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _parse_response(response: dict[str, Any]) -> list[ExtractedField]:
    """Parse a Bedrock Converse response into extracted fields.

    Malformed responses — missing keys, non-JSON text, or a wrong shape —
    return ``[]`` rather than raising: a failed cloud refinement falls back to
    the local extraction, consistent with the seam's design. Individual
    malformed entries are skipped; confidence is clamped to [0, 1].
    """
    try:
        content = response["output"]["message"]["content"]
        text = "".join(
            block["text"] for block in content if isinstance(block, dict) and "text" in block
        )
    except (KeyError, TypeError):
        return []

    try:
        data = json.loads(_strip_json_fence(text))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    raw_fields = data.get("fields")
    if not isinstance(raw_fields, list):
        return []

    fields: list[ExtractedField] = []
    for entry in raw_fields:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        value = entry.get("value")
        confidence = entry.get("confidence")
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        if isinstance(confidence, bool) or not isinstance(confidence, int | float):
            continue
        fields.append(
            ExtractedField(
                field_name=name.strip(),
                value=value.strip(),
                confidence=min(1.0, max(0.0, float(confidence))),
            )
        )
    return fields


def _page_text(path: Path, page_num: int) -> str:
    """Read one PDF page's embedded text layer for a local text model."""
    try:
        import pdfplumber
    except ImportError as exc:
        raise RuntimeError(_MISSING_RENDER_DEPS) from exc

    with pdfplumber.open(path) as pdf:
        return pdf.pages[page_num - 1].extract_text(x_tolerance=3, y_tolerance=3) or ""


def _parse_local_fields(text: str) -> list[ExtractedField]:
    """Parse a local model JSON response into model-assisted fields."""
    try:
        data = json.loads(_strip_json_fence(text))
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    raw_fields = data.get("fields")
    if not isinstance(raw_fields, list):
        return []

    fields: list[ExtractedField] = []
    for entry in raw_fields:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        value = entry.get("value")
        if not isinstance(name, str) or name not in _CANONICAL_FIELDS:
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        fields.append(ExtractedField(field_name=name, value=value.strip(), confidence=0.6))
    return fields


def _validate_loopback(host: str) -> str:
    parsed = urllib.parse.urlparse(host)
    if parsed.scheme not in ("http", "https") or parsed.hostname is None:
        raise ValueError("local seam host must be an http(s) loopback URL")
    if parsed.hostname not in _LOOPBACK_HOSTS:
        raise ValueError("local seam host must be loopback")
    return host.rstrip("/")


class NoOpSeam:
    """Disabled cloud seam. Used when policy forbids cloud calls, or when no
    cloud backend is configured. Always claims to be disabled and returns nothing
    from refine(), which the caller should never reach (the gate checks
    is_enabled() first).
    """

    def is_enabled(self) -> bool:
        return False

    def refine(self, path: Path, page_num: int) -> list[ExtractedField]:
        return []


class BedrockSeam:
    """Claude on Bedrock seam for low-confidence PDF pages.

    The seam renders the page to a PNG, sends it to a Claude model via the
    Amazon Bedrock Converse API, and parses the JSON response into
    ``ExtractedField`` values. The actual network call is deferred:
    ``is_enabled()`` checks for boto3 and a configured region, and ``refine()``
    performs the call only if enabled.

    Tests inject a fake via the ``client`` constructor argument (any object
    with a ``converse()`` method), so no boto3 install or network access is
    needed to exercise the parsing and fault-tolerance paths.
    """

    def __init__(
        self,
        model_id: str = "us.anthropic.claude-sonnet-4-6",
        client: Any | None = None,
    ) -> None:
        self._model_id = model_id
        self._client: Any | None = client

    def is_enabled(self) -> bool:
        if self._client is not None:
            return True
        try:
            import boto3

            self._client = boto3.client("bedrock-runtime")
            return True
        except ImportError:
            return False
        except Exception:
            return False

    def refine(self, path: Path, page_num: int) -> list[ExtractedField]:
        """Send a low-confidence page to Claude on Bedrock for field extraction.

        Returns ``[]`` when the seam has no client or when the Converse call
        fails, so the pipeline falls back to the local extraction. A missing
        rendering dependency raises ``RuntimeError`` (see ``_page_to_png``)
        because that is a deployment error, not a transient cloud failure.
        """
        if self._client is None:
            return []
        png_bytes = _page_to_png(path, page_num)
        try:
            with genai_call("aws.bedrock", self._model_id) as call:
                result = self._client.converse(
                    modelId=self._model_id,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {"image": {"format": "png", "source": {"bytes": png_bytes}}},
                                {"text": _build_prompt()},
                            ],
                        }
                    ],
                    inferenceConfig={"maxTokens": 1024},
                )
                usage = result.get("usage")
                if isinstance(usage, dict):
                    fresh_input_tokens = _usage_count(usage, "inputTokens")
                    output_tokens = _usage_count(usage, "outputTokens")
                    cache_creation_input_tokens = _usage_count(
                        usage, "cacheWriteInputTokens", default=0
                    )
                    cache_read_input_tokens = _usage_count(usage, "cacheReadInputTokens", default=0)
                    if (
                        fresh_input_tokens is not None
                        and output_tokens is not None
                        and cache_creation_input_tokens is not None
                        and cache_read_input_tokens is not None
                    ):
                        input_tokens = (
                            fresh_input_tokens
                            + cache_creation_input_tokens
                            + cache_read_input_tokens
                        )
                        call.record_completion(
                            model=self._model_id,
                            input_tokens=input_tokens,
                            output_tokens=output_tokens,
                            cache_creation_input_tokens=cache_creation_input_tokens,
                            cache_read_input_tokens=cache_read_input_tokens,
                            cost_usd=cost_usd(
                                Usage(
                                    self._model_id,
                                    input_tokens=input_tokens,
                                    output_tokens=output_tokens,
                                    cache_creation_input_tokens=cache_creation_input_tokens,
                                    cache_read_input_tokens=cache_read_input_tokens,
                                    provider="aws.bedrock",
                                )
                            ),
                            finish_reason=(
                                result.get("stopReason")
                                if isinstance(result.get("stopReason"), str)
                                else None
                            ),
                        )
        except Exception:
            logger.warning(
                "Bedrock refinement failed for %s page %d; keeping local extraction",
                path.name,
                page_num,
                exc_info=True,
            )
            return []
        return _parse_response(result)


class LocalSeam:
    """Loopback-only Ollama seam for model-assisted extraction without egress."""

    def __init__(
        self,
        *,
        model_id: str = "llama3.2",
        host: str = _LOCAL_HOST,
        timeout: float = 5.0,
    ) -> None:
        self._model_id = model_id
        self._host = _validate_loopback(host)
        self._timeout = timeout

    def _json_request(self, path: str, payload: dict[str, object] | None = None) -> dict[str, Any]:
        url = f"{self._host}{path}"
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 - host is loopback-validated in __init__.
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="GET" if payload is None else "POST",
        )
        # nosemgrep: dynamic-urllib-use-detected - request URL uses loopback-validated host.
        with urllib.request.urlopen(  # noqa: S310
            request,
            timeout=self._timeout,
        ) as response:
            raw = response.read()
        parsed = json.loads(raw.decode("utf-8"))
        return parsed if isinstance(parsed, dict) else {}

    def is_enabled(self) -> bool:
        try:
            self._json_request("/api/tags")
            return True
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            return False

    def refine(self, path: Path, page_num: int) -> list[ExtractedField]:
        text = _page_text(path, page_num)
        if not text.strip():
            return []
        prompt = (
            "Extract constituent fields from this intake page text. Respond only "
            "as JSON with shape "
            '{"fields":[{"name":"first_name|last_name|dob|email|phone|address",'
            '"value":"..."}]}. Use only fields present in the text.\n\n'
            f"{text}"
        )
        try:
            with genai_call("ollama", self._model_id) as call:
                response = self._json_request(
                    "/api/generate",
                    {
                        "model": self._model_id,
                        "prompt": prompt,
                        "stream": False,
                        "format": "json",
                    },
                )
                input_tokens = response.get("prompt_eval_count", 0)
                output_tokens = response.get("eval_count", 0)
                call.record_completion(
                    model=self._model_id,
                    input_tokens=input_tokens if isinstance(input_tokens, int) else 0,
                    output_tokens=output_tokens if isinstance(output_tokens, int) else 0,
                    cost_usd=0.0,
                    finish_reason=response.get("done_reason")
                    if isinstance(response.get("done_reason"), str)
                    else None,
                )
        except (OSError, urllib.error.URLError, json.JSONDecodeError):
            logger.warning(
                "Local model refinement failed for %s page %d; keeping local extraction",
                path.name,
                page_num,
                exc_info=True,
            )
            return []
        raw_text = response.get("response")
        return _parse_local_fields(raw_text) if isinstance(raw_text, str) else []


def make_seam(
    policy_pack: str,
    backend: str = "none",
    *,
    local_model_override: bool = False,
    local_model_id: str = "llama3.2",
    local_host: str = _LOCAL_HOST,
) -> NoOpSeam | BedrockSeam | LocalSeam:
    """Construct the appropriate cloud seam for this policy pack and backend.

    DV and HIPAA packs always fuse cloud seams off. Local model extraction is
    separate: it requires either a policy pack that allows it or an explicit
    recipe override, and the local seam still validates that its host is
    loopback-only before any request can be made.
    """
    policy = policy_for(policy_pack)
    if backend == "bedrock":
        if policy.forbid_cloud_seam:
            return NoOpSeam()
        return BedrockSeam()
    if backend == "local" and (policy.allow_local_seam or local_model_override):
        return LocalSeam(model_id=local_model_id, host=local_host)
    return NoOpSeam()
