from __future__ import annotations

from constituent_reconciler.normalize import (
    normalize_dob,
    normalize_email,
    normalize_name,
    normalize_phone,
)


def test_name_folds_case_accents_and_punctuation() -> None:
    assert normalize_name("O'Brien") == "obrien"
    assert normalize_name("o brien") == "obrien"
    assert normalize_name("José") == "jose"
    assert normalize_name("  Smith-Jones ") == "smithjones"


def test_dob_accepts_several_formats() -> None:
    assert normalize_dob("1990-04-12") == "1990-04-12"
    assert normalize_dob("04/12/1990") == "1990-04-12"
    assert normalize_dob("Feb 20 1978") == "1978-02-20"


def test_dob_unparseable_becomes_empty() -> None:
    assert normalize_dob("not a date") == ""
    assert normalize_dob("") == ""


def test_phone_reduces_to_last_ten_digits() -> None:
    assert normalize_phone("(530) 555-0143") == "5305550143"
    assert normalize_phone("1-530-555-0143") == "5305550143"


def test_email_lowercased_and_stripped() -> None:
    assert normalize_email(" Maria.G@Example.org ") == "maria.g@example.org"
