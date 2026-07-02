"""Deterministic normalization of constituent fields.

Normalization runs offline with no model and no network. It exists so that the
matcher compares like with like: a name typed in mixed case with an accent, a
date in one of several common formats, a phone number with punctuation, all
reduce to a stable canonical form. Normalization never invents data; an
unparseable value becomes the empty string, which the matcher treats as a null
(no evidence) rather than a mismatch.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime

from constituent_reconciler import nicknames
from constituent_reconciler.address import normalize_address
from constituent_reconciler.models import Record

_WHITESPACE = re.compile(r"\s+")
_NAME_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_NON_DIGIT = re.compile(r"\D")

# American Soundex code table. Vowels and "h", "w", "y" carry no digit; they
# are separators rather than silent letters, which matters for the
# adjacent-duplicate-code rule in ``soundex`` below.
_SOUNDEX_CODES: dict[str, str] = {
    "b": "1",
    "f": "1",
    "p": "1",
    "v": "1",
    "c": "2",
    "g": "2",
    "j": "2",
    "k": "2",
    "q": "2",
    "s": "2",
    "x": "2",
    "z": "2",
    "d": "3",
    "t": "3",
    "l": "4",
    "m": "5",
    "n": "5",
    "r": "6",
}

# Date formats tried in order; the first that parses wins. US month-first
# ordering is assumed for slash and dash numeric dates, which is the dominant
# convention in the intended deployment. Ambiguous dates that do not parse
# under any format normalize to "" rather than to a guessed value.
_DATE_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%B %d, %Y",
    "%B %d %Y",
    "%b %d, %Y",
    "%b %d %Y",
    "%d %B %Y",
    "%d %b %Y",
)


def _strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_name(value: str) -> str:
    """Lower-case, strip accents and punctuation, collapse whitespace.

    ``O'Brien`` and ``o brien`` both become ``obrien``; ``José`` becomes
    ``jose``. This makes hyphenation, apostrophes, and spacing differences stop
    being mismatches.
    """

    folded = _strip_accents(value).lower()
    no_punct = _NAME_PUNCT.sub("", folded)
    # Remove all whitespace, not just collapse it, so spacing stops being a
    # mismatch: "O'Brien", "O Brien", and "OBrien" all reduce to the same token.
    return _WHITESPACE.sub("", no_punct)


def soundex(normalized_value: str) -> str:
    """Return the 4-character American Soundex code for a normalized name.

    Used as a phonetic blocking key (``defaults.blocking_rules_for``), not as
    a comparison level: blocking only needs recall, so a coarse phonetic
    bucket that groups "Jimenez"/"Ximenez" or "Katz"/"Kats" together is
    exactly what is wanted, even though it is far too loose to score a pair
    as a match on its own.

    Soundex, not full (Double) Metaphone, is the deliberate choice here. The
    ideation note that motivated this module suggested a metaphone key, but
    this project's dependency rule keeps everything around the matcher on the
    standard library (see docs/decisions/0001), and there is no
    metaphone implementation in the standard library. Soundex is a small,
    fully-specified, public-domain algorithm that is straightforward to
    implement correctly in a few lines of stdlib Python; a hand-rolled
    Metaphone is a much larger surface to get subtly wrong. For a blocking
    key, Soundex's coarser phonetic grouping is an acceptable trade.

    Expects input that has already been through ``normalize_name`` (lower
    case, accents stripped, no punctuation or spaces). Returns ``""`` for
    empty input so it becomes a null (no evidence) in the matcher, matching
    every other normalizer in this module.
    """

    if not normalized_value:
        return ""
    letters = [ch for ch in normalized_value if ch.isalpha()]
    if not letters:
        return ""

    first_letter = letters[0]
    codes: list[str] = []
    previous_code = _SOUNDEX_CODES.get(first_letter, "")
    for ch in letters[1:]:
        code = _SOUNDEX_CODES.get(ch, "")
        if code and code != previous_code:
            codes.append(code)
        # A vowel-like separator (a, e, i, o, u, y, h, w) resets the
        # duplicate check, so "Ashcraft" codes the two "c"-family sounds
        # separately instead of collapsing them.
        previous_code = code
    body = "".join(codes)[:3].ljust(3, "0")
    return f"{first_letter}{body}"


def surname_tokens(raw_value: str) -> tuple[str, str]:
    """Split a raw last-name value into up to two normalized surname tokens.

    Modeled on the two-surname (paterno + materno) convention common in
    Spanish- and Portuguese-language naming, which ``normalize_name`` erases
    by collapsing every token into one string: "de la Cruz Gómez" becomes the
    single opaque token "delacruzgomez", so a record carrying only "Cruz" or
    only "Gómez" can never agree with it even though a human reviewer would
    recognize the connection immediately.

    This takes the last two whitespace-separated words of the raw value (the
    two tokens most likely to be the actual surname pair, since a leading
    "de la" is a preposition rather than a surname) and normalizes each one
    independently. A single-token surname yields an empty second token.

    This is a heuristic, not a rule from any naming-convention reference; it
    is documented as needing linguistic and cultural SME review, the same
    caveat that applies to ``nicknames`` (see that module's docstring and
    docs/decisions/0009-matching-depth-pack.md).
    """

    words = raw_value.split()
    if not words:
        return "", ""
    if len(words) == 1:
        return normalize_name(words[0]), ""
    return normalize_name(words[-2]), normalize_name(words[-1])


def normalize_dob(value: str) -> str:
    """Return an ISO ``YYYY-MM-DD`` date, or ``""`` if nothing parses."""

    text = value.strip()
    if not text:
        return ""
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def normalize_email(value: str) -> str:
    return value.strip().lower()


def normalize_phone(value: str) -> str:
    """Reduce to the last ten digits, dropping a leading US country code."""

    digits = _NON_DIGIT.sub("", value)
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits[-10:] if len(digits) >= 10 else digits


# Single-argument normalizers, keyed by canonical field. Address is handled
# separately in ``normalize_record`` because it takes a backend argument.
_FIELD_NORMALIZERS = {
    "first_name": normalize_name,
    "last_name": normalize_name,
    "dob": normalize_dob,
    "email": normalize_email,
    "phone": normalize_phone,
}


def normalize_record(
    record: Record,
    fields: tuple[str, ...],
    *,
    address_backend: str = "deterministic",
    failures: dict[str, dict[str, int]] | None = None,
) -> Record:
    """Return a copy of ``record`` with ``normalized`` filled for ``fields``.

    ``address_backend`` selects the address standardizer; it is ignored unless
    ``address`` is among ``fields``.

    Beyond the canonical fields themselves, this also fills in the derived
    matching-depth columns the matcher's comparisons and blocking rules read
    (``defaults.py``): a nickname-group key for ``first_name``, and a
    phonetic key plus two surname tokens for ``last_name``. These are stored
    under their own keys in ``normalized`` rather than as separate canonical
    fields, since they only ever exist as a function of the base field and a
    recipe never maps them directly.

    ``failures``, when given, is a mutable ``field -> {source: count}`` mapping
    that is incremented whenever a nonempty raw value normalizes to ``""``: the
    value existed but nothing parseable survived (an unparseable date, a name
    that was all punctuation). The caller owns the mapping, so normalization
    itself stays a pure function of the record.
    """

    normalized: dict[str, str] = {}
    for field_name in fields:
        raw_value = record.raw.get(field_name, "")
        if field_name == "address":
            normalized[field_name] = normalize_address(raw_value, backend=address_backend)
        else:
            normalizer = _FIELD_NORMALIZERS.get(field_name, normalize_name)
            normalized[field_name] = normalizer(raw_value)

        if failures is not None and raw_value.strip() and not normalized[field_name]:
            per_source = failures.setdefault(field_name, {})
            per_source[record.source] = per_source.get(record.source, 0) + 1
        if field_name == "first_name":
            normalized["first_name_nickname_key"] = nicknames.canonical_key(
                normalized["first_name"]
            )
        elif field_name == "last_name":
            normalized["last_name_soundex"] = soundex(normalized["last_name"])
            surname1, surname2 = surname_tokens(raw_value)
            normalized["last_name_surname1"] = surname1
            normalized["last_name_surname2"] = surname2

    return Record(
        unique_id=record.unique_id,
        source=record.source,
        raw=record.raw,
        normalized=normalized,
        consent=record.consent,
        spans=record.spans,
    )
