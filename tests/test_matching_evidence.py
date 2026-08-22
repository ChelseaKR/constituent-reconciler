"""Correctness tests for matching.evidence's real Splink comparison evidence.

These pin the empirically-confirmed gamma-numbering behavior the module
docstring describes: a change to Splink's numbering convention, or a bug in
``_level_for_gamma``, must fail one of these before it can reach the AI
assistant package, which trusts this module completely.
"""

from __future__ import annotations

from constituent_reconciler.matching.evidence import comparison_evidence
from constituent_reconciler.models import Record
from constituent_reconciler.normalize import normalize_record

FIELDS = ("first_name", "last_name", "dob", "email")


def _record(uid: str, first: str, last: str, dob: str, email: str = "") -> Record:
    raw = {"first_name": first, "last_name": last, "dob": dob, "email": email}
    return normalize_record(Record(unique_id=uid, source="test", raw=raw), FIELDS)


def test_nickname_level_matches_defaults_constants() -> None:
    """JOHN/JON on first_name fires the 'nickname' level, not 'close' or 'exact'."""
    records = [
        _record("a", "John", "Smith", "1980-01-01", "john@example.com"),
        _record("b", "Jon", "Smith", "1980-01-01", "jsmith@example.com"),
    ]
    evidence = comparison_evidence(records, FIELDS, [("a", "b")])
    pair = evidence[("a", "b")]

    first_name = pair.field("first_name")
    assert first_name is not None
    assert first_name.level_label == "nickname"
    assert first_name.m_probability == 0.06
    assert first_name.u_probability == 0.01
    assert first_name.bayes_factor == 6.0
    assert not first_name.is_null_level


def test_exact_last_name_carries_term_frequency_adjustment() -> None:
    records = [
        _record("a", "John", "Smith", "1980-01-01"),
        _record("b", "Jon", "Smith", "1980-01-01"),
    ]
    evidence = comparison_evidence(records, FIELDS, [("a", "b")])
    last_name = evidence[("a", "b")].field("last_name")
    assert last_name is not None
    assert last_name.level_label == "exact"
    assert last_name.m_probability == 0.87
    assert last_name.u_probability == 0.01
    assert last_name.tf_adjustment_bayes_factor is not None


def test_different_level_for_disagreeing_email() -> None:
    records = [
        _record("a", "John", "Smith", "1980-01-01", "john@example.com"),
        _record("b", "John", "Smith", "1980-01-01", "jsmith@example.com"),
    ]
    evidence = comparison_evidence(records, FIELDS, [("a", "b")])
    email = evidence[("a", "b")].field("email")
    assert email is not None
    assert email.level_label == "different"
    assert not email.is_null_level


def test_null_level_for_missing_value() -> None:
    records = [
        _record("a", "John", "Smith", "1980-01-01", ""),
        _record("b", "John", "Smith", "1980-01-01", "john@example.com"),
    ]
    evidence = comparison_evidence(records, FIELDS, [("a", "b")])
    email = evidence[("a", "b")].field("email")
    assert email is not None
    assert email.is_null_level
    assert email.level_label == "null or empty"
    # A null level is "no evidence," never scored as m/u disagreement evidence.
    assert email.m_probability == 0.0
    assert email.u_probability == 0.0


def test_pair_ordering_is_normalized_regardless_of_request_order() -> None:
    records = [
        _record("a", "John", "Smith", "1980-01-01"),
        _record("b", "Jon", "Smith", "1980-01-01"),
    ]
    forward = comparison_evidence(records, FIELDS, [("a", "b")])
    backward = comparison_evidence(records, FIELDS, [("b", "a")])
    assert set(forward) == set(backward) == {("a", "b")}


def test_unblocked_pair_is_absent_not_raised() -> None:
    """A pair Splink's own blocking rules never generate returns no evidence."""
    records = [
        _record("a", "John", "Smith", "1980-01-01"),
        _record("b", "Jon", "Smith", "1980-01-01"),
        _record("c", "Wei", "Chen", "1968-01-22"),
    ]
    evidence = comparison_evidence(records, FIELDS, [("a", "c")])
    assert evidence == {}


def test_fewer_than_two_records_returns_empty() -> None:
    records = [_record("a", "John", "Smith", "1980-01-01")]
    assert comparison_evidence(records, FIELDS, [("a", "b")]) == {}


def test_no_requested_pairs_returns_empty() -> None:
    records = [
        _record("a", "John", "Smith", "1980-01-01"),
        _record("b", "Jon", "Smith", "1980-01-01"),
    ]
    assert comparison_evidence(records, FIELDS, []) == {}


def test_match_probability_and_weight_are_present() -> None:
    records = [
        _record("a", "John", "Smith", "1980-01-01"),
        _record("b", "Jon", "Smith", "1980-01-01"),
    ]
    pair = comparison_evidence(records, FIELDS, [("a", "b")])[("a", "b")]
    assert 0.0 <= pair.match_probability <= 1.0
    assert isinstance(pair.match_weight, float)
    assert pair.field("nonexistent_field") is None
