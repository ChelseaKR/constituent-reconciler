"""Small JSON-response parsing helpers shared across assistant feature modules.

Mirrors the fence-stripping convention ``extract/seam.py`` already uses for
model responses, so the two cloud-facing modules in this codebase agree on
how a "respond with only a JSON object" instruction is parsed.
"""

from __future__ import annotations

import json
from typing import Any


def strip_json_fence(text: str) -> str:
    """Remove a Markdown code fence (``` or ```json) wrapping a JSON payload."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    lines = lines[1:]  # drop the opening fence line, with or without a language tag
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_json_object(text: str) -> dict[str, Any] | None:
    """Parse a model response as a single JSON object, or ``None`` if it is not one.

    Never raises: a malformed or non-object response is exactly the case
    every caller must treat as "nothing usable came back," not a crash.
    """
    try:
        data = json.loads(strip_json_fence(text))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None
