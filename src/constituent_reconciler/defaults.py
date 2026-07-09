"""Pre-tuned matching defaults.

This module is the contribution of the project as much as the orchestration is.
A small nonprofit cannot label training pairs or tune match weights, so the
matcher ships with hand-set m and u probabilities per comparison level. No
training, no labeled data, no expectation-maximisation: the model is fully
specified here and runs deterministically.

m_probability is read as "given two records are the same person, how often does
this level fire". u_probability is "given they are different people, how often
does this level fire". The values below encode ordinary survey-data intuition:
two records for the same person usually agree on name and date of birth, but
typos and nicknames happen; two records for different people rarely agree on all
of them at once. They are deliberately conservative on the side that matters,
so that an auto-merge needs strong agreement and weak agreement is sent to a
human instead.

These are defaults, not law. A recipe can override the prior and the thresholds;
a later version will expose per-field overrides. The numbers are documented in
docs/decisions/0001-matcher-and-defaults.md so a reviewer can see the reasoning,
not just the constants.
"""

from __future__ import annotations

from typing import Any

# Prior odds that two records drawn at random are the same person. Constituent
# files are mostly distinct people, so this is low. It is the base rate the
# per-field evidence updates from.
DEFAULT_PRIOR: float = 0.01

# The fail-closed band. A pair at or above AUTO is merged without a human; a pair
# in [REVIEW, AUTO) is sent to the review queue; below REVIEW it is dropped. The
# gap between the two thresholds is where uncertainty lives, and by design it
# goes to a person rather than to an automatic merge.
DEFAULT_AUTO_THRESHOLD: float = 0.97
DEFAULT_REVIEW_THRESHOLD: float = 0.80

# Jaro-Winkler similarity above which two names count as "close" (nickname,
# typo, transliteration) rather than equal or different.
_NAME_CLOSE: float = 0.88

# Jaro-Winkler similarity above which two standardized addresses count as
# "close" (a unit suffix present on one side, a minor typo) rather than
# different. Higher than the name threshold because addresses are longer and a
# loose match is riskier: people share addresses and move.
_ADDRESS_CLOSE: float = 0.90


def _name_null_level(column: str) -> dict[str, Any]:
    return {
        "sql_condition": f'"{column}_l" IS NULL OR "{column}_r" IS NULL '
        f"OR \"{column}_l\" = '' OR \"{column}_r\" = ''",
        "label_for_charts": "null or empty",
        "is_null_level": True,
    }


def _name_close_level(column: str) -> dict[str, Any]:
    return {
        "sql_condition": f'jaro_winkler_similarity("{column}_l", "{column}_r") >= {_NAME_CLOSE}',
        "label_for_charts": "close",
    }


def _name_else_level() -> dict[str, Any]:
    return {
        "sql_condition": "ELSE",
        "label_for_charts": "different",
    }


def _first_name_comparison(column: str = "first_name") -> dict[str, Any]:
    """Four-level given-name comparison: exact, nickname, close, else.

    The nickname level sits between exact and Jaro-Winkler "close" because a
    nickname pair (Bill/William, Peggy/Margaret) is not a character-similarity
    match at all; it needs the vendored table in :mod:`nicknames`, looked up
    through the ``first_name_nickname_key`` column normalize.py derives
    alongside ``first_name``. The four m_probabilities sum to 1.0 (the
    convention this module uses throughout, see docs/decisions/0001), moved
    down slightly from the three-level version's 0.92/0.07/0.01 split to make
    room for the new level without changing the overall shape of the prior.
    """

    return {
        "output_column_name": column,
        "comparison_levels": [
            _name_null_level(column),
            {
                "sql_condition": f'"{column}_l" = "{column}_r"',
                "label_for_charts": "exact",
                "m_probability": 0.88,
                "u_probability": 0.01,
            },
            {
                "sql_condition": f'"{column}_nickname_key_l" = "{column}_nickname_key_r" '
                f'AND "{column}_l" != "{column}_r"',
                "label_for_charts": "nickname",
                "m_probability": 0.06,
                "u_probability": 0.01,
            },
            {**_name_close_level(column), "m_probability": 0.05, "u_probability": 0.03},
            {**_name_else_level(), "m_probability": 0.01, "u_probability": 0.95},
        ],
    }


def _last_name_comparison(column: str = "last_name") -> dict[str, Any]:
    """Four-level surname comparison: exact, compound-surname, close, else.

    The exact level carries a Splink term-frequency adjustment: agreement on
    a common surname ("Smith") is weaker evidence of a true match than
    agreement on a rare one, because chance collision is far more likely for
    a common name. ``tf_adjustment_column`` points Splink at the ``last_name``
    column itself; Splink derives the frequency table from the input data at
    predict time (no separate estimation step, see the matching-depth-pack
    decision doc).

    ``tf_adjustment_weight`` is deliberately small (0.05, not Splink's
    default of 1.0). The frequency table is estimated from whatever batch is
    being resolved, which for the target user (a one or two person nonprofit
    IT shop) can be a few dozen records, not a national name-frequency
    reference. At weight 1.0 a surname that happens to be the only one
    repeated in a small batch swings the match probability by two orders of
    magnitude on that fact alone, which regressed the exact-typo fixture in
    this module's own test suite from an auto-merge to a coin flip. A small
    weight keeps the adjustment directionally correct (a common surname is
    still discounted relative to a rare one, see
    ``test_term_frequency_adjustment_favors_the_rarer_surname``) without
    letting a small batch's sampling noise dominate the score. Revisit this
    number if the batch sizes this project sees in practice turn out to be
    much larger, per docs/decisions/0009-matching-depth-pack.md.

    The compound-surname level reads the ``last_name_surname1`` and
    ``last_name_surname2`` columns normalize.py derives (the last two
    whitespace tokens of the raw value) and fires when either surname token on
    one side matches either surname token on the other, modeling the
    paterno/materno two-surname convention. It sits below exact and above
    close: agreeing on one shared surname token is real evidence, but weaker
    than agreeing on the whole string, and it is evidence a Jaro-Winkler
    similarity on the full string would usually miss (the two full strings
    can be quite different in length and character order).
    """

    surname1_l, surname1_r = f'"{column}_surname1_l"', f'"{column}_surname1_r"'
    surname2_l, surname2_r = f'"{column}_surname2_l"', f'"{column}_surname2_r"'
    compound_condition = (
        f"({surname1_l} <> '' AND {surname1_l} IN ({surname1_r}, {surname2_r})) OR "
        f"({surname2_l} <> '' AND {surname2_l} IN ({surname1_r}, {surname2_r}))"
    )

    return {
        "output_column_name": column,
        "comparison_levels": [
            _name_null_level(column),
            {
                "sql_condition": f'"{column}_l" = "{column}_r"',
                "label_for_charts": "exact",
                "m_probability": 0.90,
                "u_probability": 0.01,
                "tf_adjustment_column": column,
                "tf_adjustment_weight": 0.05,
            },
            {
                "sql_condition": compound_condition,
                "label_for_charts": "shared compound surname token",
                "m_probability": 0.06,
                "u_probability": 0.02,
            },
            {**_name_close_level(column), "m_probability": 0.03, "u_probability": 0.02},
            {**_name_else_level(), "m_probability": 0.01, "u_probability": 0.95},
        ],
    }


def _address_comparison(column: str) -> dict[str, Any]:
    """Three-level address comparison: exact, close (Jaro-Winkler), else.

    Agreement on a full standardized address is good evidence but not as decisive
    as email: families and shelter residents share an address, so the weights are
    set below the email level and a loose match is sent toward review rather than
    auto-merge.
    """

    return {
        "output_column_name": column,
        "comparison_levels": [
            {
                "sql_condition": f'"{column}_l" IS NULL OR "{column}_r" IS NULL '
                f"OR \"{column}_l\" = '' OR \"{column}_r\" = ''",
                "label_for_charts": "null or empty",
                "is_null_level": True,
            },
            {
                "sql_condition": f'"{column}_l" = "{column}_r"',
                "label_for_charts": "exact",
                "m_probability": 0.80,
                "u_probability": 0.03,
            },
            {
                "sql_condition": f'jaro_winkler_similarity("{column}_l", "{column}_r") '
                f">= {_ADDRESS_CLOSE}",
                "label_for_charts": "close",
                "m_probability": 0.13,
                "u_probability": 0.05,
            },
            {
                "sql_condition": "ELSE",
                "label_for_charts": "different",
                "m_probability": 0.07,
                "u_probability": 0.92,
            },
        ],
    }


def _exact_comparison(column: str, m_yes: float, u_yes: float) -> dict[str, Any]:
    """Two-level exact comparison (used for dob, email, phone)."""

    return {
        "output_column_name": column,
        "comparison_levels": [
            {
                "sql_condition": f'"{column}_l" IS NULL OR "{column}_r" IS NULL '
                f"OR \"{column}_l\" = '' OR \"{column}_r\" = ''",
                "label_for_charts": "null or empty",
                "is_null_level": True,
            },
            {
                "sql_condition": f'"{column}_l" = "{column}_r"',
                "label_for_charts": "exact",
                "m_probability": m_yes,
                "u_probability": u_yes,
            },
            {
                "sql_condition": "ELSE",
                "label_for_charts": "different",
                "m_probability": 1.0 - m_yes,
                "u_probability": 1.0 - u_yes,
            },
        ],
    }


# Per-field comparison builders for the canonical schema. Email and phone get
# strong exact weights because agreement on them is rare by chance; date of
# birth is strong but not decisive on its own.
_COMPARISON_BUILDERS = {
    "first_name": lambda: _first_name_comparison(),
    "last_name": lambda: _last_name_comparison(),
    "dob": lambda: _exact_comparison("dob", m_yes=0.90, u_yes=0.01),
    "email": lambda: _exact_comparison("email", m_yes=0.85, u_yes=0.005),
    "phone": lambda: _exact_comparison("phone", m_yes=0.80, u_yes=0.01),
    "address": lambda: _address_comparison("address"),
}


def comparisons_for(fields: tuple[str, ...]) -> list[dict[str, Any]]:
    """Build the comparison list for the active canonical fields."""

    return [_COMPARISON_BUILDERS[f]() for f in fields if f in _COMPARISON_BUILDERS]


def blocking_rules_for(fields: tuple[str, ...]) -> list[str]:
    """Columns to block on when generating candidate pairs.

    Blocking limits comparisons to records that already agree on something, so
    the run stays cheap. Several rules are used because no single one catches
    every true duplicate: a date-of-birth change of format is caught by the
    surname rule, a surname change is caught by the date rule, and so on. A true
    duplicate that agrees on none of these is out of scope for v0.1 and is
    reported honestly as a blocking miss in the eval.

    When ``last_name`` is active, a phonetic blocking rule on
    ``last_name_soundex`` (normalize.py derives this column alongside
    ``last_name``) is added on top of the exact-match rule. Exact-string
    blocking on last_name misses a transliteration variant of a surname
    (a name typed with different but phonetically equivalent spelling); the
    Soundex rule generates candidate pairs for those too, at the cost of a few
    more comparisons the scorer then rejects. It is additive, not a
    replacement: both rules run, and Splink unions the candidate pairs they
    produce.
    """

    candidates = ["dob", "last_name", "email", "first_name"]
    rules = [c for c in candidates if c in fields]
    if "last_name" in fields:
        rules.append("last_name_soundex")
    return rules
