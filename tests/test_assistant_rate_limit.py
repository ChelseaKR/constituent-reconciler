"""Tests for the AI assistant's per-minute rate limit and hard daily cap."""

from __future__ import annotations

from pathlib import Path

import pytest

from constituent_reconciler.assistant.errors import RateLimitExceeded
from constituent_reconciler.assistant.rate_limit import RateLimiter


def test_calls_under_the_limit_succeed(tmp_path: Path) -> None:
    limiter = RateLimiter(
        state_path=tmp_path / "ai_usage.json", max_calls_per_minute=5, daily_cap=100
    )
    for i in range(5):
        limiter.check_and_record(now=1000.0 + i)  # all within the same minute


def test_exceeding_the_per_minute_limit_raises(tmp_path: Path) -> None:
    limiter = RateLimiter(
        state_path=tmp_path / "ai_usage.json", max_calls_per_minute=3, daily_cap=100
    )
    for i in range(3):
        limiter.check_and_record(now=1000.0 + i)
    with pytest.raises(RateLimitExceeded, match="rate limit"):
        limiter.check_and_record(now=1003.0)


def test_a_rejected_call_is_not_recorded(tmp_path: Path) -> None:
    limiter = RateLimiter(
        state_path=tmp_path / "ai_usage.json", max_calls_per_minute=1, daily_cap=100
    )
    limiter.check_and_record(now=1000.0)
    with pytest.raises(RateLimitExceeded):
        limiter.check_and_record(now=1000.5)
    # A minute later the earlier accepted call has aged out, and the daily
    # cap still has room, so this call must succeed -- proving the rejected
    # call above was never recorded.
    limiter.check_and_record(now=1061.0)


def test_exceeding_the_daily_cap_raises_even_with_gaps(tmp_path: Path) -> None:
    limiter = RateLimiter(
        state_path=tmp_path / "ai_usage.json", max_calls_per_minute=100, daily_cap=2
    )
    limiter.check_and_record(now=1000.0)
    limiter.check_and_record(now=2000.0)
    with pytest.raises(RateLimitExceeded, match="daily"):
        limiter.check_and_record(now=3000.0)


def test_state_persists_across_separate_limiter_instances(tmp_path: Path) -> None:
    state_path = tmp_path / "ai_usage.json"
    RateLimiter(state_path=state_path, max_calls_per_minute=100, daily_cap=1).check_and_record(
        now=1000.0
    )
    second = RateLimiter(state_path=state_path, max_calls_per_minute=100, daily_cap=1)
    with pytest.raises(RateLimitExceeded, match="daily"):
        second.check_and_record(now=1001.0)


def test_calls_older_than_a_day_are_pruned(tmp_path: Path) -> None:
    limiter = RateLimiter(
        state_path=tmp_path / "ai_usage.json", max_calls_per_minute=100, daily_cap=1
    )
    limiter.check_and_record(now=1000.0)
    # More than 24h later, the earlier call has aged out of both windows.
    limiter.check_and_record(now=1000.0 + 86_401)


def test_corrupt_state_file_is_treated_as_empty(tmp_path: Path) -> None:
    state_path = tmp_path / "ai_usage.json"
    state_path.write_text("not json")
    limiter = RateLimiter(state_path=state_path, max_calls_per_minute=5, daily_cap=5)
    limiter.check_and_record(now=1000.0)  # must not raise despite the corrupt file
