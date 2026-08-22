"""Tests for the deterministic review-queue triage ordering.

No FakeProvider here: triage_queue never calls a model, and these tests
pin the exact ordering rule described in triage.py's docstring.
"""

from __future__ import annotations

from datetime import date

from constituent_reconciler.assistant.triage import triage_queue
from constituent_reconciler.matching.evidence import FieldEvidence, PairEvidence
from constituent_reconciler.models import Band, Consent, Pair


def _pair(left: str, right: str, probability: float) -> Pair:
    return Pair(left=left, right=right, probability=probability, band=Band.REVIEW)


def _evidence(left: str, right: str, disagreements: tuple[str, ...]) -> PairEvidence:
    fields = tuple(
        FieldEvidence(
            field=name,
            left_value="x",
            right_value="y",
            level_label="different",
            m_probability=0.1,
            u_probability=0.9,
            bayes_factor=0.11,
            is_null_level=False,
        )
        for name in disagreements
    )
    return PairEvidence(
        left_id=left, right_id=right, match_probability=0.85, match_weight=1.0, fields=fields
    )


def test_consent_conflict_sorts_first_regardless_of_probability() -> None:
    active = Consent(status="granted", granted_on=date(2020, 1, 1))
    inactive = Consent(status="revoked")
    pairs = [
        _pair("a", "b", 0.96),  # no consent conflict, high probability
        _pair("c", "d", 0.81),  # consent conflict, lower probability
    ]
    consents = {"a": active, "b": active, "c": active, "d": inactive}

    items = triage_queue(pairs, consents=consents)

    assert items[0].left_id == "c"
    assert items[0].consent_conflict is True
    assert items[1].left_id == "a"
    assert items[1].consent_conflict is False


def test_more_disagreeing_fields_sorts_before_fewer_at_equal_consent_status() -> None:
    pairs = [_pair("a", "b", 0.9), _pair("c", "d", 0.9)]
    consents: dict[str, Consent] = {}
    evidence = {
        ("a", "b"): _evidence("a", "b", ("dob",)),
        ("c", "d"): _evidence("c", "d", ("dob", "address")),
    }

    items = triage_queue(pairs, consents=consents, evidence=evidence)

    assert items[0].left_id == "c"
    assert len(items[0].disagreeing_fields) == 2
    assert items[1].left_id == "a"


def test_ties_break_by_probability_then_by_id() -> None:
    pairs = [_pair("a", "b", 0.80), _pair("c", "d", 0.95)]
    items = triage_queue(pairs, consents={})
    assert items[0].left_id == "c"  # higher probability, no other signal
    assert items[1].left_id == "a"


def test_priority_rank_is_one_indexed_and_sequential() -> None:
    pairs = [_pair("a", "b", 0.9), _pair("c", "d", 0.8), _pair("e", "f", 0.7)]
    items = triage_queue(pairs, consents={})
    assert [item.priority_rank for item in items] == [1, 2, 3]


def test_reasons_are_built_from_real_signal_only() -> None:
    active = Consent(status="granted", granted_on=date(2020, 1, 1))
    inactive = Consent(status="revoked")
    pairs = [_pair("a", "b", 0.9)]
    consents = {"a": active, "b": inactive}
    evidence = {("a", "b"): _evidence("a", "b", ("dob",))}

    items = triage_queue(pairs, consents=consents, evidence=evidence)
    item = items[0]
    assert any("consent" in reason for reason in item.reasons)
    assert any("dob" in reason for reason in item.reasons)
    assert any("0.90" in reason for reason in item.reasons)


def test_missing_consent_defaults_to_absent_not_a_crash() -> None:
    pairs = [_pair("a", "b", 0.9)]
    items = triage_queue(pairs, consents={})  # neither id has a recorded consent
    assert items[0].consent_conflict is False  # absent == absent, no conflict


def test_no_evidence_available_yields_no_disagreeing_fields() -> None:
    pairs = [_pair("a", "b", 0.9)]
    items = triage_queue(pairs, consents={}, evidence=None)
    assert items[0].disagreeing_fields == ()


def test_pair_order_in_evidence_lookup_is_independent_of_request_order() -> None:
    pairs = [_pair("b", "a", 0.9)]  # Pair itself is not pre-sorted
    evidence = {("a", "b"): _evidence("a", "b", ("dob",))}
    items = triage_queue(pairs, consents={}, evidence=evidence)
    assert items[0].disagreeing_fields == ("dob",)
