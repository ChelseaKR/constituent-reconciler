"""Conformance tests for the MatcherBackend seam.

Mirrors the connector-kit style: one reusable check exercises the contract in
``matching/base.py`` against any backend. It runs against the Splink default
and against a trivial in-test backend, proving the protocol is satisfiable
without Splink.
"""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from constituent_reconciler import matching
from constituent_reconciler.matching import MatcherBackend, get_backend
from constituent_reconciler.matching.splink_backend import SplinkBackend
from constituent_reconciler.models import Record
from constituent_reconciler.normalize import normalize_record

FIELDS = ("first_name", "last_name", "dob", "email", "phone")


def _record(
    uid: str,
    source: str,
    first: str,
    last: str,
    dob: str,
    email: str = "",
    phone: str = "",
) -> Record:
    raw = {"first_name": first, "last_name": last, "dob": dob, "email": email, "phone": phone}
    return normalize_record(Record(unique_id=uid, source=source, raw=raw), FIELDS)


def _fixture() -> list[Record]:
    # A1/B1 are exact duplicates; C1 shares the name but not the date of birth;
    # D1 agrees with nobody on anything.
    return [
        _record("A1", "existing", "Jane", "Doe", "1990-01-01", email="jane@example.org"),
        _record("B1", "incoming", "Jane", "Doe", "1990-01-01", email="jane@example.org"),
        _record("C1", "incoming", "Jane", "Doe", "1985-05-05"),
        _record("D1", "existing", "Wei", "Chen", "1968-01-22"),
    ]


class _ExactAgreementBackend:
    """Trivial backend: the fraction of populated fields that agree exactly.

    Deliberately not a linkage model. It exists only to prove the protocol is
    satisfiable without Splink and that the conformance check tests the
    contract, not Splink internals.
    """

    def score_pairs(
        self,
        records: Iterable[Record],
        fields: tuple[str, ...],
        *,
        prior: float = 0.01,
        floor: float = 0.001,
    ) -> list[tuple[str, str, float]]:
        record_list = list(records)
        if len(record_list) < 2:
            return []
        scored: list[tuple[str, str, float]] = []
        for i, one in enumerate(record_list):
            for other in record_list[i + 1 :]:
                pairs = [(one.normalized.get(f, ""), other.normalized.get(f, "")) for f in fields]
                populated = [(a, b) for a, b in pairs if a or b]
                agreed = sum(1 for a, b in populated if a and a == b)
                probability = agreed / len(populated) if populated else 0.0
                left, right = sorted((one.unique_id, other.unique_id))
                if probability >= floor:
                    scored.append((left, right, probability))
        scored.sort(key=lambda item: (-item[2], item[0], item[1]))
        return scored


def check_matcher_backend(backend: MatcherBackend) -> None:
    """Assert one backend honors the MatcherBackend contract."""

    assert isinstance(backend, MatcherBackend)

    # Fewer than two records is no work, not an error.
    assert backend.score_pairs([], FIELDS, prior=0.01, floor=0.001) == []
    only = _fixture()[:1]
    assert backend.score_pairs(only, FIELDS, prior=0.01, floor=0.001) == []

    scored = backend.score_pairs(_fixture(), FIELDS, prior=0.01, floor=0.001)
    assert scored, "the fixture contains an exact duplicate; something must score"

    # Pair identity: left id sorts before right id, regardless of engine order.
    assert all(left < right for left, right, _ in scored)
    # Strongest pairs first, ties broken by the id pair.
    assert scored == sorted(scored, key=lambda item: (-item[2], item[0], item[1]))

    by_pair = {(left, right): probability for left, right, probability in scored}
    # The exact duplicate scores decisively.
    assert by_pair[("A1", "B1")] > 0.9
    # The record that agrees with nobody is absent or scores low.
    for (left, right), probability in by_pair.items():
        if "D1" in (left, right):
            assert probability < 0.5

    # The floor drops pairs below it; the exact duplicate survives a high floor.
    high = backend.score_pairs(_fixture(), FIELDS, prior=0.01, floor=0.99)
    assert all(probability >= 0.99 for _, _, probability in high)
    high_pairs = {(left, right) for left, right, _ in high}
    assert ("A1", "B1") in high_pairs
    assert ("A1", "C1") not in high_pairs


def test_splink_backend_conforms() -> None:
    check_matcher_backend(SplinkBackend())


def test_protocol_is_satisfiable_without_splink() -> None:
    check_matcher_backend(_ExactAgreementBackend())


def test_module_level_score_pairs_delegates_to_default_backend() -> None:
    backend = matching.default_backend()
    assert isinstance(backend, SplinkBackend)
    via_module = matching.score_pairs(_fixture(), FIELDS)
    via_backend = SplinkBackend().score_pairs(_fixture(), FIELDS)
    assert via_module == via_backend


def test_get_backend_rejects_unknown_names() -> None:
    assert isinstance(get_backend("splink"), SplinkBackend)
    with pytest.raises(ValueError, match="unknown matcher backend"):
        get_backend("dedupe")
