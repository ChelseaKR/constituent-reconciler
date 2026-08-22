"""Cost control: a per-session call rate limit and a hard daily cap.

State persists to a small, PII-free JSON file (call timestamps only, never
prompt or response content) so the daily cap holds across separate CLI
invocations against the same ``out`` directory, and across the review
server's separate HTTP requests within one reviewer session. Exceeding
either limit raises ``RateLimitExceeded``; nothing in this module, or in any
caller, touches the deterministic pipeline (``pipeline.py``, ``decisions.py``,
the ``run``/``review``/``apply`` CLI commands) -- a rate limit here can only
ever disable the optional AI feature for the rest of the window, never the
deterministic path a reviewer is already using.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from constituent_reconciler.assistant.errors import RateLimitExceeded

_ONE_DAY_SECONDS = 86_400
_ONE_MINUTE_SECONDS = 60


@dataclass
class RateLimiter:
    """Enforces a per-minute call rate and a hard daily cap for one state file.

    A fresh ``RateLimiter`` is cheap to construct per call site; it reads
    and rewrites ``state_path`` on every check, so state is always current
    across process boundaries without a background thread or a lock file --
    acceptable for a local, single-operator tool where a lost race between
    two near-simultaneous calls costs at most one over-count, never an
    unbounded one.
    """

    state_path: Path
    max_calls_per_minute: int = 20
    daily_cap: int = 200

    def _load(self) -> list[float]:
        if not self.state_path.exists():
            return []
        try:
            data = json.loads(self.state_path.read_text())
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(data, dict):
            return []
        calls = data.get("calls")
        if not isinstance(calls, list):
            return []
        return [float(c) for c in calls if isinstance(c, int | float)]

    def _save(self, calls: list[float]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({"calls": calls}))

    def check_and_record(self, *, now: float | None = None) -> None:
        """Raise ``RateLimitExceeded`` if this call would exceed either
        budget; otherwise record it. A rejected call is never recorded, so
        it never counts against either budget itself.
        """

        current = now if now is not None else datetime.now(UTC).timestamp()
        calls = [c for c in self._load() if current - c < _ONE_DAY_SECONDS]

        recent = [c for c in calls if current - c < _ONE_MINUTE_SECONDS]
        if len(recent) >= self.max_calls_per_minute:
            raise RateLimitExceeded(
                f"AI call rate limit exceeded: {self.max_calls_per_minute} calls/minute"
            )

        today = datetime.fromtimestamp(current, tz=UTC).date()
        today_calls = [c for c in calls if datetime.fromtimestamp(c, tz=UTC).date() == today]
        if len(today_calls) >= self.daily_cap:
            raise RateLimitExceeded(f"AI daily call cap exceeded: {self.daily_cap} calls/day")

        calls.append(current)
        self._save(calls)
