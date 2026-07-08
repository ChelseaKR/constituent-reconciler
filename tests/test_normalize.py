from __future__ import annotations

from constituent_reconciler.models import Record
from constituent_reconciler.normalize import (
    normalize_dob,
    normalize_email,
    normalize_name,
    normalize_phone,
    normalize_record,
    soundex,
    surname_tokens,
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


def test_soundex_groups_phonetically_similar_spellings() -> None:
    assert soundex(normalize_name("Smith")) == soundex(normalize_name("Smyth"))
    assert soundex(normalize_name("Gutierrez")) == soundex(normalize_name("Gutteriez"))
    assert soundex(normalize_name("Katz")) == soundex(normalize_name("Kats"))


def test_soundex_distinguishes_clearly_different_names() -> None:
    assert soundex(normalize_name("Smith")) != soundex(normalize_name("Chen"))


def test_soundex_of_empty_is_empty() -> None:
    assert soundex("") == ""


def test_surname_tokens_splits_compound_surname() -> None:
    assert surname_tokens("Cruz Gómez") == ("cruz", "gomez")
    assert surname_tokens("de la Cruz Gómez") == ("cruz", "gomez")


def test_surname_tokens_single_word_has_empty_second_token() -> None:
    assert surname_tokens("Smith") == ("smith", "")


def test_surname_tokens_empty_input() -> None:
    assert surname_tokens("") == ("", "")


def test_normalize_record_fills_derived_matching_depth_columns() -> None:
    raw = {"first_name": "Bill", "last_name": "Cruz Gómez"}
    record = normalize_record(
        Record(unique_id="1", source="test", raw=raw),
        ("first_name", "last_name"),
    )
    assert record.normalized["first_name_nickname_key"] == "william"
    assert record.normalized["last_name_soundex"] == soundex("cruzgomez")
    assert record.normalized["last_name_surname1"] == "cruz"
    assert record.normalized["last_name_surname2"] == "gomez"
