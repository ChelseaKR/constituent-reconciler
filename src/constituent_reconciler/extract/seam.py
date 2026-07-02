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
from pathlib import Path
from typing import Any

from constituent_reconciler.extract.base import ExtractedField

logger = logging.getLogger(__name__)

# Policy packs that forbid any cloud call. PII must never leave the machine.
_CLOUD_FORBIDDEN: frozenset[str] = frozenset({"dv", "hipaa"})

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

_MISSING_RENDER_DEPS = (
    "pdfplumber (and its Pillow dependency) is required to render "
    "low-confidence pages for cloud refinement. Install it with: "
    "pip install 'constituent-reconciler[extract]'"
)


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
        model_id: str = "us.anthropic.claude-sonnet-4-6:0",
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
        except Exception:
            logger.warning(
                "Bedrock refinement failed for %s page %d; keeping local extraction",
                path.name,
                page_num,
                exc_info=True,
            )
            return []
        return _parse_response(result)


def make_seam(policy_pack: str, backend: str = "none") -> NoOpSeam | BedrockSeam:
    """Construct the appropriate cloud seam for this policy pack and backend.

    DV and HIPAA packs always return NoOpSeam: PII must not egress, period.
    Any other pack with backend='bedrock' returns a BedrockSeam; all other
    backends return NoOpSeam.
    """
    if policy_pack in _CLOUD_FORBIDDEN:
        return NoOpSeam()
    if backend == "bedrock":
        return BedrockSeam()
    return NoOpSeam()
