"""The consent-leakage eval has to be able to fail, and for the right reason.

Its own docstring calls a leak "a merge-blocking-grade finding, not a tuning
number", and it is the one eval in ``tools/ai_eval`` that always runs, because
it is deterministic and needs no provider. So it is the one carrying the load.

Until 2026-09-13 every assertion it made was inside ``for field_name in
filtered.withheld_fields()``. The oracle was the output of the component under
test, so a filter that decided to withhold nothing was reported as leaking
nothing. Measured on ``origin/main`` with ``filter_record`` replaced by one
that withholds nothing and passes every sentinel straight through::

    checks_run: 15   leaks_found: 0   pass: True

That is the shape #160 gave ``None`` to in ``evaluate.rate``: a measurement
over an empty denominator reading as a clean result.

The oracle is now the fixture's own ``must_withhold`` table. Each test below
sabotages one component and pins the clause of the pass condition that
catches it -- including the direction that is easy to forget, a filter that
withholds *everything*, which a leak-only oracle would wave through while the
feature was just as broken.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from tools.ai_eval import consent_leakage
from tools.ai_eval.fixtures import CONSENT_LEAKAGE_CASES, CONSENT_LEAKAGE_PACKS

from constituent_reconciler.assistant.consent_filter import (
    FilteredField,
    FilteredRecord,
    filter_record,
)
from constituent_reconciler.assistant.evidence_payload import evidence_payload


def _visible(record: Any, fields: tuple[str, ...]) -> FilteredRecord:
    """What ``filter_record`` returns when consent filtering has stopped working."""
    return FilteredRecord(
        record_id=record.unique_id,
        fields=tuple(
            FilteredField(name=name, value=record.normalized[name], withheld_reason=None)
            for name in fields
        ),
    )


# ---------------------------------------------------------------------------
# The denominator, and that it does not depend on the component under test.
# ---------------------------------------------------------------------------


def test_the_eval_judges_every_decision_the_fixtures_present() -> None:
    report = consent_leakage.run()

    expected = sum(len(case.fields) for case in CONSENT_LEAKAGE_CASES) * len(CONSENT_LEAKAGE_PACKS)
    assert report["decisions_available"] == expected
    assert report["decisions_judged"] == expected
    assert report["packs_without_expectation"] == []
    assert report["pass"] is True
    assert report["findings"] == []


def test_the_denominator_does_not_move_when_the_filter_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The number that was unpublished before is the number that used to collapse.

    ``checks_run`` counted pack iterations and never moved; the count of
    assertions actually made went to zero and was not published at all. The
    published denominator now comes from the fixtures, so a filter that stops
    withholding cannot shrink it -- it can only produce findings.
    """
    honest = consent_leakage.run()

    def leaky(record: Any, *, policy: Any, fields: tuple[str, ...], as_of: Any) -> FilteredRecord:
        return _visible(record, fields)

    monkeypatch.setattr(consent_leakage, "filter_record", leaky)
    sabotaged = consent_leakage.run()

    assert sabotaged["decisions_available"] == honest["decisions_available"]
    assert sabotaged["decisions_judged"] == honest["decisions_judged"]
    assert sabotaged["pass"] is False


# ---------------------------------------------------------------------------
# Clause 1: findings. Three sabotages, three distinguishable finding kinds.
# ---------------------------------------------------------------------------


def test_a_filter_that_withholds_nothing_fails_the_eval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The total failure of the feature under test must not read as a pass."""

    def leaky(record: Any, *, policy: Any, fields: tuple[str, ...], as_of: Any) -> FilteredRecord:
        return _visible(record, fields)

    monkeypatch.setattr(consent_leakage, "filter_record", leaky)
    report = consent_leakage.run()

    kinds = {finding["kind"] for finding in report["findings"]}
    assert "not_withheld" in kinds
    assert "leak" in kinds
    assert report["leaks_found"] > 0
    assert report["pass"] is False


def test_a_filter_that_leaks_only_some_fields_fails_the_eval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sabotage the previous oracle could not see.

    A filter that keeps withholding ``first_name`` and ``dob`` but lets
    ``last_name``, ``email`` and ``phone`` through under every pack leaves at
    least one withheld field in every fixture, so it satisfied both the old
    oracle and a premise check that only asks whether *something* was
    withheld. Measured against the pre-fix eval it reported ``leaks_found: 0,
    pass: True`` while leaking three fields of five under ``dv`` and
    ``hipaa``.
    """
    # Imported from its defining module rather than read off `consent_leakage`:
    # mypy's strict `no_implicit_reexport` refuses the attribute read, and the
    # monkeypatch below still targets the name the eval actually calls.
    real_filter = filter_record
    leaked = ("last_name", "email", "phone")

    def partial(record: Any, *, policy: Any, fields: tuple[str, ...], as_of: Any) -> FilteredRecord:
        honest = real_filter(record, policy=policy, fields=fields, as_of=as_of)
        return FilteredRecord(
            record_id=honest.record_id,
            fields=tuple(
                FilteredField(name=f.name, value=record.normalized[f.name], withheld_reason=None)
                if f.name in leaked
                else f
                for f in honest.fields
            ),
        )

    monkeypatch.setattr(consent_leakage, "filter_record", partial)
    report = consent_leakage.run()

    leaked_fields = {f["field"] for f in report["findings"] if f["kind"] == "leak"}
    assert leaked_fields, "the partial leak must be visible"
    assert leaked_fields <= set(leaked)
    assert "last_name" in leaked_fields
    assert report["pass"] is False


def test_a_filter_that_withholds_everything_fails_the_eval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other direction, so the eval is not satisfiable by refusing everything.

    A leak-only oracle is trivially satisfied by a filter that shows nothing,
    which destroys the feature just as thoroughly as one that shows
    everything. The fixture declares which fields must stay *visible* under
    each pack, so this is a finding rather than a perfect score.
    """

    def paranoid(
        record: Any, *, policy: Any, fields: tuple[str, ...], as_of: Any
    ) -> FilteredRecord:
        return FilteredRecord(
            record_id=record.unique_id,
            fields=tuple(
                FilteredField(name=name, value=None, withheld_reason="sabotage") for name in fields
            ),
        )

    monkeypatch.setattr(consent_leakage, "filter_record", paranoid)
    report = consent_leakage.run()

    kinds = [f["kind"] for f in report["findings"]]
    assert "withheld_when_it_should_be_visible" in kinds
    assert report["leaks_found"] == 0, "nothing leaked; the failure is the other direction"
    assert report["pass"] is False


def test_a_planted_leak_in_the_prompt_payload_fails_the_eval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The payload builder is under test too, not only the filter.

    Without this the whole pass condition could rest on the filter checks and
    the payload check could be dead, which is the shape this file is about.
    """
    # Imported from its defining module rather than read off `consent_leakage`:
    # mypy's strict `no_implicit_reexport` refuses the attribute read, and the
    # monkeypatch below still targets the name the eval actually calls.
    real_payload = evidence_payload

    def leaking(evidence: Any, *, withheld_fields: tuple[str, ...]) -> dict[str, object]:
        payload = real_payload(evidence, withheld_fields=withheld_fields)
        return {**payload, "debug_note": " ".join(consent_leakage._SENTINEL_VALUES.values())}

    monkeypatch.setattr(consent_leakage, "evidence_payload", leaking)
    report = consent_leakage.run()

    leaked_into = {f.get("leaked_into") for f in report["findings"] if f["kind"] == "leak"}
    assert "evidence_payload (the actual model prompt payload)" in leaked_into
    assert report["pass"] is False


def test_an_expectation_naming_a_field_the_case_does_not_carry_is_a_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale expectation must not sit there looking like coverage."""
    case = CONSENT_LEAKAGE_CASES[0]
    stale = replace(
        case,
        must_withhold={**case.must_withhold, "dv": (*case.must_withhold["dv"], "address")},
    )
    monkeypatch.setattr(
        consent_leakage, "CONSENT_LEAKAGE_CASES", (stale, *CONSENT_LEAKAGE_CASES[1:])
    )
    report = consent_leakage.run()

    kinds = [f["kind"] for f in report["findings"]]
    assert "expectation_names_an_unknown_field" in kinds
    assert report["pass"] is False


# ---------------------------------------------------------------------------
# Clause 2: the judged/available equality, reachable without any finding.
# ---------------------------------------------------------------------------


def test_a_pack_with_no_declared_expectation_fails_the_eval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dropping an expectation must redden the gate, not shrink the denominator.

    This is the clause that cannot be reached by any of the sabotages above:
    it produces no finding at all, only a judged count below the available
    one. Gating on both is what keeps either from being decorative.
    """
    case = CONSENT_LEAKAGE_CASES[0]
    without_dv = replace(
        case,
        must_withhold={k: v for k, v in case.must_withhold.items() if k != "dv"},
    )
    monkeypatch.setattr(
        consent_leakage, "CONSENT_LEAKAGE_CASES", (without_dv, *CONSENT_LEAKAGE_CASES[1:])
    )
    report = consent_leakage.run()

    assert report["findings"] == [], "no finding fires; only the denominator clause does"
    assert report["decisions_judged"] < report["decisions_available"]
    assert report["packs_without_expectation"] == ["revoked/dv"]
    assert report["pass"] is False


# ---------------------------------------------------------------------------
# The fixture table itself.
# ---------------------------------------------------------------------------


def test_every_fixture_declares_an_expectation_for_every_pack() -> None:
    for case in CONSENT_LEAKAGE_CASES:
        assert set(case.must_withhold) == set(CONSENT_LEAKAGE_PACKS), case.name
        for pack, fields in case.must_withhold.items():
            assert set(fields) <= set(case.fields), (case.name, pack)


def test_every_fixture_field_has_a_sentinel_value() -> None:
    """A field with no sentinel could not be searched for, so it could not leak."""
    for case in CONSENT_LEAKAGE_CASES:
        for name in case.fields:
            assert name in consent_leakage._SENTINEL_VALUES, (case.name, name)
