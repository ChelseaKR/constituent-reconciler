"""Deterministic CASS-style address standardization.

This is a vendored, deterministic ruleset, not a USPS-CASS-certified one. Real
CASS certification requires licensed USPS data and is legally constrained; this
module standardizes an address into USPS-style abbreviations so that two writings
of the same address stop being a mismatch ("123 North Main Street" and
"123 N Main St" both reduce to "123 N MAIN ST"). It does not validate that the
address exists or is deliverable. The README states this plainly under non-goals.

The abbreviation tables follow USPS Publication 28 (Postal Addressing Standards),
Appendix C. The standardization is position-insensitive token mapping: a real
CASS engine is position-aware (a leading versus trailing directional, "ST" as
Street suffix versus "Saint" prefix). The simplification is documented and is
acceptable for a matching key; it is not acceptable to call it certified.

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
    "STREET": "ST", "ST": "ST", "STR": "ST",
    "AVENUE": "AVE", "AVE": "AVE", "AV": "AVE",
    "BOULEVARD": "BLVD", "BLVD": "BLVD", "BOUL": "BLVD",
    "DRIVE": "DR", "DR": "DR",
    "ROAD": "RD", "RD": "RD",
    "LANE": "LN", "LN": "LN",
    "COURT": "CT", "CT": "CT",
    "PLACE": "PL", "PL": "PL",
    "CIRCLE": "CIR", "CIR": "CIR",
    "TERRACE": "TER", "TER": "TER", "TERR": "TER",
    "PARKWAY": "PKWY", "PKWY": "PKWY",
    "HIGHWAY": "HWY", "HWY": "HWY",
    "TRAIL": "TRL", "TRL": "TRL",
    "SQUARE": "SQ", "SQ": "SQ",
    "PLAZA": "PLZ", "PLZ": "PLZ",
    "WAY": "WAY",
    "LOOP": "LOOP",
    "ALLEY": "ALY", "ALY": "ALY",
    "CRESCENT": "CRES", "CRES": "CRES",
}

# USPS Publication 28, Appendix C2 — directional abbreviations.
_DIRECTIONALS: dict[str, str] = {
    "NORTH": "N", "N": "N",
    "SOUTH": "S", "S": "S",
    "EAST": "E", "E": "E",
    "WEST": "W", "W": "W",
    "NORTHEAST": "NE", "NE": "NE",
    "NORTHWEST": "NW", "NW": "NW",
    "SOUTHEAST": "SE", "SE": "SE",
    "SOUTHWEST": "SW", "SW": "SW",
}

# USPS Publication 28, Appendix C2 — secondary unit designators.
_UNIT_DESIGNATORS: dict[str, str] = {
    "APARTMENT": "APT", "APT": "APT",
    "SUITE": "STE", "STE": "STE",
    "BUILDING": "BLDG", "BLDG": "BLDG",
    "FLOOR": "FL", "FL": "FL",
    "ROOM": "RM", "RM": "RM",
    "DEPARTMENT": "DEPT", "DEPT": "DEPT",
    "UNIT": "UNIT",
}

# Punctuation that becomes a space before tokenizing. A pound sign is kept because
# it carries unit information ("# 4"); everything else separates tokens.
_PUNCT = re.compile(r"[^\w#\s]", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")


def _standardize_token(token: str) -> str:
    """Map one upper-cased token through the abbreviation tables, in order."""

    if token in _DIRECTIONALS:
        return _DIRECTIONALS[token]
    if token in _STREET_SUFFIXES:
        return _STREET_SUFFIXES[token]
    if token in _UNIT_DESIGNATORS:
        return _UNIT_DESIGNATORS[token]
    return token


def normalize_address_deterministic(value: str) -> str:
    """Standardize an address with the vendored ruleset. Always deterministic.

    Upper-cases, drops punctuation other than a unit pound sign, and maps each
    token through the USPS-style abbreviation tables. An empty or blank input
    returns ``""`` so the matcher treats it as no evidence, not a mismatch.
    """

    text = value.strip().upper()
    if not text:
        return ""
    text = _PUNCT.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip()
    if not text:
        return ""
    return " ".join(_standardize_token(token) for token in text.split())


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
            "address_backend = \"deterministic\" (the default) in the recipe."
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
    raise ValueError(
        f"unknown address backend {backend!r}; use 'deterministic' or 'libpostal'"
    )
