"""The consent-leakage eval has to be able to fail.

Its own docstring calls a leak "a merge-blocking-grade finding, not a tuning
number", and it is the one eval in `tools/ai_eval` that always runs, because it
is deterministic and needs no provider. So it is the one carrying the load.

Every assertion it makes is inside `for field_name in filtered.withheld_fields()`.
The oracle is the output of the component under test, and `checks_run` counted
pack iterations rather than field assertions, so a filter that withheld nothing
produced no assertions and moved no published number.

Measured on origin/main, 2026-09-06, with `filter_record` replaced by one that
withholds nothing and passes every sentinel value straight into the payload:

    checks_run: 15   leaks_found: 0   pass: True

That is the shape #160 gave `None` to in `evaluate.rate`: a measurement over an
empty denominator reading as a clean result.
"""

from __future__ import annotations

from typing import Any

import pytest
from tools.ai_eval import consent_leakage


class _Field:
    def __init__(self, name: str, value: str) -> None:
        self.name = name
        self.value = value


class _WithholdsNothing:
    """What `filter_record` returns when consent filtering has stopped working."""

    def __init__(self, fields: tuple[_Field, ...]) -> None:
        self._fields = fields

    @property
    def fields(self) -> tuple[_Field, ...]:
        return self._fields

    def withheld_fields(self) -> tuple[str, ...]:
        return ()


def test_the_eval_asserts_something_and_says_how_much() -> None:
    """The published denominator is the field assertions, not the iterations.

    `checks_run` counts pack iterations and does not move when nothing is
    asserted, so it cannot be read as evidence on its own.
    """
    report = consent_leakage.run()
    assert report["pass"] is True
    assert report["cases_with_nothing_withheld"] == []
    assert report["checks_run"] > 0
    assert report["fields_asserted"] > report["checks_run"], (
        "the two numbers must be distinguishable; if they can be confused the "
        "published sentence is back where it started"
    )


def test_a_filter_that_withholds_nothing_fails_the_eval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The total failure of the feature under test must not read as a pass."""

    def leaky(record: Any, *, policy: Any, fields: tuple[str, ...], as_of: Any) -> Any:
        return _WithholdsNothing(tuple(_Field(name, record.raw[name]) for name in fields))

    monkeypatch.setattr(consent_leakage, "filter_record", leaky)
    report = consent_leakage.run()

    assert report["fields_asserted"] == 0
    assert report["leaks_found"] == 0, "there is nothing to find, which is the point"
    assert report["cases_with_nothing_withheld"], (
        "every fixture is built so consent does not clear the gate for at least "
        "one field; a run where none does contradicts the eval's own premise"
    )
    assert report["pass"] is False


def test_a_planted_leak_fails_the_eval(monkeypatch: pytest.MonkeyPatch) -> None:
    """The other clause of the pass condition, exercised on its own.

    Without this, the whole pass condition could rest on the premise check and
    the leak check could be dead, which is the shape this file is about.
    """
    real_payload = consent_leakage.evidence_payload

    def leaking(evidence: Any, *, withheld_fields: tuple[str, ...]) -> Any:
        payload = real_payload(evidence, withheld_fields=withheld_fields)
        return {**payload, "debug_note": " ".join(consent_leakage._SENTINEL_VALUES.values())}

    monkeypatch.setattr(consent_leakage, "evidence_payload", leaking)
    report = consent_leakage.run()

    assert report["fields_asserted"] > 0, "the assertions still ran"
    assert report["cases_with_nothing_withheld"] == [], "the premise still holds"
    assert report["leaks_found"] > 0
    assert report["pass"] is False
