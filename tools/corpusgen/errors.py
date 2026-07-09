"""Error-injection functions: one per planted-duplicate error channel.

Each function takes a Random instance (so generation is reproducible from a
seed) and the "true" field value, and returns a plausibly-erred variant. They
are deliberately small and independently testable; ``generate.py`` composes
them per planted cluster.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

_QWERTY_NEIGHBORS: dict[str, str] = {
    "a": "qsz",
    "b": "vghn",
    "c": "xdfv",
    "d": "serfcx",
    "e": "wsdr",
    "f": "drtgvc",
    "g": "ftyhbv",
    "h": "gyujnb",
    "i": "ujko",
    "j": "huiknm",
    "k": "jiolm",
    "l": "kop",
    "m": "njk",
    "n": "bhjm",
    "o": "iklp",
    "p": "ol",
    "q": "wa",
    "r": "edft",
    "s": "awedxz",
    "t": "rfgy",
    "u": "yhji",
    "v": "cfgb",
    "w": "qase",
    "x": "zsdc",
    "y": "tghu",
    "z": "asx",
}


def typo_name(rng: random.Random, value: str) -> str:
    """Apply one small keyboard-adjacent typo: swap, drop, or substitute.

    Never touches the first or last character, so the result still blocks on
    the same first-letter-adjacent keys a real matcher would use.
    """

    if len(value) < 4:
        return value
    i = rng.randint(1, len(value) - 2)
    kind = rng.choice(("swap", "drop", "substitute"))
    chars = list(value)
    if kind == "swap" and i + 1 < len(value) - 1:
        chars[i], chars[i + 1] = chars[i + 1], chars[i]
    elif kind == "drop":
        del chars[i]
    else:
        lower = chars[i].lower()
        neighbors = _QWERTY_NEIGHBORS.get(lower, lower)
        replacement = rng.choice(neighbors)
        chars[i] = replacement.upper() if chars[i].isupper() else replacement
    return "".join(chars)


def nickname(rng: random.Random, value: str, table: dict[str, tuple[str, ...]]) -> str | None:
    """Return a conventional nickname for ``value``, or ``None`` if not in the table."""

    options = table.get(value)
    if not options:
        return None
    return rng.choice(options)


def transliteration(
    rng: random.Random, value: str, table: dict[str, tuple[str, ...]]
) -> str | None:
    """Return an alternate transliteration of ``value``, or ``None`` if not in the table."""

    options = table.get(value)
    if not options:
        return None
    return rng.choice(options)


def compound_surname(
    rng: random.Random, value: str, table: dict[str, tuple[str, ...]]
) -> str | None:
    """Return an alternate rendering of a compound surname, or ``None`` if not covered."""

    options = table.get(value)
    if not options:
        return None
    return rng.choice(options)


# Date formats a real intake form might use, matched against
# normalize.py's ``_DATE_FORMATS`` so most of these still resolve to the same
# ISO date after normalization (format drift the pipeline is supposed to
# absorb). "dd/mm" is intentionally NOT in normalize.py's table for most
# forms, so it stays available as a genuine-mismatch case below.
_DRIFT_FORMATS: tuple[str, ...] = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%m/%d/%Y",
    "%m-%d-%Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %B %Y",
)


def date_format_drift(rng: random.Random, iso_date: str) -> str:
    """Re-render an ISO date in a different, still-normalizable format."""

    parsed = datetime.strptime(iso_date, "%Y-%m-%d")
    fmt = rng.choice(_DRIFT_FORMATS)
    return parsed.strftime(fmt)


def dob_typo(rng: random.Random, iso_date: str) -> str:
    """Perturb a date by one day, keeping the same ISO format.

    Models a genuine data-entry slip (13th typed as 12th) that survives date
    normalization as a real mismatch, the way the demo fixture's E002/N004
    pair (DOB typo) is deliberately unresolvable from the data alone.
    """

    parsed = datetime.strptime(iso_date, "%Y-%m-%d").date()
    delta_days = rng.choice((-1, 1, 10))
    shifted = parsed + timedelta(days=delta_days)
    return shifted.isoformat()


def address_variant(rng: random.Random, long_form_address: str) -> str:
    """Abbreviate a subset of tokens in a long-form address, USPS-style.

    Mirrors the token classes address.py's deterministic standardizer
    recognizes (directionals, street suffixes, unit designators), so the
    variant and the canonical form standardize to the same key while the raw
    text differs, exactly the case the matcher must absorb.
    """

    abbreviations = {
        "Street": "St",
        "Avenue": "Ave",
        "Boulevard": "Blvd",
        "Drive": "Dr",
        "Road": "Rd",
        "Lane": "Ln",
        "Court": "Ct",
        "Place": "Pl",
        "Terrace": "Ter",
        "North": "N",
        "South": "S",
        "East": "E",
        "West": "W",
        "Apartment": "Apt",
        "Suite": "Ste",
        "Unit": "Unit",
    }
    tokens = long_form_address.split()
    out = []
    for token in tokens:
        bare = token.rstrip(",")
        suffix = "," if token.endswith(",") else ""
        if bare in abbreviations and rng.random() < 0.8:
            out.append(abbreviations[bare] + suffix)
        else:
            out.append(token)
    return " ".join(out)
