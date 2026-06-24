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

from constituent_reconciler.models import Record

_WHITESPACE = re.compile(r"\s+")
_NAME_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_NON_DIGIT = re.compile(r"\D")

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


_FIELD_NORMALIZERS = {
    "first_name": normalize_name,
    "last_name": normalize_name,
    "dob": normalize_dob,
    "email": normalize_email,
    "phone": normalize_phone,
}


def normalize_record(record: Record, fields: tuple[str, ...]) -> Record:
    """Return a copy of ``record`` with ``normalized`` filled for ``fields``."""

    normalized: dict[str, str] = {}
    for field_name in fields:
        normalizer = _FIELD_NORMALIZERS.get(field_name, normalize_name)
        normalized[field_name] = normalizer(record.raw.get(field_name, ""))
    return Record(
        unique_id=record.unique_id,
        source=record.source,
        raw=record.raw,
        normalized=normalized,
        consent_status=record.consent_status,
    )
