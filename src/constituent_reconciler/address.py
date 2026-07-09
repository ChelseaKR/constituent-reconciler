"""Deterministic CASS-style address standardization.

This is a vendored, deterministic ruleset, not a USPS-CASS-certified one. Real
CASS certification requires licensed USPS data and is legally constrained; this
module standardizes an address into USPS-style abbreviations so that two writings
of the same address stop being a mismatch ("123 North Main Street" and
"123 N Main St" both reduce to "123 N MAIN ST"). It does not validate that the
address exists or is deliverable. The README states this plainly under non-goals.

The abbreviation tables follow USPS Publication 28 (Postal Addressing Standards),
Appendix C. The standardization is position-aware: a directional abbreviates only
directly after the leading house number or at the end of the street portion, a
street suffix abbreviates only in the suffix position (so "ST" leading a street
name stays "Saint" and is never rewritten), and a unit designator maps only when
a unit value follows it. Words that merely resemble a suffix or directional
elsewhere in the street name are left alone. This retires the earlier
position-insensitive simplification; it still does not make the ruleset
USPS-CASS-certified, and it does not validate that an address exists.

An optional libpostal backend is available for callers who have the libpostal C
library and the ``postal`` Python package installed. It is never required; the
deterministic backend is the default and the one the committed eval scores.
"""

from __future__ import annotations

import re

# USPS Publication 28, Appendix C1 — common street-suffix abbreviations. Each key
# (the long form or a variant) maps to the standard short form. Both the long and
# short forms are present so that an already-abbreviated input is left stable.
_STREET_SUFFIXES: dict[str, str] = {
    "STREET": "ST",
    "ST": "ST",
    "STR": "ST",
    "AVENUE": "AVE",
    "AVE": "AVE",
    "AV": "AVE",
    "BOULEVARD": "BLVD",
    "BLVD": "BLVD",
    "BOUL": "BLVD",
    "DRIVE": "DR",
    "DR": "DR",
    "ROAD": "RD",
    "RD": "RD",
    "LANE": "LN",
    "LN": "LN",
    "COURT": "CT",
    "CT": "CT",
    "PLACE": "PL",
    "PL": "PL",
    "CIRCLE": "CIR",
    "CIR": "CIR",
    "TERRACE": "TER",
    "TER": "TER",
    "TERR": "TER",
    "PARKWAY": "PKWY",
    "PKWY": "PKWY",
    "HIGHWAY": "HWY",
    "HWY": "HWY",
    "TRAIL": "TRL",
    "TRL": "TRL",
    "SQUARE": "SQ",
    "SQ": "SQ",
    "PLAZA": "PLZ",
    "PLZ": "PLZ",
    "WAY": "WAY",
    "LOOP": "LOOP",
    "ALLEY": "ALY",
    "ALY": "ALY",
    "CRESCENT": "CRES",
    "CRES": "CRES",
}

# USPS Publication 28, Appendix C2 — directional abbreviations.
_DIRECTIONALS: dict[str, str] = {
    "NORTH": "N",
    "N": "N",
    "SOUTH": "S",
    "S": "S",
    "EAST": "E",
    "E": "E",
    "WEST": "W",
    "W": "W",
    "NORTHEAST": "NE",
    "NE": "NE",
    "NORTHWEST": "NW",
    "NW": "NW",
    "SOUTHEAST": "SE",
    "SE": "SE",
    "SOUTHWEST": "SW",
    "SW": "SW",
}

# USPS Publication 28, Appendix C2 — secondary unit designators.
_UNIT_DESIGNATORS: dict[str, str] = {
    "APARTMENT": "APT",
    "APT": "APT",
    "SUITE": "STE",
    "STE": "STE",
    "BUILDING": "BLDG",
    "BLDG": "BLDG",
    "FLOOR": "FL",
    "FL": "FL",
    "ROOM": "RM",
    "RM": "RM",
    "DEPARTMENT": "DEPT",
    "DEPT": "DEPT",
    "UNIT": "UNIT",
}

# Punctuation that becomes a space before tokenizing. A pound sign is kept because
# it carries unit information ("# 4"); everything else separates tokens.
_PUNCT = re.compile(r"[^\w#\s]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


def _is_house_number(token: str) -> bool:
    """A leading token carrying a digit reads as a house number ("123", "123B")."""

    return any(ch.isdigit() for ch in token)


def _is_unit_value(token: str) -> bool:
    """A token that can follow a unit designator as its value: "#", "4", "4B", "B".

    A multi-letter word without a digit ("HILL") is not a unit value, which is
    what keeps a designator word inside a street name from being rewritten.
    """

    return token == "#" or len(token) == 1 or any(ch.isdigit() for ch in token)


def _find_unit_start(tokens: list[str], start: int) -> int:
    """Index where the secondary-unit phrase begins, or ``len(tokens)`` if none.

    A unit phrase starts at a bare ``#`` or at a unit designator that is
    followed by a unit value ("APT 4", "SUITE 200", "APT # 4").
    """

    for i in range(start, len(tokens)):
        if tokens[i] == "#":
            return i
        if tokens[i] in _UNIT_DESIGNATORS and i + 1 < len(tokens) and _is_unit_value(tokens[i + 1]):
            return i
    return len(tokens)


def normalize_address_deterministic(value: str) -> str:
    """Standardize an address with the vendored ruleset. Always deterministic.

    Upper-cases, drops punctuation other than a unit pound sign, and maps
    tokens through the USPS-style abbreviation tables with position rules:

    - A directional abbreviates directly after the leading house number
      ("123 NORTH MAIN ST" -> "123 N MAIN ST") or at the end of the street
      portion ("123 MAIN ST NORTH" -> "123 MAIN ST N"). A directional word in
      the interior of a street name is left alone.
    - A street suffix abbreviates only in the suffix position: the last token
      of the street portion, allowing for a trailing directional or a unit
      phrase after it. "ST" leading a street name ("123 ST CHARLES AVE") is
      Saint, not Street, and stays as written; a suffix word that is itself
      the whole street name ("123 AVENUE B") also stays.
    - A unit designator maps only when a unit value follows it, so a
      designator word inside a street name is untouched.

    Every mapping replaces one token with one token, so the pass is idempotent.
    An empty or blank input returns ``""`` so the matcher treats it as no
    evidence, not a mismatch.
    """

    text = value.strip().upper()
    if not text:
        return ""
    text = _PUNCT.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()
    if not text:
        return ""

    tokens = text.split()
    out = list(tokens)

    start = 1 if _is_house_number(tokens[0]) else 0
    unit_start = _find_unit_start(tokens, start)

    # Unit designators, wherever they sit, map only when a unit value follows.
    for i in range(len(tokens) - 1):
        if tokens[i] in _UNIT_DESIGNATORS and _is_unit_value(tokens[i + 1]):
            out[i] = _UNIT_DESIGNATORS[tokens[i]]

    # The street portion runs from just after the house number to just before
    # the unit phrase. Directionals and suffixes are positional within it.
    if unit_start > start:
        last = unit_start - 1
        trailing_directional = tokens[last] in _DIRECTIONALS
        if trailing_directional:
            out[last] = _DIRECTIONALS[tokens[last]]
        if start == 1 and tokens[start] in _DIRECTIONALS:
            out[start] = _DIRECTIONALS[tokens[start]]
        suffix_idx = last - 1 if trailing_directional else last
        if suffix_idx > start and tokens[suffix_idx] in _STREET_SUFFIXES:
            out[suffix_idx] = _STREET_SUFFIXES[tokens[suffix_idx]]

    return " ".join(out)


def normalize_address_libpostal(value: str) -> str:
    """Standardize an address using libpostal's expansion, if available.

    ``expand_address`` returns one or more normalized expansions; the
    lexicographically first is taken so the result is deterministic across runs.
    Falls back to the deterministic ruleset when libpostal returns nothing.

    Raises ``ImportError`` if the ``postal`` package or the libpostal C library
    is not installed, with a message pointing to the deterministic backend.
    """

    try:
        from postal.expand import expand_address
    except ImportError as exc:
        raise ImportError(
            "the libpostal address backend requires the 'postal' package and the "
            "libpostal C library. Install both per the libpostal docs, or set "
            'address_backend = "deterministic" (the default) in the recipe.'
        ) from exc

    text = value.strip()
    if not text:
        return ""
    expansions: list[str] = list(expand_address(text))
    if not expansions:
        return normalize_address_deterministic(text)
    return sorted(expansions)[0].upper()


def normalize_address(value: str, *, backend: str = "deterministic") -> str:
    """Standardize an address with the selected backend.

    ``backend`` is ``"deterministic"`` (the default, vendored ruleset) or
    ``"libpostal"`` (optional, requires the libpostal C library). An unknown
    backend raises ``ValueError`` rather than silently falling back, so a recipe
    typo is caught instead of changing the matching key.
    """

    if backend == "deterministic":
        return normalize_address_deterministic(value)
    if backend == "libpostal":
        return normalize_address_libpostal(value)
    raise ValueError(f"unknown address backend {backend!r}; use 'deterministic' or 'libpostal'")
