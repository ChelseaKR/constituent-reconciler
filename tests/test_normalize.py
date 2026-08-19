from __future__ import annotations

from constituent_reconciler.models import Record
from constituent_reconciler.normalize import (
    name_pair_key,
    normalize_dob,
    normalize_email,
    normalize_name,
    normalize_phone,
    normalize_record,
    normalized_keys,
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


def test_dob_accepts_iso_basic_yyyymmdd() -> None:
    """The compact ISO form, which the FEBRL4 benchmark exports (docs/BENCHMARK.md).

    Before this was handled, every date in that corpus normalized to "" and the
    matcher scored 10,000 records with no date of birth at all.
    """

    assert normalize_dob("19151111") == "1915-11-11"
    assert normalize_dob("20040229") == "2004-02-29"


def test_dob_rejects_eight_digits_that_are_not_a_plausible_year_first_date() -> None:
    """DDMMYYYY and MMDDYYYY must not be silently read as YYYYMMDD.

    Guessing here would write a confidently wrong date rather than an empty one,
    which is the failure mode the ambiguous-date rule exists to prevent.
    """

    assert normalize_dob("12041990") == ""
    assert normalize_dob("04121990") == ""


def test_dob_rejects_impossible_calendar_dates() -> None:
    """Corrupted dates stay empty instead of rolling over into a valid date."""

    assert normalize_dob("19960094") == ""
    assert normalize_dob("19450493") == ""
    assert normalize_dob("20230229") == ""


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


def test_name_pair_key_is_the_same_whichever_box_each_name_landed_in() -> None:
    """The key is order-free, which is the whole point of it.

    A constituent whose family name is written first lands the two values in
    the opposite fields from the intake worker who typed the earlier record.
    Both records still produce the same key, so blocking offers the pair to
    the scorer instead of never generating it.
    """

    assert name_pair_key("wei", "li") == name_pair_key("li", "wei")


def test_name_pair_key_needs_both_names() -> None:
    """One name alone is not a key: it would bucket unrelated records."""

    assert name_pair_key("wei", "") == ""
    assert name_pair_key("", "li") == ""
    assert name_pair_key("", "") == ""


def test_name_pair_key_is_derived_only_when_both_name_fields_are_active() -> None:
    record = normalize_record(
        Record(unique_id="1", source="test", raw={"first_name": "Li", "last_name": "Wei"}),
        ("first_name", "last_name"),
    )
    assert record.normalized["name_pair_key"] == name_pair_key("li", "wei")
    assert "name_pair_key" in normalized_keys(("first_name", "last_name"))

    first_only = normalize_record(
        Record(unique_id="2", source="test", raw={"first_name": "Li"}),
        ("first_name",),
    )
    assert "name_pair_key" not in first_only.normalized
    assert "name_pair_key" not in normalized_keys(("first_name",))
    assert "name_pair_key" not in normalized_keys(("last_name",))
