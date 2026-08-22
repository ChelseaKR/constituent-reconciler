"""Shared provenance stamping for every AI eval result.

Every result this package writes carries ``provider``, ``model``,
``prompt_version``, ``commit``, ``date``, and ``status`` -- enough to answer
"what exactly produced this number, and did it actually run" without
re-running anything. A result whose model never actually ran (no configured
provider) still carries every field: ``status`` is ``"not run"`` and
``provider``/``model`` name what was *attempted*, never a fabricated number
standing in for a live measurement.
"""

from __future__ import annotations

import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from constituent_reconciler.assistant.prompts import PROMPT_VERSION

REQUIRED_PROVENANCE_FIELDS: tuple[str, ...] = (
    "provider",
    "model",
    "prompt_version",
    "commit",
    "date",
    "status",
)

Status = Literal["ran", "not run", "deterministic"]


def current_commit() -> str:
    """The current git commit SHA, or ``"unknown"`` outside a git checkout."""
    try:
        argv = ["git", "rev-parse", "HEAD"]  # noqa: S607 - PATH-resolved git is intended
        result = subprocess.run(  # noqa: S603 - fixed argv, no shell, no untrusted input
            argv,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


@dataclass(frozen=True)
class Provenance:
    provider: str
    model: str
    prompt_version: str
    commit: str
    date: str
    status: Status

    @classmethod
    def stamp(cls, *, provider: str, model: str, status: Status) -> Provenance:
        return cls(
            provider=provider,
            model=model,
            prompt_version=PROMPT_VERSION,
            commit=current_commit(),
            date=datetime.now(UTC).date().isoformat(),
            status=status,
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def missing_provenance_fields(record: dict[str, Any]) -> tuple[str, ...]:
    """The subset of ``REQUIRED_PROVENANCE_FIELDS`` missing or empty in ``record``.

    An empty tuple means the record carries complete provenance.
    ``tests/test_ai_eval_provenance.py`` calls this on every entry of the
    committed ``eval/ai/results.json`` and fails the suite on any non-empty
    result.
    """
    missing = []
    for field_name in REQUIRED_PROVENANCE_FIELDS:
        value = record.get(field_name)
        if not isinstance(value, str) or not value.strip():
            missing.append(field_name)
    if record.get("status") not in ("ran", "not run", "deterministic"):
        missing.append("status")
    return tuple(missing)
