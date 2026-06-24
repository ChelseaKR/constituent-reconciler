from __future__ import annotations

from constituent_reconciler.matching import score_pairs
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


def _scores(records: list[Record]) -> dict[frozenset[str], float]:
    return {frozenset((left, right)): prob for left, right, prob in score_pairs(records, FIELDS)}


def test_typo_and_dateformat_duplicate_scores_for_auto() -> None:
    records = [
        _record("E3", "existing", "Jonathan", "Reyes", "1990-04-12"),
        _record("N2", "incoming", "Jonathon", "Reyes", "04/12/1990"),
        _record("X1", "existing", "Wei", "Chen", "1968-01-22"),
    ]
    scores = _scores(records)
    assert scores[frozenset(("E3", "N2"))] >= 0.97


def test_same_name_different_dob_lands_in_review_band() -> None:
    records = [
        _record("E8", "existing", "Maria", "Lopez", "1991-06-14"),
        _record("N7", "incoming", "Maria", "Lopez", "1996-02-22"),
    ]
    probability = _scores(records)[frozenset(("E8", "N7"))]
    assert 0.80 <= probability < 0.97


def test_nickname_rescued_by_matching_phone() -> None:
    records = [
        _record("E12", "existing", "Robert", "Smith", "1965-07-19", phone="(530) 555-0143"),
        _record("N10", "incoming", "Bob", "Smith", "1965-07-19", phone="530-555-0143"),
    ]
    assert _scores(records)[frozenset(("E12", "N10"))] >= 0.97
